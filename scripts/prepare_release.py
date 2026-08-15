#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

REQUIRED_PACKAGES = (
    ("darwin", "amd64"),
    ("darwin", "arm64"),
    ("deb", "amd64"),
    ("deb", "arm64"),
    ("deb", "armv6"),
    ("deb", "armv7"),
    ("linux", "amd64"),
    ("linux", "arm64"),
    ("linux", "armv6"),
    ("linux", "armv7"),
    ("rhel", "amd64"),
    ("rhel", "arm64"),
    ("win", "amd64"),
    ("win", "arm64"),
    ("win-installer", "amd64"),
)
PACKAGE_EXTENSIONS = {
    "darwin": ".tar.gz",
    "deb": ".deb",
    "linux": ".tar.gz",
    "rhel": ".rpm",
    "win": ".tar.gz",
    "win-installer": ".msi",
}
EXPECTED_RELEASE_ASSET_COUNT = len(REQUIRED_PACKAGES) + 5
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

STABLE_METADATA_URL = "https://grafana.com/api/grafana/versions/stable"
SOURCE_URL = "https://github.com/grafana/grafana/archive/refs/tags/v{version}.tar.gz"
LICENSE_URL = "https://raw.githubusercontent.com/grafana/grafana/v{version}/LICENSE"
USER_AGENT = "grafana-oss-release-mirror/1.0 (+https://github.com/VladislavGatsenko/grafana-oss-release-mirror)"
NETWORK_TIMEOUT_SECONDS = 60
TRADEMARK_NOTICE = (
    "The Grafana Labs Marks are trademarks of Grafana Labs, and are used with Grafana Labs’ permission. "
    "We are not affiliated with, endorsed or sponsored by Grafana Labs or its affiliates."
)


class MirrorError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MirrorError(message)


def _validate_package(package: dict, version: str, package_os: str, arch: str) -> None:
    _require(package.get("product") == "grafana", "package product must be grafana")
    _require(package.get("version") == version, "package version must match release version")
    _require(package.get("license") == "agplv3", "package license must be agplv3")
    _require(package.get("os") == package_os, f"package OS must be {package_os}")
    _require(package.get("arch") == arch, f"package architecture must be {arch}")

    url = package.get("url")
    _require(isinstance(url, str) and url, "package URL is missing")
    parsed = urlparse(url)
    expected_prefix = f"/grafana/release/{version}/"
    _require(
        parsed.scheme == "https"
        and parsed.netloc == "dl.grafana.com"
        and parsed.path.startswith(expected_prefix)
        and not parsed.query
        and not parsed.fragment,
        "package URL is outside the allowed Grafana release location",
    )
    filename = PurePosixPath(parsed.path).name
    _require(parsed.path == expected_prefix + filename, "package URL must use the direct Grafana release path")
    prefix = "grafana-rpi" if (package_os, arch) == ("deb", "armv6") else "grafana"
    platform = "darwin" if package_os == "darwin" else "windows" if package_os.startswith("win") else "linux"
    filename_arch = {"armv6": "arm-6", "armv7": "arm-7"}.get(arch, arch)
    extension = PACKAGE_EXTENSIONS[package_os]
    expected_filename = re.compile(
        rf"^{re.escape(prefix)}_{re.escape(version)}_[0-9]+_{platform}_{filename_arch}{re.escape(extension)}$"
    )
    _require(
        expected_filename.fullmatch(filename) is not None,
        f"package URL filename does not match {package_os}/{arch}",
    )

    sha256 = package.get("sha256")
    _require(isinstance(sha256, str) and SHA256_RE.fullmatch(sha256) is not None, "package SHA-256 is invalid")


