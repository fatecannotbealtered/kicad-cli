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

真实软件记录里的运行在命令登记或这次运行用到的适配层代码改动之前一直算作证据；只是版本号变了，什么都不影响。记录里从不出现机密，带机密的参数显示为 `***`。
<!-- driver:spec-procedure:end -->

`python tests/live/run_all.py --targets <清单里的文件夹> [command ...]` 按真实软件记录的做法运行全部矩阵。需要事先准备情况的行用 `--row` 单独运行。`read_back_mismatch` 行带 `--tamper` 运行，它在 kicad-cli 读回之前等待的时间里改掉结果：`--tamper "{python} tests/live/disturb.py delete"` 删掉新建、导出或绘图生成的文件，`--tamper "{python} tests/live/disturb.py restore <原始副本>"` 用原始内容覆盖修改命令写过的文件，就像另一个程序同时写同一个文件；`{python}` 代表 `run_matrix` 自己用的解释器。期望 `E_CONFLICT`（`file_open_in_session`）的 `conflict` 行在文件被 KiCad 打开时运行，这时文件旁有 KiCad 的锁文件。原理图和 PCB 命令的 `ambiguous` 行用 `--set doc=` 指向有重复位号的那份练习副本。

## 证据记录

`docs/evidence/` 里的真实软件记录，由 `python scripts/spec.py generate` 生成：

