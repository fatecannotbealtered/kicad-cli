# DRIVER-REPO-SPEC

[English](DRIVER-REPO-SPEC.md) | 中文

> 版本 1.2.0 · 条款 DREPO-001 至 DREPO-062

## 怎么读

本规范规定驱动工具项目怎样布局、写哪些文档、用哪些标签、怎样定版本和发布。每条规则是一条有固定编号的条款，如 `DREPO-020`。条款最后有两行：「为什么」说明理由，「检查」说明怎么检查：🤖 表示由脚本或测试检查，👀 表示由人在评审时检查。

规范用词的含义：

- 必须、禁止：强制要求。违反了就不符合本规范。
- 应该、不应该：默认的做法。只有说得出理由时才偏离。
- 可以：允许，但不要求。

机器契约 `contract/driver-contract.json` 以数据形式记录本规范的文件名、章节名、徽章和标签。重复这些值的表格带有契约标记。这样的表格与契约不一致时，以契约为准。

占位符：`<tool>` 是工具的命令名，`<package>` 是它的 npm 包名，`<pkg>` 是它的源码包目录，`<PREFIX>` 是它的环境变量前缀（DREPO-053）。`X.Y.Z` 表示版本号。

## 适用范围

本规范适用于所有驱动工具项目，不论驱动哪种软件、用哪些模式、用什么语言实现。内容包括：

- 项目的文件和目录
- 项目文档的章节，英文版和中文版
- 徽章行、信息卡和状态标签
- 规范、契约、驱动运行时和共用脚本的固定副本
- 版本、CI 和发布
- 通过 npm 分发
- 能力调查，以及接入新软件的步骤

用模板生成的项目一开始就符合这些规则。之后每次合并都通过 `check_project`，工具就一直符合。

命令行为、安全、Skill 和验证另有条款。项目规则依赖它们的地方，本规范按编号引用。

## 术语

| 术语 | 含义 |
|---|---|
| 驱动工具 | 让 AI Agent 操作专业桌面软件或设备的命令行工具 |
| 工具项目 | 一个驱动工具的代码仓库 |
| 模板 | 随本规范提供的项目模板，用来生成新的工具项目 |
| 固定副本 | 从某一个规范发布原样复制来的文件：规范、契约、驱动运行时、它们的许可证，以及每个工具共用的脚本 |
| 同步脚本 | `sync_spec`，把固定副本复制进工具项目，升级时整体替换 |
| 项目检查 | `check_project`，对照本规范检查工具项目的脚本 |
| 驱动运行时 | `<pkg>/_driver/` 里的共享代码，实现内置命令、标准命令、写操作流程和自更新 |
| 宿主组件 | 工具装进软件进程里的代码，用于只能在软件内部调用的接口（DCLI-262） |
| 原生程序 | 为某一种操作系统和 CPU 架构打成的独立可执行程序 |
| 平台包 | 装着某一种操作系统和 CPU 架构的原生程序的 npm 包 |
| 启动器 | npm 主包里的 Node.js 小脚本，负责找到并启动原生程序 |
| 命令入口 | npm 放在 PATH 上的文件，输入 `<tool>` 时由它启动工具 |
| 出包平台 | 工具为它发布平台包的操作系统 |
| 来源证明 | npm 上的证明，表明某个包由本工具自己的发布工作流构建 |
| 能力调查 | `docs/APPLICATION.md`：软件能做什么、能怎样自动化 |
| 能力编号 | 调查表里每项能力的编号 `CAP-nnn` |
| 处置 | 工具怎样对待一项能力：`provided`、`deferred`、`excluded` 或 `unknown` |
| 徽章行 | README 顶部的一排徽章 |
| 信息卡 | README 标题区下面的表格，写全徽章背后的细节 |
| 支持矩阵 | `docs/COMPATIBILITY.md` 里说明工具在哪里能用的表格，由真实软件记录和平台包生成（DREPO-016） |
| 兼容性标签 | 支持矩阵每一行里的状态标记，如 ✅ verified |
| 真实软件记录 | 在真实软件上运行一次的 JSON 记录，放在 `docs/evidence/`（DVER-042） |
| 合并门槛 | 每次改动合并前、每次发布前都要通过的检查 |
| 平台交付 | 发布时的一个步骤：在每个平台包对应的操作系统和 CPU 架构上安装这个包，运行一个关键操作 |
| 交付记录 | 平台交付为每个平台记下的包摘要和结果 |

## 运行时与工具的分工

| 方面 | 模板、同步脚本和检查提供 | 工具作者要做 |
|---|---|---|
| 项目骨架 | 所有必需的文件和目录，两种语言的固定标题和固定原文都已写好：工作方式、必须遵守的底线、安装块、SECURITY 的五个固定章节和 `docs/E2E.md` 的授权规则 | 填写工具自己的章节，去掉所有占位符 |
| 固定副本 | `sync_spec` 复制规范、契约、运行时、许可证和共用脚本，升级时整体替换 | 升级时运行 `sync_spec`，从不手改固定副本 |
| 生成的内容 | 根据数据生成 `contract/commands.json`、命令地图、徽章行、信息卡和支持矩阵，并按发布写入各工具共用文字的脚本（DREPO-036） | 登记命令，让证据和 `docs/COMPATIBILITY.md` 的其他章节保持真实 |
| 检查 | `check_project`：布局、根目录文件、章节、双语对应、占位符、哈希、徽章、标签和能力对应 | 改正 `check_project` 报出的每个问题 |
| CI 与发布 | 工作流 `ci.yml` 和 `release.yml`，包含合并门槛、平台交付和带来源证明的发布 | 让两个工作流保持通过，为平台交付选定关键操作 |
| 分发 | 原生程序、平台包和启动器的打包脚本；运行时里的 `update` 和直接命令入口（DCLI-070、DCLI-075） | 在 `package.json` 里写好包名、命令名和平台包 |
| 调查与接入 | `docs/APPLICATION.md` 的骨架和开发用 Skill `build-driver-cli` | 调查软件，在每个接入步骤里做出决定 |

## 项目布局

### DREPO-001 项目布局

下面的目录树是一个典型的工具项目。它只是示意，本身不是规则：必需的文件和路径以 DREPO-002 和 DREPO-004 为准，其他条款还会在其中加入路径，如 `tests/matrix/`（DVER-030）。示例是 Python 工具；用其他构建系统的工具，把 `pyproject.toml` 换成自己的构建配置。

