# AGENTS.md

[English](AGENTS.md) | 中文

本文件写给在 kicad-cli 项目上干活的编程 Agent。改动任何东西之前先读它。

## 必须遵守的底线

1. 禁止修改固定副本：`.agent/`、`contract/driver-contract.json`、`kicad_cli/_driver/`、`licenses/`，以及 `scripts/` 里的 `spec.py`、`build.py`、`pack.py`、`platforms.py` 和 `release_gate.py`。它们原样来自 tool-driver-cli-spec 的某一个发布。要换到另一个发布，运行 `python scripts/spec.py sync <version>`，它会把这些副本一起替换。
2. 禁止手改生成的内容：`contract/commands.json`、README 和 Skill 里的命令地图、徽章行、信息卡、风险块、支持矩阵、证据索引，以及 `<!-- driver:spec-...:start -->` 和 `:end -->` 标记之间来自规范发布的文字；本工具自己的文字写在这些标记外面。改它们的来源，再运行 `python scripts/spec.py generate`。
3. 每个领域命令都在 `kicad_cli/commands/` 里登记，写明它的全部效果、模式、每个物理参数的单位量和目标类型。软件无法撤回的修改要标为 `irreversible`。禁止降低确认级别。
4. 每个领域命令都要在 `tests/matrix/` 里有行为矩阵。写命令的矩阵中，`normal` 行就是它的样例。
5. 每次提交前运行 `python -m ruff check .`、`python scripts/spec.py check`、`python -m unittest discover -s tests` 和 `python scripts/spec.py suites`，四项都必须通过。禁止跳过、削弱或修改一致性测试。
6. 真实软件测试只能用 `docs/E2E.md` 里「Authorized Targets」一节列出的目标，按其中「授权规则」做。只有人在自己发的消息里授予目标时才添加，加完把加的那一行告诉人。在列出的目标里不用问人，直接做：建练习副本，启动要测的实例并只关闭这些实例，自己写授权范围文件。
7. 测试把 `KICAD_CLI_STATE_DIR` 设为新建的临时目录，从不碰真实的状态目录。
8. 禁止提交机密、许可证文件或密钥，以及无权分享的客户文件和项目文件，证据里也不行。
9. 脚本和测试在 Windows 上启动子进程时，传入 `CREATE_NO_WINDOW`。
10. 每次改动都在 `CHANGELOG.md` 的 `[Unreleased]` 下加一条记录。
11. 代码、代码注释、提交信息、命令输出和错误信息都用英文。

## 本项目

kicad-cli 驱动 KiCad，支持的版本是 >=10.0.6 <11，模式如下：

- `file`：直接读写 KiCad 的文件。

代码的位置：

- 领域命令：`kicad_cli/commands/`
- 适配层：`kicad_cli/adapter/`
- 驱动运行时，固定副本：`kicad_cli/_driver/`

适配层怎样连到 KiCad：

- **KiCad 的文件**（`kicad_cli/adapter/fileformat/`）：每次修改都直接改文件里的 s-expression 文本，文件其余部分保持 KiCad 写的原样。各组命令的处理代码在 `kicad_cli/adapter/handlers/`，它们用到的引擎放在旁边（`sch_edit_*`、`pcb_read_*`、`pcb_edit_*`）。
- **KiCad 自带的命令行程序**（`kicad_cli/adapter/kicad_tools.py`）：ERC、DRC、导出和最新网表。它和本工具同名；`kicad_cli/adapter/install.py` 只在 KiCad 安装目录里找它，从不只凭 PATH。每次运行都有时限（读 25 秒，写 40 秒），保证调用按时返回。
- 只有文件模式：不和正在运行的 KiCad 通信。文件旁有 KiCad 的锁文件 `~<文件名>.lck` 时，写入会被拒绝（`base.refuse_if_open`）。
- 适配层只返回 DCLI-279 列出的宿主错误码；用 `kicad_cli/adapter/base.py` 里的辅助函数：`ambiguous`（E_AMBIGUOUS，带候选）、`refuse_if_open`（E_CONFLICT，`file_open_in_session`）、`kicad`（没装 KiCad 时返回 E_BACKEND_UNAVAILABLE）。读取时 KiCad 运行超时返回 E_TIMEOUT。
- 单元测试从不运行 KiCad：它们模拟 `kicad_tools`，把 `KICAD_CONFIG_HOME` 指向空文件夹，并替换 `base._found`。

常用命令见 `CONTRIBUTING_zh.md` 的「常用命令」一节。真实软件测试只用 `docs/E2E.md` 里「Authorized Targets」一节的目标；本文件不列任何目标。
