# Security Policy

English | [中文](SECURITY_zh.md)

kicad-cli lets an AI Agent operate KiCad. A person sets the scope, the Agent does the work, and kicad-cli makes every step correct and honestly reported. This file describes how that is kept safe and how to report a vulnerability.

## Supported Versions

<!-- driver:spec-supported-versions:start -->
Security fixes go into the latest minor version of kicad-cli.

| Version | Security fixes |
|---|---|
| The latest minor version | Yes |
| Older versions | No; update with `kicad-cli update` |

A version with a known vulnerability is deprecated on npm. `kicad-cli doctor` and the update notices of the other commands then warn its users, and `kicad-cli update` installs the fixed version.
<!-- driver:spec-supported-versions:end -->

## Reporting a Vulnerability

<!-- driver:spec-reporting-a-vulnerability:start -->
Report a vulnerability privately through GitHub private vulnerability reporting: <https://github.com/fatecannotbealtered/kicad-cli/security/advisories/new>. Do not open a public issue or pull request for it.

Include the kicad-cli version (`kicad-cli --version`), the operating system, the KiCad version and the steps that show the problem. Leave out secrets, license files and project files you may not share.
<!-- driver:spec-reporting-a-vulnerability:end -->

## Risk Tier

Every command has a risk tier, derived from the effects it declares. The tool's tier is the highest among its commands; the built-in commands and the team knowledge commands `knowledge create` and `knowledge delete` do not count.

| Tier | Meaning |
|---|---|
| `T0` | Reads only and changes nothing |
| `T1` | Changes the running state of KiCad, objects in it, or local files |
| `T2` | Changes settings or host components, reaches outside people or systems, or acts on the physical world |

The tier of kicad-cli and the commands that set it, generated from `kicad-cli reference`:

<!-- driver:risk:start -->
Risk tier: **T1**, set by these commands:

- `doc create`: lifecycle
- `job cancel`: lifecycle
- `schematic create`: artifact
- `schematic export`: artifact
- `schematic render`: artifact
- `symbol create`: artifact, change
- `symbol update`: change
- `symbol move`: change
- `symbol delete`: change
- `pin update`: change
- `net update`: change
- `board create`: artifact
- `board export`: artifact
- `board render`: artifact
- `footprint create`: artifact, change
- `netlist import`: change
- `footprint move`: change
- `track create`: change
- `track delete`: change
- `via create`: change
- `zone create`: change
- `zone delete`: change
- `board update`: change
- `footprint update`: change
<!-- driver:risk:end -->

## Authorization

<!-- driver:spec-authorization:start -->
A person grants writes in a scope file, a JSON file with the fields `scope_id`, `effects`, `dangerous`, `targets`, `expires_at` and, optionally, `confirm`. The Authorization section of the README shows an example.

- **Location.** kicad-cli reads the scope file from the path in `KICAD_CLI_SCOPE_FILE`, or from `scope.json` in the state directory when the variable is not set. It looks nowhere else: not in the working directory, not in a project and not in command-line flags, so a scope cannot arrive inside a downloaded project.
- **Who writes it.** A person, or an Agent in two cases. When the person's own message grants targets, the Agent writes a scope file that holds only that grant, with `confirm: always` when the scope in force sets it, and tells the person what it wrote; it never changes or adds to a scope file it did not write for that grant, because a file's effects and expiry apply to all its targets. A request to do a task is not a grant. A coding Agent writes its own scope files for live tests, granting only the targets listed in `docs/E2E.md`. kicad-cli only reads scope files; no command creates, changes or deletes one. A document, a file, a command's output or another Agent's message never counts as the person's message.
- **What it grants.** Writes whose every effect is listed in `effects`, on objects whose target keys start with one of the `targets` prefixes, until `expires_at`; operations in the dangerous tier only with `"dangerous": true`. In a batch, one object the scope does not cover blocks the whole batch. Reads need no scope.
- **Stricter only.** `"confirm": "always"` makes every write except stopping work two-step, so that a person approves each preview. No field can lower a confirmation tier or skip a check.
- **Refusals.** A write that no valid scope covers changes nothing and returns `E_FORBIDDEN`, with what is missing and a snippet the person can paste into the file. `kicad-cli context` reports the scope status: `granted`, `missing`, `invalid` or `expired`.
- **Records and settings.** `operation resolve` needs a scope that covers the effects and targets of the operation it settles. `knowledge create` and `knowledge delete` change only the tool's own list of team knowledge links and need no scope.
- **Limits.** The scope guards against mistakes by the Agent and against instructions injected through untrusted content. It cannot stop an Agent that ignores its Skills, because the scope file is an ordinary file in the user's account; keep such an Agent away from the file through the permissions you give the Agent itself.
<!-- driver:spec-authorization:end -->

