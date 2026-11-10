# 真实软件验收

[English](E2E.md) | 中文

真实软件运行用真实的适配层，在真实的 KiCad 上执行 kicad-cli 的行为矩阵。它会做出真实的修改，所以只使用下面授权过的目标。

## 授权规则

真实软件运行和在真实 KiCad 上的评测可以使用的文件、文件夹和设备，唯一的清单在英文版 `docs/E2E.md` 的「Authorized Targets」一节；检查脚本和运行器只读那一份。目标只由人加入，或者在人自己发的消息里授予后由编码 Agent 加入；Agent 加入后把加的那一行告诉人，目标的说明也由它自己保持更新。每个目标占一个列表项，以反引号括起的目标开头，写法与授权范围文件里的目标相同：文件或文件夹的 `file://` URL，或者设备的 `device:<id>`；后面跟一个冒号和对目标的说明。列出的文件夹就是工作目录：编码 Agent 在里面建练习副本、输出文件夹和评测工作目录，不用再问人。KiCad 的实例没有固定的 ID，所以真实软件运行只使用为这次运行启动的实例，由人或编码 Agent 启动，打开的文档也只来自清单里的文件和文件夹；编码 Agent 只关闭它自己启动的实例。真实软件运行不使用任何其他目标。

真实软件运行所用的授权范围文件只授予清单里的目标，以及为这次运行启动的实例里的新文档。矩阵运行器和评测运行器各自为自己的运行写授权范围文件；其他真实软件工作，比如试一条命令或安装宿主组件，由编码 Agent 自己写，不用问人。评测题的 `SCOPE` 用到的目标也要列在清单里。

## 授权目标

清单以 `docs/E2E.md` 为准，本节不另列目标。

## 步骤

真实软件运行使用这些电脑：

- 一台 Windows 11 x64 电脑，KiCad 10.0.6，文件模式。macOS 和 Linux，以及各系统的 arm64，还没有真实软件运行；支持矩阵把它们标为未测试。

<!-- driver:spec-procedure:start -->
在真实的 KiCad 上运行行为矩阵：

1. 准备目标，比如在列出的文件夹里专为测试复制的项目、为这次运行启动的实例，或为测试准备好的设备。清单之外的目标，先按「授权规则」列进「授权目标」。
2. 把 `KICAD_CLI_STATE_DIR` 设为专供真实软件运行使用的目录。
3. 运行 `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>`，矩阵用到的每个占位符各给一个 `--set`，占位符名不带花括号，如 `--set doc=<file>`：`{session}` 是为这次运行启动的实例，`{doc}` 是授权的文档或文件，`{device}` 是授权的设备，`{dir}` 是授权文件夹里新建的空目录。`{job}` 是作业 ID；不给时，运行器先运行 `--job-from` 指定的长命令（只有一个长命令时就是它）的 `normal` 行，用它启动的作业。`{inputs}` 不用 `--set`：它就是 `tests/inputs/`，里面的文件命令只读取，所以不需要授权。
4. 运行器执行适用的行，把真实软件记录写进 `docs/evidence/`，并填上每一行的实际结果和 `live:` 证据引用。实际结果与预期不符的行算作失败。
5. 在真实的 KiCad 上无法按需制造的行，如 `disconnect`，可以标为 `unrunnable` 并写明原因；`normal`、`dialog` 和 `read_back_mismatch` 这三行不可以。`read_back_mismatch` 用 `--row read_back_mismatch` 运行：工具会在读回之前等一会儿，在这段时间里，到 KiCad 里改掉它刚写的内容，或删掉它生成的文件。
6. 运行 `python scripts/spec.py generate` 更新支持矩阵和下面的索引，再把记录和矩阵一起提交。

要在真实的 KiCad 上运行评测题，用列出的文件夹里的一个文件夹运行 `python scripts/spec.py eval --workspace-root <folder>`。运行器为每道题写好授权范围文件，并把评测报告写进 `docs/evidence/`。

真实软件记录里的运行在命令登记或适配层代码改动之前一直算作证据；只是版本号变了，什么都不影响。记录里从不出现机密，带机密的参数显示为 `***`。
<!-- driver:spec-procedure:end -->