```
<tool>/
├── README.md / README_zh.md        标题区、信息卡和九个固定章节
├── AGENTS.md / AGENTS_zh.md
├── CHANGELOG.md
├── CONTRIBUTING.md / CONTRIBUTING_zh.md
├── SECURITY.md / SECURITY_zh.md
├── CODE_OF_CONDUCT.md
├── LICENSE
├── NOTICE.md                       所驱动的软件及其商标、第三方许可
├── package.json                    npm 包：包名、版本、命令名、各平台包
├── pyproject.toml
├── .agent/                         本规范，原样复制
│   └── SPEC_VERSION
├── contract/                       driver-contract.json（原样复制）、commands.json（由命令登记生成）
├── licenses/                       本规范和运行时的许可证
├── <pkg>/
│   ├── _driver/                    驱动运行时，原样复制
│   ├── commands/                   领域命令（工具作者写）
│   └── adapter/                    适配层（工具作者写）
├── plugin/                         宿主组件，用软件支持的语言（需要时才建）；source/ 放要编译、不安装的代码
├── skills/
│   ├── <tool>/                     入口 Skill
│   └── <product>-<area>/           领域 Skill
├── knowledge/                      内置知识
├── docs/
│   ├── APPLICATION.md / _zh        能力调查
│   ├── COMPATIBILITY.md / _zh      支持矩阵
│   ├── E2E.md / _zh                真实软件验收和授权目标清单
│   └── evidence/                   真实软件记录和评测报告（JSON）
├── scripts/                        构建、打 npm 平台包、冒烟测试、检查脚本
├── tests/                          工具的测试和一致性测试的运行
│   ├── matrix/                     行为矩阵，每条命令一个 JSON 文件
│   ├── inputs/                     样例只读取的文件，即 {inputs}（DVER-002）
│   └── eval/                       评测题（命令要标 stable 时必需）
└── .github/                        工作流 ci.yml 和 release.yml、Issue 与 PR 模板
```

领域命令必须放在 `<pkg>/commands/`，适配层代码放在 `<pkg>/adapter/`。宿主组件必须放在 `plugin/`，要编译进安装文件的代码放在 `plugin/source/`（DCLI-240）；只有工具有宿主组件时才建 `plugin/`（DCLI-262）。

为什么：每个工具把同样的东西放在同样的地方，熟悉一个工具的人和 Agent，到了下一个工具也能马上找到。
检查：🤖 `check_project` 检查 DREPO-002 和 DREPO-004 列出的路径；👀 评审时检查代码放的位置。

### DREPO-002 根目录文件

每个工具项目的根目录都必须有下面每一个文件：

<!-- contract:root_files -->
| 文件 | 要求 |
|---|---|
| `README.md` | 标题区、信息卡和九个固定章节（DREPO-011） |
| `README_zh.md` | 中文 README（DREPO-010） |
| `AGENTS.md` | 写给在本项目上工作的编程 Agent 的规则，两个固定章节（DREPO-012） |
| `AGENTS_zh.md` | 中文 AGENTS 文件 |
| `CHANGELOG.md` | Keep a Changelog 格式，只用英文（DREPO-018） |
| `CONTRIBUTING.md` | 怎样参与贡献，固定章节（DREPO-014） |
| `CONTRIBUTING_zh.md` | 中文 CONTRIBUTING 文件 |
| `SECURITY.md` | 安全模型和漏洞报告，固定章节（DREPO-013） |
| `SECURITY_zh.md` | 中文 SECURITY 文件 |
| `CODE_OF_CONDUCT.md` | 所有参与者的行为准则 |
| `LICENSE` | 工具的许可证 |
| `NOTICE.md` | 工具所驱动的软件及其商标，以及第三方组件的许可 |
| `package.json` | npm 包：包名、版本、命令名和平台包（DREPO-053） |

为什么：这些文件回答大家对一个工具最先想问的问题：它是什么、怎么装、安不安全、怎么参与。
检查：🤖 `check_project`。

### DREPO-003 根目录不放别的文件

除了 DREPO-002 的文件，根目录只可以再放两类文件：

- 构建配置，如 `pyproject.toml` 和 DSEC-070 要求的锁文件
- 工具配置，如 `.gitignore` 和 `.editorconfig`

所有脚本都必须放在 `scripts/`。根目录禁止出现其他文件。

为什么：根目录整洁，每个工具都只摆着同样几个文件，读者一眼就能看到要紧的东西。
检查：🤖 `check_project` 拒绝根目录里的其他文件。

### DREPO-004 必需的路径

每个工具项目都必须有下面这些路径。这张表和下面按条件存在的路径才是规定的清单；DREPO-001 的目录树只是示意。

<!-- contract:required_paths -->
| 路径 | 内容 |
|---|---|
| `.agent/` | 固定的规范副本（DREPO-030） |
| `.agent/SPEC_VERSION` | 固定的规范的版本（DREPO-030） |
| `contract/driver-contract.json` | 固定的契约副本（DREPO-031） |
| `contract/commands.json` | 工具的命令清单，由命令登记（DCLI-029）生成，并通过 `contract/schemas/commands.schema.json` 的校验（DREPO-031） |
| `licenses/` | 规范和运行时的许可证（DREPO-033） |
| `knowledge/` | 内置知识，至少包含 DSKL-126 要求的条目 |
| `skills/` | 入口 Skill 和领域 Skill（DSKL-022、DSKL-023） |
| `docs/APPLICATION.md` | 能力调查（DREPO-015、DREPO-060） |
| `docs/APPLICATION_zh.md` | 中文能力调查 |
| `docs/COMPATIBILITY.md` | 支持矩阵（DREPO-016） |
| `docs/COMPATIBILITY_zh.md` | 中文支持矩阵 |
| `docs/E2E.md` | 真实软件验收，含唯一的授权目标清单（DREPO-017、DVER-051） |
| `docs/E2E_zh.md` | 中文真实软件验收文档 |
| `docs/evidence/` | 真实软件记录和评测报告（DVER-042、DSKL-043） |
| `scripts/` | 构建、打包、冒烟测试和检查脚本；其中一部分是固定副本（DREPO-035） |
| `tests/` | 工具的测试，包括一致性测试的运行和行为矩阵（DVER-001、DVER-030） |
| `tests/matrix/` | 每条命令一份行为矩阵（DVER-030） |
| `.github/workflows/ci.yml` | 合并门槛（DREPO-041） |
| `.github/workflows/release.yml` | 发布工作流，只由标签触发（DREPO-043） |

