# 参与 kicad-cli 的贡献

[English](CONTRIBUTING.md) | 中文

人和编程 Agent 都按同样的流程贡献。编程 Agent 还要遵守 `AGENTS_zh.md`。

## 开发环境

<!-- driver:spec-development-setup:start -->
需要准备：

- Python 3.12 或更新版本
- Node.js 和 npm，用来构建和测试 npm 包
- Git

测试和一致性测试从不需要 KiCad：模拟宿主代替它。只有在授权目标上做真实软件运行时才需要 KiCad（授权目标的清单在 `docs/E2E.md`）。

克隆仓库：

```bash
git clone https://github.com/fatecannotbealtered/kicad-cli.git
```

然后在克隆下来的文件夹里创建虚拟环境、安装开发依赖，再运行项目检查：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --no-deps -r requirements-dev.txt
python scripts/spec.py check
```

在 Windows 上改用 `.venv\Scripts\activate` 激活环境。`requirements-dev.txt` 为每个开发依赖固定了版本和哈希，装上的只会是审过的版本。
<!-- driver:spec-development-setup:end -->

## 常用命令

<!-- driver:spec-commands:start -->
| 命令 | 作用 |
|---|---|
| `python scripts/spec.py check` | 运行项目检查：布局、文档及其固定原文、生成的内容、固定副本、版本、Skill、知识、行为矩阵、证据和成熟度 |
| `python scripts/spec.py generate` | 重新生成 `contract/commands.json`、命令地图、徽章行、信息卡、风险块、支持矩阵和证据索引，并按规范发布写入 `driver:spec-` 标记之间的文字 |
| `python scripts/spec.py suites` | 用模拟宿主代替 KiCad，对 kicad-cli 运行两套一致性测试 |
| `python scripts/spec.py sync <version>` | 用 tool-driver-cli-spec 另一个发布里的副本替换固定副本 |
| `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>` | 在真实的 KiCad 上、在授权目标上运行一个命令的行为矩阵，并写下真实软件记录 |
| `python scripts/spec.py eval --workspace-root <folder>` | 在授权的文件夹里，用 Claude Code 在真实的 KiCad 上运行 `tests/eval/` 里的评测题，并写出评测报告；加 `--check --fake-host` 时，用脚本代理检查每个评分程序 |
| `python -m unittest discover -s tests` | 运行单元测试 |
| `python scripts/build.py` | 为本机构建原生程序和平台包 |
| `python scripts/pack.py` | 构建 npm 包，并做包内容检查 |
| `python scripts/smoke.py` | 对构建好的包运行平台交付的关键操作 |

固定副本是 `.agent/`、`contract/driver-contract.json`、`kicad_cli/_driver/`、`licenses/`，以及 `scripts/` 里的 `spec.py`、`build.py`、`pack.py`、`platforms.py` 和 `release_gate.py`。禁止修改它们；`sync` 会把它们一起替换，并列出自上次固定的发布以来模板里改过的其他文件，供对照本项目的副本。
<!-- driver:spec-commands:end -->

## 分支与提交约定

<!-- driver:spec-branch-and-commit-convention:start -->
- `main` 始终能通过合并门槛。在分支上工作，通过合并请求合入。
- 分支命名为 `<kind>/<topic>`，kind 取 `feat`、`fix`、`docs`、`test` 或 `chore`，例如 `feat/part-move`。
- 提交信息用英文：先是一行不超过 72 个字符的祈使句标题，如 `Add part move`，空一行，再在正文里说明原因。
- 每个提交只做一件事，连同它的测试、行为矩阵、两种语言的文档和 CHANGELOG 条目一起提交。
- 只有维护者才能发布。发布时在 `package.json` 里设新版本（这是版本号的唯一来源），把 `[Unreleased]` 下的条目移进 `[X.Y.Z] - YYYY-MM-DD` 一节。合入后，维护者推送附注标签 `vX.Y.Z`，剩下的由发布工作流完成。
<!-- driver:spec-branch-and-commit-convention:end -->

## CI 检查

<!-- driver:spec-ci-checks:start -->
`.github/workflows/ci.yml` 对每次改动运行合并门槛。它的最后一个作业 `merge gate passed` 只在每个出包系统上的每项检查都通过时才成功；把它设为主分支必须通过的状态检查，任何一项检查失败就会挡住合并。发布运行同样的检查。

| 检查 | 检查什么 | 本地怎样运行 |
|---|---|---|
| Lint | 工具的代码风格规则 | `python -m ruff check .` |
| 单元测试 | `tests/` 里工具自己的测试 | `python -m unittest discover -s tests` |
| 一致性测试 | 两套测试，在每个出包平台上运行 | `python scripts/spec.py suites` |
| 项目检查 | 布局、文档、标签、固定副本、Skill、知识和能力对应 | `python scripts/spec.py check` |
| 输出大小检查 | 每个信封都在大小上限之内；由通用一致性测试 R04 执行 | `python scripts/spec.py suites` |
| npm 包内容检查 | 每个包只含允许的内容，没有安装脚本 | `python scripts/pack.py` |

`.github/workflows/release.yml` 只在维护者推送版本标签时运行。它先跑同样的检查，再做平台交付：在每个平台包对应的操作系统和 CPU 上，把将要发布的包装进干净的环境，启动 kicad-cli，运行一个关键操作，做法与 `python scripts/smoke.py` 相同。只有所有平台包都全部通过，它才带着来源证明发布到 npm。带预发布部分的版本，如 `1.2.0-rc.1`，以 npm 标签 `next` 发布，`update` 默认不会安装它。Linux 包在 Debian 11（glibc 2.31）上构建，所以较老的 Linux 系统也能运行。包在 npmjs.com 上配好可信发布时，工作流用可信发布，否则用 `NPM_TOKEN` 密钥；首次发布后就配好可信发布，并禁止用令牌发布。
<!-- driver:spec-ci-checks:end -->

## 测试与证据

<!-- driver:spec-tests-and-evidence:start -->
**一致性测试。** `python scripts/spec.py suites` 运行通用和驱动两套一致性测试。它们设好 `KICAD_CLI_FAKE_HOST`，把 kicad-cli 作为独立进程启动，由模拟宿主代替 KiCad，只根据退出码、输出和模拟宿主收到的内容做判断。禁止跳过、削弱或修改测试，也禁止把测试标为预期失败；适用却没有运行的测试算作失败。模拟宿主给出的是模拟结果，从不算作证据。

**单元测试。** 工具自己的测试放在 `tests/`。每个测试都把 `KICAD_CLI_STATE_DIR` 设为新建的临时目录，并且要么把 `KICAD_CLI_SCOPE_FILE` 指向这个目录里的授权范围文件，要么去掉这个变量。

**行为矩阵。** 每个领域命令，以及每个调用适配层能力的标准命令，都在 `tests/matrix/` 里有一个矩阵文件，以命令路径命名，如 `tests/matrix/part-move.json`。文件把每一行各列一次：`normal`、`empty`、`invalid_input`、`not_found`、`ambiguous`、`conflict`、`no_scope`、`busy`、`dialog`、`disconnect`、`unsaved` 和 `read_back_mismatch`。适用的行写明参数和预期的退出码与错误码；不适用的行写 `applies: false` 和原因。写命令 `normal` 行的参数就是它的样例，通用一致性测试会拿它在模拟宿主上运行。参数只通过占位符 `{session}`、`{doc}`、`{device}` 和 `{dir}` 指定会话、文档、设备和输出文件夹，通过 `{job}` 指定作业，通过 `{inputs}`（即 `tests/inputs/` 文件夹）指定命令只读取的文件。

**真实软件记录。** `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>` 在真实的 KiCad 上运行一个矩阵，矩阵用到的每个占位符各给一个 `--set`，占位符名不带花括号，如 `--set doc=<file>`；每个值都必须是授权目标。它把真实软件记录写进 `docs/evidence/`，并填上每一行的实际结果和 `live:` 证据引用。具体步骤见 `docs/E2E_zh.md`。无法按需制造的行，如 `disconnect`，可以标为 `unrunnable` 并写明原因；`normal`、`dialog` 和 `read_back_mismatch` 这三行不可以。`--row read_back_mismatch` 让工具在读回之前等一会儿，其间从外部改掉结果；列出了多种 `doc_kinds` 的命令，用 `--kind` 在每种文档上各运行一次 `normal` 行，只列一种的不用写 `--kind`；对象在不同种类里不一样时，用这一行的 `kind_arguments` 给出该种类的参数。

**评测。** 命令标为 stable 之前，`tests/eval/` 里要为每个适用于 kicad-cli、需要工具自测的场景各出一题，`docs/evidence/` 里在真实 KiCad 上得到的评测报告要给每道题都留下当前有效的结果，每道题最新的结果都通过，其中有一道在 `COMMANDS` 里列出这条命令、并实际运行了它的题通过了。几份报告里的结果合在一起算。每道题是一个以题目 ID 命名的 Python 文件；名为 `_*.py` 的文件放各题共用的辅助代码。

```python
"""Move one footprint inside the scope."""
SCENARIO = "scoped_change"
COMMANDS = ["footprint move"]
TURNS = ["Move footprint R1 in {documents}/board.kicad_pcb to x 12.5 mm, y 30 mm."]
FILES = "boards"          # copied into the question's documents folder


