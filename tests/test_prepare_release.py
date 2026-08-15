import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.prepare_release as pr
from scripts.prepare_release import MirrorError, validate_release


VALID = {
    "product": "grafana",
    "version": "13.1.3",
    "channels": {"stable": True, "preview": False, "beta": False, "nightly": False},
    "license": "agplv3",
    "packages": [
        {
            "product": "grafana",
            "version": "13.1.3",
            "arch": "amd64",
            "os": "deb",
            "url": "https://dl.grafana.com/grafana/release/13.1.3/grafana_13.1.3_build_linux_amd64.deb",
            "sha256": "a" * 64,
            "license": "agplv3",
        },
        {
            "product": "grafana",
            "version": "13.1.3",
            "arch": "arm64",
            "os": "deb",
            "url": "https://dl.grafana.com/grafana/release/13.1.3/grafana_13.1.3_build_linux_arm64.deb",
            "sha256": "b" * 64,
            "license": "agplv3",
        },
    ],
}


class ValidateReleaseTests(unittest.TestCase):
    def assert_rejected(self, mutator, pattern=None):
        metadata = copy.deepcopy(VALID)
        mutator(metadata)
        with self.assertRaises(MirrorError) as ctx:
            validate_release(metadata)
        if pattern:
            self.assertIn(pattern, str(ctx.exception))

    def test_accepts_exactly_required_debian_architectures(self):
        version, packages = validate_release(copy.deepcopy(VALID))
        self.assertEqual(version, "13.1.3")
        self.assertEqual([p["arch"] for p in packages], ["amd64", "arm64"])

    def test_rejects_non_grafana_root_product(self):
        self.assert_rejected(lambda m: m.__setitem__("product", "grafana-enterprise"), "product")

    def test_rejects_non_agpl_root_license(self):
        self.assert_rejected(lambda m: m.__setitem__("license", "proprietary"), "license")

    def test_rejects_non_stable_channel(self):
        self.assert_rejected(lambda m: m["channels"].__setitem__("stable", False), "stable")

    def test_rejects_preview_beta_or_nightly_channels(self):
        for key in ("preview", "beta", "nightly"):
            with self.subTest(channel=key):
                self.assert_rejected(lambda m, k=key: m["channels"].__setitem__(k, True), key)

    def test_rejects_malformed_version(self):
        self.assert_rejected(lambda m: m.__setitem__("version", "13.1.3-beta.1"), "version")

    def test_rejects_missing_required_architecture(self):
        self.assert_rejected(lambda m: m["packages"].pop(), "arm64")

    def test_rejects_duplicate_required_architecture(self):
        def mutate(m):
            duplicate = copy.deepcopy(m["packages"][0])
            duplicate["url"] = duplicate["url"].replace("amd64.deb", "amd64-copy.deb")
            m["packages"].append(duplicate)
        self.assert_rejected(mutate, "amd64")

    def test_rejects_package_version_mismatch(self):
        self.assert_rejected(lambda m: m["packages"][0].__setitem__("version", "13.1.2"), "version")

    def test_rejects_non_agpl_package(self):
        self.assert_rejected(lambda m: m["packages"][0].__setitem__("license", "enterprise"), "license")

    def test_ignores_non_debian_packages_but_requires_debian_pair(self):
        metadata = copy.deepcopy(VALID)
        extra = copy.deepcopy(metadata["packages"][0])
        extra["os"] = "rhel"
        extra["url"] = "https://dl.grafana.com/grafana/release/13.1.3/grafana_13.1.3_build_linux_amd64.rpm"
        metadata["packages"].append(extra)
        version, packages = validate_release(metadata)
        self.assertEqual(version, "13.1.3")
        self.assertEqual(len(packages), 2)

    def test_ignores_unexpected_architecture(self):
        metadata = copy.deepcopy(VALID)
        extra = copy.deepcopy(metadata["packages"][0])
        extra["arch"] = "armv7"
        extra["url"] = "https://dl.grafana.com/grafana/release/13.1.3/grafana_13.1.3_build_linux_arm-7.deb"
        metadata["packages"].append(extra)
        _, packages = validate_release(metadata)
        self.assertEqual([p["arch"] for p in packages], ["amd64", "arm64"])

    def test_rejects_wrong_package_product(self):
        self.assert_rejected(lambda m: m["packages"][0].__setitem__("product", "other"), "product")

    def test_rejects_http_package_url(self):
        self.assert_rejected(
            lambda m: m["packages"][0].__setitem__(
                "url", m["packages"][0]["url"].replace("https://", "http://")
            ),
            "URL",
        )

    def test_rejects_unexpected_package_host(self):
        self.assert_rejected(
            lambda m: m["packages"][0].__setitem__(
                "url", m["packages"][0]["url"].replace("dl.grafana.com", "example.com")
            ),
            "URL",
        )

    def test_rejects_unexpected_release_path(self):
        self.assert_rejected(
            lambda m: m["packages"][0].__setitem__(
                "url", m["packages"][0]["url"].replace("/13.1.3/", "/13.1.2/")
            ),
            "URL",
        )

    def test_rejects_non_deb_url(self):
        self.assert_rejected(
            lambda m: m["packages"][0].__setitem__(
                "url", m["packages"][0]["url"].replace(".deb", ".rpm")
            ),
            ".deb",
        )

    def test_rejects_query_or_fragment_in_package_url(self):
        for suffix in ("?x=1", "#fragment"):
            with self.subTest(suffix=suffix):
                self.assert_rejected(
                    lambda m, s=suffix: m["packages"][0].__setitem__(
                        "url", m["packages"][0]["url"] + s
                    ),
                    "URL",
                )

    def test_rejects_malformed_sha256(self):
        self.assert_rejected(lambda m: m["packages"][0].__setitem__("sha256", "xyz"), "SHA-256")