下面的路径在满足条件时也必须存在：

- `<pkg>/_driver/` 及其中的 `MANIFEST.json`：每个工具都有（DREPO-032）
- `plugin/`：工具有宿主组件时
- `tests/eval/`：有命令标为 stable 时（DSKL-043）

为什么：项目检查、工作流和 Agent 都按名字找这些路径。
检查：🤖 `check_project`。

## 文档骨架

### DREPO-010 双语文档

下面每份文档都必须有英文版和中文版。英文文件是主文件；中文文件同名，在 `.md` 前加 `_zh`。

<!-- contract:bilingual_files -->
| 英文文件 | 中文文件 |
|---|---|
| `README.md` | `README_zh.md` |
| `AGENTS.md` | `AGENTS_zh.md` |
| `SECURITY.md` | `SECURITY_zh.md` |
| `CONTRIBUTING.md` | `CONTRIBUTING_zh.md` |
| `docs/APPLICATION.md` | `docs/APPLICATION_zh.md` |
| `docs/COMPATIBILITY.md` | `docs/COMPATIBILITY_zh.md` |
| `docs/E2E.md` | `docs/E2E_zh.md` |

- 一对文件必须说同样的内容。中文版用自然的中文写，不逐字翻译。
- 两个文件必须有相同的 `##` 章节，顺序相同，每个章节里的表格行数也相同。
- 英文文件的章节标题用英文，中文文件用中文。每个固定章节的中文标题列在契约的 `project.sections_zh` 里，本规范中文版的各表也把它写在英文名旁边。
- 每个文件都必须用一行语言切换链接指向另一个：英文文件写 `English | [中文](<name>_zh.md)`，中文文件写 `[English](<name>.md) | 中文`。README 里这一行属于标题区（DREPO-011）；其他文件里，它是标题后的第一行。
- 下面这些只用英文：`CHANGELOG.md`（DREPO-018）、命令输出和错误信息、代码和代码注释。
- Skill 只写一份，正文用英文，`description` 同时写中英文的说法（DSKL-001）。
- 内置知识条目不要求双语，每条在 `lang` 里写明语言（DSKL-120）。

为什么：两种语言的读者看到的事实相同；只改了一边，马上就能发现。
检查：🤖 `check_project` 比较每对文件的章节、表格行数和语言切换行；👀 评审时检查中文是否自然。

### DREPO-011 README 章节

README 必须以标题区开头，依次是：写着工具名称的 `#` 标题、一句话定位、徽章行（DREPO-020）和语言切换行。接着是信息卡（DREPO-021）。然后是下面这些 `##` 章节，名称和顺序都按此表：

<!-- contract:readme_sections -->
| 章节 | 内容 |
|---|---|
| `Install` 安装 | 安装块（DREPO-054） |
| `Capabilities` 能力 | 全部命令的命令地图，由命令登记生成，与 Skill 里的命令地图出自同一个生成器（DSKL-006） |
| `How It Works` 工作方式 | 怎样选实例和文档、确认级别、保存，固定原文由模板提供 |
| `Authorization` 授权 | 人怎样用授权范围文件授予写权限（DSEC-010 至 DSEC-018），以及本工具的命令需要哪些效果 |
| `About This Tool` 关于本工具 | 自由章节：领域特点、典型任务、截图，凡是能体现工具特色的都放这里 |
| `Configuration` 配置 | 环境变量，如 `<PREFIX>_STATE_DIR`（DSEC-030）和 `<PREFIX>_SCOPE_FILE`（DSEC-010），以及工具怎样找到软件 |
| `Uninstall` 卸载 | 怎样卸载宿主组件（有的话）、npm 包、已安装的 Skill 和状态目录 |
| `Development` 开发 | 怎样构建和测试工具，细节见 `CONTRIBUTING.md` |
| `Links` 链接 | 规范版本、`CHANGELOG.md`、`SECURITY.md`、`docs/` 下的文档和问题跟踪 |

「关于本工具」是唯一的自由章节。其他章节必须保持名称和位置不变，并写上表中的内容。禁止修改「工作方式」的固定原文。有宿主组件时，「安装」加一步 `plugin install`（DREPO-054）。

为什么：熟悉一个工具 README 的读者，在下一个工具里能在同样的位置找到同样的内容；自由章节让每个工具保留自己的特色。
检查：🤖 `check_project` 检查标题区、信息卡、章节顺序和固定原文；👀 评审时阅读「关于本工具」。

### DREPO-012 AGENTS 章节

`AGENTS.md` 写给在工具项目上干活的编程 Agent。它必须有下面这些 `##` 章节，按此顺序：

<!-- contract:agents_sections -->
| 章节 | 内容 |
|---|---|
| `Bare minimum to obey` 必须遵守的底线 | 模板提供的固定规则 |
| `This project` 本项目 | 工具驱动的软件、模式、适配层代码的位置和常用命令。真实软件测试的授权目标只指向 `docs/E2E.md` 的「授权目标」一节，本节自己不列任何目标（DVER-050） |

禁止修改「必须遵守的底线」的原文。

为什么：编程 Agent 打开任何一个工具项目，先读到同样的基本规则，再读到这个项目自己的情况。
检查：🤖 `check_project` 检查章节，并把「必须遵守的底线」与模板比对。

### DREPO-013 SECURITY 章节

`SECURITY.md` 必须有下面这些 `##` 章节，按此顺序：