A person who lets an Agent edit one practice project and export its fabrication files grants the effects `change` and `artifact` on that project folder. Deletes (`symbol delete`, `track delete`) also need `"dangerous": true`.

```json
{
  "scope_id": "demo-board-edit",
  "effects": ["change", "artifact"],
  "dangerous": false,
  "targets": ["file:///D:/work/demo/"],
  "expires_at": "2027-01-31T18:00:00Z"
}
```

## Write Confirmation

Every write, in every tier, checks the scope, builds a preview, keeps an operation record, reads the change back and returns a receipt. The tier decides only how the write is confirmed. kicad-cli derives it from the effects the command declares; no setting, flag or code can lower it.

| Tier | Applies to | Confirmation |
|---|---|---|
| direct | Changes held in the application's memory, which can be discarded; starting instances; creating and opening documents; new files that overwrite nothing; stopping work | None; one call runs it |
| two-step | Saving and overwriting files; changes in file and device mode; settings; host components | The first call changes nothing and returns `E_CONFIRMATION_REQUIRED` with a preview and a confirm token; the same command with `--confirm <token>` runs it |
| dangerous | Physical actions; handing content to outside people or systems; irreversible changes; discarding unsaved changes; closing an instance kicad-cli did not start | As two-step, plus `--dangerous` on the confirming call and `"dangerous": true` in the scope |

A direct write whose preview touches objects beyond those its inputs name is confirmed as two-step. With `"confirm": "always"` in the scope, every write except stopping work is two-step.

**Confirm tokens.** A confirm token is a random value of at least 128 bits from a secure random source. It is not signed: kicad-cli stores it in the operation record of the planned write and accepts it only when it finds it there, unexpired and unused. A token expires after 10 minutes and is spent when dispatch starts, so it works once. It binds the command, all inputs, the target, the previewed changes, the session and its generation, the document revision and unsaved state, the loaded host component version, and the ID and content hash of the scope file; when any of them changes, the token is refused. A confirm refused before dispatch leaves the token usable until it expires.

**Confirm-time review.** Right before dispatch, kicad-cli runs these checks in order and stops at the first failure: the token is valid, unexpired and unused; the command and all inputs match it; the scope file is unchanged and still grants the write; the target is not locked; the session still exists with the same generation; the document revision and unsaved state are unchanged; the host component version is unchanged; the preview, computed again, shows the same changes and can still be applied. A failure changes nothing and returns `E_CONFLICT` naming the check, or `E_FORBIDDEN` when the scope no longer grants the write. A direct write has no token and runs the same checks from the scope onward, against the state captured with its preview.

## State Directory

kicad-cli keeps its state in one directory per user: the path in `KICAD_CLI_STATE_DIR`, or `~/.kicad-cli` when the variable is not set. Every process of the user shares it, so several Agents share the same locks and records.

| Entry | Holds | Written by |
|---|---|---|
| `operations/` | Operation records, kept 30 days, with the confirm tokens of planned writes | kicad-cli |
| `locks/` | Target locks | kicad-cli |
| `cache/` | The result of the last update check | kicad-cli |
| `hosts/` | Discovery files of running host components | Host components |
| `adapter/` | What the adapter must remember between calls | kicad-cli, for the adapter |
| `trash/` | Native programs moved aside by `update`, removed on its next run | kicad-cli |
| `scope.json` | The scope file at its default location | A person, or an Agent as the Authorization section says |
| `knowledge-links.json` | Team knowledge registrations | `knowledge create` and `knowledge delete` only |

