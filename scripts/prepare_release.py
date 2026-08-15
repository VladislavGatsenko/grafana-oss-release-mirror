#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

REQUIRED_ARCHES = ("amd64", "arm64")
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


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    assets: tuple[Path, ...]
    notes_path: Path


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MirrorError(message)


def _validate_package(package: dict, version: str) -> None:
    _require(package.get("product") == "grafana", "package product must be grafana")
    _require(package.get("version") == version, "package version must match release version")
    _require(package.get("license") == "agplv3", "package license must be agplv3")
    _require(package.get("os") == "deb", "selected package OS must be deb")
    _require(package.get("arch") in REQUIRED_ARCHES, "selected package architecture is not allowed")

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
    _require(filename.endswith(".deb"), "package URL must end in .deb")
    _require(filename not in {"", ".", ".."}, "package URL has an invalid filename")

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

    selected: list[dict] = []
    for arch in REQUIRED_ARCHES:
        matches = [p for p in packages if isinstance(p, dict) and p.get("os") == "deb" and p.get("arch") == arch]
        _require(len(matches) == 1, f"expected exactly one deb package for {arch}, found {len(matches)}")
        _validate_package(matches[0], version)
        selected.append(matches[0])

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


def _metadata_from_bytes(raw: bytes) -> dict:
    try:
        decoded = raw.decode("utf-8")
        metadata = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MirrorError(f"stable metadata is not valid UTF-8 JSON: {exc}") from exc
    _require(isinstance(metadata, dict), "metadata must be an object")
    return metadata


def _release_notes(version: str) -> str:
    return f"""# Grafana OSS {version} — unofficial mirror

This release is an independent, unofficial mirror of **unmodified** Grafana OSS Debian/Ubuntu packages downloaded from Grafana's official release infrastructure.

## Integrity and provenance

- The two `.deb` files are stored with their original upstream filenames and are not modified, renamed, recompressed, or repackaged.
- Each `.deb` is verified against the **SHA-256** published by the Grafana stable release API before this release is created.
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


def prepare_release(output_dir: Path, metadata_url: str = STABLE_METADATA_URL) -> ReleaseInfo:
    output_dir = Path(output_dir)
    if output_dir.exists():
        _require(output_dir.is_dir(), "output directory path exists and is not a directory")
        _require(not any(output_dir.iterdir()), "output directory must be empty")
    else:
        output_dir.mkdir(parents=True)

    metadata_raw = fetch_bytes(metadata_url)
    metadata = _metadata_from_bytes(metadata_raw)
    version, packages = validate_release(metadata)
    tag = f"grafana-oss-{version}"

    assets: list[Path] = []
    upstream_lines: list[str] = []

    for package in packages:
        url = package["url"]
        filename = PurePosixPath(urlparse(url).path).name
        destination = output_dir / filename
        download_to_path(url, destination)
        actual = sha256_file(destination)
        expected = package["sha256"].lower()
        if actual != expected:
            destination.unlink(missing_ok=True)
            raise MirrorError(
                f"SHA-256 mismatch for {filename}: expected {expected}, got {actual}"
            )
        upstream_lines.append(f"{expected}  {filename}")
        assets.append(destination)

    source_path = output_dir / f"grafana-{version}-source.tar.gz"
    download_to_path(SOURCE_URL.format(version=version), source_path)
    assets.append(source_path)

    license_path = output_dir / f"grafana-{version}-LICENSE.txt"
    license_path.write_bytes(fetch_bytes(LICENSE_URL.format(version=version)))
    assets.append(license_path)

    metadata_path = output_dir / "upstream-metadata.json"
    metadata_path.write_bytes(metadata_raw)
    assets.append(metadata_path)

    upstream_manifest = output_dir / "UPSTREAM-SHA256SUMS"
    upstream_manifest.write_text("\n".join(upstream_lines) + "\n", encoding="utf-8")
    assets.append(upstream_manifest)

    mirror_manifest = output_dir / "MIRROR-SHA256SUMS"
    mirror_lines = [f"{sha256_file(path)}  {path.name}" for path in sorted(assets, key=lambda p: p.name)]
    mirror_manifest.write_text("\n".join(mirror_lines) + "\n", encoding="utf-8")
    assets.append(mirror_manifest)

    notes_path = output_dir / "RELEASE-NOTES.md"
    notes_path.write_text(_release_notes(version), encoding="utf-8")

    return ReleaseInfo(version=version, tag=tag, assets=tuple(assets), notes_path=notes_path)


def get_release_identity(metadata_url: str = STABLE_METADATA_URL) -> tuple[str, str]:
    metadata_raw = fetch_bytes(metadata_url)
    metadata = _metadata_from_bytes(metadata_raw)
    version, _ = validate_release(metadata)
    return version, f"grafana-oss-{version}"


def _write_github_output(path: Path, version: str, tag: str, notes: Path | None = None) -> None:
    lines = [f"version={version}", f"tag={tag}"]
    if notes is not None:
        lines.append(f"notes={notes}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a verified Grafana OSS GitHub Release mirror payload")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--metadata-url", default=STABLE_METADATA_URL)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)

    if args.metadata_only:
        version, tag = get_release_identity(args.metadata_url)
        if args.github_output:
            _write_github_output(args.github_output, version, tag)
        print(f"Stable Grafana OSS: {version} ({tag})")
        return 0

    info = prepare_release(args.output_dir, args.metadata_url)
    if args.github_output:
        _write_github_output(args.github_output, info.version, info.tag, info.notes_path)
    print(f"Prepared Grafana OSS {info.version}: {len(info.assets)} release assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