<!-- contract:security_sections -->
| 章节 | 内容 |
|---|---|
| `Supported Versions` 支持的版本 | 哪些版本会得到安全修复；有已知漏洞的版本在 npm 上标为弃用，更新提示就会向它的用户发出警告（DCLI-079） |
| `Reporting a Vulnerability` 报告漏洞 | 怎样私下报告漏洞 |
| `Risk Tier` 风险等级 | 工具的风险等级，以及决定它的命令（DSEC-002） |
| `Authorization` 授权 | 授权范围文件：放在哪里、谁来写、授予什么（DSEC-010 至 DSEC-018） |
| `Write Confirmation` 写入确认 | 固定原文：确认级别、确认令牌和确认时的复查（DCLI-052 至 DCLI-056） |
| `Host Component` 宿主组件 | 什么代码在软件里运行、怎样安装、怎样连接（DSEC-050 至 DSEC-053） |
| `Physical Effects` 物理动作 | 怎样防护物理动作（DSEC-061 和 DSEC-062） |
| `State Directory` 状态目录 | 固定原文：状态目录的位置、内容和访问保护（DSEC-030 至 DSEC-032） |
| `Credentials` 凭据 | 工具经手哪些机密、怎样遮盖，以及工具连接哪些地址（DSEC-040 至 DSEC-043） |
| `Untrusted Content` 不可信内容 | 固定原文：哪些内容不可信、怎样标记（DSEC-020 至 DSEC-022） |
| `Supply Chain` 供应链 | 固定原文：锁定的依赖、带来源证明的发布、没有安装脚本、Skill 来自已安装的包（DSEC-070 至 DSEC-073） |
| `Self-update` 自更新 | 固定原文：`update` 改动什么、检查什么（DCLI-070 至 DCLI-079） |

「写入确认」「状态目录」「不可信内容」「供应链」和「自更新」描述的是每个工具都一样的运行时行为。这几节的原文和「工作方式」一样固定，由模板提供，工具禁止改动。

有两个章节看工具的情况：

- 工具有宿主组件时必须有「宿主组件」一节，没有时禁止出现。
- 工具支持设备模式时必须有「物理动作」一节，不支持时禁止出现。

其他章节必须始终存在。

为什么：人在决定是否授权之前，对每个工具都按同样的顺序读到同样的安全信息；描述共用运行时的文字只写一次，就不会与运行时对不上。
检查：🤖 `check_project` 检查章节，把五个固定章节与模板比对，并对照 `reference` 核对两个按条件出现的章节。

### DREPO-014 CONTRIBUTING 章节

`CONTRIBUTING.md` 必须有下面这些 `##` 章节，按此顺序：

<!-- contract:contributing_sections -->
| 章节 | 内容 |
|---|---|
| `Development Setup` 开发环境 | 要装什么、怎样准备工作副本 |
| `Commands` 常用命令 | 开发者构建、测试和检查工具时运行的命令 |
| `Branch and Commit Convention` 分支与提交约定 | 分支怎样命名、提交信息怎样写 |
| `CI Checks` CI 检查 | 合并门槛（DREPO-041），以及怎样在本地运行 |
| `Tests and Evidence` 测试与证据 | 一致性测试、行为矩阵、真实软件记录、评测报告，以及证据怎样决定成熟度（DVER-001、DVER-030、DVER-040） |
| `Adding or Changing a Command` 新增或修改命令 | 登记时写明效果、单位和目标类型（DCLI-029）；调查表里对应能力的处置（DREPO-060）；行为矩阵，矩阵的 `normal` 行就是写命令样例（DVER-002、DVER-030）；以及 Skill 和 CHANGELOG 条目 |
| `Pull Requests` 合并请求 | 合并请求包含什么、怎样评审 |
| `Security` 安全 | 漏洞按 `SECURITY.md` 报告，不开公开的 Issue |

为什么：贡献者不论是人还是 Agent，学一次流程就能用在每个工具上。
检查：🤖 `check_project`。

### DREPO-015 能力调查的章节

`docs/APPLICATION.md` 必须有下面这些 `##` 章节，按此顺序：

<!-- contract:application_sections -->
| 章节 | 内容 |
|---|---|
| `Application` 软件 | 名称、厂商、调查过的版本，以及软件（设备模式下是设备的驱动或 SDK）能运行的操作系统和 CPU 架构（DREPO-052） |
| `Automation Interfaces` 自动化接口 | 软件提供的每种接口，以及 CLI 能否直接调用，还是只能在软件进程里或主线程上调用（DCLI-262） |
| `Object Model` 对象模型 | 软件里的对象及其名称，它们会成为命令的名词（DCLI-023） |
| `Units and Coordinates` 单位与坐标 | 内部单位和显示单位、原点和坐标轴方向（DCLI-080 至 DCLI-084） |
| `Threading and Blocking` 线程与阻塞 | 主线程的规则、软件忙的状态，以及会挡住自动化的弹窗 |
| `Read-back` 读回 | 每类修改怎样读回、读回有多准 |
| `Save and Undo` 保存与撤销 | 保存怎样进行、未保存的修改怎样体现、有没有撤销（DCLI-058、DCLI-067） |
| `Instances and Concurrency` 实例与并发 | 同时运行多个实例、怎样区分它们，以及人同时在操作的情况 |
| `Licensing` 许可 | 许可类型、自动化需要什么许可、许可相关的弹窗 |
| `Capabilities` 能力 | 带编号和处置的能力表（DREPO-060） |
| `Risks` 风险 | 调查中发现的风险和限制；每项都写成知识条目，或写明不收的原因（DSKL-126） |

为什么：调查让人在设计命令之前，先回答关于软件的难题，而且每个评审者都熟悉这个顺序。
检查：🤖 `check_project`。

### DREPO-016 兼容性文档的章节

`docs/COMPATIBILITY.md` 必须有下面这些 `##` 章节，按此顺序：

<!-- contract:compatibility_sections -->
| 章节 | 内容 |
|---|---|
| `Support Matrix` 支持矩阵 | 软件版本（取主版本和次版本；还没有真实软件记录写明版本时，写支持的版本范围）、操作系统和模式的每种组合占一行，带兼容性标签（DREPO-022）；由脚本生成，不手写 |
| `Known Limitations` 已知限制 | 已知的缺口和故障，以及受影响的版本 |

「支持矩阵」一节必须由模板的脚本根据 `docs/evidence/` 里的真实软件记录和 `package.json` 里的平台包生成，禁止手工编辑。支持矩阵的样子如下：

| 软件版本 | 操作系统 | 模式 | 状态 |
|---|---|---|---|
| 10.0 | Windows | session | ✅ verified |
| 10.0 | Linux | session | ⏳ untested |
| 9.0 | macOS | file | ⛔ unsupported |

为什么：人在安装之前，一眼就能看出工具在哪里能用、有多大把握。由记录生成的矩阵，不会说得比记录多。
检查：🤖 `check_project` 检查章节，重新生成支持矩阵并比对。

### DREPO-017 验收文档的章节

`docs/E2E.md` 必须有下面这些 `##` 章节，按此顺序：

