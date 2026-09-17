# End-to-end evidence

[English](E2E.md) · [中文](E2E_zh.md)

What `release_readiness` claims, and what actually backs it. `kicad-cli
reference` reports the claim and `doctor` carries a check that must agree with
it; this page is the evidence behind both.

## Current level: `stable`

| Evidence | Required for | Status | Where |
|----------|--------------|--------|-------|
| Functional contract coverage 100% | any published level | **Verified** | `tests/test_contract.py`, `tests/test_fcc_guard.py` |
| Mock upstream / contract tests | `beta` and `stable` | **Verified** | `tests/test_mock_upstream.py` |
| Recorded live smoke / E2E | `stable` | **Verified** | [`evidence/live-smoke-1.0.0.md`](evidence/live-smoke-1.0.0.md) |
| The artifact actually starts | publishing at all | **Verified** | `scripts/smoke_binary.py`, recorded in the same file |

## Why there are two kinds of test

The live tests and the mock tests are not two roads to the same place.

**Live** tests launch the real `kicad-cli`, which launches KiCad's real
interpreter and binary, against KiCad's own demo projects copied into a temp
directory. Nothing is recorded or replayed. They prove the commands work.

**Mock** tests replace both KiCad boundaries — the official binary and the
bundled interpreter — with stubs that fail on command. They exist because a real
KiCad will not refuse to launch, exit zero with no output, hang, emit rubbish,
or fail twice and then succeed. Those are the paths that decide whether a
healthy board gets reported as broken, and they cannot be reached any other way.
They also run on a machine with no KiCad, where every live test skips.

Both boundaries are resolved by absolute path from an environment variable,
which is what makes the substitution possible without a seam in the product code.

## What the tests actually check

- **Contract.** Every command in `reference` runs with `KICAD_CLI_STRICT` set.
  Strict mode makes the envelope refuse to emit a field the schema does not
  declare, and fail if a declared field is missing. The first run of it found
  `board audit` advertising three fields it never emitted and omitting four it
  always did.
- **Coverage.** The guard does not trust that a test exists. It reads a dispatch
  trace the CLI writes during the run and fails if any advertised command was
  never reached. Runs against a stub deliberately do not count, so coverage
  cannot be satisfied by a fake. Verified by mutation: skipping one test makes
  it name the command that went unexercised. It enforces only where the live
  tests can run — on a machine with no KiCad they skip, and treating a
  skipped test as absent coverage would be the same mistake in reverse. The
  measurement that counts is the recorded one below.
- **Write gate.** Write commands go through the full `--dry-run` → `--confirm`
  sequence on copies, and the result is read back.
- **The Skill.** `SKILL.md` is checked against the live registry, because
  CLI-SPEC §13 counts it as public contract. It cannot advertise a command,
  field, option or install target that does not exist.

## Testing the source is not testing the artifact

The suite runs `python -m kicad_cli.main`. The thing users install is a frozen
one-file binary with no package context, its payloads carried as data and its
IPC client frozen in from a vendored directory. Those are different programs.

The gap is not hypothetical: the first binary this repo produced failed on its
first relative import and printed a Python traceback instead of an envelope,
for every command, while the suite was green. `scripts/smoke_binary.py` runs the
artifact — it must start, emit exactly one parseable envelope, carry its
payloads and changelog, report the version in `package.json`, and turn an
unknown command into an envelope rather than a traceback. The release workflow
runs it before anything is archived.

## Behavioural breadth, per CLI-SPEC §11

| Category | Covered by |
|----------|-----------|
| Success | live tests (all commands) and mock |
| Output schema | strict mode, both live and mock |
| Validation | argument gate, unknown options, unknown layers |
| Config failure | `board live` with the API unreachable |
| Upstream failure | mock: non-zero exit, and exit-zero-with-no-output |
| Timeout | mock: a stub that outlives the caller's timeout |
| Retry recovery | mock: two failures then a success, asserted to recover |
| Protocol noise | mock: SWIG chatter, including a decoy line starting with `{` |
| Empty results | mock: an empty netlist is refused, not reported as `add: 0` |
| Exit codes | asserted against the generated contract table |
| stdout/stderr boundary | exactly one envelope on stdout, asserted on every mock call |
| Auth, permission | **not applicable** — no credentials exist |
| Pagination, rate limiting | **not applicable** — no service, no paged results |

## What `stable` does not mean here

The recorded live run is one platform and one KiCad version: Windows, KiCad
10.0.6. CI does run on Linux and macOS across three Python versions, but with no
KiCad installed there, so what it proves is that the tool starts, parses and
answers correctly on those platforms — not that `pcbnew` behaves the same. The
mock tests run everywhere and cover the failure paths, but they cannot tell you
that KiCad 10.0.7 still writes the file format the same way.

Separately, most commands depend on the `pcbnew` SWIG binding, which KiCad has
scheduled for removal in version 11. [`COMPATIBILITY.md`](COMPATIBILITY.md)
lists which commands that touches and what each would need instead.

Neither of these is hidden by the level — both are in
`release_readiness.reason`, which an agent reads before trusting it.

## Reproducing

```bash
pip install -e ".[dev]"
pytest tests/ -v --tb=short
```

With KiCad installed that is the full suite. Without it, the live tests skip and
the mock tests still run — that subset passing is a real result, not a pass by
default.