class PrepareReleaseTests(unittest.TestCase):
    def make_fixture(self):
        amd64 = b"amd64-deb-content\n"
        arm64 = b"arm64-deb-content\n"
        source = b"source-tarball-content\n"
        license_bytes = b"GNU AFFERO GENERAL PUBLIC LICENSE\n"
        metadata = copy.deepcopy(VALID)
        metadata["packages"][0]["sha256"] = hashlib.sha256(amd64).hexdigest()
        metadata["packages"][1]["sha256"] = hashlib.sha256(arm64).hexdigest()
        raw = json.dumps(metadata, indent=2, sort_keys=False).encode() + b"\n"
        return metadata, raw, amd64, arm64, source, license_bytes

    def run_preparation(self, root: Path, mutate_metadata=None):
        metadata, raw, amd64, arm64, source, license_bytes = self.make_fixture()
        if mutate_metadata:
            mutate_metadata(metadata)
            raw = json.dumps(metadata, indent=2, sort_keys=False).encode() + b"\n"

        url_bytes = {
            metadata["packages"][0]["url"]: amd64,
            metadata["packages"][1]["url"]: arm64,
            pr.SOURCE_URL.format(version=metadata["version"]): source,
        }

        def fake_fetch_bytes(url):
            if url == pr.STABLE_METADATA_URL:
                return raw
            if url == pr.LICENSE_URL.format(version=metadata["version"]):
                return license_bytes
            raise AssertionError(f"unexpected fetch URL: {url}")

        def fake_download_to_path(url, path):
            try:
                content = url_bytes[url]
            except KeyError as exc:
                raise AssertionError(f"unexpected download URL: {url}") from exc
            Path(path).write_bytes(content)

        with patch.object(pr, "fetch_bytes", side_effect=fake_fetch_bytes), patch.object(
            pr, "download_to_path", side_effect=fake_download_to_path
        ):
            info = pr.prepare_release(root)
        return info, raw, amd64, arm64, source, license_bytes

    def test_rejects_binary_when_downloaded_sha256_does_not_match_upstream(self):
        metadata, raw, amd64, arm64, source, license_bytes = self.make_fixture()
        raw = json.dumps(metadata).encode()

        def fake_fetch_bytes(url):
            if url == pr.STABLE_METADATA_URL:
                return raw
            if url == pr.LICENSE_URL.format(version="13.1.3"):
                return license_bytes
            raise AssertionError(url)

        def fake_download_to_path(url, path):
            if url.endswith("amd64.deb"):
                Path(path).write_bytes(b"tampered")
            elif url.endswith("arm64.deb"):
                Path(path).write_bytes(arm64)
            elif url == pr.SOURCE_URL.format(version="13.1.3"):
                Path(path).write_bytes(source)
            else:
                raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            pr, "fetch_bytes", side_effect=fake_fetch_bytes
        ), patch.object(pr, "download_to_path", side_effect=fake_download_to_path):
            with self.assertRaises(MirrorError) as ctx:
                pr.prepare_release(Path(tmp) / "dist")
            self.assertIn("SHA-256 mismatch", str(ctx.exception))

    def test_preserves_upstream_metadata_bytes_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            _, raw, *_ = self.run_preparation(out)
            self.assertEqual((out / "upstream-metadata.json").read_bytes(), raw)

    def test_writes_upstream_manifest_only_for_two_binary_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            _, _, amd64, arm64, *_ = self.run_preparation(out)
            text = (out / "UPSTREAM-SHA256SUMS").read_text()
            lines = [line for line in text.splitlines() if line]
            self.assertEqual(len(lines), 2)
            self.assertIn(hashlib.sha256(amd64).hexdigest(), lines[0])
            self.assertIn("amd64.deb", lines[0])
            self.assertIn(hashlib.sha256(arm64).hexdigest(), lines[1])
            self.assertIn("arm64.deb", lines[1])

    def test_writes_versioned_source_and_license_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            _, _, _, _, source, license_bytes = self.run_preparation(out)
            self.assertEqual((out / "grafana-13.1.3-source.tar.gz").read_bytes(), source)
            self.assertEqual((out / "grafana-13.1.3-LICENSE.txt").read_bytes(), license_bytes)

    def test_mirror_manifest_covers_every_asset_except_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            info, *_ = self.run_preparation(out)
            manifest = (out / "MIRROR-SHA256SUMS").read_text().splitlines()
            names = {line.split("  ", 1)[1] for line in manifest if line}
            expected = {p.name for p in info.assets if p.name != "MIRROR-SHA256SUMS"}
            self.assertEqual(names, expected)
            for line in manifest:
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), digest)

    def test_release_info_identifies_version_tag_assets_and_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            info, *_ = self.run_preparation(out)
            self.assertEqual(info.version, "13.1.3")
            self.assertEqual(info.tag, "grafana-oss-13.1.3")
            self.assertTrue(info.notes_path.name == "RELEASE-NOTES.md")
            self.assertTrue(info.notes_path.exists())
            self.assertNotIn(info.notes_path, info.assets)
            self.assertEqual(len(info.assets), 7)

    def test_release_notes_include_provenance_source_license_and_trademark_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            info, *_ = self.run_preparation(out)
            notes = info.notes_path.read_text()
            self.assertIn("Grafana OSS 13.1.3", notes)
            self.assertIn("unmodified", notes.lower())
            self.assertIn("SHA-256", notes)
            self.assertIn("AGPL-3.0-only", notes)
            self.assertIn("https://github.com/grafana/grafana/tree/v13.1.3", notes)
            self.assertIn(
                "The Grafana Labs Marks are trademarks of Grafana Labs, and are used with Grafana Labs’ permission. "
                "We are not affiliated with, endorsed or sponsored by Grafana Labs or its affiliates.",
                notes,
            )

    def test_rejects_nonempty_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            out.mkdir()
            (out / "stale.bin").write_bytes(b"stale")
            with self.assertRaises(MirrorError) as ctx:
                pr.prepare_release(out)
            self.assertIn("output directory", str(ctx.exception))