`python tests/live/run_all.py --targets <清单里的文件夹> [command ...]` 按真实软件记录的做法运行全部矩阵。需要事先准备情况的行用 `--row` 单独运行。`read_back_mismatch` 行带 `--tamper` 运行，它在 kicad-cli 读回之前等待的时间里改掉结果：`--tamper "{python} tests/live/disturb.py delete"` 删掉新建、导出或绘图生成的文件，`--tamper "{python} tests/live/disturb.py restore <原始副本>"` 用原始内容覆盖修改命令写过的文件，就像另一个程序同时写同一个文件；`{python}` 代表 `run_matrix` 自己用的解释器。期望 `E_CONFLICT`（`file_open_in_session`）的 `conflict` 行在文件被 KiCad 打开时运行，这时文件旁有 KiCad 的锁文件。原理图和 PCB 命令的 `ambiguous` 行用 `--set doc=` 指向有重复位号的那份练习副本。

## 证据记录

`docs/evidence/` 里的真实软件记录，由 `python scripts/spec.py generate` 生成：

<!-- driver:evidence:start -->
| 记录 | 运行时间 | 软件 | 平台 | 结果 |
|---|---|---|---|---|
| [2026-11-09-183140-via-create.json](evidence/2026-11-09-183140-via-create.json) | 2026-11-09T18:31:40Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-183108-via-create.json](evidence/2026-11-09-183108-via-create.json) | 2026-11-09T18:31:08Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-183038-board-update.json](evidence/2026-11-09-183038-board-update.json) | 2026-11-09T18:30:38Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182938-zone-delete.json](evidence/2026-11-09-182938-zone-delete.json) | 2026-11-09T18:29:38Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182923-zone-delete.json](evidence/2026-11-09-182923-zone-delete.json) | 2026-11-09T18:29:23Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182907-zone-delete.json](evidence/2026-11-09-182907-zone-delete.json) | 2026-11-09T18:29:07Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182845-zone-delete.json](evidence/2026-11-09-182845-zone-delete.json) | 2026-11-09T18:28:45Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-182826-zone-create.json](evidence/2026-11-09-182826-zone-create.json) | 2026-11-09T18:28:26Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182811-zone-create.json](evidence/2026-11-09-182811-zone-create.json) | 2026-11-09T18:28:11Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182803-zone-create.json](evidence/2026-11-09-182803-zone-create.json) | 2026-11-09T18:28:03Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182741-zone-create.json](evidence/2026-11-09-182741-zone-create.json) | 2026-11-09T18:27:41Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-182725-zone-list.json](evidence/2026-11-09-182725-zone-list.json) | 2026-11-09T18:27:25Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-182713-via-create.json](evidence/2026-11-09-182713-via-create.json) | 2026-11-09T18:27:13Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182705-via-create.json](evidence/2026-11-09-182705-via-create.json) | 2026-11-09T18:27:05Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182647-via-create.json](evidence/2026-11-09-182647-via-create.json) | 2026-11-09T18:26:47Z | KiCad 10.0.6 | windows | 4 次运行中 3 次通过 |
| [2026-11-09-182637-footprint-update.json](evidence/2026-11-09-182637-footprint-update.json) | 2026-11-09T18:26:37Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182634-footprint-update.json](evidence/2026-11-09-182634-footprint-update.json) | 2026-11-09T18:26:34Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182631-footprint-update.json](evidence/2026-11-09-182631-footprint-update.json) | 2026-11-09T18:26:31Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182619-footprint-update.json](evidence/2026-11-09-182619-footprint-update.json) | 2026-11-09T18:26:19Z | KiCad 10.0.6 | windows | 5 次运行中 5 次通过 |
| [2026-11-09-182615-board-update.json](evidence/2026-11-09-182615-board-update.json) | 2026-11-09T18:26:15Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182600-board-update.json](evidence/2026-11-09-182600-board-update.json) | 2026-11-09T18:26:00Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182550-board-update.json](evidence/2026-11-09-182550-board-update.json) | 2026-11-09T18:25:50Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182522-board-update.json](evidence/2026-11-09-182522-board-update.json) | 2026-11-09T18:25:22Z | KiCad 10.0.6 | windows | 4 次运行中 3 次通过 |
| [2026-11-09-182506-track-delete.json](evidence/2026-11-09-182506-track-delete.json) | 2026-11-09T18:25:06Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182503-track-delete.json](evidence/2026-11-09-182503-track-delete.json) | 2026-11-09T18:25:03Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182500-track-delete.json](evidence/2026-11-09-182500-track-delete.json) | 2026-11-09T18:25:00Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182449-track-delete.json](evidence/2026-11-09-182449-track-delete.json) | 2026-11-09T18:24:49Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-182446-track-create.json](evidence/2026-11-09-182446-track-create.json) | 2026-11-09T18:24:46Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182429-track-create.json](evidence/2026-11-09-182429-track-create.json) | 2026-11-09T18:24:29Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182420-track-create.json](evidence/2026-11-09-182420-track-create.json) | 2026-11-09T18:24:20Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182353-track-create.json](evidence/2026-11-09-182353-track-create.json) | 2026-11-09T18:23:53Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-182336-footprint-move.json](evidence/2026-11-09-182336-footprint-move.json) | 2026-11-09T18:23:36Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182317-footprint-move.json](evidence/2026-11-09-182317-footprint-move.json) | 2026-11-09T18:23:17Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182309-footprint-move.json](evidence/2026-11-09-182309-footprint-move.json) | 2026-11-09T18:23:09Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182254-footprint-move.json](evidence/2026-11-09-182254-footprint-move.json) | 2026-11-09T18:22:54Z | KiCad 10.0.6 | windows | 5 次运行中 5 次通过 |
| [2026-11-09-182231-board-get.json](evidence/2026-11-09-182231-board-get.json) | 2026-11-09T18:22:31Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-182228-netlist-import.json](evidence/2026-11-09-182228-netlist-import.json) | 2026-11-09T18:22:28Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182223-netlist-import.json](evidence/2026-11-09-182223-netlist-import.json) | 2026-11-09T18:22:23Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182220-netlist-import.json](evidence/2026-11-09-182220-netlist-import.json) | 2026-11-09T18:22:20Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182210-netlist-import.json](evidence/2026-11-09-182210-netlist-import.json) | 2026-11-09T18:22:10Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-182205-footprint-create.json](evidence/2026-11-09-182205-footprint-create.json) | 2026-11-09T18:22:05Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182202-footprint-create.json](evidence/2026-11-09-182202-footprint-create.json) | 2026-11-09T18:22:02Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182200-footprint-create.json](evidence/2026-11-09-182200-footprint-create.json) | 2026-11-09T18:22:00Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-182157-board-render.json](evidence/2026-11-09-182157-board-render.json) | 2026-11-09T18:21:57Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182154-board-render.json](evidence/2026-11-09-182154-board-render.json) | 2026-11-09T18:21:54Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182152-board-render.json](evidence/2026-11-09-182152-board-render.json) | 2026-11-09T18:21:52Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-182149-board-export.json](evidence/2026-11-09-182149-board-export.json) | 2026-11-09T18:21:49Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182146-board-export.json](evidence/2026-11-09-182146-board-export.json) | 2026-11-09T18:21:46Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182144-board-export.json](evidence/2026-11-09-182144-board-export.json) | 2026-11-09T18:21:44Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-182141-track-list.json](evidence/2026-11-09-182141-track-list.json) | 2026-11-09T18:21:41Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-182139-footprint-list.json](evidence/2026-11-09-182139-footprint-list.json) | 2026-11-09T18:21:39Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-182136-board-check.json](evidence/2026-11-09-182136-board-check.json) | 2026-11-09T18:21:36Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-182126-board-create.json](evidence/2026-11-09-182126-board-create.json) | 2026-11-09T18:21:26Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182122-board-create.json](evidence/2026-11-09-182122-board-create.json) | 2026-11-09T18:21:22Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182117-board-create.json](evidence/2026-11-09-182117-board-create.json) | 2026-11-09T18:21:17Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-182104-schematic-render.json](evidence/2026-11-09-182104-schematic-render.json) | 2026-11-09T18:21:04Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182059-net-update.json](evidence/2026-11-09-182059-net-update.json) | 2026-11-09T18:20:59Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182056-symbol-delete.json](evidence/2026-11-09-182056-symbol-delete.json) | 2026-11-09T18:20:56Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182054-symbol-create.json](evidence/2026-11-09-182054-symbol-create.json) | 2026-11-09T18:20:54Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182052-schematic-create.json](evidence/2026-11-09-182052-schematic-create.json) | 2026-11-09T18:20:52Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182009-net-update.json](evidence/2026-11-09-182009-net-update.json) | 2026-11-09T18:20:09Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-182004-net-update.json](evidence/2026-11-09-182004-net-update.json) | 2026-11-09T18:20:04Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181958-net-update.json](evidence/2026-11-09-181958-net-update.json) | 2026-11-09T18:19:58Z | KiCad 10.0.6 | windows | 4 次运行中 3 次通过 |
| [2026-11-09-181954-pin-update.json](evidence/2026-11-09-181954-pin-update.json) | 2026-11-09T18:19:54Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181949-pin-update.json](evidence/2026-11-09-181949-pin-update.json) | 2026-11-09T18:19:49Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181944-pin-update.json](evidence/2026-11-09-181944-pin-update.json) | 2026-11-09T18:19:44Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181942-pin-update.json](evidence/2026-11-09-181942-pin-update.json) | 2026-11-09T18:19:42Z | KiCad 10.0.6 | windows | 5 次运行中 5 次通过 |
| [2026-11-09-181939-symbol-delete.json](evidence/2026-11-09-181939-symbol-delete.json) | 2026-11-09T18:19:39Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181935-symbol-delete.json](evidence/2026-11-09-181935-symbol-delete.json) | 2026-11-09T18:19:35Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181930-symbol-delete.json](evidence/2026-11-09-181930-symbol-delete.json) | 2026-11-09T18:19:30Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181928-symbol-delete.json](evidence/2026-11-09-181928-symbol-delete.json) | 2026-11-09T18:19:28Z | KiCad 10.0.6 | windows | 5 次运行中 4 次通过 |
| [2026-11-09-181923-symbol-move.json](evidence/2026-11-09-181923-symbol-move.json) | 2026-11-09T18:19:23Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181918-symbol-move.json](evidence/2026-11-09-181918-symbol-move.json) | 2026-11-09T18:19:18Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181912-symbol-move.json](evidence/2026-11-09-181912-symbol-move.json) | 2026-11-09T18:19:12Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181910-symbol-move.json](evidence/2026-11-09-181910-symbol-move.json) | 2026-11-09T18:19:10Z | KiCad 10.0.6 | windows | 5 次运行中 5 次通过 |
| [2026-11-09-181906-symbol-update.json](evidence/2026-11-09-181906-symbol-update.json) | 2026-11-09T18:19:06Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181901-symbol-update.json](evidence/2026-11-09-181901-symbol-update.json) | 2026-11-09T18:19:01Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181854-symbol-update.json](evidence/2026-11-09-181854-symbol-update.json) | 2026-11-09T18:18:54Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181853-symbol-update.json](evidence/2026-11-09-181853-symbol-update.json) | 2026-11-09T18:18:53Z | KiCad 10.0.6 | windows | 5 次运行中 5 次通过 |
| [2026-11-09-181848-symbol-create.json](evidence/2026-11-09-181848-symbol-create.json) | 2026-11-09T18:18:48Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181843-symbol-create.json](evidence/2026-11-09-181843-symbol-create.json) | 2026-11-09T18:18:43Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181835-symbol-create.json](evidence/2026-11-09-181835-symbol-create.json) | 2026-11-09T18:18:35Z | KiCad 10.0.6 | windows | 4 次运行中 3 次通过 |
| [2026-11-09-181830-schematic-create.json](evidence/2026-11-09-181830-schematic-create.json) | 2026-11-09T18:18:30Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181827-schematic-create.json](evidence/2026-11-09-181827-schematic-create.json) | 2026-11-09T18:18:27Z | KiCad 10.0.6 | windows | 5 次运行中 4 次通过 |
| [2026-11-09-181819-schematic-render.json](evidence/2026-11-09-181819-schematic-render.json) | 2026-11-09T18:18:19Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-181814-schematic-export.json](evidence/2026-11-09-181814-schematic-export.json) | 2026-11-09T18:18:14Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181810-schematic-export.json](evidence/2026-11-09-181810-schematic-export.json) | 2026-11-09T18:18:10Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-181755-symbol-get.json](evidence/2026-11-09-181755-symbol-get.json) | 2026-11-09T18:17:55Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-09-181753-symbol-get.json](evidence/2026-11-09-181753-symbol-get.json) | 2026-11-09T18:17:53Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-181750-net-list.json](evidence/2026-11-09-181750-net-list.json) | 2026-11-09T18:17:50Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-181746-symbol-list.json](evidence/2026-11-09-181746-symbol-list.json) | 2026-11-09T18:17:46Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-181741-schematic-check.json](evidence/2026-11-09-181741-schematic-check.json) | 2026-11-09T18:17:41Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-181721-doc-list.json](evidence/2026-11-09-181721-doc-list.json) | 2026-11-09T18:17:21Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-181718-doc-get.json](evidence/2026-11-09-181718-doc-get.json) | 2026-11-09T18:17:18Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-09-181714-library-get.json](evidence/2026-11-09-181714-library-get.json) | 2026-11-09T18:17:14Z | KiCad 10.0.6 | windows | 5 次运行中 5 次通过 |
| [2026-11-09-181710-library-list.json](evidence/2026-11-09-181710-library-list.json) | 2026-11-09T18:17:10Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-09-181706-project-get.json](evidence/2026-11-09-181706-project-get.json) | 2026-11-09T18:17:06Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
<!-- driver:evidence:end -->
