# Grafana OSS Release Mirror — Design

Date: 2026-08-15

## Goal

Deployment target: `VladislavGatsenko/grafana-oss-release-mirror` (public).

Create a public, unattended GitHub release mirror for unmodified Grafana OSS Debian/Ubuntu packages so users who cannot reliably reach Grafana's download infrastructure can obtain verified binaries from GitHub Releases.

## Scope

The mirror publishes only stable Grafana OSS packages whose upstream metadata declares `license=agplv3`.

For each stable version it mirrors exactly:

- Debian/Ubuntu amd64 `.deb`
- Debian/Ubuntu arm64 `.deb`
- the complete source snapshot for the matching upstream Git tag `v{version}`
- the matching upstream `LICENSE`
- the complete upstream stable metadata JSON used for the decision
- upstream SHA-256 manifest for the two binary packages
- a mirror-generated SHA-256 manifest for all release assets

It does not mirror Enterprise packages, beta/preview/nightly releases, RPMs, Windows/macOS binaries, containers, plugins, or historical releases from before the automation is first run.

## Approaches considered

### 1. Targeted GitHub Release mirror — selected

Use the Grafana stable release API as the source of truth and GitHub Releases as binary storage. Mirror only Debian/Ubuntu amd64 and arm64 packages plus corresponding source.

Advantages: minimal storage/bandwidth, simple consumer model, no package repository metadata to maintain, strong provenance checks, small attack surface.

### 2. Full all-platform OSS Release mirror

Mirror every AGPL package exposed by the Grafana stable API.

Rejected for now because it substantially increases bandwidth/storage without serving the stated Debian use case. It can be added later by changing the package policy.

### 3. Full APT repository mirror

Mirror `apt.grafana.com`, package indices, signatures, and packages.

Rejected because it is operationally more complex, requires preserving repository metadata/signature semantics, and is unnecessary when the desired distribution mechanism is GitHub Releases.

## Upstream source of truth

Metadata endpoint:

`https://grafana.com/api/grafana/versions/stable`

A release is eligible only when all of the following hold:

- `.product == "grafana"`
- `.channels.stable == true`
- `.channels.preview == false`
- `.channels.beta == false`
- `.channels.nightly == false`
- `.license == "agplv3"`
- `.version` is a plain three-component semantic version

Each mirrored package must additionally satisfy:

- `.product == "grafana"`
- `.version == root version`
- `.license == "agplv3"`
- `.os == "deb"`
- `.arch` is exactly `amd64` or `arm64`
- URL is HTTPS and begins with `https://dl.grafana.com/grafana/release/{version}/`
- URL basename ends in `.deb`
- SHA-256 is exactly 64 hexadecimal characters

Exactly one package per required architecture must be present. Missing or duplicate matches fail closed.

## Release identity and idempotency

Mirror tag: `grafana-oss-{version}`

Release title: `Grafana OSS {version} — unofficial mirror`

Before downloads, the workflow queries GitHub for that tag. If a non-draft release already exists, the job exits successfully without changing it. If a stale draft exists, it is deleted and rebuilt from scratch.

The release is created as a draft only after all assets have been downloaded and verified. It is published only after every upload succeeds and GitHub reports the exact expected asset names, sizes, uploaded states, and SHA-256 digests. A failed run therefore cannot expose a partial public release.

## Integrity and provenance

The upstream API's SHA-256 values are authoritative for binary package verification. Every `.deb` is hashed locally and must match before release creation.

The source snapshot and upstream LICENSE are downloaded from the matching `grafana/grafana` Git tag. Their hashes are mirror-generated because the stable package API does not publish checksums for those GitHub-generated files.

The release includes:

- `UPSTREAM-SHA256SUMS` — only checksums supplied by Grafana for the two `.deb` packages
- `MIRROR-SHA256SUMS` — locally computed checksums for every distributed asset except the manifest itself
- `upstream-metadata.json` — the exact API response used for the release

No mirrored Grafana binary is modified, renamed, recompressed, or repackaged.

## Licensing and trademarks

Grafana OSS declares AGPL-3.0-only as its default project license, with documented exceptions for some source directories. The binary distribution is accompanied by corresponding source access through the source archive in the same GitHub Release and by the matching upstream license text.

The repository automation code uses the MIT license. README and release notes explicitly state that this license does not relicense mirrored Grafana artifacts.

The repository name deliberately does not contain a Grafana Labs mark. README and release notes use Grafana only to identify the upstream project and include Grafana Labs' required trademark attribution statement.

## GitHub Actions security model

The workflow requires only `contents: write`. It uses the ephemeral repository-scoped `GITHUB_TOKEN`; no PAT or long-lived secret is required.

The only referenced action is GitHub's own `actions/checkout`, pinned to an immutable commit SHA. Release preparation is performed by repository code using Python's standard library; orchestration uses tools present on GitHub-hosted Ubuntu runners (`bash`, `python3`, `git`, `sed`, `gh`).

Because GitHub disables scheduled workflows in public repositories after 60 days without repository activity, a second weekly schedule writes a small `.mirror/heartbeat.txt` commit. The heartbeat path is outside the workflow's `push` paths, so it cannot recursively start the mirror job. A push that changes the workflow or preparation script does start the mirror job, making initial deployment self-triggering.

Network downloads are restricted by validation to the expected Grafana and GitHub source locations before download.

## Components

### `scripts/prepare_release.py`

Pure preparation/verification stage. Given an output directory, it:

1. fetches stable metadata;
2. validates release-level policy;
3. selects and validates exactly two required packages;
4. downloads both binaries;
5. verifies official SHA-256 hashes;
6. downloads corresponding source and LICENSE;
7. writes manifests and release notes;
8. emits version/tag metadata for the caller.

It never writes to GitHub.

### `.github/workflows/mirror.yml`

Orchestration layer. It:

1. runs on a six-hour schedule, on relevant automation-code pushes, and manually;
2. checks whether the stable mirror tag is already published;
3. calls `prepare_release.py` only when necessary;
4. creates a draft release;
5. uploads all verified assets;
6. verifies the complete draft asset set against local names, sizes, and SHA-256 digests;
7. publishes the release;
8. cleans up only a release confirmed to remain a draft on failure;
9. performs a weekly heartbeat commit to prevent GitHub from disabling scheduled runs after repository inactivity.

### `tests/test_prepare_release.py`

Offline `unittest` contract tests using fixture metadata and local fixture files. Tests fail-closed behavior for Enterprise/non-AGPL metadata, malformed URLs/checksums, missing/duplicate architectures, version mismatch, and successful manifest generation.

A live smoke test runs the preparation logic against Grafana's current stable API and verifies the two downloaded `.deb` hashes.

## Error handling

The preparation script uses strict shell settings and exits non-zero on any policy, network, or integrity failure. The workflow never publishes a release after a failed preparation or upload.

If Grafana changes the API schema, package naming, license marker, or release URL structure, the mirror intentionally stops rather than guessing.

## Success criteria

The implementation is complete when:

1. all offline tests pass;
2. a live preparation run against current stable Grafana succeeds and validates both Debian packages;
3. the GitHub workflow is syntactically valid;
4. the first workflow run publishes exactly one public release for the current stable version;
5. release assets include the two verified `.deb` files, source, license, metadata, both checksum manifests, and release notes provenance;
6. a second workflow run exits cleanly without replacing the published release.
