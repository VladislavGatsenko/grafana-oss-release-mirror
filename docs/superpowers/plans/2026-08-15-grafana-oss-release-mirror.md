# Grafana OSS Release Mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish an unattended GitHub Release mirror for unmodified stable Grafana OSS Debian/Ubuntu amd64 and arm64 packages, with corresponding source, license, metadata, and integrity manifests.

**Architecture:** A Python standard-library preparation tool owns all upstream policy, download, and checksum verification. A thin GitHub Actions workflow owns only schedule/idempotency/release publication via the ephemeral `GITHUB_TOKEN`. Unit tests exercise fail-closed policy offline; a live smoke mode verifies the current upstream release before deployment.

**Tech Stack:** Python 3 standard library, `unittest`, GitHub Actions, GitHub CLI (`gh`) on `ubuntu-latest`, GitHub Releases.

## Global Constraints

- Deployment target is `VladislavGatsenko/grafana-oss-release-mirror`, public.
- Mirror only stable Grafana OSS where root and package license equal `agplv3`.
- Mirror exactly Debian/Ubuntu `amd64` and `arm64` `.deb` packages.
- Never mirror Enterprise, preview, beta, nightly, RPM, Windows/macOS, Docker, or plugins.
- Never modify, rename, recompress, or repackage Grafana binaries.
- Include source snapshot for tag `v{version}`, upstream LICENSE, exact upstream metadata, upstream binary checksum manifest, and mirror checksum manifest.
- Fail closed on any schema/policy/URL/checksum mismatch.
- GitHub Actions permission is only `contents: write`; no PAT or long-lived secret.
- Prevent GitHub's 60-day public-repository schedule deactivation with a weekly heartbeat commit that cannot recursively trigger the mirror job.
- Relevant pushes to the workflow or preparation script trigger the mirror job so initial deployment runs without a separate dispatch API call.
- No third-party GitHub Actions are needed in the mirror path.
- Mirror tag is `grafana-oss-{version}`.
- Existing non-draft release for the tag is immutable/idempotent; stale draft is deleted and rebuilt.

---

### Task 1: Metadata policy and package selection

**Files:**
- Create: `scripts/prepare_release.py`
- Create: `tests/test_prepare_release.py`

**Interfaces:**
- Consumes: Grafana stable metadata as a Python `dict`.
- Produces: `validate_release(metadata: dict) -> tuple[str, list[dict]]`, returning the stable version and exactly two package dictionaries ordered `amd64`, `arm64`.

- [ ] **Step 1: Write failing policy tests**

Create tests for: valid metadata; root non-AGPL; non-stable channel; malformed semantic version; missing arm64; duplicate amd64; package version mismatch; non-AGPL package; wrong OS; unexpected architecture; non-HTTPS/unexpected host path; non-`.deb` URL; malformed SHA-256.

Core valid fixture:

```python
VALID = {
    "product": "grafana",
    "version": "13.1.3",
    "channels": {"stable": True, "preview": False, "beta": False, "nightly": False},
    "license": "agplv3",
    "packages": [
        {
            "product": "grafana", "version": "13.1.3", "arch": "amd64", "os": "deb",
            "url": "https://dl.grafana.com/grafana/release/13.1.3/grafana_13.1.3_build_linux_amd64.deb",
            "sha256": "a" * 64, "license": "agplv3"
        },
        {
            "product": "grafana", "version": "13.1.3", "arch": "arm64", "os": "deb",
            "url": "https://dl.grafana.com/grafana/release/13.1.3/grafana_13.1.3_build_linux_arm64.deb",
            "sha256": "b" * 64, "license": "agplv3"
        }
    ]
}
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest -v tests.test_prepare_release
```

Expected: import failure because `scripts.prepare_release` does not exist.

- [ ] **Step 3: Implement strict validation**

Implement constants:

```python
REQUIRED_ARCHES = ("amd64", "arm64")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
```

`validate_release()` must reject anything outside Global Constraints and require exactly one matching package for each required architecture. Package URL parsing uses `urllib.parse.urlparse`; scheme must be `https`, netloc exactly `dl.grafana.com`, path prefix exactly `/grafana/release/{version}/`, basename must end `.deb`, and query/fragment must be empty.

- [ ] **Step 4: Run policy tests and verify pass**

