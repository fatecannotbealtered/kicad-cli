# Release admission

Normal version tags run CI only. The manually invoked stable GitHub Release workflow keeps exact tag/package/runtime version, actual frozen executable, FCC, mock-upstream and live-smoke gates. Public npm publication is disabled. The limited internal 1.0.0 product remains unpublishable under the whole-product contract and must be refused by the stable checker.

Before building, preflight runs scripts/check-version.js and scripts/check_release.py for the explicitly selected tag. Each actual frozen executable is checked again before packaging/signing. Missing, unknown, malformed or inapplicable evidence is rejected. The evidence checker recomputes behavior from intact source/artifact-bound JUnit; inventory text or hand-authored verified metadata alone is insufficient.

The bounded internal profile and its exact package acceptance are recorded separately, without weakening the stable gate or implying complete engineering, recovery, cross-platform or manufacturing qualification. The workflow does not grant SDK redistribution permission. Runtime SDK/schema use remains limited by the preserved user authorization and third-party notices.