<!-- contract:e2e_sections -->
| 章节 | 内容 |
|---|---|
| `Authorization Rules` 授权规则 | 固定原文：谁往授权目标里加目标、目标怎么写、真实软件运行和在真实软件上评测所用的授权范围文件由谁来写（DVER-050） |
| `Authorized Targets` 授权目标 | 允许真实软件运行使用的文件、文件夹和设备，这是唯一的一份清单，每个目标一个列表项，不放其他文字（DVER-050） |
| `Procedure` 步骤 | 真实软件运行用到的电脑、操作系统、软件版本和模式，以及怎样准备并在真实软件上运行行为矩阵（DVER-030、DVER-031） |
| `Evidence Records` 证据记录 | `docs/evidence/` 里真实软件记录的索引（DVER-042） |

工具禁止改动授权规则的原文。真实软件运行的结果不在这里重复：支持矩阵由同样的记录生成，已经展示了结果（DREPO-016）。

为什么：评审者或下一位维护者需要一个地方，查到哪些目标可以用、真实软件运行怎么做、记录在哪里。授权目标的规则每个工具都一样，改动随模板到达每个工具。
检查：🤖 `check_project` 检查章节，并把授权规则与模板比对。

### DREPO-018 CHANGELOG

`CHANGELOG.md` 必须采用 Keep a Changelog 格式：

- `## [Unreleased]` 始终是第一节。
- 每个发布是一节 `## [X.Y.Z] - YYYY-MM-DD`，新的在上。
- 一节里的改动放在 `###` 小节下。只可以用下面六个小节，按此顺序；空的小节不写。
- 全文只用英文。

<!-- contract:changelog_sections -->
| 小节 | 放什么 |
|---|---|
| `Added` | 新增的命令、参数、字段或能力 |
| `Changed` | 现有行为的变化 |
| `Deprecated` | 以后的版本会删除的功能 |
| `Removed` | 本版本删除的功能 |
| `Fixed` | 缺陷修复 |
| `Security` | 漏洞修复 |

`changelog` 命令读取这个文件（DCLI-034），发布说明也从它生成（DREPO-040）。

为什么：格式统一，`changelog` 命令、发布说明和更新提示就能用同一种方式读每个工具的历史。
检查：🤖 `check_project` 检查标题和小节，并确认文件里没有中文。

### DREPO-019 不留模板残余

工具项目禁止保留模板插入的任何占位符。`check_project` 在 DREPO-010 至 DREPO-018 涉及的每份文档里查找模板占位符，同时做这些条款规定的文件、章节和双语对应检查。

为什么：留着占位符，读者就知道这份文档没人写完，徽章想建立的信任也随之落空。
检查：🤖 `check_project`。

## 徽章与标签

### DREPO-020 徽章行

README 必须在一句话定位下面用一行显示下面这些徽章，按此顺序：

<!-- contract:badges -->
| 徽章 | 显示 | 来源 |
|---|---|---|
| `agent_native` | 🤖 Agent-native，系列徽章，所有工具都一样 | 模板 |
| `ci` | 主分支上 CI 工作流的状态 | CI |
| `npm` | npm 上最新发布的版本 | npm 仓库 |
| `drives` | 驱动的软件和版本范围，如 drives KiCad 10.0 | `reference` 的驱动信息（DCLI-031） |
| `mode` | 支持的模式：file、session、device | `reference` 的驱动信息 |
| `platform` | 出包平台：Windows、macOS、Linux | `package.json` 里的平台包（DREPO-043） |
| `risk` | 工具的风险等级，T0 至 T2（DSEC-002） | `reference` |
| `maturity` | 工具各领域命令中最低的成熟度（DVER-040） | `reference` |
| `spec` | 固定的规范版本，如 tool-driver-cli-spec 1.2.0 | `.agent/SPEC_VERSION` |
| `license` | 工具的许可证 | `package.json` |

驱动 KiCad 10.0、支持文件和会话模式的工具，徽章行大致如下；具体样式由模板定义：

```markdown
![Agent-native](https://img.shields.io/badge/🤖-Agent--native-6f42c1)
[![CI](https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg)](https://github.com/<owner>/<repo>/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/<package>)](https://www.npmjs.com/package/<package>)
![drives](https://img.shields.io/badge/drives-KiCad_10.0-1f6feb)
![mode](https://img.shields.io/badge/mode-file_·_session-0e8a16)
![platform](https://img.shields.io/badge/platform-Windows_·_macOS_·_Linux-555555)
![risk](https://img.shields.io/badge/risk-T1-d29922)
![maturity](https://img.shields.io/badge/maturity-verified-8957e5)
![spec](https://img.shields.io/badge/spec-tool--driver--cli--spec_1.2.0-0969da)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)
```

为什么：每个工具都有同样一排徽章，一看就知道属于这个系列，也马上知道最先想问的事：驱动什么、在哪里能跑、风险多大、验证到了什么程度。
检查：🤖 `check_project` 根据来源重新生成徽章行并比对。

### DREPO-021 信息卡

信息卡是紧跟在标题区后面的表格。它必须按顺序包含下面这些行，每个值都必须与对应的徽章取自同一来源：

| 行 | 内容 | 来源 |
|---|---|---|
| 软件 | 驱动的软件和支持的版本范围 | `reference` 的驱动信息 |
| 模式 | 支持的每种模式 | `reference` 的驱动信息 |
| 系统和 CPU | 每个出包平台及其 CPU 架构 | `package.json` 里的平台包 |
| 风险等级 | 工具的风险等级 | `reference` |
| 成熟度 | 徽章上的等级，以及每个等级各有多少条领域命令 | `reference` |
| 规范 | 固定的规范版本 | `.agent/SPEC_VERSION` |
| 宿主组件 | 是否需要宿主组件 | `reference`：工具有 `plugin` 命令（DCLI-240） |

为什么：徽章要短；信息卡给出人在安装前需要的细节，又不另起一个可能对不上的来源。
检查：🤖 `check_project` 重新生成信息卡并比对。

### DREPO-022 兼容性标签

支持矩阵（DREPO-016）的每一行都必须带且只带下面一个标签，写成标记加标签名，如 ✅ verified：