Run the unittest command above. Expected: all policy tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/prepare_release.py tests/test_prepare_release.py
git commit -m "feat: validate Grafana OSS release metadata"
```

---

### Task 2: Verified asset preparation and manifests

**Files:**
- Modify: `scripts/prepare_release.py`
- Modify: `tests/test_prepare_release.py`

**Interfaces:**
- Consumes: `prepare_release(output_dir: Path, metadata_url: str = STABLE_METADATA_URL) -> ReleaseInfo`.
- Produces: verified files in `output_dir`; `ReleaseInfo(version, tag, assets, notes_path)`; CLI output file selected by `--github-output` with `version=...`, `tag=...`, and `notes=...`.

- [ ] **Step 1: Write failing download/hash/manifest tests**

Use `tempfile.TemporaryDirectory()` and `unittest.mock.patch` to replace `download_bytes()` with deterministic bytes. Test that:

- a binary with mismatched hash raises `MirrorError`;
- correct binaries generate `UPSTREAM-SHA256SUMS` containing only upstream hashes;
- `upstream-metadata.json` is byte-for-byte the fetched metadata payload;
- source and license filenames are versioned and included;
- `MIRROR-SHA256SUMS` contains every release asset except itself;
- release notes include the version, exact source tag URL, AGPL statement, unmodified/checksum provenance, and required Grafana Labs trademark attribution.

- [ ] **Step 2: Run tests and verify failure**

```bash
python3 -m unittest -v tests.test_prepare_release
```

Expected: failures for missing preparation functions.

- [ ] **Step 3: Implement verified preparation**

Implement:

```python
@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    assets: tuple[Path, ...]
    notes_path: Path
