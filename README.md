# Grafana OSS Release Mirror

This repository is an independent, **unofficial** release mirror for unmodified **Grafana® OSS** packages. It exists to provide an alternate download location when Grafana's primary download infrastructure is difficult or unreliable to reach.

The mirror publishes every stable package marked `agplv3` by the Grafana release API. Enterprise packages, beta/preview/nightly builds, containers, and plugins are not mirrored.

## What each release contains

Each GitHub Release contains:

- macOS tarballs for `amd64` and `arm64`;
- Debian packages for `amd64`, `arm64`, `armv6`, and `armv7`;
- Linux tarballs for `amd64`, `arm64`, `armv6`, and `armv7`;
- RPM packages for `amd64` and `arm64`;
- Windows tarballs for `amd64` and `arm64`, plus the `amd64` MSI installer;
- corresponding source for the exact upstream `v<version>` tag;
- the matching upstream Grafana license text;
- `upstream-metadata.json`, preserved exactly as received from the Grafana stable release API;
- `UPSTREAM-SHA256SUMS`, containing the SHA-256 values published by Grafana for every mirrored package;
- `MIRROR-SHA256SUMS`, containing locally calculated SHA-256 values for the release assets.

The Grafana binary packages are not modified, renamed, recompressed, or repackaged.

## Integrity model

The automation treats the Grafana stable release API as the source of truth and fails closed unless the release and all 15 expected packages declare `agplv3`, the release is stable only, package versions match, package URLs and filenames match their platforms, and exactly one package exists for every required OS/architecture pair.

Before a release is published, every mirrored package is downloaded and verified against the upstream SHA-256 value. After upload to a draft Release, the automation also requires GitHub's asset names, upload states, and server-computed SHA-256 digests to match the complete payload. If any policy, checksum, upload, or asset-set check fails, no public release is published.

After downloading a release, verify the upstream binary checksums with:

```bash
sha256sum -c UPSTREAM-SHA256SUMS
```

To verify all release assets covered by the mirror-generated manifest:

```bash
sha256sum -c MIRROR-SHA256SUMS
```

## Source and licensing

Grafana OSS is distributed by its upstream project under **AGPL-3.0-only**, with upstream-documented licensing exceptions for some source directories. Corresponding source for each mirrored binary release is included in the same GitHub Release as `grafana-<version>-source.tar.gz`.

The automation code in this repository is licensed under the **MIT License**. That repository license does not relicense, replace, or modify the licenses of mirrored Grafana artifacts.

Upstream project: https://grafana.com/

Upstream source: https://github.com/grafana/grafana

## Automation

`.github/workflows/mirror.yml` checks the upstream stable release every hour and can also be started manually. For a new version, a 15-entry job matrix downloads, verifies, and uploads every official package independently and in parallel; source and provenance files are prepared alongside it. A final job publishes the draft only after all 20 release assets and their server-computed SHA-256 digests match. A push that changes the workflow or release-preparation script triggers the same check. The workflow uses only the repository-scoped ephemeral `GITHUB_TOKEN`; no personal access token or long-lived secret is required.

GitHub automatically disables scheduled workflows in a public repository after 60 days without repository activity. To prevent this mirror from silently stopping during a long period without upstream releases, the workflow performs one small weekly heartbeat commit to `.mirror/heartbeat.txt`. The heartbeat path is intentionally excluded from the `push` trigger, so that commit does not recursively start another mirror run.

An already published mirror release is treated as immutable. Re-running the workflow for the same stable version exits successfully without replacing the release. An incomplete draft from a failed run is deleted and rebuilt.

## Trademark attribution

The Grafana Labs Marks are trademarks of Grafana Labs, and are used with Grafana Labs’ permission. We are not affiliated with, endorsed or sponsored by Grafana Labs or its affiliates.
