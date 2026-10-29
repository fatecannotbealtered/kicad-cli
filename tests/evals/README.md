# Skill evaluation inputs

`skill-scenarios.json` is the maintained developer input for the six Skills.
Its nine `common` scenarios apply to every module; `modules` holds the 26
module-specific scenarios. Combining the common cases with each module's cases
preserves all 80 former per-Skill evaluation cases, including their prompts,
expected behavior, routing and `agent_evaluation: not_run` status.

These are evaluation inputs, not recorded Agent runs. Source/CLI tests and package
structure checks do not demonstrate that an Agent completed the scenarios.
Record actual evaluations separately, bound to the tested Skill and executable.

The fixed `.agent/SKILL-SPEC.md` seed describes in-Skill eval files. This product
keeps those inputs here instead: user-facing Skills contain only task resources.
The seed and SDK pins are unchanged. `tests/test_skill.py` checks scenario coverage
and verifies that generation does not distribute evaluation inputs or instructions.

Native package acceptance remains in the existing smoke-workflow receipts beside
the exact artifacts, outside the user bundle. `examples/arc-scope` is retained as
a synthetic rejection fixture in source and is not shipped as a user example.
The original Schmitt example's development notes are preserved byte-for-byte in
`bench/functional-schmitt-notes.md`; the shipped example retains its engineering
description, source facts and hardware-validation limits.