```

Network endpoints:

```python
STABLE_METADATA_URL = "https://grafana.com/api/grafana/versions/stable"
SOURCE_URL = "https://github.com/grafana/grafana/archive/refs/tags/v{version}.tar.gz"
LICENSE_URL = "https://raw.githubusercontent.com/grafana/grafana/v{version}/LICENSE"
```

Downloads use `urllib.request.Request` with a descriptive User-Agent and timeout. Binary files are saved using their exact upstream basename. Hash verification uses streaming `hashlib.sha256()` before any publish metadata is emitted.

Source filename: `grafana-{version}-source.tar.gz`.
License filename: `grafana-{version}-LICENSE.txt`.
Metadata filename: `upstream-metadata.json`.

Write `UPSTREAM-SHA256SUMS` sorted by architecture and `MIRROR-SHA256SUMS` sorted by filename. Generate `RELEASE-NOTES.md` but do not upload it as a release asset; it is passed as release body.

- [ ] **Step 4: Run complete unit suite**

```bash
python3 -m unittest -v tests.test_prepare_release
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/prepare_release.py tests/test_prepare_release.py
git commit -m "feat: prepare and verify mirror release assets"
```

---

### Task 3: Repository documentation and GitHub Actions orchestration

**Files:**
- Create: `.github/workflows/mirror.yml`
- Create: `README.md`
- Create: `LICENSE`
- Create: `.gitignore`
- Modify: `tests/test_prepare_release.py`

**Interfaces:**
- Consumes: `python3 scripts/prepare_release.py --output-dir dist --github-output "$GITHUB_OUTPUT"`.
- Produces: one immutable public GitHub Release per stable version.

- [ ] **Step 1: Add static workflow contract tests**

Read `.github/workflows/mirror.yml` as text and assert it contains:

- `push`, `schedule`, and `workflow_dispatch`;
- mirror cron `17 */6 * * *`;
- heartbeat cron `41 3 * * 0` and a heartbeat commit to `.mirror/heartbeat.txt`;
- `contents: write` and no broader permission declaration;
- `concurrency`;
- no `uses:` third-party action dependency;
- calls to the preparation script;
- `gh release view`, draft creation, asset upload, publication, and failure cleanup.

- [ ] **Step 2: Run tests and verify failure**

```bash
python3 -m unittest -v tests.test_prepare_release
```

Expected: workflow contract test fails because workflow file does not exist.

- [ ] **Step 3: Implement workflow**

The mirror job first calls the preparation script with a metadata-only mode to obtain `version/tag` without large downloads. A separate heartbeat job runs only for the weekly heartbeat cron, commits `.mirror/heartbeat.txt`, and pushes it with the repository `GITHUB_TOKEN`; the push trigger excludes that path. It checks `gh release view "$TAG" --json isDraft`; published release exits zero, stale draft is deleted. It then runs full preparation, creates a draft release using `RELEASE-NOTES.md`, uploads `dist/*` except release notes, publishes with `gh release edit --draft=false --latest`, and traps failures to delete an incomplete draft.

Use:

```yaml
permissions:
  contents: write

concurrency:
  group: grafana-oss-release-mirror
  cancel-in-progress: false
```

Set `GH_TOKEN: ${{ github.token }}` and `GH_REPO: ${{ github.repository }}` at job level.

- [ ] **Step 4: Write README, repository MIT license, and ignore rules**

README must prominently state:

- independent/unofficial mirror;
- only unmodified Grafana OSS Debian/Ubuntu packages;
- release verification model;
- corresponding source is included in the same release;
- how users verify with `sha256sum -c UPSTREAM-SHA256SUMS`;
- automation code is MIT but Grafana artifacts keep upstream licenses;
- exact required Grafana Labs trademark attribution.

`.gitignore` contains `dist/`, `__pycache__/`, and `*.pyc`.

- [ ] **Step 5: Run complete tests**

```bash
python3 -m unittest -v tests.test_prepare_release
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/mirror.yml README.md LICENSE .gitignore tests/test_prepare_release.py
git commit -m "feat: automate verified GitHub releases"
```

---

### Task 4: Live upstream verification

**Files:**
- No source changes expected unless a verified defect is found.

**Interfaces:**
- Consumes: live `https://grafana.com/api/grafana/versions/stable` and Grafana/GitHub download endpoints.
- Produces: locally verified `dist-live/` release payload.

- [ ] **Step 1: Run metadata-only live smoke test**

```bash
python3 scripts/prepare_release.py --metadata-only --github-output /tmp/mirror-output
cat /tmp/mirror-output
```

Expected current values include `version=13.1.3` and `tag=grafana-oss-13.1.3` as of 2026-08-15.

- [ ] **Step 2: Run full live preparation**

```bash
rm -rf dist-live
python3 scripts/prepare_release.py --output-dir dist-live
```

Expected: two `.deb` files download from `dl.grafana.com`; their calculated SHA-256 values equal the current stable API values; source/LICENSE/metadata/manifests/notes are created.

- [ ] **Step 3: Independently verify manifests**

```bash
cd dist-live
sha256sum -c UPSTREAM-SHA256SUMS
sha256sum -c MIRROR-SHA256SUMS
```

Expected: every line reports `OK`.

- [ ] **Step 4: Inspect Debian package identity without installing**

```bash
dpkg-deb -f dist-live/*linux_amd64.deb Package Version Architecture
dpkg-deb -f dist-live/*linux_arm64.deb Package Version Architecture
```

Expected package `grafana`, version corresponding to stable upstream build, and architectures `amd64` / `arm64`.

---

### Task 5: GitHub deployment and end-to-end verification

**Files:**
- Push all committed repository files to `VladislavGatsenko/grafana-oss-release-mirror`.

**Interfaces:**
- Consumes: repository write access and GitHub Actions.
- Produces: public repository and first verified Release.

- [ ] **Step 1: Create public repository**

Create `VladislavGatsenko/grafana-oss-release-mirror` with default branch `main`, no generated README/license/gitignore because repository content already exists locally.

- [ ] **Step 2: Push commits**

Push local `main` preserving commit history.

- [ ] **Step 3: Observe automatic initial workflow run**

The atomic deployment commit changes the workflow/preparation script and therefore starts `Mirror Grafana OSS` through the restricted `push` trigger. Manual `workflow_dispatch` remains available for later operator use.

- [ ] **Step 4: Inspect workflow run and release**

Verify run conclusion is `success`, release is non-draft, tag matches the detected stable version, and assets match the prepared payload.

- [ ] **Step 5: Verify released binary checksum against upstream API**

Fetch the release metadata and compare the release asset's checksum to the corresponding `.sha256` value in the current Grafana stable API. Do not rely only on the first workflow's own log.

- [ ] **Step 6: Re-run workflow to prove idempotency**

Dispatch it a second time. Expected: successful early exit reporting that the non-draft release already exists; no asset or release replacement occurs.
