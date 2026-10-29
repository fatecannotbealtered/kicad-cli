# Installation and upgrade

Download the complete native package for your OS and CPU from
[Releases](https://github.com/fatecannotbealtered/kicad-cli/releases/latest).
The direct executable includes Python and its runtime dependencies. It needs no
development Python, Node, compiler or SDK. The optional Node wrapper needs Node 16
or later in addition to the same external KiCad installation.

| Package | Executable | External KiCad root | System baseline |
| --- | --- | --- | --- |
| Windows x64 | bin/kicad-cli.exe | installed version root | KiCad 10.0.6 or newer 10.0 patch |
| macOS arm64 | bin/kicad-cli | /Applications/KiCad/KiCad.app | macOS 14 |
| macOS x64 | bin/kicad-cli | /Applications/KiCad/KiCad.app | macOS 15 |
| Linux x64 | bin/kicad-cli | installation prefix, normally /usr | Ubuntu 24.04 / glibc 2.39 |

Install KiCad 10.0.6 or a newer 10.0 patch, including symbol and footprint libraries,
separately. Select the actual package target: x64 and arm64 are different builds.
Windows ARM64 and Linux ARM64 packages are not provided. See
[compatibility](COMPATIBILITY.md) for operation-specific platform limits.

## Direct executable

Extract into a new owned directory. On macOS/Linux prefer the runtime tar.gz,
which preserves executable permissions. Windows uses the runtime ZIP.
Keep the complete extracted folder together. Create UTF-8 `runtime-config.json`
beside the binary, or select an existing configuration with
`KICAD_CLI_RUNTIME_CONFIG`:

```json
{"format_version":1,"kicad_root":"/usr","profile_dir":"/owned/kicad-profile"}
```

On macOS replace /usr with the installed KiCad.app path. On Windows use absolute
Windows paths, for example `C:/Program Files/KiCad/10.0` and an owned profile directory.
For a first installation choose a new profile; for an upgrade retain the existing
valid configuration and knowledge index paths.

From the extracted package root on macOS/Linux:

```sh
./bin/kicad-cli --check-environment --compact
./bin/kicad-cli reference --index
./bin/kicad-cli modules
```

From the extracted package root on Windows PowerShell:

```powershell
& .\bin\kicad-cli.exe --check-environment --compact
& .\bin\kicad-cli.exe reference --index
& .\bin\kicad-cli.exe modules
```

Then inspect `context` and `doctor`, including any unavailable dependencies or
commands. The product and KiCad's own binary share a name; use the intended
executable's absolute path when both are installed.

## Optional Node installation

For the optional Node installation, explicitly install the exact local target TGZ
into a new prefix, for example:

```sh
npm install --global --prefix /owned/kicad-node /owned/downloads/fateforge-kicad-cli-1.0.1-linux-x64-internal.tgz
```

Use the matching version and target TGZ from the release; Windows uses its
win32-x64 TGZ, and macOS uses darwin-arm64 or darwin-x64. The wrapper checks OS, CPU,
version and executable hash. Configure the runtime using the same procedure as
direct execution. When upgrading, use the current installation prefix so the
existing command entry is updated.

## Six Skills

Install or copy complete folders from `skills/`, including references, manifests
and bindings. To install one Skill from an extracted package:

```sh
npx skills add /path/to/extracted-package --skill kicad-cli -y -g
```

The modules are `kicad-cli`, `kicad-library`, `kicad-sch`, `kicad-layout`,
`kicad-review` and `kicad-fabrication`. The PCB Skill's CLI module ID is `kicad-pcb`.
Node is only needed if choosing the `npx` installer, not for copying Skill folders
or running the direct executable. After updating, check `modules` and read
`changelog --since <previous-version>`.

## Upgrade an existing installation

1. Find the active command with `Get-Command kicad-cli` on Windows or
   `command -v kicad-cli` on macOS/Linux. Record its current package location and
   retain a rollback copy.
2. Download the matching complete new package. Keep `runtime-config.json`, its
   `profile_dir`, knowledge index paths and CAD projects. Do not reset a profile
   or overwrite a linked source checkout to update the command.
3. Install into the existing Node prefix, or switch the direct command entry to
   the new complete package. Keep using the existing explicit runtime configuration.
4. Update the six matching Skill folders and run the environment and discovery
   commands above. Do not mix resource folders from different packages.

An upgrade does not edit or roll back CAD files. Preserve old operation records
for diagnosis, but do not replay old scopes or confirmation tokens as new authority.
For older 2.2.2 installations, select the exact new package explicitly: an ordinary
semver update range does not cross a version reset.
