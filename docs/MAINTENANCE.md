# Repository maintenance

- kicad_cli: product runtime and pinned dependency inputs. Do not delete source,
  command coverage or a capability to make a cleanup pass.
- docs/modules: maintained six-module Skill sources. Regenerate skills with
  python scripts/build_module_skills.py --out skills; do not edit generated copies.
- knowledge: provider-neutral public defaults and disabled external examples.
- tests, bench, examples: executable regressions, frozen comparison inputs and
  editable examples. Retained baseline evidence is not current release acceptance.
- release: generic product scope and module names only. Company endpoints, group
  identifiers, credentials and publication receipts belong in private deployment
  storage outside the repository. No secret is a safe default.
- dist, build, internal-dist and release-work: ignored generated output. Do not
  commit native binaries, private profiles, operation state or local task logs.

Before committing: check versions and fixed-spec hashes, regenerate Skills, run
resource/knowledge/package checks and affected regressions. Keep unresolved findings
visible; do not edit test results or readiness to manufacture acceptance.

Before changing repository visibility: inspect every branch/tag and reachable
history, PRs/issues/comments, workflow logs/artifacts, Release bodies/attachments and
third-party redistribution terms. A clean HEAD does not sanitize old objects.
History rewriting or permanent removal of published assets is a separate operation.
Do not describe this maintenance pass as completion of that public-exposure audit.
