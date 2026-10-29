# Compatibility

The product targets Windows x64, macOS arm64/x64 and Linux x64 with user-supplied
KiCad 10.0 patch 6 or later. Build baselines are Ubuntu 24.04 (glibc 2.39), macOS 14
arm64 and macOS 15 Intel. Exact native versions and artifact hashes belong in each
package's acceptance record. Windows arm64 and Linux arm64 are not current native package targets.

Two operations still require the Windows NTFS handle backend: board sync with
candidate-out, and incremental schematic hierarchy batches that publish new source
files. Do not infer their POSIX support from basic package smoke. Other operations
retain their selected-reference limits; no full-command parity is claimed.

Saved-file parsers, KiCad-supplied pcbnew Python and official KiCad CLI consumers
have distinct roles. Use context, doctor and selected reference to inspect actual
requirements. Product and official binaries share a name: use exact absolute paths.
Custom checks are not official ERC/DRC; zero findings are not manufacturing approval.

The direct binary contains Python and the fixed authorized SDK, whose license is
separate. External KiCad is not redistributed. See [installation](INSTALLATION.md)
and [knowledge configuration](KNOWLEDGE.md).