def grade(run):
    part = run.tool("footprint", "get", "--doc", str(run.documents / "board.kicad_pcb"), "--id", "R1")["data"]["items"][0]
    run.check(part["x_mm"] == 12.5, "R1 did not move")
    run.check(len([c for c in run.write_calls() if c.ok]) == 1, "not exactly one write")
```

`python scripts/spec.py eval --workspace-root <folder>` 用 Claude Code 在真实的 KiCad 上运行这些题目并写出报告；这个文件夹必须列在 `docs/E2E.md` 的「Authorized Targets」一节里，或者位于列出的文件夹里面。运行器为每道题在这个文件夹里新建一个工作目录，放进 Skill 和授权范围文件，把文档放在旁边的文件夹里，并记下 kicad-cli 的每次运行；Agent 的 shell 只能运行 kicad-cli，文件工具只能用在工作目录里。`TURNS` 和授权范围目标里的 `{documents}` 代表文档所在的文件夹。默认的授权范围授予这个文件夹、`COMMANDS` 里命令的效果，只有其中某条命令需要时才授予 `dangerous`；`SCOPE` 可以替换其中任何字段，命令都只读取的题目不写授权范围文件。这个授权范围文件由运行器自己写，谁都不手改。在真实的 KiCad 上，kicad-cli 沿用你的环境，这样才找得到正在运行的实例；会话模式的题目在 `before` 里打开文档，在 `after` 里关闭，`after` 总会被调用。

| 评分程序和钩子可用 | 作用 |
|---|---|
| `run.check(condition, problem)` | 条件不成立时记一处失败 |
| `run.tool(*args)` | 以评分程序的身份运行 kicad-cli，返回它的信封；不算 Agent 的运行 |
| `run.read(name)`、`run.documents` | 文档文件夹里的一个文件，以及这个文件夹 |
| `run.calls`、`run.ran(path, turn)`、`run.write_calls(turn)` | 记下的 Agent 运行；每条有 `args`、`exit`、`ok`、`code`、`details`、`data`、`value(flag)` 和 `matches(line)` |
| `run.messages`、`run.said(*texts, turn=-1)` | Agent 每轮告诉人的话；按意思判定，中文和英文都要认 |
| `run.scope_changed()`、`run.notes` | 授权范围文件有没有被改；钩子留给评分程序的字典 |
| `run.host(...)`、`run.state()`、`run.applied()`、`run.document(id)` | 只在模拟宿主上可用 |

可选的 `right(agent, turn)` 和 `wrong(agent, turn)` 脚本代理使用 `agent.run(*args)`、`agent.line(text)`、`agent.say(text)` 和 `agent.calls`。`python scripts/spec.py eval --check --fake-host` 在模拟宿主上证明每个评分程序判 `right` 通过、判每个 `wrong` 不通过；把 `--fake-host` 换成 `--workspace-root`，就在真实的 KiCad 上做同样的检查，`FAKE_HOST = None` 的题只能这样检查。规范自己的评测题在发布包的 `conformance/eval/` 里，可以当作范例；其中 `read_answer`、`scoped_change`、`knowledge_lookup` 和 `separate_save` 的评分只用在真实 KiCad 上也能用的接口。

**成熟度。** 每个命令都声明自己的成熟度和背后的证据，`kicad-cli reference` 会显示这两项。

| 级别 | 需要的证据 |
|---|---|
| `draft` | 不需要；但写命令仍要有样例 |
| `verified` | 当前有效的真实软件记录，矩阵中适用的每一行都通过，标为 `unrunnable` 的行除外 |
| `stable` | 另加当前有效的评测结果 |

证据在它依赖的东西没变时一直算数，与版本号无关：真实运行在命令登记或适配层代码改动之前有效，评测结果在它的题目、它列出的命令或手写的 Skill 和知识改动之前有效。有了这样的改动，只重跑它涉及的矩阵和评测题：`python scripts/spec.py check` 会列出它们，`python scripts/spec.py eval --workspace-root <folder> --stale` 只跑需要重跑的题。证据引用的格式是 `live:docs/evidence/<file>.json#<row>` 或 `eval:docs/evidence/eval-<date>.json`。
<!-- driver:spec-tests-and-evidence:end -->