<!-- driver:evidence:start -->
| 记录 | 运行时间 | 软件 | 平台 | 结果 |
|---|---|---|---|---|
| [2026-11-12-122657-zone-delete.json](evidence/2026-11-12-122657-zone-delete.json) | 2026-11-12T12:26:57Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122645-zone-delete.json](evidence/2026-11-12-122645-zone-delete.json) | 2026-11-12T12:26:45Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122632-zone-delete.json](evidence/2026-11-12-122632-zone-delete.json) | 2026-11-12T12:26:32Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122612-zone-delete.json](evidence/2026-11-12-122612-zone-delete.json) | 2026-11-12T12:26:12Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-122556-zone-create.json](evidence/2026-11-12-122556-zone-create.json) | 2026-11-12T12:25:56Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122542-zone-create.json](evidence/2026-11-12-122542-zone-create.json) | 2026-11-12T12:25:42Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122534-zone-create.json](evidence/2026-11-12-122534-zone-create.json) | 2026-11-12T12:25:34Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122515-zone-create.json](evidence/2026-11-12-122515-zone-create.json) | 2026-11-12T12:25:15Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-122501-zone-list.json](evidence/2026-11-12-122501-zone-list.json) | 2026-11-12T12:25:01Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-122456-via-create.json](evidence/2026-11-12-122456-via-create.json) | 2026-11-12T12:24:56Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122432-via-create.json](evidence/2026-11-12-122432-via-create.json) | 2026-11-12T12:24:32Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122413-via-create.json](evidence/2026-11-12-122413-via-create.json) | 2026-11-12T12:24:13Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122343-via-create.json](evidence/2026-11-12-122343-via-create.json) | 2026-11-12T12:23:43Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-122316-footprint-update.json](evidence/2026-11-12-122316-footprint-update.json) | 2026-11-12T12:23:16Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122313-footprint-update.json](evidence/2026-11-12-122313-footprint-update.json) | 2026-11-12T12:23:13Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122310-footprint-update.json](evidence/2026-11-12-122310-footprint-update.json) | 2026-11-12T12:23:10Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122303-footprint-update.json](evidence/2026-11-12-122303-footprint-update.json) | 2026-11-12T12:23:03Z | KiCad 10.0.6 | windows | 5 次运行中 5 次通过 |
| [2026-11-12-122256-board-update.json](evidence/2026-11-12-122256-board-update.json) | 2026-11-12T12:22:56Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122241-board-update.json](evidence/2026-11-12-122241-board-update.json) | 2026-11-12T12:22:41Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122233-board-update.json](evidence/2026-11-12-122233-board-update.json) | 2026-11-12T12:22:33Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122215-board-update.json](evidence/2026-11-12-122215-board-update.json) | 2026-11-12T12:22:15Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-122200-track-delete.json](evidence/2026-11-12-122200-track-delete.json) | 2026-11-12T12:22:00Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122157-track-delete.json](evidence/2026-11-12-122157-track-delete.json) | 2026-11-12T12:21:57Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122154-track-delete.json](evidence/2026-11-12-122154-track-delete.json) | 2026-11-12T12:21:54Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122146-track-delete.json](evidence/2026-11-12-122146-track-delete.json) | 2026-11-12T12:21:46Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-122142-track-create.json](evidence/2026-11-12-122142-track-create.json) | 2026-11-12T12:21:42Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122128-track-create.json](evidence/2026-11-12-122128-track-create.json) | 2026-11-12T12:21:28Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122120-track-create.json](evidence/2026-11-12-122120-track-create.json) | 2026-11-12T12:21:20Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122101-track-create.json](evidence/2026-11-12-122101-track-create.json) | 2026-11-12T12:21:01Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-122046-footprint-move.json](evidence/2026-11-12-122046-footprint-move.json) | 2026-11-12T12:20:46Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122032-footprint-move.json](evidence/2026-11-12-122032-footprint-move.json) | 2026-11-12T12:20:32Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122024-footprint-move.json](evidence/2026-11-12-122024-footprint-move.json) | 2026-11-12T12:20:24Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-122012-footprint-move.json](evidence/2026-11-12-122012-footprint-move.json) | 2026-11-12T12:20:12Z | KiCad 10.0.6 | windows | 5 次运行中 5 次通过 |
| [2026-11-12-121954-board-get.json](evidence/2026-11-12-121954-board-get.json) | 2026-11-12T12:19:54Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121951-netlist-import.json](evidence/2026-11-12-121951-netlist-import.json) | 2026-11-12T12:19:51Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121946-netlist-import.json](evidence/2026-11-12-121946-netlist-import.json) | 2026-11-12T12:19:46Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121943-netlist-import.json](evidence/2026-11-12-121943-netlist-import.json) | 2026-11-12T12:19:43Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121933-netlist-import.json](evidence/2026-11-12-121933-netlist-import.json) | 2026-11-12T12:19:33Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121926-footprint-create.json](evidence/2026-11-12-121926-footprint-create.json) | 2026-11-12T12:19:26Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121923-footprint-create.json](evidence/2026-11-12-121923-footprint-create.json) | 2026-11-12T12:19:23Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121921-footprint-create.json](evidence/2026-11-12-121921-footprint-create.json) | 2026-11-12T12:19:21Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121917-board-render.json](evidence/2026-11-12-121917-board-render.json) | 2026-11-12T12:19:17Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121913-board-render.json](evidence/2026-11-12-121913-board-render.json) | 2026-11-12T12:19:13Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121911-board-render.json](evidence/2026-11-12-121911-board-render.json) | 2026-11-12T12:19:11Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121907-board-export.json](evidence/2026-11-12-121907-board-export.json) | 2026-11-12T12:19:07Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121903-board-export.json](evidence/2026-11-12-121903-board-export.json) | 2026-11-12T12:19:03Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121901-board-export.json](evidence/2026-11-12-121901-board-export.json) | 2026-11-12T12:19:01Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121857-track-list.json](evidence/2026-11-12-121857-track-list.json) | 2026-11-12T12:18:57Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121852-footprint-list.json](evidence/2026-11-12-121852-footprint-list.json) | 2026-11-12T12:18:52Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121848-board-check.json](evidence/2026-11-12-121848-board-check.json) | 2026-11-12T12:18:48Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121836-board-create.json](evidence/2026-11-12-121836-board-create.json) | 2026-11-12T12:18:36Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121831-board-create.json](evidence/2026-11-12-121831-board-create.json) | 2026-11-12T12:18:31Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121828-board-create.json](evidence/2026-11-12-121828-board-create.json) | 2026-11-12T12:18:28Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121807-net-update.json](evidence/2026-11-12-121807-net-update.json) | 2026-11-12T12:18:07Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121803-net-update.json](evidence/2026-11-12-121803-net-update.json) | 2026-11-12T12:18:03Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121800-net-update.json](evidence/2026-11-12-121800-net-update.json) | 2026-11-12T12:18:00Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121754-net-update.json](evidence/2026-11-12-121754-net-update.json) | 2026-11-12T12:17:54Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121749-pin-update.json](evidence/2026-11-12-121749-pin-update.json) | 2026-11-12T12:17:49Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121745-pin-update.json](evidence/2026-11-12-121745-pin-update.json) | 2026-11-12T12:17:45Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121742-pin-update.json](evidence/2026-11-12-121742-pin-update.json) | 2026-11-12T12:17:42Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121736-pin-update.json](evidence/2026-11-12-121736-pin-update.json) | 2026-11-12T12:17:36Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121734-pin-update.json](evidence/2026-11-12-121734-pin-update.json) | 2026-11-12T12:17:34Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121729-symbol-delete.json](evidence/2026-11-12-121729-symbol-delete.json) | 2026-11-12T12:17:29Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121725-symbol-delete.json](evidence/2026-11-12-121725-symbol-delete.json) | 2026-11-12T12:17:25Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121722-symbol-delete.json](evidence/2026-11-12-121722-symbol-delete.json) | 2026-11-12T12:17:22Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121716-symbol-delete.json](evidence/2026-11-12-121716-symbol-delete.json) | 2026-11-12T12:17:16Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121715-symbol-delete.json](evidence/2026-11-12-121715-symbol-delete.json) | 2026-11-12T12:17:15Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121709-symbol-move.json](evidence/2026-11-12-121709-symbol-move.json) | 2026-11-12T12:17:09Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121706-symbol-move.json](evidence/2026-11-12-121706-symbol-move.json) | 2026-11-12T12:17:06Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121703-symbol-move.json](evidence/2026-11-12-121703-symbol-move.json) | 2026-11-12T12:17:03Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121657-symbol-move.json](evidence/2026-11-12-121657-symbol-move.json) | 2026-11-12T12:16:57Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121656-symbol-move.json](evidence/2026-11-12-121656-symbol-move.json) | 2026-11-12T12:16:56Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121651-symbol-update.json](evidence/2026-11-12-121651-symbol-update.json) | 2026-11-12T12:16:51Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121647-symbol-update.json](evidence/2026-11-12-121647-symbol-update.json) | 2026-11-12T12:16:47Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121644-symbol-update.json](evidence/2026-11-12-121644-symbol-update.json) | 2026-11-12T12:16:44Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121638-symbol-update.json](evidence/2026-11-12-121638-symbol-update.json) | 2026-11-12T12:16:38Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121636-symbol-update.json](evidence/2026-11-12-121636-symbol-update.json) | 2026-11-12T12:16:36Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121631-symbol-create.json](evidence/2026-11-12-121631-symbol-create.json) | 2026-11-12T12:16:31Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121628-symbol-create.json](evidence/2026-11-12-121628-symbol-create.json) | 2026-11-12T12:16:28Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121625-symbol-create.json](evidence/2026-11-12-121625-symbol-create.json) | 2026-11-12T12:16:25Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121618-symbol-create.json](evidence/2026-11-12-121618-symbol-create.json) | 2026-11-12T12:16:18Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121613-schematic-create.json](evidence/2026-11-12-121613-schematic-create.json) | 2026-11-12T12:16:13Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121610-schematic-create.json](evidence/2026-11-12-121610-schematic-create.json) | 2026-11-12T12:16:10Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121608-schematic-create.json](evidence/2026-11-12-121608-schematic-create.json) | 2026-11-12T12:16:08Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121604-schematic-render.json](evidence/2026-11-12-121604-schematic-render.json) | 2026-11-12T12:16:04Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121600-schematic-render.json](evidence/2026-11-12-121600-schematic-render.json) | 2026-11-12T12:16:00Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121558-schematic-render.json](evidence/2026-11-12-121558-schematic-render.json) | 2026-11-12T12:15:58Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121553-schematic-export.json](evidence/2026-11-12-121553-schematic-export.json) | 2026-11-12T12:15:53Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121550-schematic-export.json](evidence/2026-11-12-121550-schematic-export.json) | 2026-11-12T12:15:50Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121548-schematic-export.json](evidence/2026-11-12-121548-schematic-export.json) | 2026-11-12T12:15:48Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121544-symbol-get.json](evidence/2026-11-12-121544-symbol-get.json) | 2026-11-12T12:15:44Z | KiCad 10.0.6 | windows | 1 次运行中 1 次通过 |
| [2026-11-12-121542-symbol-get.json](evidence/2026-11-12-121542-symbol-get.json) | 2026-11-12T12:15:42Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121538-net-list.json](evidence/2026-11-12-121538-net-list.json) | 2026-11-12T12:15:38Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121534-symbol-list.json](evidence/2026-11-12-121534-symbol-list.json) | 2026-11-12T12:15:34Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121529-schematic-check.json](evidence/2026-11-12-121529-schematic-check.json) | 2026-11-12T12:15:29Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121518-doc-list.json](evidence/2026-11-12-121518-doc-list.json) | 2026-11-12T12:15:18Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121514-doc-get.json](evidence/2026-11-12-121514-doc-get.json) | 2026-11-12T12:15:14Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
| [2026-11-12-121511-library-get.json](evidence/2026-11-12-121511-library-get.json) | 2026-11-12T12:15:11Z | KiCad 10.0.6 | windows | 5 次运行中 5 次通过 |
| [2026-11-12-121504-library-list.json](evidence/2026-11-12-121504-library-list.json) | 2026-11-12T12:15:04Z | KiCad 10.0.6 | windows | 4 次运行中 4 次通过 |
| [2026-11-12-121459-project-get.json](evidence/2026-11-12-121459-project-get.json) | 2026-11-12T12:14:59Z | KiCad 10.0.6 | windows | 3 次运行中 3 次通过 |
<!-- driver:evidence:end -->