def validate_release(metadata: dict) -> tuple[str, list[dict]]:
    _require(isinstance(metadata, dict), "metadata must be an object")
    _require(metadata.get("product") == "grafana", "release product must be grafana")
    _require(metadata.get("license") == "agplv3", "release license must be agplv3")

    channels = metadata.get("channels")
    _require(isinstance(channels, dict), "release channels are missing")
    _require(channels.get("stable") is True, "stable channel must be true")
    for channel in ("preview", "beta", "nightly"):
        _require(channels.get(channel) is False, f"{channel} channel must be false")

    version = metadata.get("version")
    _require(isinstance(version, str) and VERSION_RE.fullmatch(version) is not None, "release version must be x.y.z")

    packages = metadata.get("packages")
    _require(isinstance(packages, list), "release packages must be a list")
    _require(
        len(packages) == len(REQUIRED_PACKAGES),
        f"expected exactly {len(REQUIRED_PACKAGES)} stable OSS packages, found {len(packages)}",
    )
    _require(all(isinstance(package, dict) for package in packages), "every package entry must be an object")

    selected: list[dict] = []
    for package_os, arch in REQUIRED_PACKAGES:
        matches = [package for package in packages if package.get("os") == package_os and package.get("arch") == arch]
        _require(
            len(matches) == 1,
            f"expected exactly one {package_os}/{arch} package, found {len(matches)}",
        )
        _validate_package(matches[0], version, package_os, arch)
        selected.append(matches[0])

    filenames = [PurePosixPath(urlparse(package["url"]).path).name for package in selected]
    _require(len(set(filenames)) == len(filenames), "package filenames must be distinct")

    return version, selected


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})


def fetch_bytes(url: str) -> bytes:
    try:
        with urllib.request.urlopen(_request(url), timeout=NETWORK_TIMEOUT_SECONDS) as response:
            return response.read()
    except Exception as exc:  # urllib exposes several transport exception classes
        raise MirrorError(f"failed to fetch {url}: {exc}") from exc


