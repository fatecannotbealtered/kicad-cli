# Native 1.0.0 installation

Choose the package matching the OS and CPU. The direct executable includes Python,
validators and the authorized fixed SDK. The optional Node wrapper also needs Node;
no development SDK or host Python is needed for direct execution.

| Package | Executable | External KiCad root |
| --- | --- | --- |
| Windows x64 | bin/kicad-cli.exe | installed version root |
| macOS arm64 / x64 | bin/kicad-cli | /Applications/KiCad/KiCad.app |
| Linux x64 | bin/kicad-cli | installation prefix, normally /usr |

The external KiCad 10.0 series must be patch 6 or newer, with its libraries.
The current Linux build/smoke baseline is Ubuntu 24.04 (glibc 2.39); macOS baseline
runners are macOS 14 arm64 and macOS 15 Intel. Select the actual package target;
Linux arm64 is not included in this iteration's published target set.

Extract into a new owned directory. On macOS/Linux prefer the runtime tar.gz,
which preserves executable permissions. Windows uses the runtime ZIP.
Create UTF-8 runtime-config.json beside the binary or select it explicitly:

```json
{"format_version":1,"kicad_root":"/usr","profile_dir":"/owned/kicad-profile"}
```

On macOS replace /usr with the installed KiCad.app path. On Windows use absolute
Windows paths. Run the selected binary with --check-environment --compact, then
reference --index, modules, context and doctor. A new profile is initialized without
overwriting existing user settings. Whole-product FCC readiness is distinct from
this package's basic native smoke and remains visible in doctor/reference.

For the optional Node installation, explicitly install the exact local target TGZ
into a new prefix, for example:

```sh
npm install --global --prefix /owned/kicad-node /owned/downloads/fateforge-kicad-cli-1.0.0-linux-x64-internal.tgz
```

Windows can use the same command with owned Windows paths and its win32-x64 TGZ.
The Node package checks OS, CPU, version and executable hash. Copy/install each
complete selected skills/<name> folder, including references and manifests.
The PCB Skill install name is kicad-layout; its logical module remains kicad-pcb.

## Existing installations

Keep the immutable 1.0.0 package and existing CAD files until explicitly choosing
the new installation. Older internal 2.2.2 installs need explicit package selection;
semver ranges do not automatically migrate that version reset. Use a fresh profile,
not old scope, operation state or confirmation tokens. The six matching module
files and their bindings belong to the new package, not merely its display version.

## Builder

Use an isolated Python 3.12 environment with requirements-build.lock and the dev
extra, keep the fixed SDK/spec unchanged, then run scripts/build_module_candidate.py.
The native runner builds its own OS/CPU binary. Never relabel another platform's
binary or use an emulated run as native evidence. External KiCad is not redistributed.
