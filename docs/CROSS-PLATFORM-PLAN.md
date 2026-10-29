# Cross-platform 1.0.0

Normal iteration from immutable 1.0.0, with no history rewrite or replacement of
its tag, Release or Hub artifacts. The command tree remains the same 43 commands.

| Package | Native build/smoke baseline |
| --- | --- |
| Windows x64 | Existing licensed Windows host; Windows CI regression |
| macOS arm64 | macOS 14 runner, official KiCad 10.0.7 universal image |
| macOS x64 | macOS 15 Intel runner, same official universal image |
| Linux x64 | Ubuntu 24.04, official KiCad 10.0 stable PPA |

Linux arm64 exists only in the older stable workflow template and is not a claim
of this release. Native Windows arm64 is likewise outside this target set.

Installation selection recognizes prefix layouts and macOS SharedSupport,
MacOS executables and the application's framework Python. Direct binaries bundle
Python, validators and the authorized fixed SDK. Node packages carry and verify
their own OS/CPU and binary binding; POSIX archives preserve executable mode.
The existing fixed seed/driver/base stay unchanged pending parent-reviewed adoption.

Native jobs perform package extraction, direct startup without build Python on
PATH, offline local Node installation, public library/SCH/PCB creation and a native
PCB save/reopen comparison. Six module resources and runtime bindings are checked
on the installed package. Wrong installation roots and wrong package platforms
are rejected. This is basic product smoke; full FCC, every recovery mode and all
CAD scenarios are not prerequisites for the cross-platform delivery.

Advanced candidate output/new hierarchy source publication retains its Windows
NTFS backend restriction. Custom checks remain distinct from official ERC/DRC.

Actions artifact export is paused after the service refused CreateArtifact for
storage quota. Builds and smoke continue independently. No alternative export or
new Release/Hub publication is executed without the parent's decision.