def download_to_path(url: str, path: Path) -> None:
    path = Path(path)
    part = path.with_name(path.name + ".part")
    try:
        with urllib.request.urlopen(_request(url), timeout=NETWORK_TIMEOUT_SECONDS) as response, part.open("wb") as dst:
            shutil.copyfileobj(response, dst, length=1024 * 1024)
        part.replace(path)
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise MirrorError(f"failed to download {url}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_uploaded_assets(release_json_path: Path, mirror_manifest_path: Path) -> None:
    try:
        release = json.loads(Path(release_json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MirrorError(f"release asset metadata is invalid: {exc}") from exc

    _require(isinstance(release, dict), "release asset metadata must be an object")
    _require(release.get("isDraft") is True, "release must remain a draft during asset verification")
    remote_assets = release.get("assets")
    _require(isinstance(remote_assets, list), "release assets must be a list")

    manifest_path = Path(mirror_manifest_path)
    try:
        manifest_lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MirrorError(f"mirror checksum manifest is invalid: {exc}") from exc

    expected: dict[str, str] = {}
    for line in manifest_lines:
        parts = line.split("  ", 1)
        _require(len(parts) == 2, "mirror checksum manifest line is invalid")
        digest, name = parts
        _require(SHA256_RE.fullmatch(digest) is not None, f"mirror checksum is invalid: {name}")
        _require(PurePosixPath(name).name == name and name not in {"", ".", ".."}, "mirror asset name is invalid")
        _require(name not in expected, f"duplicate mirror asset name: {name}")
        expected[name] = digest.lower()

    _require("MIRROR-SHA256SUMS" not in expected, "mirror manifest must not include itself")
    expected["MIRROR-SHA256SUMS"] = sha256_file(manifest_path)
    _require(
        len(expected) == EXPECTED_RELEASE_ASSET_COUNT,
        f"expected exactly {EXPECTED_RELEASE_ASSET_COUNT} release assets, found {len(expected)}",
    )

    remote_by_name: dict[str, dict] = {}
    for asset in remote_assets:
        _require(isinstance(asset, dict), "release asset entry must be an object")
        name = asset.get("name")
        _require(isinstance(name, str) and name, "release asset name is invalid")
        _require(name not in remote_by_name, f"duplicate release asset name: {name}")
        remote_by_name[name] = asset

    _require(set(remote_by_name) == set(expected), "uploaded release asset set does not match verified manifest")
    for name, digest in expected.items():
        asset = remote_by_name[name]
        _require(asset.get("state") == "uploaded", f"release asset state is not uploaded: {name}")
        _require(asset.get("digest") == f"sha256:{digest}", f"release asset digest mismatch: {name}")


def _metadata_from_bytes(raw: bytes) -> dict:
    try:
        decoded = raw.decode("utf-8")
        metadata = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MirrorError(f"stable metadata is not valid UTF-8 JSON: {exc}") from exc
    _require(isinstance(metadata, dict), "metadata must be an object")
    return metadata


def _load_release(metadata_url: str) -> tuple[bytes, str, list[dict]]:
    metadata_raw = fetch_bytes(metadata_url)
    metadata = _metadata_from_bytes(metadata_raw)
    version, packages = validate_release(metadata)
    return metadata_raw, version, packages


def _prepare_output_dir(output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    if output_dir.exists():
        _require(output_dir.is_dir(), "output directory path exists and is not a directory")
        _require(not any(output_dir.iterdir()), "output directory must be empty")
    else:
        output_dir.mkdir(parents=True)
    return output_dir


def _release_notes(version: str) -> str:
    return f"""# Grafana OSS {version} — unofficial mirror

This release is an independent, unofficial mirror of **unmodified** Grafana OSS packages for every platform published by the Grafana stable release API.

## Integrity and provenance

- All 15 package files are stored with their original upstream filenames and are not modified, renamed, recompressed, or repackaged.
- Every package is verified against the **SHA-256** published by the Grafana stable release API before this release is published.
- `UPSTREAM-SHA256SUMS` contains only SHA-256 values published by Grafana for the mirrored binary packages.
- `MIRROR-SHA256SUMS` contains locally calculated hashes for all other release assets as well as the verified binaries.
- `upstream-metadata.json` is the exact stable API response used to select this release.

## Source and license

The mirrored Grafana OSS software is distributed under **AGPL-3.0-only** by the upstream project, with upstream-documented licensing exceptions for some source directories. Corresponding source for this version is included in this release as `grafana-{version}-source.tar.gz`.

Upstream source tag: https://github.com/grafana/grafana/tree/v{version}

Upstream project: https://grafana.com/

The automation code in this mirror repository has its own repository license and does not relicense Grafana artifacts.

## Trademark attribution

{TRADEMARK_NOTICE}
"""


def prepare_package(
    output_dir: Path,
    package_os: str,
    arch: str,
    metadata_url: str = STABLE_METADATA_URL,
) -> tuple[str, str, Path]:
    output_dir = _prepare_output_dir(output_dir)
    _, version, packages = _load_release(metadata_url)
    _require((package_os, arch) in REQUIRED_PACKAGES, f"unsupported package target: {package_os}/{arch}")
    package = next(package for package in packages if package["os"] == package_os and package["arch"] == arch)
    tag = f"grafana-oss-{version}"

    url = package["url"]
    filename = PurePosixPath(urlparse(url).path).name
    destination = output_dir / filename
    download_to_path(url, destination)
    actual = sha256_file(destination)
    expected = package["sha256"].lower()
    if actual != expected:
        destination.unlink(missing_ok=True)
        raise MirrorError(f"SHA-256 mismatch for {filename}: expected {expected}, got {actual}")
    return version, tag, destination


def prepare_provenance(
    output_dir: Path,
    metadata_url: str = STABLE_METADATA_URL,
) -> tuple[str, str, tuple[Path, ...], Path]:
    output_dir = _prepare_output_dir(output_dir)
    metadata_raw, version, packages = _load_release(metadata_url)
    tag = f"grafana-oss-{version}"
    assets: list[Path] = []

    source_path = output_dir / f"grafana-{version}-source.tar.gz"
    download_to_path(SOURCE_URL.format(version=version), source_path)
    assets.append(source_path)

    license_path = output_dir / f"grafana-{version}-LICENSE.txt"
    license_path.write_bytes(fetch_bytes(LICENSE_URL.format(version=version)))
    assets.append(license_path)

    metadata_path = output_dir / "upstream-metadata.json"
    metadata_path.write_bytes(metadata_raw)
    assets.append(metadata_path)

    package_hashes = {
        PurePosixPath(urlparse(package["url"]).path).name: package["sha256"].lower()
        for package in packages
    }
    upstream_manifest = output_dir / "UPSTREAM-SHA256SUMS"
    upstream_manifest.write_text(
        "\n".join(f"{digest}  {name}" for name, digest in sorted(package_hashes.items())) + "\n",
        encoding="utf-8",
    )
    assets.append(upstream_manifest)

    mirror_manifest = output_dir / "MIRROR-SHA256SUMS"
    mirror_hashes = package_hashes | {path.name: sha256_file(path) for path in assets}
    mirror_manifest.write_text(
        "\n".join(f"{digest}  {name}" for name, digest in sorted(mirror_hashes.items())) + "\n",
        encoding="utf-8",
    )
    assets.append(mirror_manifest)

    notes_path = output_dir / "RELEASE-NOTES.md"
    notes_path.write_text(_release_notes(version), encoding="utf-8")

    return version, tag, tuple(assets), notes_path


def _write_github_output(path: Path, version: str, tag: str) -> None:
    matrix = {"include": [{"package_os": package_os, "arch": arch} for package_os, arch in REQUIRED_PACKAGES]}
    lines = [
        f"version={version}",
        f"tag={tag}",
        f"matrix={json.dumps(matrix, separators=(',', ':'))}",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a verified Grafana OSS GitHub Release mirror payload")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--metadata-url", default=STABLE_METADATA_URL)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--metadata-only", action="store_true")
    mode.add_argument("--prepare-package", nargs=2, metavar=("OS", "ARCH"))
    mode.add_argument("--prepare-provenance", action="store_true")
    mode.add_argument("--verify-uploaded-assets", type=Path, metavar="RELEASE_JSON")
    parser.add_argument("--metadata-output", type=Path)
    parser.add_argument("--mirror-manifest", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)

    if args.verify_uploaded_assets:
        if not args.mirror_manifest:
            parser.error("--verify-uploaded-assets requires --mirror-manifest")
        verify_uploaded_assets(args.verify_uploaded_assets, args.mirror_manifest)
        print("Verified uploaded draft release assets")
        return 0

    if args.metadata_only:
        metadata_raw, version, _ = _load_release(args.metadata_url)
        tag = f"grafana-oss-{version}"
        if args.metadata_output:
            args.metadata_output.write_bytes(metadata_raw)
        if args.github_output:
            _write_github_output(args.github_output, version, tag)
        print(f"Stable Grafana OSS: {version} ({tag})")
        return 0

    if args.prepare_package:
        version, tag, asset = prepare_package(args.output_dir, *args.prepare_package, args.metadata_url)
        if args.github_output:
            _write_github_output(args.github_output, version, tag)
        print(f"Prepared and verified {asset.name}")
        return 0

    if args.prepare_provenance:
        version, tag, assets, _ = prepare_provenance(args.output_dir, args.metadata_url)
        if args.github_output:
            _write_github_output(args.github_output, version, tag)
        print(f"Prepared Grafana OSS {version}: {len(assets)} provenance assets")
        return 0

    parser.error("select --metadata-only, --prepare-package, --prepare-provenance, or --verify-uploaded-assets")


if __name__ == "__main__":
    raise SystemExit(main())
