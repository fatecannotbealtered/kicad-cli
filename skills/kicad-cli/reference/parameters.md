# Reading the machine parameter contract

`kicad-cli reference` is now the source of parameter names **and** values.
Use `kicad-cli reference --command "board route" --compact` for one command,
or omit the selector to discover the full tree. Global options are in
`data.global_options`; each command carries `params` and `required_any`.

## Interpreting a parameter

`type`, `required` and `multiple` are enforced at the CLI boundary. `default`
is the literal fallback; null means no literal fallback. `default_from`
describes a value resolved from the project rather than guessed by the Agent.
`enum`, `minimum`, `exclusive_minimum` and `unit` describe accepted values.
`when`, `conflicts_with` and `requires` declare mode and combination constraints.
Repeated scalar options, empty values and non-finite numbers are errors.
Repeated layer selections are combined in order, not overwritten.

The route network selector currently applies **only to rewidth**. Repair/full
with an explicit network selector fail before launching KiCad; they never
silently route every network. A network selector and a class selector cannot
be supplied together. This is a safety restriction, not a newly implemented
per-network repair feature.

The legacy default netclasses still describe the original development board.
Read the real classes from the board audit and supply the intended classes.
Copper weight and temperature rise are assumptions; validate them against the
design before reporting ampacity. Output field selection saves response bytes,
not board computation, and currently projects only top-level keys.

## Interpreting status

Status enums are also exposed by `schemas[...].field_values.status`.
PASS/FAIL describe the scope actually checked, not all electrical correctness.
PARTIAL/NOOP distinguish incomplete and unchanged relink results.
CLEAN/DESTRUCTIVE describe footprint additions/deletions in a sync preview;
CLEAN does **not** exclude field updates. Read not_checked and the counts.
