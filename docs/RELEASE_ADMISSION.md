# Stable release admission

The release workflow publishes to GitHub Releases and npm's normal distribution
channel. It is **stable-only**. Beta publication is deliberately not supported
by this workflow; changing that policy requires an explicit prerelease and npm
distribution-tag design, not treating a beta as a stable release.

Before any build, the read-only `preflight` job runs the existing version-sync
check and `python scripts/check_release.py --tag "$RELEASE_TAG"`. The latter
executes this checkout's `reference` and requires the exact tag/package/runtime
version, `stable`, and verified FCC, mock-upstream and live-smoke statuses with
the tool's corresponding requirements enabled. Missing/unknown/not-applicable
statuses, string booleans and malformed output are rejected. This KiCad tool
has a real upstream, so mock/live checks cannot be disabled as inapplicable.

Each build checks the **actual frozen executable** again before packaging.
The release job depends on all builds, and npm publication depends on release.
Preflight and builds have read-only repository permissions. Only the publishing
jobs receive their necessary write or identity-token permissions.

The checker does not run KiCad or certify engineering safety. Evidence strings
are pointers for review, not attestations the checker can prove. Fresh full
contract coverage, real KiCad runs and frozen smoke evidence for the candidate
still require review; manually changing `level` is not a substitute. This gate
blocks declared-unpublishable candidates, not malicious edits to the workflow
or dishonest evidence claims. Historical tags retain their historical workflow.

Current development `reference` is unpublishable and **must fail this check**.
That expected refusal is regression-tested. No release tag, package upload,
credential change or actual signing operation is needed to test the checker.
Python package metadata now derives `kicad_cli.__version__` through setuptools;
there is no independently drifting version literal in `pyproject.toml`.

## 本次边界

发布前先检查源码候选版本，打包前再检查实际冻结二进制；失败就阻止后续
签名及发布。当前开发版本应被拒绝。此门禁验证声明和版本一致性，不代替
真实 KiCad、完整契约覆盖或实机证据审查，也不会自动批准任何发布。