<!-- contract:compatibility_labels -->
| 标签 | 标记 | 含义 | 需要的证据 |
|---|---|---|---|
| `verified` | ✅ | 在真实软件上测过 | 这个软件版本、操作系统和模式下当前有效的真实软件记录（DVER-042、DVER-043） |
| `untested` | ⏳ | 还没在真实软件上测过 | 无 |
| `unsupported` | ⛔ | 工具不支持这种组合 | 无 |

为什么：三个标记在每个工具里含义都一样，人读任何一张支持矩阵都不用看图例。
检查：🤖 `check_project` 检查每个标签及其证据。

### DREPO-023 不虚标

徽章、信息卡和兼容性标签禁止标得比数据显示的更好：

- 徽章行、信息卡和支持矩阵必须从 DREPO-016、DREPO-020 和 DREPO-021 写明的来源生成，禁止手写。
- 成熟度徽章必须等于从 `reference` 得出的等级。
- ✅ verified 必须有当前有效的真实软件记录作依据（DVER-043）。只有模拟宿主结果支撑的行，标 ⏳ untested（DVER-021）。
- 只有交付过的平台才可以写成支持：`package.json` 为它列出了平台包，而且发布工作流在这个包通过平台交付之后才发布了它（DREPO-042、DREPO-043）。这一点处处适用：徽章行、信息卡、`docs/COMPATIBILITY.md` 和发布说明都一样。

为什么：没运行不等于通过，模拟不等于真实；标签说得比证据多，误导的正是相信它的人。
检查：🤖 `check_project` 重新生成徽章、信息卡和支持矩阵，并用真实软件记录核对每个 ✅。

### DREPO-024 不自创

工具只可以使用 DREPO-020 的徽章、DREPO-021 的信息卡行和 DREPO-022 的标签，禁止在文档里加入其他徽章或状态标签，比如「beta」或「可用于生产」这样的徽章。颜色、图标和顺序由模板统一定义，禁止改动。

为什么：徽章和标签是这一系列工具共同的标志；只有每个工具都用同样几个、含义也相同，它们才起作用。
检查：🤖 `check_project`。

## 固定副本

### DREPO-030 规范副本

`.agent/` 必须原样存放同一个发布的五份规范，每份都有英文版和中文版。`.agent/SPEC_VERSION` 必须只有一行，就是这个发布的版本号。`.agent/SPEC_SHA256` 可以存放这个发布的发布包的 SHA-256；有了它，项目的脚本只接受摘要与它一致的发布包。

为什么：在工具上工作的 Agent 不联网也能读到工具构建时依据的那套规则，一字不差。
检查：🤖 `check_project` 核对哈希（DREPO-034）。

### DREPO-031 契约副本

`contract/driver-contract.json` 必须是同一个发布的契约，原样不改。`contract/commands.json` 必须由工具的命令登记（DCLI-029）生成，必须通过 `contract/schemas/commands.schema.json` 的校验，禁止手改；命令有任何变化都重新生成。

为什么：测试和脚本把规则和命令当作数据读取；手改的副本会让数据和代码渐渐对不上。
检查：🤖 `check_project` 核对契约的哈希，重新生成 `commands.json` 比对，并用 `commands.schema.json` 校验它。

### DREPO-032 运行时副本

驱动运行时必须原样放在 `<pkg>/_driver/`。`<pkg>/_driver/MANIFEST.json` 必须记录运行时来自哪个规范发布，以及每个运行时文件的 SHA-256 哈希。工具禁止修改运行时文件，也禁止自己重新实现运行时提供的任何东西，即各份规范「运行时与工具的分工」表中列在运行时名下的内容。

为什么：这样每个工具在信封、写操作和更新上都跑同一份经过测试的代码，谁改了都会出现哈希不符。
检查：🤖 `check_project` 用 `MANIFEST.json` 核对每个文件。

### DREPO-033 许可证

`licenses/` 必须存放规范和运行时的许可证文本，与发布里附带的一致。软件和第三方组件的许可写在 `NOTICE.md`（DREPO-002）。

为什么：复制来的规范和运行时有自己的许可条款，这些条款必须跟着副本走。
检查：🤖 `check_project` 核对哈希（DREPO-034）。

### DREPO-034 只同步，不手改

- 固定副本（DREPO-030 至 DREPO-033 和 DREPO-035）禁止手改。
- 升级时，作者运行同步脚本 `sync_spec`，它从同一个规范发布整体替换所有固定副本。
- 禁止用其他方式固定规范、契约或运行时，如只复制一部分、用子模块或包依赖。

为什么：只有一种固定方式，又按哈希核对，每个工具依据哪套规则、运行哪份运行时就一清二楚，谁的本地修补也藏不进副本里。
检查：🤖 `check_project` 按 `.agent/SPEC_VERSION` 指明的发布核对每个固定文件的哈希。

### DREPO-035 脚本副本

`scripts/spec.py`、`scripts/build.py`、`scripts/pack.py`、`scripts/platforms.py` 和 `scripts/release_gate.py` 必须是同一个发布里模板的脚本，原样不改。`scripts/smoke.py` 和 `scripts/` 里的其他脚本归作者。

为什么：这些脚本在每个工具里都一样；只有 `sync_spec` 把它们和发布的其余部分一起替换，这些脚本的修正才会进到各个工具里。
检查：🤖 `check_project` 核对哈希（DREPO-034）。

### DREPO-036 共用文字

模板把每个工具都一样的文字放在 `<!-- driver:spec-<name>:start -->` 和 `<!-- driver:spec-<name>:end -->` 之间：入口 Skill 和 `CONTRIBUTING.md` 的若干整节，以及 README、`SECURITY.md` 和 `docs/E2E.md` 的部分内容，两种语言都有。项目必须把固定的那个发布里的每个区块连同标记留在它所在的章节里。`python scripts/spec.py generate` 按那个发布写入每个区块的文字，`python scripts/spec.py sync` 换版本时也会写，所以作者禁止手改。工具自己的文字写在标记外面。

为什么：各工具共用的文字随规范一起变。由脚本写入，它在每个工具里都跟着每个发布更新；靠手改，它会落后，检查却照样通过。
检查：🤖 `check_project`：模板里的每个区块都在项目里出现一次，文字与固定的发布一致。

## 版本、CI 与发布

### DREPO-040 版本与标签