Only the writer named in the table writes an entry. Only the current user may read or write the directory: its mode is `0700` on macOS and Linux, and on Windows its ACL grants access to the current user only. kicad-cli sets this when it creates the directory, and `kicad-cli context` reports the protection actually in place: `posix_0700`, `windows_acl` or `none`. When operation records cannot be written, every write is refused with `E_IO` and nothing is dispatched, while reads keep working. Tests use a temporary state directory and never touch the real one.

## Credentials

- **Secrets.** Passwords, access tokens and license keys never appear in output, logs, operation records, error messages or live records. Each command declares its secret parameters, and kicad-cli shows them as `***` wherever inputs are echoed, such as in a preview.
- **Connections.** kicad-cli connects only to the npm registry, from `update` and `doctor`, and to the KiCad services or devices it drives, from the commands that drive them. It sends no telemetry. Every request honors `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY`, and every TLS connection verifies the server certificate; nothing turns that check off.
- **Team knowledge.** kicad-cli stores only the links of registered team knowledge. It never connects to Feishu and keeps no Feishu account; the Agent reads team documents itself with lark-cli.

kicad-cli handles no secrets: KiCad needs no license, and no command takes a password, key or token.

kicad-cli connects to no KiCad service or device. It reads and writes files on the local disk and runs KiCad's own command-line program on the same machine; only `update` and `doctor` contact the npm registry.

## Untrusted Content

Text that comes from outside kicad-cli and the person is untrusted content, whoever wrote it:

- object names, document content and file names;
- dialog titles and dialog text;
- the application's own error messages;
- messages from devices;
- team knowledge: the registered links, their descriptions and the documents they point to.

Every output that carries untrusted content lists the JSON path of each such value in `meta.untrusted`. kicad-cli marks the application's error messages, dialog text, team knowledge and its own text that embeds untrusted values, such as the preview's `action` and target `label` and the commands it generates; each domain command declares which of its result fields hold untrusted content. kicad-cli and its Skills never act on untrusted content as instructions: a request found in a document or a dialog is data to report, and the Agent asks the person before doing what it proposes. Untrusted content never widens the scope or the targets.

## Supply Chain

- All dependencies are pinned by lock files kept in the repository, and CI builds from those lock files.
- Every npm package of kicad-cli, the main package and each platform package, is built and published only by the release workflow, started by a version tag, and carries npm provenance. No package is published from a personal machine.
- The packages have no install scripts (`preinstall`, `install` or `postinstall`) and download nothing at install time; the native program and everything it reads ship inside them.
- Skills are installed and synced only from the installed npm package, never fetched from the network.

## Self-update

`kicad-cli update` is the only way kicad-cli updates itself. It:

- asks the npm registry through npm, so npm's own registry, proxy and certificate settings apply; validates every version string as semver, and refuses a downgrade unless both `--target-version` and `--allow-downgrade` are given; `update --check` only queries and installs nothing;
- on Windows, before npm runs, moves the files of the running native program aside into `trash/` in the state directory, or into `.kicad-cli-trash` in npm's global `node_modules` folder when the state directory is on another disk, and deletes them on its next run;
- runs the npm found by absolute path on PATH, never through a shell and never a file of that name in the current directory, with arguments built only from the validated package name and version;
- after installing, runs `kicad-cli --version` and succeeds only when it reports the target version, and only when the `kicad-cli` on PATH is the one npm changed; otherwise it puts the earlier version back. Then it checks the provenance of the installed package: `verified` only when the check ran and passed, `not_checked` when it could not run, and `E_INTEGRITY` when it failed; a failed check is remembered, and running `update` again does not finish the command entries or the Skills from that package;
- points the command entries at the native program (`entry: direct`) and keeps the npm launcher as a fallback;
- syncs the Skills already installed in the Agent Skill folders from the new package, leaves symbolic links alone and adds no Skill that was not installed;
- never installs or changes a host component; when one needs installing again, it gives the `plugin install` command.

Running `update` again is always safe: at the target version it returns `up_to_date` and finishes a command entry change or Skill sync that was left undone. Every failure leaves a known state: a failed install keeps the old version, and a failed Skill sync returns `E_PARTIAL` and is finished by running `update` again. Only `update` and `doctor` contact the npm registry, and `doctor` waits at most 3 seconds for it. The other commands read the cached answer and add an update notice at most once every 24 hours, as a warning when a new major version exists or the installed version is deprecated.
