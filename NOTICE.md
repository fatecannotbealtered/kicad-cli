# Notice

> 中文版 → [NOTICE_zh.md](NOTICE_zh.md)

`kicad-cli` is an independent open-source project. It is **not** affiliated with, endorsed by, sponsored by, or supported by KiCad or any of its affiliates.

"KiCad", related product and company names, logos, and brands are trademarks or registered trademarks of their respective owners. They are used here only to identify the API-compatibility target — that is, the service this tool talks to — and do not imply any association or endorsement.

This project does not redistribute KiCad code or assets. `kicad-cli` reads and writes KiCad's documented file formats, runs KiCad's own `kicad-cli` binary and bundled Python interpreter from the user's local installation, and talks to a running KiCad through its public IPC API. There are no credentials involved: everything happens on the user's own machine, against the user's own KiCad.

`board route --engine freerouting` is optional and works the same way. Freerouting is GPL-3.0, distributed by its own project; this tool ships none of its bytes, locates the copy the user installed via `KICAD_CLI_FREEROUTING`, and invokes it as a separate process over command-line arguments and Specctra DSN/SES files. `doctor` reports whether it is present. The default routing engine needs no external program at all.

The MIT license (see [LICENSE](LICENSE)) applies only to the `kicad-cli` source code. It grants no rights to KiCad's trademarks, services, data, API behavior, or upstream availability.