class CliTests(unittest.TestCase):
    def test_metadata_only_writes_version_and_tag_without_downloading_assets(self):
        raw = json.dumps(VALID).encode() + b"\n"
        with tempfile.TemporaryDirectory() as tmp, patch.object(pr, "fetch_bytes", return_value=raw), patch.object(
            pr, "download_to_path"
        ) as downloader:
            output = Path(tmp) / "github-output"
            rc = pr.main(["--metadata-only", "--github-output", str(output)])
            self.assertEqual(rc, 0)
            self.assertEqual(output.read_text().splitlines(), ["version=13.1.3", "tag=grafana-oss-13.1.3"])
            downloader.assert_not_called()

    def test_full_mode_writes_version_tag_and_notes_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            notes = out / "RELEASE-NOTES.md"
            output = Path(tmp) / "github-output"
            fake_info = pr.ReleaseInfo(
                version="13.1.3",
                tag="grafana-oss-13.1.3",
                assets=(),
                notes_path=notes,
            )
            with patch.object(pr, "prepare_release", return_value=fake_info) as prepare:
                rc = pr.main(["--output-dir", str(out), "--github-output", str(output)])
            self.assertEqual(rc, 0)
            prepare.assert_called_once_with(out, pr.STABLE_METADATA_URL)
            self.assertEqual(
                output.read_text().splitlines(),
                [f"version=13.1.3", "tag=grafana-oss-13.1.3", f"notes={notes}"],
            )