- 工具版本必须遵循 SemVer。
- `package.json` 的 `version` 字段是版本号的唯一来源。`<tool> --version` 的输出、Skill 的 `metadata.version`（DSKL-002）、各平台包和 CHANGELOG 的发布标题都必须与它相同。
- 每个发布都必须在发布的提交上打带注释的标签 `vX.Y.Z`。
- 发布说明必须从 `CHANGELOG.md` 中该版本的一节生成。

为什么：版本只写在一个地方，工具、Skill、各个包和发布说明对「装的是哪个版本」就不会有分歧。
检查：🤖 `check_project` 比对各处的版本；发布工作流比对标签与 `package.json`。

### DREPO-041 合并门槛

每次改动合并前都必须通过下面的检查。发布工作流在发布前也运行同样的检查（DREPO-043）。

| 检查 | 内容 |
|---|---|
| Lint | 工具的代码风格规则 |
| 单元测试 | 工具自己的测试 |
| 一致性测试 | 两组测试（DVER-001），在每个出包平台上运行 |
| 项目检查 | `check_project`：布局、文档、标签、固定副本和能力对应 |
| 输出大小检查 | 每个信封都不超过大小上限（DCLI-008）；由通用一致性测试 R04 完成 |
| npm 包内容检查 | 每个包只含 DREPO-051 允许的内容，没有安装脚本（DSEC-072） |

合并门槛在 `.github/workflows/ci.yml` 里运行。任何一项不通过，就不能合并，也不能发布。

为什么：合并和发布过的是同一道门槛，合并时会被拒的东西就不会发到用户手里。
检查：👀 评审 `.github/workflows/ci.yml` 和 `.github/workflows/release.yml`。

### DREPO-042 平台交付

发布之前，CI 必须对 `package.json` 列出的每个平台包，在它对应的操作系统和 CPU 架构上完成下面几步：

1. 从干净的环境开始：没有装过本工具，也没有开发依赖。
2. 安装将要发布的实际的包。
3. 启动工具。
4. 运行一个有代表性的关键操作。干净环境里装不了被驱动的软件时，这个操作改为对模拟宿主运行（DVER-020），并在记录里写明。
5. 记录包摘要和结果。

只有 `package.json` 列出的每个平台包都通过了平台交付，发布工作流才会发布（DREPO-043）。没有交付过的平台，从不写成支持（DREPO-023）。

为什么：开发机上的测试证明不了发布出去的包能装、能启动；只有在干净环境里装真实的包才能证明。
检查：🤖 任何一个平台包交付失败，发布工作流都在发布前停下；👀 评审 `.github/workflows/release.yml`。

### DREPO-043 只由 CI 发布

发布必须只在发布工作流 `.github/workflows/release.yml` 里进行，由推送带注释的标签 `vX.Y.Z` 触发，标签里的版本必须等于 `package.json` 里的版本。工作流对 `package.json` 列出的每个平台包运行合并门槛（DREPO-041）和平台交付（DREPO-042）。只要有一个平台包没通过，就必须什么都不发布；全部通过后，才把所有包带来源证明发布到 npm（DSEC-071）。禁止用其他任何方式发布包，比如从自己的电脑上发布。

为什么：由 CI 从标签构建的发布能追溯到源码；从笔记本电脑上发的发布追溯不了。
检查：👀 评审发布工作流；`update` 每次安装后都会报告 `provenance_status`（DCLI-074）。

## 分发

### DREPO-050 只通过 npm 分发

工具必须只通过 npm 分发。禁止提供其他安装渠道，如其他包管理器、安装程序，或附在发布页上的程序。

为什么：只有一个渠道，每个工具的安装、更新和来源证明就走同一条路，`update`（DCLI-070）也依赖这一点。
检查：👀 评审 README、发布工作流和发布页。

### DREPO-051 原生程序与平台包

- CI 必须为每个出包平台和 CPU 架构，把工具打成独立的原生程序。用 Python 写的工具也一样。原生程序运行时不需要另外安装语言运行环境。
- 每个原生程序单独放在一个平台包里，包名为 `<package>-<os>-<cpu>`：`<os>` 取 `win32`、`darwin` 或 `linux`，`<cpu>` 取 `x64` 或 `arm64`，与 npm 选包时用的取值相同。npm 只安装与本机匹配的平台包。
- 主包除了 npm 总会带上的文件（`package.json`、README、LICENSE）和 `NOTICE.md`，必须只含启动器、`skills/`（DSKL-030）和平台包清单。每个平台包还要带上 `NOTICE.md` 和 `licenses/`（DREPO-033）。
- 命令运行时要读的所有内容，如内置知识和随附的 CHANGELOG（DCLI-034），都和原生程序一起放在平台包里。

为什么：原生程序启动快，用户电脑上也不需要 Python 或别的运行环境；主包也保持小巧。
检查：🤖 合并门槛里的 npm 包内容检查（DREPO-041）。

### DREPO-052 平台跟着软件走

- 工具需要的东西（被驱动的软件，设备模式下是设备的驱动或 SDK）能在 Windows、macOS、Linux 中哪个平台上运行，工具就应该为哪个平台出包。它们不能运行的平台，禁止出包；没有交付过的平台，禁止声称支持（DREPO-023）。
- 既不需要软件、也不需要特定平台驱动的工具，比如自己读写文件的文件模式工具，在哪里都能运行，所以应该三个平台都出包。
- 每个出包平台都必须有 x64 平台包。软件（设备模式下是设备的驱动或 SDK）在这个系统上提供 arm64 版本时，工具还应该出 arm64 平台包。
- 工具和软件始终在同一台电脑上运行。禁止只为了驱动另一台电脑上的软件而出某个平台的包。
- `docs/APPLICATION.md` 的「软件」一节记录软件（设备模式下是设备的驱动或 SDK）能运行的操作系统和 CPU 架构；`package.json` 里的平台包必须按上面的规则与它对应。

为什么：用户在哪里能运行软件，就能在哪里用上工具；也不会有哪个包号称能驱动一个在本机跑不起来的软件，或者声称支持一个没人交付过的平台。
检查：🤖 `check_project` 对照能力调查核对平台包。

### DREPO-053 package.json 是唯一来源

包名、版本号、命令名和平台包必须只来自 `package.json`。其他需要这些值的地方，如 `update`（DCLI-070）、徽章、安装块、构建脚本和 `pyproject.toml`，都必须从 `package.json` 读取，或者经检查与它一致。环境变量前缀 `<PREFIX>` 是命令名转成大写、`-` 换成 `_`：命令 `kicad-cli` 的前缀是 `KICAD_CLI`。源码包目录 `<pkg>` 是命令名把 `-` 换成 `_`：`kicad_cli`。