## 新增或修改命令

<!-- driver:spec-adding-or-changing-a-command:start -->
1. **调查。** 在 `docs/APPLICATION_zh.md` 的能力表里找到这项能力。没有时，加一行并给它新的 `CAP-nnn`。有命令提供它之后，把处置改为 `provided`。
2. **命名。** 用 `noun verb` 的形式。动词从固定列表里选：`list`、`get`、`search`、`check`、`create`、`update`、`delete`、`move`、`import`、`render`、`export`、`run`、`stop`。名词用 KiCad 自己的术语，写成完整的英文单词。名词不能用内置命令或标准命令的名称，如 `session`、`doc` 或 `job`。
3. **登记。** 在 `kicad_cli/commands/` 里登记命令，写明说明、效果、模式、目标类型、参数及其类型、每个物理量的单位量、机密参数和输出中的不可信字段。每个效果都要声明。KiCad 无法撤回的修改标 `irreversible: true`，只用来停止工作的命令标 `halt: true`。KiCad 会取整或吸附数值的地方，给出读回容差；相关的知识条目写在 `related_knowledge` 里。
4. **实现。** 在 `kicad_cli/adapter/` 里实现命令需要的适配层能力；KiCad 的接口只能在它的进程里或主线程上调用时，写在 `plugin/` 的宿主组件里。禁止重新实现驱动运行时已经做的事。
5. **矩阵。** 在 `tests/matrix/` 里写行为矩阵，每一行都要有。写命令的 `normal` 行就是它的样例。
6. **生成与检查。** 运行 `python scripts/spec.py generate`，再运行 `python scripts/spec.py check`、`python -m unittest discover -s tests` 和 `python scripts/spec.py suites`。
7. **证明。** 命令一开始是 `draft`。用 `python scripts/spec.py matrix` 在真实的 KiCad 上、在授权目标上运行它的矩阵，并提交真实软件记录，命令就可以标为 `verified`。只要改动影响到命令与 KiCad 的交互，就重新运行矩阵。
8. **Skill 与知识。** 命令地图会自动更新。命令对应的任务需要好几步时，在拥有这个命令的 Skill 里加一个剧本。入口 Skill 要超过 150 行时，按用户所说的任务拆成领域 Skill，每个都在 `metadata.commands` 里列出自己的命令。把发现的限制和错误写成 `knowledge/` 里的条目。
9. **CHANGELOG。** 在 `[Unreleased]` 下加一条：新命令或新参数用 `Added`，行为变化用 `Changed`，以后要删除的用 `Deprecated`，已经删除的用 `Removed`，缺陷修复用 `Fixed`，漏洞修复用 `Security`。
<!-- driver:spec-adding-or-changing-a-command:end -->

## 合并请求

<!-- driver:spec-pull-requests:start -->
- 一个合并请求只做一件事。描述里写明改了什么、为什么改，列出涉及的命令和能力编号，并附上新的真实软件记录或评测报告的链接。
- 所有 CI 检查都通过，生成的内容重新生成，不手改。
- 英文和中文文档一起改，内容一致。
- 代码、测试和 `docs/evidence/` 里都不能有机密、许可证数据或无权分享的文件。
- 评审者对照能力调查检查声明的效果，以及 `irreversible` 和 `halt` 标记，并检查不可信字段、Skill 文字，以及矩阵中不适用或无法运行的行所给的原因。
- 由维护者合入。包只由发布工作流发布。
<!-- driver:spec-pull-requests:end -->

## 安全

<!-- driver:spec-security:start -->
不要在公开的 Issue 或合并请求里报告漏洞。请按 `SECURITY_zh.md` 的说明，通过 <https://github.com/fatecannotbealtered/kicad-cli/security/advisories/new> 私下报告。
<!-- driver:spec-security:end -->