class RepositoryContractTests(unittest.TestCase):
    def test_repository_name_is_canonical_in_public_metadata(self):
        canonical = "VladislavGatsenko/grafana-oss-release-mirror"
        legacy = "VladislavGatsenko/oss-release-mirror"
        self.assertIn(canonical, pr.USER_AGENT)
        for path in (
            Path("docs/superpowers/specs/2026-08-15-grafana-oss-release-mirror-design.md"),
            Path("docs/superpowers/plans/2026-08-15-grafana-oss-release-mirror.md"),
        ):
            with self.subTest(path=path):
                text = path.read_text()
                self.assertIn(canonical, text)
                self.assertNotIn(legacy, text)

    def test_workflow_has_narrow_permissions_schedule_and_release_lifecycle(self):
        workflow = Path(".github/workflows/mirror.yml").read_text()
        self.assertIn('cron: "17 */6 * * *"', workflow)
        self.assertIn('cron: "41 3 * * 0"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("push:", workflow)
        self.assertIn('paths:', workflow)
        self.assertIn('".github/workflows/mirror.yml"', workflow)
        self.assertIn('"scripts/prepare_release.py"', workflow)
        self.assertIn("contents: write", workflow)
        self.assertNotIn("issues: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn("python3 scripts/prepare_release.py", workflow)
        self.assertIn("--metadata-only", workflow)
        self.assertIn("gh release view", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("--draft", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("gh release edit", workflow)
        self.assertIn("--draft=false", workflow)
        self.assertIn("gh release delete", workflow)
        self.assertIn("github.event.schedule == '41 3 * * 0'", workflow)
        self.assertIn(".mirror/heartbeat.txt", workflow)
        self.assertIn('git commit -m "chore: keep scheduled mirror active"', workflow)
        self.assertIn("git push", workflow)
        uses_lines = [line.strip() for line in workflow.splitlines() if line.strip().startswith("uses:")]
        self.assertEqual(
            uses_lines,
            [
                "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
                "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
            ],
        )

    def test_readme_documents_integrity_source_license_and_unofficial_status(self):
        readme = Path("README.md").read_text()
        self.assertIn("unofficial", readme.lower())
        self.assertIn("amd64", readme)
        self.assertIn("arm64", readme)
        self.assertIn("sha256sum -c UPSTREAM-SHA256SUMS", readme)
        self.assertIn("corresponding source", readme.lower())
        self.assertIn("AGPL-3.0-only", readme)
        self.assertIn("MIT", readme)
        self.assertIn("60 days", readme)
        self.assertIn("heartbeat", readme.lower())
        self.assertIn(pr.TRADEMARK_NOTICE, readme)


if __name__ == "__main__":
    unittest.main()