为什么：一个值写在两处就会走样；只有一个来源，安装命令、更新目标和徽章才能始终一致。
检查：🤖 `check_project`。

### DREPO-054 安装块

README 的「安装」一节必须按顺序给出下面几步：

1. `npm install -g <package>`：安装工具。
2. `<tool> update`：运行一次，把命令入口指向原生程序（DCLI-075）。此时已经是最新版本，`update` 只补完命令入口（DCLI-077）。
3. 从已安装的包里安装 Skill（DSKL-031）。Skill 从不从网络获取（DSEC-073）。
4. 只有带宿主组件的工具才有这一步：`<tool> plugin install`，它是两步确认的写操作，需要授权范围（DSEC-050）。

安装块的文字由模板提供；`<package>` 和 `<tool>` 取自 `package.json`。

为什么：每次经 npm 启动器调用都要先启动 Node.js；运行一次 `update` 就省掉了这笔开销，而且同一个安装块适用于每个工具。
检查：🤖 `check_project` 把「安装」一节与模板的安装块比对。

## 能力调查与接入

### DREPO-060 能力调查

设计命令之前，作者必须按 DREPO-015 的章节填写 `docs/APPLICATION.md`。在「能力」一节里，每项能力占一行。每行必须有一个编号 `CAP-nnn`（三位数字，在调查表里唯一）和一个处置：

<!-- contract:capability_dispositions -->
| 处置 | 含义 |
|---|---|
| `provided` 提供 | 工具通过一条或多条领域命令提供，或通过 `doc open`、`doc save`、`doc close` 这类标准命令提供 |
| `deferred` 延后 | 值得提供，但留到以后的版本 |
| `excluded` 排除 | 工具不会提供，比如因为不安全，或无法可靠地自动化 |
| `unknown` 未知 | 还不知道软件是否允许 |

每行还应该写明能用哪个自动化接口做到这项能力；处置不是 `provided` 时，再用一句话写明原因。

为什么：根据调查设计的命令能覆盖用户需要的事；软件做不安全的事，则是有意地、白纸黑字地排除在外。
检查：🤖 `check_project` 检查编号和处置。

### DREPO-061 命令对应到能力

已移除：这条规则已并入 DREPO-060。

### DREPO-062 接入步骤

开发新工具的多半是 AI Agent。它应该按顺序走下面八步：

| 步骤 | 做什么 |
|---|---|
| 1 调查 | 填写 `docs/APPLICATION.md`；给每项能力编号并写明处置（DREPO-060） |
| 2 选模式 | 根据调查结果选择文件、会话或设备模式，可以组合（DCLI-201） |
| 3 生成项目 | 用模板脚本 `instantiate` 生成项目；之后 `check_project` 只列出后续步骤要补的内容：占位符（DREPO-019）和还没登记的领域命令 |
| 4 登记命令 | 按命名规则（DCLI-020 至 DCLI-024）和登记字段（DCLI-029）登记领域命令，每条写明效果、单位和目标类型，并把它们提供的能力标为 `provided`（DREPO-060） |
| 5 实现适配层 | 只实现所选模式需要的适配能力（DCLI-260）；每项能力决定放在适配层还是宿主组件里（DCLI-262） |
| 6 测试 | 在模拟宿主上运行两组一致性测试（DVER-001）；为每个领域命令写行为矩阵（DVER-030），并在授权目标上用真实软件运行（DVER-031、DVER-050） |
| 7 写 Skill 和知识库 | 决定是否拆分 Skill（DSKL-020、DSKL-021）；在模板提供的固定段落上补充剧本（DSKL-009）；把调查里的操作要点、限制和故障处理写成知识条目（DSKL-126）；把命令标为 stable 之前先跑评测（DSKL-043） |
| 8 发布 | 按 DREPO-052 为该出的平台和 CPU 架构做平台包，完成平台交付（DREPO-042），带来源证明发布到 npm（DREPO-043） |

随本规范提供的开发用 Skill `build-driver-cli` 带着 Agent 走完这八步。每一步它都说明要读哪几条条款、填哪个模板、跑哪项检查。做完后，它对照本规范检查工具，列出不合规的地方。

为什么：路线固定、每步都有检查，Agent 就能很快做出合规的工具，不必自己摸索先做什么、后做什么。
检查：👀 评审；`build-driver-cli` 列出工具没有满足的每条条款。

## 检查清单

- [ ] DREPO-001 项目布局
- [ ] DREPO-002 根目录文件
- [ ] DREPO-003 根目录不放别的文件
- [ ] DREPO-004 必需的路径
- [ ] DREPO-010 双语文档
- [ ] DREPO-011 README 章节
- [ ] DREPO-012 AGENTS 章节
- [ ] DREPO-013 SECURITY 章节
- [ ] DREPO-014 CONTRIBUTING 章节
- [ ] DREPO-015 能力调查的章节
- [ ] DREPO-016 兼容性文档的章节
- [ ] DREPO-017 验收文档的章节
- [ ] DREPO-018 CHANGELOG
- [ ] DREPO-019 不留模板残余
- [ ] DREPO-020 徽章行
- [ ] DREPO-021 信息卡
- [ ] DREPO-022 兼容性标签
- [ ] DREPO-023 不虚标
- [ ] DREPO-024 不自创
- [ ] DREPO-030 规范副本
- [ ] DREPO-031 契约副本
- [ ] DREPO-032 运行时副本
- [ ] DREPO-033 许可证
- [ ] DREPO-034 只同步，不手改
- [ ] DREPO-035 脚本副本
- [ ] DREPO-036 共用文字
- [ ] DREPO-040 版本与标签
- [ ] DREPO-041 合并门槛
- [ ] DREPO-042 平台交付
- [ ] DREPO-043 只由 CI 发布
- [ ] DREPO-050 只通过 npm 分发
- [ ] DREPO-051 原生程序与平台包
- [ ] DREPO-052 平台跟着软件走
- [ ] DREPO-053 package.json 是唯一来源
- [ ] DREPO-054 安装块
- [ ] DREPO-060 能力调查
- [ ] DREPO-061 命令对应到能力
- [ ] DREPO-062 接入步骤
