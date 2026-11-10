# DRIVER-CLI-SPEC

[English](DRIVER-CLI-SPEC.md) | 中文

> 版本 1.2.0 · 条款 DCLI-001 至 DCLI-281

## 怎么读

本规范规定驱动工具在命令行上的行为：输出什么、怎样报错、命令怎么命名、怎样写入、怎样自更新，以及怎样连接它所驱动的软件。

用词的含义：

- 必须、禁止：硬性要求。违反的工具不符合本规范。
- 应该、不应该：默认做法。只有说得出理由时才偏离。
- 可以：允许，但不要求。

每条条款有固定编号（如 DCLI-202）、标题、规则正文，以及「为什么：」和「检查：」两行。🤖 表示由测试或脚本检查，👀 表示由人在评审时检查。G00 至 G10、R01 至 R05 是通用一致性测试，D01 至 D26 是驱动一致性测试。引用其他规则时只写条款编号，如 DSEC-015。

标有 `<!-- contract:NAME -->` 的表格重复机器契约 `contract/driver-contract.json` 里的值。这类表格与契约不一致时，以契约为准。

## 适用范围

本规范适用于驱动工具：让 AI Agent 操作专业桌面软件（如 CAD、EDA、仿真软件）和设备（如机床、仪器）的命令行工具。Agent 调用工具；人通过授权范围文件决定 Agent 能改什么。

内容包括：

- 输出信封、错误和退出码；
- 命令名、参数、目标键和命令登记；
- 内置命令和驱动标准命令；
- 写操作流程：确认级别、预览、确认令牌、读回、回执、操作记录、批量操作和停止命令；
- 自更新；
- 单位和坐标；
- 驱动模型：模式、会话、文档、长任务、设备和宿主组件；
- 适配接口、驱动运行时和宿主协议。

本规范不规定领域命令具体做什么（比如怎么布线），只规定它们的命名和形式。本规范不覆盖调用云端服务接口的工具，不提供多步工作流命令，也不涉及跨机器驱动软件。

## 术语

| 术语 | 含义 |
|---|---|
| Agent | 代替人调用工具的 AI |
| 驱动工具 | 让 Agent 操作桌面软件或设备的命令行工具 |
| 软件 | 工具所驱动的桌面软件 |
| 实例、会话 | 实例是软件的一个运行中的副本。会话是工具对一个实例的引用，有会话 ID |
| 文档 | 在实例里打开的文件；文件模式下是工具直接读写的文件 |
| 驱动运行时 | 共享代码，实现信封、内置命令和标准命令、写操作流程和自更新；每个工具原样复制 |
| 适配层 | 工具作者为某个软件实现适配能力（DCLI-260）的代码 |
| 宿主组件 | 装进软件、在软件里运行的那部分工具，比如插件、加载项、脚本服务 |
| 宿主协议 | CLI 与宿主组件之间的本机协议（DCLI-270 至 DCLI-281） |
| 模拟宿主 | 为一致性测试模拟软件、实例和设备的小程序 |
| 信封 | 命令写到 stdout 的那一个 JSON 对象 |
| 命令登记 | 运行时读取的命令声明：路径、效果、参数、单位等（DCLI-029） |
| 效果 | 命令做的是哪一类改动，如 `observe`、`change`、`physical`（DCLI-051） |
| 确认级别 | 写操作怎样确认：直接执行、两步确认或危险（DCLI-052） |
| 停止命令 | 停下正在进行的工作的命令：动词为 `stop` 的命令，以及 `job cancel`（DSEC-018） |
| 风险等级 | T0、T1 或 T2：命令可能造成多大损害（DSEC-001） |
| 授权范围、授权范围文件 | 人允许的效果和目标，写在授权范围文件里（DSEC-010 至 DSEC-018） |
| 目标键 | 对象身份的唯一写法（DCLI-028） |
| 预演、预览 | 带 `--dry-run` 运行，什么都不改，返回这次改动的预览；两步确认级的写操作不带 `--confirm` 运行时，返回同样的预览（DCLI-053、DCLI-054） |
| 确认令牌 | 随预览一起给出、存放在操作记录里的随机值，只能用一次，让同一个写操作执行一次（DCLI-055） |
| 确认时的复查 | 写操作执行前，运行时再做一遍的检查（DCLI-056） |
| 派发 | 把修改发给软件或设备 |
| 读回 | 派发后重新读取被改的字段，以核实修改 |
| 回执 | 写操作的报告，按五个维度分别说明结果（DCLI-061） |
| 操作记录 | 一次写操作的留存记录，ID 以 `op_` 开头（DCLI-059） |
| 结果未知 | 写操作可能生效了，也可能没有（DCLI-063） |
| 长任务 | 超出单次调用允许时长的工作；工具返回一个任务，供 Agent 查询（DCLI-065） |
| 状态目录 | 工具按用户存放操作记录、锁、缓存和适配层状态的目录（DSEC-030） |
| 不可信内容 | 来自文档、软件或工具之外的文字，一律不当成指令（DSEC-020） |
| 内置命令、标准命令、领域命令 | 运行时为每个工具实现的命令（DCLI-030）；运行时依据适配能力实现的命令（DCLI-200）；工具作者自己写的命令 |
| 一致性测试 | 运行时提供的测试套件：通用测试 G00 至 G10、R01 至 R05，驱动测试 D01 至 D26 |
| 成熟度、证据 | 命令被证明到什么程度，以及证明它的记录（DVER-040） |
| 平台包、启动器、命令入口 | 装着某个平台原生程序的 npm 包；npm 主包里的启动脚本；npm 放到 PATH 上用来启动工具的文件 |
| 来源证明 | 证明 npm 包由本工具发布工作流构建的签名声明 |

## 运行时与工具的分工

| 方面 | 驱动运行时已经提供 | 工具作者还需要做 |
|---|---|---|
| 输出与错误 | 信封、错误对象、退出码、列表分页、大小限制、状态到错误码的映射 | 领域结果数据；把软件自己的报错原文放进 `native_message`；声明哪些结果字段是不可信内容（DSEC-021） |
| 命令 | 参数解析，全局、共享和目标参数，目标键规范化，检查每一份登记 | 按 DCLI-029 规定的字段登记每个领域命令 |
| 内置命令 | `reference`、`context`、`doctor`、`changelog`、`operation`、`update`、`--version`、`--help` | 提供 npm 包名和 CHANGELOG |
| 标准命令 | `session`、`doc`、`job`、`device`、`knowledge` 和 `plugin` 命令 | 实现它们调用的适配能力（DCLI-260） |
| 写操作 | 授权范围检查、确认分级、预览与令牌、确认时的复查、操作记录与锁、把读回与预览比较、回执与 `ok` 的推导、批量操作、长任务、停止命令 | 实现 `preview`、`apply` 和 `read_back`，它们只报告事实（DCLI-260）；为每个领域写命令提供写入样例，即它的行为矩阵中的 `normal` 行（DVER-002） |
| 单位 | 解析参数里的单位，拒绝不带单位的数，把值换算成标准单位 | 为每个物理量参数和输出字段声明物理量；在适配层里换算成软件的单位 |
| 自更新 | `update`、更新提示、命令入口、Skill 同步 | 除 npm 包名外无需再做什么 |
| 适配层状态 | 状态目录下 `adapter/` 里的适配层状态存储（DCLI-260） | 把适配层在两次调用之间需要记住的东西存在那里 |
| 宿主组件 | 宿主协议的 CLI 端；已提供的宿主端库 | 用软件支持的语言编写宿主组件 |
| 测试 | 通用和驱动两组一致性测试、模拟宿主 | 每条命令的行为矩阵，以及在真实软件上的验收（DVER-030、DVER-031） |

## 输出

### DCLI-001 stdout 上只有一个信封

在默认的 `--format json` 下，命令必须向 stdout 写恰好一个 JSON 信封，后面跟一个换行，此外什么都不写。进度、警告和诊断必须写到 stderr。stderr 不是终端时，禁止写进度条、动画和横幅。`--quiet` 必须关掉 stderr 上除错误以外的全部输出。

为什么：Agent 把 stdout 当作一个 JSON 文档解析，多出一行就会解析失败，白白浪费一次调用。
检查：🤖 通用一致性测试 R04 校验测试中见到的每一个信封。

### DCLI-002 输出格式

`--format` 取以下值之一：

- `json`：默认格式，也是给 Agent 的唯一格式。
- `text`：给人看。不同版本之间可以变化；Agent 和脚本禁止解析它。
- `raw`：只限声明了它的命令，比如导出日志或文件内容。必须按字节原样输出。

`--json` 必须等同于 `--format json`。JSON 默认必须紧凑输出；`--pretty` 加缩进，给人看。命令不支持的格式必须返回 `E_USAGE`。

为什么：只有一种机器格式，Agent 读所有工具的方式都一样；会变化的文本格式只有在没人解析时才安全。
检查：🤖 运行时单元测试。

### DCLI-003 信封的键

成功信封必须有 `ok` 和 `data` 两个键，失败信封必须有 `ok` 和 `error` 两个键；`meta` 只在有内容时出现（DCLI-004），此外没有别的键。失败时禁止返回 `data`。

成功：

```json
{"ok":true,"data":{"id":"doc_1","path":"D:/proj/board.kicad_pcb","session":"kicad-1234","revision":"r7","dirty":true,"readonly":false,"disk":"in_sync"}}
```

失败：

```json
{"ok":false,"error":{"code":"E_BUSY","message":"KiCad is running a design rule check; nothing was sent.","retryable":true,"details":{"dispatch_state":"not_started","next_action":"Wait 5 s, then run the same command again.","retry_after_ms":5000}}}
```

为什么：键固定后，Agent 先看 `ok`，再到一个确定的位置读结果或错误。
检查：🤖 通用一致性测试 R04。

### DCLI-004 meta

`meta` 必须只有以下两个键，两个都没有时整个省略：

- `notices`：提示列表，每条为 `{type, severity, message}`，`severity` 取 `info` 或 `warning`。更新提示（DCLI-079）就是一例。为空时省略。
- `untrusted`：信封中存放不可信内容的 JSON 路径列表（DSEC-021）。为空时省略。

运行时自己写的文字里嵌有不可信的值时，`untrusted` 也必须列出它，比如 `message`、预览的 `action` 或 `label`，或者含有取自文档的名字的生成命令（DCLI-011）。适配层加的提示（DCLI-260）类型为 `application`，它的 `message` 列入 `untrusted`。

为什么：在每个工具里，Agent 都在同一个位置找到提示和不可信标记；文档里的文字也没法借工具写的句子混进来。
检查：🤖 通用一致性测试 R04。

### DCLI-005 命名与取值

- 所有键必须用 snake_case，`data` 和 `details` 里的键也一样。只有两类键保持原样：回执 `observed` 的键，它们是目标键或对象名（DCLI-061）；作为数据携带的 JSON Schema 的关键字，如 `reference` 里的 `output_schema`（DCLI-031）。
- ID 必须是字符串。
- 时间必须是带 `Z` 后缀的 ISO 8601 UTC 时间，放在以 `_at` 结尾的键下；不带时刻的日期必须放在以 `_on` 结尾的键下。
- 枚举值必须用小写 snake_case。
- 物理量的键名带单位后缀，如 `x_mm`、`angle_deg`，具体要求见 DCLI-081。

为什么：只有一套命名规则，Agent 不用猜键名怎么拼、时间是哪个时区、数值是什么单位。
检查：🤖 通用一致性测试 R04。

### DCLI-006 列表

返回列表的命令必须返回 `items`、`count`（本页条数）和 `has_more`。知道总数时必须另给 `total`；`has_more` 为 `true` 时必须另给 `next_offset`。没有匹配时必须返回空的 `items`，`ok` 为 `true`，禁止返回 `E_NOT_FOUND`。`--limit` 和 `--offset` 给 `items` 分页（DCLI-026），所以 `list` 命令的适配层结果里没有 `items` 列表时，运行时必须以 `E_PROTOCOL` 拒绝。

为什么：Agent 用同一种方式翻页，并能分清「没有匹配」和「目标不对」。
检查：🤖 通用一致性测试 R04 检查列表的键；行为矩阵（DVER-030）的「空结果」一行覆盖空列表的情况。

### DCLI-007 默认字段

每个列表命令必须在登记里声明默认字段（DCLI-029），按 Agent 对这类对象最常问的问题来选。`--fields a,b` 必须只返回指定的字段，`--fields all` 必须返回全部字段。

为什么：默认结果简短，省下 Agent 的上下文；需要更多时再用 `--fields` 取。
检查：👀 评审 `reference` 里的默认字段。

### DCLI-008 响应大小

用默认参数调用时，成功信封必须不超过 8000 个字符，错误信封必须不超过 2000 个字符，按整个信封计。`E_CONFIRMATION_REQUIRED` 信封带着预览，必须按成功信封的上限计，即不超过 8000 个字符（DCLI-054）。列表靠分页保持在上限之内（DCLI-006）。结果可能大到放不下的命令，比如返回文件内容或长日志的命令，必须提供 `--output <path>`：完整结果写进这个文件，响应里只给摘要和文件路径。预演的预览先按确认时的办法缩短（DCLI-054）；仍然放不下时，下一步要求一次少点几个对象。其他仍然超长的成功信封以 `E_USAGE` 失败，它的下一步只提这条命令有的办法：`--fields`，列表的 `--limit`，带 `--output` 的读命令的 `--output`，以及把长任务的整份操作记录（包括结果）写进文件的 `operation get --output`。写操作已经执行时，它说明这一点并给出操作编号，免得 Agent 再发一次。

为什么：工具返回的每个字符都占用 Agent 的上下文，固定上限让每次调用都不贵。
检查：🤖 通用一致性测试 R04 检查大小上限。

## 错误

### DCLI-010 错误对象

`error` 必须恰好有 `code`、`message`、`retryable`、`details` 四个键。

- `message` 是写给模型看的一两句话：出了什么事，改动了什么。
- 只有什么都没改变、稍后可以原样再调用时，`retryable` 才可以为 `true`。写操作要重试时，必须按它的确认级别从头开始（DCLI-052）。

为什么：Agent 根据 `code` 和 `retryable` 决定下一步，`message` 只是补充背景。
检查：🤖 通用一致性测试 R04。

### DCLI-011 派发状态与下一步

`details` 必须始终有以下两项：

- `dispatch_state`：修改请求有没有到达软件或设备。`not_started` 表示没发出，`dispatched` 表示已发出，`unknown` 表示无法确定。读命令必须始终报告 `not_started`。
- `next_action`：一句话告诉 Agent 下一步做什么。有确切命令时，必须放在反引号里。

运行时为 Agent 生成的每条命令，无论在 `next_action`、`resume`、`lookup` 还是 `poll` 里，都必须完整：写明它需要的会话、文档和每个 ID（`--session`、`--doc`、`--id`、`--job`），以后原样运行也不会出错。含有空格或以 `-` 开头的参数都必须加引号。嵌有不可信值的生成命令列入 `meta.untrusted`（DCLI-004）。

为什么：出错后 Agent 先要知道有没有发出去，再要知道接下来运行什么；依赖默认实例的命令，以后可能作用到另一个实例上。
检查：🤖 通用一致性测试 G01、R03 和 R04。

### DCLI-012 固定的 details 字段

以下错误码和情形必须在 `details` 里带对应的字段：

| 错误码或情形 | 字段 | 内容 |
|---|---|---|
| `E_USAGE` | `problem`、`parameter` | 哪里有误，是哪个参数；数值没带单位时，`problem` 列出可接受的单位（DCLI-082） |
| `E_AMBIGUOUS` | `candidates` | 匹配到的对象或实例；实例带 DCLI-202 规定的会话摘要。只有用户点名了某一个，Agent 才能选它 |
| `E_CONFLICT` | `conflict` | 哪个前置条件不满足，取值见契约的 `conflict_values`（DCLI-056、DCLI-201、DCLI-206、DCLI-211、DCLI-212）。软件内部的冲突，比如已经打开了另一个同名模型、模型正被别的模型使用，取 `application_state`，由 `message` 说明是什么挡住了它；它用于读操作和没有自己预览的能力，比如 `open_doc`、`close_doc`，或者派发时状态已经变了。领域命令的预览把这类冲突报告为 `can_apply: false` 加 `blocked_by`（DCLI-054） |
| `E_FORBIDDEN` | `scope` | `{missing, snippet}`：`missing` 为 `{effects, targets, dangerous}`，即授权范围缺什么；`snippet` 是人可以直接粘进授权范围文件的片段（DSEC-015） |
| `E_OUTCOME_UNKNOWN` | `lookup` | 查清结果的确切命令（DCLI-063） |
| `E_BUSY` | `retry_after_ms` | 等多久再试 |
| `E_HUMAN_REQUIRED` | `action`、`resume` | 人要做什么，以及做完后运行的命令（DCLI-066） |
| `E_CONFIRMATION_REQUIRED` | `preview`、`confirm_token`、`expires_at` | 预览和用来确认它的令牌（DCLI-053） |
| `E_PARTIAL` | `receipt`、`batch` 或 `update` | 单个写操作的回执（DCLI-062）、批量操作的结果（DCLI-064），或 Skill 同步失败时 `update` 的结果（DCLI-078） |
| `E_VERIFY_FAILED`、`E_VERIFY_INCOMPLETE` | `receipt` 或 `batch` | 单个写操作的回执，或批量操作的结果 |
| 其他回执推导出 `ok: false` 的写操作 | `receipt` | 这次写操作的回执（DCLI-062） |
| 其他没有全部成功的批量操作 | `batch` | 这批操作的结果 `{items, summary}`（DCLI-064） |
| 软件报了错 | `native_message` | 软件自己的报错原文，列入 `meta.untrusted` |
| 有知识条目说明怎么处理 | `knowledge` | 相关知识条目的 ID（DSKL-124） |

为什么：字段固定，Agent 不用解析 `message` 就能处理错误。
检查：🤖 通用一致性测试 R03 检查用法错误带 `problem`；R04 按契约检查每个信封。

### DCLI-013 错误码

工具必须只用本表中的错误码，禁止自创。需要新错误码时，先加进契约，再加进本表。每个错误码的退出码和 `retryable` 都是固定的。

<!-- contract:error_codes -->
| 错误码 | 退出码 | 可重试 | 含义 |
|---|---|---|---|
| `E_USAGE` | 2 | 否 | 命令行有误：未知命令或参数、缺参数、参数值无效 |
| `E_VALIDATION` | 2 | 否 | 格式正确，但内容被软件或工具拒绝，如坐标超出板框 |
| `E_AMBIGUOUS` | 2 | 否 | 一个名字匹配到多个对象或实例；从 `candidates` 中选一个 |
| `E_UNSUPPORTED` | 2 | 否 | 请求有效，但当前软件版本、模式或适配层做不到 |
| `E_NOT_FOUND` | 3 | 否 | 对象、文档、会话、长任务或操作不存在 |
| `E_AUTH` | 4 | 否 | 联网设备或服务要求的凭证缺失、无效或过期 |
| `E_FORBIDDEN` | 4 | 否 | 授权范围没有授予这次操作；`scope` 说明缺什么 |
| `E_CONFIG` | 4 | 否 | 本地配置缺失或无效 |
| `E_BACKEND_UNAVAILABLE` | 4 | 否 | 软件没装、没运行、没有许可或连不上；什么都没派发 |
| `E_CONFIRMATION_REQUIRED` | 5 | 否 | 什么都没改；核对预览后，带 `--confirm <token>` 再运行同一条命令，预览的级别为 `dangerous` 时还要加 `--dangerous`（DCLI-053） |
| `E_CONFLICT` | 6 | 否 | 前置条件不满足；`conflict` 说明是哪一项（DCLI-056） |
| `E_NETWORK` | 7 | 是 | 网络不通（自更新、联网设备）；什么都没发出 |
| `E_BUSY` | 7 | 是 | 软件主线程、实例或设备忙，或没有回应；什么都没派发；等 `retry_after_ms` 后再试 |
| `E_TIMEOUT` | 7 | 是 | 读操作超时；什么都没改 |
| `E_OUTCOME_UNKNOWN` | 8 | 否 | 修改可能已经生效；先运行 `lookup`，再做别的，绝不重发写操作（DCLI-063） |
| `E_HUMAN_REQUIRED` | 9 | 否 | 需要人先处理（`action`），处理完 Agent 运行 `resume`（DCLI-066） |
| `E_PARTIAL` | 1 | 否 | 修改部分生效；看 `receipt`、`batch` 或 `update`，只重发失败的部分 |
| `E_VERIFY_FAILED` | 1 | 否 | 修改已生效，但读回与预览不符 |
| `E_VERIFY_INCOMPLETE` | 1 | 否 | 修改已生效，但没能读回核实 |
| `E_NATIVE_FAILURE` | 1 | 否 | 软件拒绝或回滚了这次操作，或接收方拒收了送达的内容；没有留下持久改动 |
| `E_PROTOCOL` | 1 | 否 | CLI 与宿主组件的协议版本不匹配，或回复无法解析 |
| `E_INTEGRITY` | 1 | 否 | 更新包的完整性或来源核对失败；不要重试 |
| `E_IO` | 1 | 否 | 本地文件或状态目录出错；什么都没派发 |
| `E_INTERRUPTED` | 130 | 是 | 在产生任何持久影响之前被中断 |
| `E_UNKNOWN` | 1 | 否 | 内部错误；看 `message` 和 `details` |

为什么：错误码集合固定，一个 Agent 就能用同一套办法处理所有工具的错误。
检查：🤖 通用一致性测试 R03 检查 `E_USAGE`；R04 按契约检查每个错误码。

### DCLI-014 退出码

退出码必须由 DCLI-013 规定的错误码决定。成功时退出码必须为 0。

<!-- contract:exit_codes -->
| 退出码 | 含义 | Agent 的下一步 |
|---|---|---|
| `0` | 成功 | 继续 |
| `1` | 部分生效、核实失败或未完成、软件报错、协议、完整性、本地 IO、内部错误 | 读 `error.details` |
| `2` | 用法、校验、歧义、不支持 | 修正请求，不要原样重试 |
| `3` | 找不到 | 找到正确的目标 |
| `4` | 凭证、授权范围、配置、软件不可用 | 修环境或授权范围，或询问用户 |
| `5` | 需要确认 | 核对预览后，带 `--confirm <token>` 再运行同一条命令；预览的级别为 `dangerous` 时还要加 `--dangerous` |
| `6` | 冲突 | 重新读取状态，再作决定 |
| `7` | 网络不通，软件或设备忙或没有回应，超时 | 等一会儿再试 |
| `8` | 结果未知 | 先运行 `lookup`；绝不重发写操作 |
| `9` | 需要人处理 | 把 `action` 转告用户，人处理后运行 `resume` |
| `130` | 被中断 | 检查状态后再试 |

为什么：只看退出码的脚本或 Agent 也知道该走哪一类下一步；退出码 8 只有一个意思：「先查清，绝不重发」。
检查：🤖 通用一致性测试 G01、G06 和 G09 检查退出码 5；驱动一致性测试 D06 检查退出码 8；R04 按契约检查其他退出码。

## 命令形式

### DCLI-020 先名词后动词

领域命令必须是「名词 动词」的形式，如 `footprint move`。命令树应该扁平：用 `footprint move`，不用 `board footprint move`。

为什么：形式固定，Agent 从对象和动作就能猜出命令。
检查：🤖 通用一致性测试 R02。

### DCLI-021 路径与别名

一个命令路径必须要么是命令组，要么是命令，不能两者都是。别名必须在同级之间唯一；别名不能用哪些名字，见 DCLI-024。

为什么：一个路径如果能有两种意思，同样的话就会执行不同的命令。
检查：🤖 通用一致性测试 R02。

### DCLI-022 动词

领域命令必须从本表中选动词，禁止使用 `show`、`inspect`、`info`、`status` 这类同义词。内置命令和标准命令的名字和效果以 DCLI-030 和 DCLI-200 为准。动词为 `stop` 的命令必须登记为停止命令（DSEC-018）。

<!-- contract:verbs -->
| 动词 | 效果 | 含义 |
|---|---|---|
| `list` | `observe` | 返回一页对象；没有匹配时返回空列表，不报 `E_NOT_FOUND` |
| `get` | `observe` | 返回一个对象；不存在时报 `E_NOT_FOUND`，一个名字匹配到多个时报 `E_AMBIGUOUS` |
| `search` | `observe` | 按 `--query` 查找，返回一页结果 |
| `check` | `observe` | 检查规则或一致性（如设计规则检查），不做修改 |
| `create` | `change` | 新建对象，结果里带新对象的 `id` |
| `update` | `change` | 只修改给出的属性，没给的不动；一个属性都没给时返回 `E_USAGE` |
| `delete` | `change` | 删除；目标不存在时在预览阶段就报 `E_NOT_FOUND` |
| `move` | `change` | 移动、改变位置 |
| `import` | `change` | 把外部文件导入当前文档 |
| `render` | `artifact` | 生成图片、PDF 或其他预览 |
| `export` | `artifact` | 导出为其他格式的文件 |
| `run` | — | 执行一个动作，可长可短，效果由命令自行声明，比如一次分析；只读取、返回计算结果的计算（比如算出一份布线方案）也用它，声明为 `observe`；超过 60 秒的工作做成长任务（DCLI-065） |
| `stop` | `lifecycle` | 停下正在进行的工作，如机床的加工循环或正在运行的分析（DSEC-018） |

为什么：动词表短而固定，Agent 不用纠结该调 `show`、`info` 还是 `get`。
检查：🤖 通用一致性测试 R02。

### DCLI-023 名词

名词必须是完整的英文单词，或用连字符连起来的几个完整单词，如 `forward-annotation`、`cross-section`。唯一的例外是行业或软件里通用的缩写，如 `pcb`、`bom`，可以使用；禁止使用 `sch`、`fab`、`kb` 这类自造缩写。名词应该用软件自己的叫法，即界面和手册里的词，因为用户和 Agent 说的就是这些词。

为什么：Agent 要把用户的话对应到命令上，自造缩写会让这种对应失效。
检查：👀 评审命令树。

### DCLI-024 保留名

以下名字留给内置命令和标准命令：`reference`、`context`、`doctor`、`changelog`、`update`、`operation`、`help`、`version`、`session`、`doc`、`job`、`device`、`plugin`、`knowledge`。领域命令禁止用它们作名词，别名也禁止与它们相同。

为什么：这样每个工具的内置命令和标准命令都在同样的名字下。
检查：🤖 通用一致性测试 R02。

### DCLI-025 全局参数

全局参数只有本表这些，在每个工具里含义相同。工具禁止新增全局参数；命令参数禁止与全局参数重名。

<!-- contract:global_flags -->
| 参数 | 取值 | 含义 |
|---|---|---|
| `--format` | `json\|text\|raw` | 输出格式（DCLI-002） |
| `--json` | — | 等于 `--format json` |
| `--pretty` | — | 缩进 JSON |
| `--fields` | `<a,b\|all>` | 选择返回的字段（DCLI-007） |
| `--quiet` | — | 关掉 stderr 上的非错误输出 |
| `--dry-run` | — | 只预演，不修改（DCLI-054） |
| `--confirm` | `<token>` | 用 `E_CONFIRMATION_REQUIRED` 或预演给出的令牌执行写操作（DCLI-053、DCLI-055） |
| `--dangerous` | — | 危险级写操作确认的那次调用必须带（DCLI-053） |

为什么：Agent 学一次，就能在所有工具里用。
检查：🤖 运行时单元测试（参数解析）。

### DCLI-026 共享参数

命令提供以下功能时，必须用这里的参数名和含义。

<!-- contract:shared_flags -->
| 参数 | 取值 | 含义 |
|---|---|---|
| `--limit` | `<n>` | 每页条数：默认 20，最多 100，所有列表都一样，领域命令的列表也是；运行时给每个 `list` 命令加上 `--limit` 和 `--offset`，并掌握这两个值。超过最大值时返回 `E_USAGE`，不悄悄调低 |
| `--offset` | `<n>` | 本页的起始位置 |
| `--query` | `<text>` | `search` 要找的内容 |
| `--sort` | `<field>` | 列表按哪个字段排序 |
| `--order` | `asc\|desc` | 排序方向 |
| `--state` | `<state>` | 按状态过滤；`all` 表示不过滤 |
| `--ids` | `<a,b,c>` | 一次作用于多个对象，用同样的值（DCLI-064） |
| `--continue-on-error` | `true\|false` | 批量操作中某个对象失败后是否继续（DCLI-064） |
| `--wait` | `<duration>` | 等到状态变化或时长用完再返回（DCLI-220） |
| `--output` | `<path>` | 把结果或完整的预览写到本地文件，响应里只给摘要（DCLI-008、DCLI-054） |
| `--input` | `<path\|->` | 从文件或 stdin 读入全部参数，格式为一个 JSON 对象；`{"items": [...]}` 为批量中的每个对象各给一组值（DCLI-064） |

时长属于 `time` 物理量：必须写作 `<n>ms`、`<n>s`、`<n>min` 或 `<n>h`，不带单位的数按 DCLI-082 拒绝。其他物理量参数见 DCLI-082。

为什么：同一功能在每个工具里都用同一个参数，Agent 不用再查。
检查：🤖 驱动一致性测试 D13 检查时长；👀 评审 `reference` 里每个命令的参数。

### DCLI-027 目标参数

作用于会话、文档、设备和长任务的命令，必须用以下命令级参数选择对象，所有工具用同样的名字，如 `job get --job 7`。

<!-- contract:target_flags -->
| 参数 | 对象 | 含义 |
|---|---|---|
| `--session` `<id>` | 会话 | 选择实例（DCLI-202） |
| `--doc` `<path-or-id>` | 文档 | 选择文档；文件模式下就是文件路径（DCLI-201） |
| `--device` `<id>` | 设备 | 选择设备 |
| `--job` `<id>` | 长任务 | 选择长任务 |

命令作用的其他对象，必须用 `--id <id>` 选择一个，或用 `--ids <a,b,c>` 选择多个（DCLI-064），输出里必须用键 `id` 指称它。软件自己给对象起的名字，如位号 `R1`，就是 ID 的值：`footprint move --id R1`。操作记录和知识条目也用 `--id` 选择。

为什么：同一类对象在每个工具里都用同一种方式指定。
检查：👀 评审 `reference`。

### DCLI-028 目标键

目标键是对象身份的唯一写法。授权范围文件、预览、回执和操作记录必须用它。目标键由人能事先写下的东西构成，比如文件路径或设备 ID，因为写授权范围文件时还没有会话 ID。

<!-- contract:target_keys -->
| 类型 | 格式 | 例子 |
|---|---|---|
| `file`：整个文件 | `file:///<absolute-path>` | `file:///D:/proj/board.kicad_pcb` |
| `file_object`：文件里的对象 | `file:///<absolute-path>#<domain-path>` | `file:///D:/proj/board.kicad_pcb#/footprints/R1` |
| `untitled_document`：未保存的新文档 | `untitled:<session-id>/<n>` | `untitled:kicad-1234/1` |
| `untitled_object`：新文档里的对象 | `untitled:<session-id>/<n>#<domain-path>` | `untitled:kicad-1234/1#/footprints/R1` |
| `device_part`：设备的部件 | `device:<device-id>/<part>` | `device:cnc-01/axis-x` |

匹配必须按前缀进行：授权范围文件里写 `file:///D:/proj/`，就覆盖这个文件夹下所有文件里的所有对象；写 `untitled:`，就覆盖所有新文档。比较之前，运行时必须先规范化路径：绝对路径、统一分隔符，Windows 上不区分大小写。前缀必须只在边界上匹配，即满足以下任一条件：目标键与前缀相同；前缀以 `/`、`#` 或 `:` 结尾；目标键在前缀之后紧接着 `/` 或 `#`。所以 `file:///D:/proj` 覆盖 `file:///D:/proj/board.kicad_pcb`，不覆盖 `file:///D:/proj2/board.kicad_pcb`。目标锁使用同样的边界规则（DCLI-060）。

为什么：工作开始前写好的授权范围，必须能匹配 Agent 之后碰到的对象。
检查：🤖 运行时单元测试（规范化与前缀匹配）。

### DCLI-029 命令登记

每个命令都必须先登记才能运行。内置命令和标准命令由运行时登记；每个领域命令由工具作者登记，软件要花一分钟以上的标准写命令由工具作者列进 `long_commands`（DCLI-065），软件有几种文档时，这些种类由工具作者列进 `doc_kinds`（DCLI-210）。登记包括以下字段：

<!-- contract:registration_fields -->
| 字段 | 内容 |
|---|---|
| `path` | 命令路径，如 `footprint move`（DCLI-020） |
| `summary` | 一句话说明命令做什么 |
| `effects` | 命令的效果（DCLI-051） |
| `modes` | 命令支持的模式（DCLI-201） |
| `target_kind` | 命令作用的对象类型，也就是目标键里的类型（DCLI-028） |
| `flags` | 每个参数，各带类型（`string`、`integer`、`number`、`boolean`、`quantity`、`path` 或 `json`；DCLI-026 的共享参数还有 `list`、`input` 和 `duration`），`json` 类型参数的 `schema`，`quantity` 类型参数所属的物理量（DCLI-080），携带机密时的 `secret: true`（DSEC-040），以及指明命令所创建的文件或文件夹时的 `output: true`，此时类型为 `path`（DCLI-051） |
| `untrusted_fields` | 存放不可信文字的输出字段（DSEC-021） |
| `irreversible` | 软件无法撤回这项修改时为 `true`，命令因此成为危险级（DCLI-051） |
| `halt` | 停止命令为 `true`（DSEC-018） |
| `tolerances` | 按字段给出读回值与预览之差在多大范围内仍算一致（DCLI-057） |
| `default_fields` | 列表默认返回的字段（DCLI-007） |
| `related_knowledge` | 相关知识条目的 ID（DSKL-124） |

登记还可以包括以下字段：

| 字段 | 内容 |
|---|---|
| `tier_when` | 让一次调用的确认更严的、这个命令自己的条件，每项为 `{when, tier, description}`；适配层在预览里写明哪些条件对这次调用成立（DCLI-052） |
| `doc_kinds` | 软件有几种文档时，命令适用的文档种类，如 `part`、`assembly`、`drawing`（DCLI-210） |
| `verify_after_save` | 会话模式下，软件要等文档保存之后才能正确显示这项修改时为 `true`，由 `doc save` 核实（DCLI-057） |
| `long` | 工作可能超出单次调用允许的时长时为 `true`，运行时把它作为长任务运行（DCLI-065） |
| `jobs` | 软件可能把这个写操作作为长任务运行时为 `true`：`apply` 报告任务，运行时用 `job_status` 跟进（DCLI-065）。一条命令要么是 `jobs`，要么是 `long`，不能两者都是 |

领域命令要调用哪些适配能力，由登记推出：读命令是 `read`；写命令是 `preview`、`apply` 和 `read_back`，带 `jobs` 的再加 `job_status` 和 `cancel`（DCLI-260）。

`json` 类型的参数接收结构化的值，比如焊盘表：在命令行上写成内联 JSON，或写成存放它的文件路径；在 `--input` 的条目里直接写值本身。它的 `schema` 是类型为 `object` 或 `array` 的 JSON Schema，`reference` 会显示出来。值里的物理量数字必须在键名里带上单位，用标准单位的后缀，如 `width_mm`（DCLI-081）。运行时检查最外层的类型，其余由适配层检查，不对时返回 `E_VALIDATION`。

运行时必须拒绝违反这些规则的登记，比如没有声明所属物理量的物理量参数（DCLI-080）；确认级别必须由运行时根据登记推导，禁止由作者指定（DCLI-052）。`reference` 由登记生成（DCLI-031）。工具项目里的 `contract/commands.json` 必须由登记生成，并由 `contract/schemas/commands.schema.json` 校验。

为什么：每个命令只有一份声明，`reference`、确认级别、授权范围检查、机密遮盖和读回都从它来，彼此就不会对不上。
检查：🤖 `check_project` 用 `commands.schema.json` 校验 `contract/commands.json`；运行时单元测试检查运行时会拒绝违反规则的登记。

## 内置命令

### DCLI-030 内置命令

驱动运行时为每个工具实现以下命令，工具作者禁止自己写。它们必须都按 `observe` 效果声明：工具自己的状态（操作记录、锁、更新缓存）和 `update` 都不算效果。`operation resolve` 仍然需要授权范围（DCLI-035）。

<!-- contract:builtin_commands -->
| 命令 | 参数 | 返回什么 |
|---|---|---|
| `reference` | `--command <path>`、`--all --output <path>` | 索引、单条命令，或写进文件的全部命令（DCLI-031） |
| `context` | — | 本地状态，不联网（DCLI-032） |
| `doctor` | — | 健康检查（DCLI-033） |
| `changelog` | `--since <version>` | 来自内置 CHANGELOG 的发布说明（DCLI-034） |
| `operation get` | `--id`、`--output` | 一条操作记录（DCLI-035） |
| `operation list` | `--state`、`--batch`、`--limit`、`--offset` | 操作记录，按时间倒序（DCLI-035） |
| `operation resolve` | `--id`、`--outcome`、`--evidence` | 读回目标，查清结果未知的写操作；或登记 Agent 查到的结果（DCLI-035） |
| `update` | `--check`、`--target-version <v>`、`--allow-downgrade` | 自更新（DCLI-070） |

为什么：每个工具都用同样的命令回答同样的问题，作者也不用重复造这些命令。
检查：🤖 通用一致性测试 R01 和 R05。

### DCLI-031 reference

`reference` 必须根据登记（DCLI-029）说明每条命令：路径、摘要、参数、输出 Schema、示例、效果、确认级别、成熟度、适用模式，以及相关的知识条目（DSKL-124）。它还必须返回 `driver` 块：支持的模式、所驱动的软件及其支持的版本范围。命令所需的适配能力和成熟度背后的证据，只写在 `reference --all` 生成的文件里，供项目脚本检查（DVER-041）。

- `tier` 必须按命令支持的每种模式分别给出确认级别。取决于具体调用的级别，必须在 `tier_when` 里说明，比如 `doc close --discard`、关闭不是本工具启动的实例、另存为或导出时覆盖已有文件，以及命令自己登记的条件及其 `description`（DCLI-029）。
- 命令登记了 `doc_kinds`、`long` 或 `verify_after_save` 时（登记了 `jobs` 的命令也显示 `long`，因为它同样返回任务），`reference` 必须显示出来，让 Agent 在调用之前就知道它接受哪些文档、会返回长任务、核实要等到保存。
- 表示坐标的每个参数和输出字段，都必须写明它的 `coordinate_system`（DCLI-084）。
- 必填参数能由 `--input` 给出（`--id` 还能由 `--ids` 给出，DCLI-064）时，必须用 `required_without` 列出这些参数，不写 `required`，免得 Agent 把它和这些参数一起传。
- 不带参数的 `reference` 必须只返回索引，不超过 8000 个字符：每条命令只有路径和一句话摘要。限制和条件写在这条命令自己的说明里。
- `reference --command <path>` 必须只返回一条命令，不超过 6000 个字符。
- `reference --all --output <path>` 必须把全部命令的说明写进这个文件，响应里只给摘要（DCLI-008）。

为什么：Agent 用一次简短的调用就知道工具能做什么，再用一次调用就知道一条命令的完整用法，包括它在每种模式下的确认级别，不用猜。
检查：🤖 通用一致性测试 R01 和 R02。

### DCLI-032 context

`context` 必须不联网，只读本地状态。它返回以下键：

<!-- contract:context_keys -->
| 键 | 内容 |
|---|---|
| `tool` | 工具名 |
| `version` | 工具版本 |
| `spec_version` | 工具遵循的本规范版本 |
| `state_dir` | 状态目录：路径、是否可用、实际的访问保护方式（DSEC-032） |
| `authorization` | 授权范围的状态（DSEC-016）、ID、文件路径、允许的效果、是否允许危险操作、到期时间 |
| `application` | 软件的安装位置、版本和许可状态 |
| `sessions` | 运行中实例的概况（DCLI-202） |
| `components` | CLI 版本、磁盘上的宿主组件版本、各实例已加载的宿主组件版本 |
| `knowledge` | 内置知识的条目数，以及团队知识的登记列表：`id`、`title`、`kind`、`url`（DSKL-125） |

为什么：动手之前，Agent 用一次不联网的调用就能看清自己所处的环境。
检查：🤖 通用一致性测试 R05。

### DCLI-033 doctor

`doctor` 必须返回 `checks` 列表，每项为 `{check, status, message, fix}`，`status` 取 `pass`、`warn` 或 `fail`。它必须做以下检查：

<!-- contract:doctor_checks -->
| 检查项 | 检查什么 |
|---|---|
| `state_store` | 状态目录可用且受保护（DSEC-032） |
| `authorization` | 授权范围文件的状态（DSEC-016） |
| `version` | CLI 与已安装的 Skill 版本一致（DSKL-031） |
| `update` | 有没有新版本（DCLI-079） |
| `application` | 软件的安装位置和版本 |
| `license` | 软件的许可状态 |
| `plugin` | 磁盘上的宿主组件版本与各实例已加载的版本一致（DSEC-053） |
| `sessions` | 运行中的实例及其状态 |
| `knowledge` | 条目格式和登记文件；登记了团队知识时，PATH 上有没有 lark-cli（DSKL-125） |

为什么：一条命令就能找出常见的环境问题，并说明每个问题怎么修。
检查：🤖 通用一致性测试 R05 检查固定检查项是否齐全。

### DCLI-034 changelog

`changelog` 必须读取工具自带的 CHANGELOG，返回 `current_version` 和 `entries`。每条为 `{version, date, changes}`，`changes` 必须按 `added`、`changed`、`deprecated`、`removed`、`fixed`、`security` 分组。`changelog --since <version>` 必须只返回比该版本新的条目。

为什么：更新之后，Agent 不联网也能看到改了什么。
检查：🤖 运行时单元测试。

### DCLI-035 操作记录命令

- `operation get --id <id> [--output <path>]` 必须返回一条操作记录。带 `--output` 时，把整条记录连同写操作生成的每个文件写进那个文件（DCLI-061）。
- `operation list [--state <state>] [--batch <id>] [--limit <n>] [--offset <n>]` 必须按时间倒序列出操作记录。`--batch` 取一个批量操作的操作 ID，只列出它的各项（DCLI-064）。
- `operation resolve --id <id>` 查清结果未知的写操作（DCLI-063）。不带 `--outcome` 时，运行时必须经适配层读回目标，与预览比较，把查到的结果连同 `resolved_by: read_back` 和读回的值一起登记并返回。不改字段的标准写命令按它改动的东西读回：`session open` 和实例里的 `doc create` 与发出前的实例和文档对比，`session close`、`doc open` 和 `doc close` 查现在的实例和文档，`doc save` 看文档是否已无未保存的修改以及它的修订号，`doc save-as` 和文件模式下的 `doc create` 查以这个文件名或以它开头的名字写出的文件（DCLI-057），`job cancel` 看任务状态，`plugin install` 和 `plugin remove` 看磁盘上的版本。实例正在启动、正忙或被弹窗挡住时，先如实报告，并把这条命令作为人处理完后要再运行的命令（DCLI-204）。改动的东西读不到，或者看不出结果时，比如新实例还没列出来，或者文档有未保存的修改、而它在保存发出后又被改过，运行时禁止猜测结果，必须如实说明，这时允许使用 `--outcome`。
- `operation resolve --id <id> --outcome applied|not_applied|partial --evidence <text>` 登记 Agent 用其他办法查到的结果，记为 `resolved_by: agent`。只有目标无法读回时才允许这样做；否则必须以 `E_VALIDATION` 拒绝。
- 当前生效的授权范围没有覆盖该操作的目标和效果时，查清操作必须以 `E_FORBIDDEN` 拒绝，`details.scope` 说明缺什么（DSEC-017）。

为什么：Agent 随时能回看自己做过什么，并按软件里的实际情况、而不是靠猜，把结果未知的写操作查清结案。
检查：🤖 通用一致性测试 R05 在空状态下运行 `operation list`；驱动一致性测试 D06 登记一次结果未知的写操作。

### DCLI-036 版本与帮助

每个工具必须支持 `--version`（报告工具版本）和 `--help`（显示用法）。

为什么：`update` 要靠 `<tool> --version` 确认实际装上的版本（DCLI-074）。
检查：🤖 运行时单元测试。

## 写操作

### DCLI-050 写操作流程

每个写操作都必须按以下顺序进行，全程保留操作记录（DCLI-059）：

1. 检查授权范围（DSEC-014、DSEC-015）。
2. 生成预览（DCLI-054）。两步确认级或危险级的写操作没带 `--confirm` 时，到这一步为止，返回 `E_CONFIRMATION_REQUIRED`（DCLI-053）。
3. 派发前复查状态（DCLI-056）。
4. 锁住目标并派发修改（DCLI-057、DCLI-060）。
5. 读回核实（DCLI-057）。
6. 返回回执（DCLI-061）。

每一级都必须检查授权范围、生成预览、保留操作记录、读回核实并返回回执。直接执行只省掉确认这一步，安全检查一样不少。停止命令同样走这些步骤，例外见 DSEC-018。

因此 Agent 的写入配方对所有写操作都一样：Agent 运行命令时不带 `--dry-run`，也不带 `--confirm`。直接执行级的写操作就此执行。两步确认级和危险级的写操作返回 `E_CONFIRMATION_REQUIRED`；Agent 按 STOP 检查点（DSKL-008）核对预览，再带 `--confirm <token>` 把同一条命令运行一次；`preview.tier` 为 `dangerous` 时，同时加上 `--dangerous`。`--dry-run` 只用于只看不改（DCLI-054）。

为什么：流程固定，每个工具的每次写操作都同样谨慎，报告也同样清楚；配方只有一个，Agent 写入前不用先去查怎么写。
检查：🤖 通用一致性测试 G01 和 G07；驱动一致性测试 D16。

### DCLI-051 效果

每个命令都必须声明效果。效果决定默认的确认级别和最低风险等级：

<!-- contract:effects -->
| 效果 | 含义 | 确认级别 | 风险等级 |
|---|---|---|---|
| `observe` | 只读 | 不需要 | `T0` |
| `lifecycle` | 生命周期操作：启动、打开、新建、关闭或停止某个东西，不改内容，如启动实例、新建或打开文档、停止长任务 | 直接执行 | `T1` |
| `change` | 修改软件里或文件里的对象 | 会话模式直接执行；文件模式和设备模式两步确认 | `T1` |
| `artifact` | 新建本地文件，如导出、另存为 | 直接执行；目标文件已存在时与 `persist` 一样两步确认 | `T1` |
| `persist` | 覆盖已有的本地文件，如保存 | 两步确认 | `T1` |
| `configure` | 修改设置、权限或宿主组件 | 两步确认 | `T2` |
| `deliver` | 把内容交给外部的人或系统，如提交到 PLM 系统、发邮件 | 危险 | `T2` |
| `physical` | 物理动作，如移动机床的轴、通电 | 危险 | `T2` |

- 声明了多个效果的命令，必须取其中最严的确认级别。
- 既修改文档、又新建文件的命令，比如往工程里加一块板，在其他效果之外再声明 `artifact`。只在部分调用里新建文件的命令也这样声明，比如只在库还不存在时才新建库文件、再在里面定义符号的命令。
- `output` 参数可以指明一个文件夹，命令把文件写进这个文件夹，比如一套生产文件。按 `artifact` 定确认级别时，文件夹里有文件或子文件夹才算已存在；执行时在 `files` 里逐个报告写出的文件（DCLI-260）。
- 文件名由软件自己定的 `artifact` 命令没有 `output` 参数；它的预览必须把这个文件或文件所在的文档列为目标，授权范围才能覆盖它，执行时在 `files` 里报告这个文件（DCLI-260）。
- `output` 参数指定的文件已存在时，运行时自己能发现。`artifact` 命令要覆盖的文件不由 `output` 参数指定时，比如用 `--doc` 指定的新文档，文件已存在时，预览必须在 `tier_raises` 里写明 `target_exists`（DCLI-052）；运行时为每个 `artifact` 命令都登记了这个条件。
- 软件会把修改立即写进文件的命令，会话模式下也一样，比如直接写进工程数据库的规则，在 `change` 之外再声明 `persist`（DCLI-058）。
- 不论效果是什么，以下操作都必须是危险级：不可撤回的修改、放弃未保存的修改、关闭不是本工具启动的实例。软件和本工具都没有命令能恢复原状的修改，就是不可撤回的：没有撤销功能的删除不可撤回，文件模式下也一样；移动和改字段可以用另一次调用改回去，不算不可撤回。这样的命令必须在登记里声明 `irreversible: true`（DCLI-029）；运行时据此把它定为危险级，从不靠猜。
- 停止命令声明 `lifecycle`，并且总是直接执行（DSEC-018）。

为什么：确认的严格程度跟着风险走：只改内存的直接执行，写盘和影响外部世界的才要确认。
检查：🤖 通用一致性测试 R01 检查每个命令都声明了效果；G01 和 G09 检查由效果得出的确认级别。

### DCLI-052 确认级别

| 级别 | 适用 | 调用方式 |
|---|---|---|
| 直接执行 | 只改软件内存、可以丢弃的操作：会话模式下对文档的修改；启动实例、新建和打开文档；新建文件且不覆盖已有文件；停止命令 | 一次调用就执行。Agent 可以加 `--dry-run` 先看预览 |
| 两步确认 | 写到磁盘或改变环境的操作：保存和覆盖文件；文件模式和设备模式下的修改；修改设置；安装宿主组件 | 第一次调用返回 `E_CONFIRMATION_REQUIRED`，带预览和令牌；再带 `--confirm <token>` 运行同一条命令才执行（DCLI-053） |
| 危险 | 物理动作；把内容交给外部；不可撤回的修改；放弃未保存的修改；关闭不是本工具启动的实例 | 与两步确认相同，确认的那次调用还要加 `--dangerous`；授权范围必须允许危险操作 |

- 级别按 DCLI-051 的规则得出，由运行时判定。工具禁止降低级别（DSEC-003）。
- 直接执行级的写操作，如果预览涉及输入里没有点名的目标键（比如移动器件时连带拖动了相连的走线），必须按两步确认处理。
- 命令可以在 `tier_when` 里登记自己的条件（DCLI-029），比如删除整张图页。某个条件对这次调用成立时，适配层在预览的 `tier_raises` 里写明，运行时必须把这次调用提到该条件的级别。命令没有登记的条件，必须以 `E_PROTOCOL` 拒绝。
- `reference` 必须标出每个写命令在每种模式下的级别（DCLI-031），让 Agent 不用猜。
- 授权范围文件设了 `confirm: always` 时，除停止命令外的所有写操作都必须两步确认，直接执行级的也一样（DSEC-013、DSEC-018）。

举例：移动 20 个器件再保存。用批量命令时，一次直接执行的批量移动，加上保存的两次调用（一次拿到预览和令牌，一次确认），共 3 次调用；逐个移动时要 22 次。

为什么：会话模式下修改在保存前只在内存里，每个小改动都确认只会多花调用；真正需要把关的是保存和影响外部世界的操作。
检查：🤖 通用一致性测试 G09；驱动一致性测试 D16 和 D17。

### DCLI-053 需要确认

两步确认级或危险级的命令没带 `--confirm` 时，必须什么都不改，返回 `E_CONFIRMATION_REQUIRED`，`details` 为 `{preview, confirm_token, expires_at}`。不必另做预演：Agent 核对预览后，带 `--confirm <token>` 把同一条命令再运行一次。

- `preview.tier` 为 `dangerous` 时，确认的那次调用还必须带 `--dangerous`。没带时，运行时必须什么都不改，再次返回 `E_CONFIRMATION_REQUIRED`；令牌不会因此用掉（DCLI-055）。危险级命令不带 `--dangerous` 绝不会执行。
- 授权范围设了 `confirm: always` 时，Agent 必须把预览给人看，人同意后才能确认（DSKL-008）。
- 这个信封按成功信封的大小上限计（DCLI-008）。
- `--format text` 禁止改变以上任何一点。

为什么：漏带标志不会多花调用，也绝不会导致写入。
检查：🤖 通用一致性测试 G01、G06 和 G09。

### DCLI-054 预演与预览

预演（`--dry-run`）必须什么都不改。两步确认级或危险级的写操作，预演返回 `data` = `{preview, confirm_token, expires_at}`，与 `E_CONFIRMATION_REQUIRED` 的 `details` 是同样的键。直接执行级的写操作，预演只返回 `{preview}`；授权范围设了 `confirm: always` 时，这个写操作改为两步确认，预演同样给出令牌（DSEC-013）。计划中的操作的 ID 是 `preview.operation_id`。

每个写操作都会生成预览，直接执行级的也一样，此时预览由运行时留作派发前复查之用（DCLI-056）。预览包括：

<!-- contract:preview_fields -->
| 字段 | 内容 |
|---|---|
| `operation_id` | 计划中的操作的 ID |
| `command` | 命令路径 |
| `action` | 一句话说明要做什么 |
| `target` | `{key, kind, label}`；`key` 是目标键（DCLI-028）。没有目标对象的命令没有这个字段，如不带 `--doc` 的 `session open`、`session close`、不带 `--path` 的 `doc create`、`knowledge create`、`knowledge delete`、`plugin install`、`plugin remove`、`operation resolve` |
| `effects` | 命令的效果 |
| `tier` | `direct`、`two_step` 或 `dangerous` |
| `summary` | 改动的条数，按 `origin` 和目标类型分别计数 |
| `changes` | 最多 50 条，每项改动一条：`{target, field, before, after, origin}`；`origin` 取 `tool` 或 `external`（DCLI-058） |
| `changes_truncated` | 改动多于 `changes` 所列时为 `true` |
| `session` | `{id, generation}` |
| `document` | `{id, revision, dirty}` |
| `can_apply` | 能不能执行 |
| `blocked_by` | 不能执行时的原因 |

- 每项改动都写明所改对象的目标键。不是本工具做的改动按对象归并，每个对象一条。
- `after` 必须是软件实际会存下的值，比如对齐到栅格后的位置，而不是 Agent 请求的值；读回时与它比较（DCLI-057）。
- 改动必须包括适配层能预见到的直接影响，而不只是输入设定的字段：一条把两个网络连在一起的走线，要列出它改动的网络，让人在确认之前就看到（DCLI-068）。
- 带 `--output <path>` 时，完整的预览（含全部改动）写进这个文件，响应里只给预览的其余部分（DCLI-008）。
- `action` 和每个 `label` 含有不可信文字时，列入 `meta.untrusted`（DCLI-004）。

为什么：任何改动发生之前，Agent 和人都能看清要改什么、改在哪个对象上，改动很多时也一样。
检查：🤖 通用一致性测试 G02。

### DCLI-055 确认令牌

确认令牌必须绑定以下各项；任何一项变了，都禁止再接受这个令牌。

<!-- contract:token_binds -->
| 绑定的值 | 含义 |
|---|---|
| `command` | 命令路径 |
| `inputs` | 规范化后的全部输入 |
| `target` | 目标键 |
| `changes` | 预览列出的修改，每项带修改前后的值 |
| `session_id` | 会话 ID |
| `session_generation` | 会话代际（DCLI-203） |
| `document_revision` | 文档版本（DCLI-210） |
| `document_dirty` | 文档有没有未保存的修改 |
| `plugin_version` | 实例已加载的宿主组件版本 |
| `scope_id` | 授权范围文件的 ID |
| `scope_hash` | 授权范围文件内容的哈希 |

- 运行时必须用密码学安全随机源把令牌生成为至少 128 位的随机值，连同绑定的各项和到期时间存进计划中的操作记录（DCLI-059）。核对令牌就是与这条记录比对，不做任何签名，运行时也不保存签名密钥。确认时，运行时必须在计划中的操作记录里找到这个令牌，且令牌没有过期、没有用过，才接受它（DCLI-056 第 1 项）。
- 令牌必须在 10 分钟后过期。
- 令牌必须只能用一次，开始派发时即用掉。确认的那次调用如果在派发前被拒绝，比如返回 `E_BUSY`、`E_HUMAN_REQUIRED`、`E_FORBIDDEN` 或冲突 `target_locked`，令牌在过期前仍然可用。
- 全局参数（DCLI-025），如 `--dangerous`、`--format`、`--quiet`，不属于输入，不绑定。

为什么：令牌绑定了预览里的一切，就不能被挪用到另一次改动上；什么都没改的拒绝，也不会逼 Agent 从头再来。
检查：🤖 通用一致性测试 G03、G04 和 G05；驱动一致性测试 D04 和 D23。

### DCLI-056 确认时的复查

命令带 `--confirm` 执行时，运行时必须先在计划中的操作记录里查到这个令牌（DCLI-055、DCLI-059），再按顺序做以下检查，遇到第一项不通过就停。不通过的检查必须阻止写入，返回 `E_CONFLICT`，`details.conflict` 写明是哪一项。第 3 项发现授权范围不再授予这次操作时，必须改为返回 `E_FORBIDDEN`。

<!-- contract:confirm_review -->
| 步骤 | 检查 | 不通过时的 `conflict` |
|---|---|---|
| `1` | 令牌有效、没过期、没用过 | `token_invalid`、`token_expired`、`token_used` |
| `2` | 命令和全部输入与令牌一致；错误里指出不一致的参数 | `token_mismatch` |
| `3` | 授权范围文件没变，且仍然授予这次操作 | `scope_changed` |
| `4` | 目标没有被锁（DCLI-060） | `target_locked` |
| `5` | 会话还在，代际不变 | `session_changed` |
| `6` | 文档版本和未保存状态不变 | `document_changed` |
| `7` | 宿主组件版本不变 | `plugin_changed` |
| `8` | 重新计算预览，修改与原来相同，且仍然可以执行 | `preview_changed`、`blocked` |

直接执行级的命令没有令牌，派发前必须同样做第 3 至 8 项检查，比较的基准是运行时生成预览时记下的状态。对停止命令，第 3 项只要求授权范围覆盖它的目标（DSEC-018），第 4 项不适用（DCLI-060）。

为什么：从预览到确认之间情况可能变了，派发前再查一遍，能拦下与批准内容不再相符的写入。
检查：🤖 通用一致性测试 G03、G04 和 G10；驱动一致性测试 D02 和 D03。

### DCLI-057 执行与读回

- 派发前，运行时必须检查会话状态（DCLI-204）、文档状态（DCLI-211）、设备状态（DCLI-230）和已加载的宿主组件版本（DSEC-053）。其中任何一项拦下这次写入时，必须返回这些条款规定的错误。能确定没有发出时，`dispatch_state` 必须为 `not_started`。
- 然后运行时经适配层的 `apply` 派发修改。适配层只报告事实：派发状态、变化、送达、保存情况和观察到的值（DCLI-260），禁止自己判定写入是否成功。
- 派发后，运行时必须从运行中的实例、文件或设备读回这次修改涉及的内容；适配层的回复报告没有持久效果，但预览或调用指定的文件预期有变化时，也要读回。读回时，把读到的值放进回执的 `observed`，并与预览里的 `after` 比较（DCLI-054）。值落在命令登记为该字段声明的容差之内（DCLI-029），或在没有声明容差时完全相等，才算一致。之前的写入改过的字段，比如 `doc save` 读回的、`doc undo` 撤回的，必须按改它的那条命令的容差比较。只有全部一致时，验证才可以记为 `passed`；不一致的字段必须列在 `mismatched` 里。

读回核实什么，取决于写操作的种类：

| 写操作 | 读回核实的内容 |
|---|---|
| 修改字段，如 `update`、`move` | 每个被改的字段都等于预览里的 `after` |
| `create` | 对象存在，且带有给定的字段 |
| `delete` | 对象已不存在 |
| `import` | 导入的对象都在，按数量核对 |
| `artifact` 类写操作，如 `export`、`render`、`doc save-as` | 生成的每个文件都存在，并记下路径、大小和 SHA-256；可以有多个文件。`output` 参数指明的文件夹里有文件或子文件夹。软件没用调用给的文件名、另起名字写出文件时（比如把 `part.prt` 写成 `part.prt.1`），适配层在 `files` 里列出它，并在 `requested_path` 里写上调用给的路径；这个文件就算作那个路径的文件。回执不对软件自己写出的字节内容作任何声明 |
| 会话模式下的 `doc save` | 重新读取保存后的文件，在其中找到被改的字段，且文档的 `saved` 字段为 `true`。最后一次改动是删除的目标，只核对它已不存在；之前的写入给它的字段和它下面的目标都不再读取。软件要把文档关掉再打开才能读这个文件时，适配层改为从实例读这些字段，只有看到磁盘上的文件确实变了才报 `saved: true`。适配层判断不了时，验证为 `incomplete` |
| 长任务 | 任务结束时核实它的产出，和同一命令的单次调用一样（DCLI-065） |

- 既新建文件、又修改文档的写操作，必须两边都核实：文件存在，被改的字段也读回一致。两边都通过，验证才是 `passed`。某次调用的 `files`、预览和 `output` 参数里都没有文件时，只核实它的其他修改。
- 登记了 `verify_after_save` 的命令（DCLI-029），在会话模式下报告验证为 `deferred`，并注明由 `doc save` 核实，因为软件要等保存之后才能正确显示这项修改。这份文档下一次 `doc save` 时，必须同时从保存后的文档读回这些推迟核实的修改，有不一致的就报告 `E_VERIFY_FAILED`，并把结果写回每一条推迟核实的操作记录。保存后的读回禁止改动文档，也禁止丢弃任何内容。推迟核实的修改按会话和文档对应。文档或它的会话没有保存就关闭时，这些记录必须以验证 `not_run` 结束。

只要有内容已改动、已保存或已送达，`verification` 就禁止为 `not_applicable`。

为什么：只有工具亲眼看到修改落实，并与软件应当存下的值对上，才算完成。
检查：🤖 驱动一致性测试 D04、D07 和 D08。

### DCLI-058 保存才是提交点

会话模式下，修改不等于保存。会话模式下修改之后，回执的 `persistence` 必须为 `unchanged`，文档的 `dirty` 必须为 `true`；声明了 `persist` 的命令除外。只有 `doc save`、文件模式下的写操作（DCLI-201）、新建文件的写操作，以及声明了 `persist` 的命令所做的修改，才报告 `saved`。

软件会把修改立即写进文件时，命令在 `change` 之外再声明 `persist`（DCLI-051）。它的回执必须报告 `persistence: saved`；文档的 `dirty` 不因这次修改改变，只看文档的其他修改。宿主组件，没有宿主组件时是适配层，禁止把它列进上次保存以来的修改，这样下一次 `doc save` 的预览里就没有它。它按 DCLI-057 核实。

效果为 `artifact` 的写操作，如 `doc save-as`、`export`、`render`，会新建文件。它的回执必须报告 `change: not_applicable` 和 `persistence: saved`，并按 DCLI-057 核实。

`doc save` 的预览必须列出上次保存以来的全部修改：本工具做的修改标为 `origin: tool`；不是本工具做的修改，比如人在软件里手动改的，标为 `origin: external`。上次保存以来的修改清单由宿主组件保存，没有宿主组件时由适配层保存（DCLI-277）。软件说不清改了什么时，只要文档版本在本工具没有写入的情况下变了，预览也必须至少列出一条外来修改，不带字段细节。运行时拿文档版本和本工具在这个会话里对这份文档最后成功完成的那次写操作的回执比较（DCLI-061）；那次写操作之后没能重新读到文档时，就不做这项比较。由软件运行的长任务，在工具第一次看到它结束时读文档，所以在此之前人做的修改会算作工具的修改。

文档上有结果未知的写操作时，它的目标一直锁着（DCLI-060），这把锁会挡住整个文件的保存。这时保存预览必须在 `blocked_by` 里列出每个这样的操作，并给出查清它的 `operation resolve` 命令（DCLI-063）。

为什么：「改了没存」绝不能看起来像「做完了」，保存时也要看清写到磁盘上的到底是什么，包括谁也还不知道结果的修改。
检查：🤖 驱动一致性测试 D09 和 D18。

### DCLI-059 操作记录

- 每个写操作都必须有一个以 `op_` 开头的操作 ID。
- 记录必须保存在状态目录里，内容包括：命令、目标（命令有目标时，DCLI-054）、效果、授权范围 ID、预览、确认令牌及其绑定的值和到期时间（DCLI-055）、回执、各阶段的时间、状态（`planned`、`dispatched`、`completed`、`failed` 或 `unknown`），以及 `operation resolve` 登记的结果，含 `resolved_by` 和读回的值或 Agent 给出的依据（DCLI-035）。
- 批量操作有自己的操作 ID，其中每个对象是一个子操作，有自己的记录，并注明所属的批量操作（DCLI-064）。长任务的操作在任务结束时补全（DCLI-065）。
- 记录必须保留 30 天。
- 操作记录写不进去时，所有写操作都必须以 `E_IO` 拒绝，什么都不派发；没有记录，确认令牌既存不下也核对不了。读操作必须照常工作。

为什么：没有记录，事后谁也说不清 Agent 做过什么，结果未知的写操作也无从结案。
检查：🤖 通用一致性测试 G08。

### DCLI-060 目标锁

- 派发前，运行时必须锁住目标。两个目标键中，一个在边界上是另一个的前缀时，二者冲突（DCLI-028）：锁住 `file:///D:/proj/board.kicad_pcb#/footprints/R1`，也会挡住 `file:///D:/proj/board.kicad_pcb` 的 `doc save`。相互冲突的目标上同一时间只允许一个写操作；另一个写操作必须得到 `E_CONFLICT`（`target_locked`），可以稍后再试。
- 批量操作必须在派发第一个对象之前锁住全部目标，并按目标键排序后依次加锁。
- 写操作有了确定的结果，不论成功还是失败，都会解锁。长任务的锁在任务结束时解开（DCLI-065）。写操作结果未知的目标必须保持锁定，直到用 `operation resolve` 登记结果（DCLI-063）。
- 锁必须放在状态目录里，由同一用户的所有进程共用；这样多个 Agent 对同一目标的写操作会一个接一个地进行。
- 停止命令（DSEC-018）禁止被它所停止的工作持有的锁挡住。
- 工具不检测人是否正在软件里操作。人改过文档，文档版本就会变：确认时得到 `document_changed`（DCLI-056），保存预览里显示为外来的修改（DCLI-058）。

为什么：两个写入方改同一个对象，或者一个改对象、一个存整个文件，都会互相覆盖；加锁把这种情况变成明确的冲突，按顺序加锁则让两个批量操作不会互相等待、永远卡住。
检查：🤖 驱动一致性测试 D06；运行时单元测试（两个进程写同一目标）。

### DCLI-061 回执

写操作成功时必须返回 `data` = `{operation_id, receipt, result}`，其中 `result` 是命令自己的结果。回执推导出 `ok: false` 时，回执改放在 `error.details.receipt` 里（DCLI-062）。回执由运行时根据适配层报告的事实和自己的读回比较生成（DCLI-057），包括：

- `operation_id`、`command`、`target`（没有目标对象的命令没有这一项，DCLI-054）、`effects`、`tier`；
- `outcome`，即下表的五个维度；
- `observed`：读回的值，每个键一项：适配层 `read_back` 报告的每个目标键（DCLI-260）；生成的每个文件，键为 `file:///<path>`，带 `path`、`bytes` 和 `sha256`；没有目标对象的命令和 `session open`，键为对象名，如 `session`、`document` 或 `plugin`；
- `mismatched`：每个不一致的字段，写成 `<field>@<key>`，`<key>` 是它在 `observed` 里的键；
- `session` `{id, generation}` 和 `document` `{id, revision, dirty}`；`document` 是写操作之后重新读到的状态，长任务在任务结束时读；读不到时为写之前的版本，`dirty` 为 null；
- `notes`：补充说明；
- `simulated: true`：结果来自模拟设备时带上（DSEC-062）；
- `more_files` `{count, command}`：写入生成的文件多于契约的 `receipt.listed_files`（10 个）时带上。`observed` 只保留这么多个，缺失的文件排在前面，`count` 是略去的个数；`command` 是把整条记录写进文件的 `operation get --output` 命令（DCLI-008）。

<!-- contract:receipt_dimensions -->
| 维度 | 取值 |
|---|---|
| `execution` 执行 | `not_started`、`running`、`completed`、`failed`、`interrupted`、`unknown` |
| `change` 变化 | `not_applicable`、`not_applied`、`applied`、`partial`、`rolled_back`、`unknown` |
| `delivery` 送达 | `not_applicable`、`not_attempted`、`delivered`、`partial`、`rejected`、`unknown` |
| `persistence` 保存 | `not_applicable`、`unchanged`、`saved`、`partial`、`unknown` |
| `verification` 验证 | `not_applicable`、`not_run`、`passed`、`failed`、`incomplete`、`deferred` |

五个维度必须分开报告，禁止合并成一个成功标记。

```json
{"ok":true,"data":{"operation_id":"op_7mq2xk4v","receipt":{"operation_id":"op_7mq2xk4v","command":"footprint move","target":{"key":"file:///D:/proj/board.kicad_pcb#/footprints/R1","kind":"footprint"},"effects":["change"],"tier":"direct","outcome":{"execution":"completed","change":"applied","delivery":"not_applicable","persistence":"unchanged","verification":"passed"},"observed":{"file:///D:/proj/board.kicad_pcb#/footprints/R1":{"x_mm":12.5,"y_mm":30.0}},"mismatched":[],"session":{"id":"kicad-1234","generation":3},"document":{"id":"doc_1","revision":"r8","dirty":true}},"result":{"id":"R1","x_mm":12.5,"y_mm":30.0}}}
```

为什么：执行了、改了、送达了、存了、核实了是不同的事实，Agent 必须逐一如实报告。
检查：🤖 通用一致性测试 R04；驱动一致性测试 D09。

### DCLI-062 推导 ok 和错误码

工具禁止自己决定写操作的 `ok` 和错误码。运行时必须按下表从回执推导这两者，取第一条匹配的规则。`ok` 为 `false` 时，回执必须放在 `error.details.receipt` 里。

<!-- contract:derivation -->
| 规则 | 回执中有 | ok | 错误码 |
|---|---|---|---|
| `1` | 执行、变化、送达或保存中有 `unknown`，或执行为 `running` | false | `E_OUTCOME_UNKNOWN` |
| `2` | 变化、送达或保存中有 `partial` | false | `E_PARTIAL` |
| `3` | 执行为 `failed`、`interrupted` 或 `not_started`，但已有持久效果（变化 `applied`、送达 `delivered` 或保存 `saved`） | false | `E_PARTIAL` |
| `4` | 执行为 `failed`、`interrupted` 或 `not_started`，且没有持久效果 | false | 失败原因的错误码，默认 `E_NATIVE_FAILURE` |
| `5` | 变化为 `rolled_back` | false | 回滚原因的错误码，默认 `E_NATIVE_FAILURE` |
| `6` | 送达为 `rejected` | false | 拒收原因的错误码，默认 `E_NATIVE_FAILURE` |
| `7` | 验证为 `failed` | false | `E_VERIFY_FAILED` |
| `8` | 验证为 `incomplete`；或验证为 `not_run`，而变化为 `applied`、保存为 `saved` 或送达为 `delivered` | false | `E_VERIFY_INCOMPLETE` |
| `9` | 其他情况 | true | 无 |

第 8 条保证「改了但没核实」不会被当成成功。出于同样的理由，只要有内容已改动、已保存或已送达，`verification` 就禁止为 `not_applicable`（DCLI-057）。

为什么：`ok` 由运行时根据事实推导，任何工具都没法报告它没亲眼看到的成功。
检查：🤖 驱动一致性测试 D07 和 D08；运行时单元测试覆盖每一条规则。

### DCLI-063 结果未知

- 派发之后连接在回复前关闭、软件崩溃或在期限内没有回复（DCLI-278），工具无法判断修改是否生效时，命令必须返回 `E_OUTCOME_UNKNOWN`，`dispatch_state` 为 `unknown`。修改本身弹出了弹窗时，同样处理（DCLI-066）。
- `details.lookup` 必须是查清结果的命令：单个写操作为 `operation resolve --id <id>`，批量操作为 `operation list --state unknown --batch <id>`（DCLI-064）。
- Agent 必须先运行这条查询，禁止重发写操作。
- `operation resolve` 读回目标并登记结果（DCLI-035）。在此之前目标一直锁着（DCLI-060）；登记后目标解锁。原始回执必须保持不变。

为什么：重发一个已经生效的写操作，可能让它生效两次；读回目标，就能按实际情况把问题查清。
检查：🤖 驱动一致性测试 D06 和 D21。

### DCLI-064 批量操作

- 作用于多个对象的命令必须用以下两种形式之一接收这些对象。两种形式都只给整批一份预览、一个令牌。
  - `--ids a,b,c` 对每个对象用同样的值。运行时必须去重并保持输入顺序。
  - `--input <file|->` 读入 `{"items": [{...}, ...]}`，每个对象有自己的值，比如把 20 个器件移到 20 个不同的位置。每一项用 `id` 指明对象；同一个对象出现在两项里时，必须返回 `E_USAGE`。`create` 命令的条目可以不带 `id`：每一项新建一个对象，由适配层在预览里给它目标键。
- 列表为空时必须返回 `E_USAGE`。预览显示任何一个对象不能执行（`can_apply: false`）时，整批都禁止执行。批量操作先锁住全部目标（DCLI-060）。
- 批量操作有自己的操作 ID，其中每个对象是一个子操作，有自己的记录和回执（DCLI-059）。
- 批量操作成功时必须返回 `data` = `{operation_id, items, summary}`。每项是精简的 `{target, operation_id, ok, code}`，被跳过的对象为 `{target, ok: false, skipped: true}`；某一项的完整回执用 `operation get --id <operation_id>` 获取。成功的项还必须在响应放得下的范围内带上 `result`，即只对这个对象调用时会返回的结果（DCLI-068）；每一项的记录都保存它的结果，有放不下的时，`more_results` 给出取结果的 `operation get` 命令。`summary` 为 `{total, succeeded, failed, unknown, skipped}`。批量没有全部成功时，`error.details.batch` 里放同样的对象。
- 全部对象成功才算成功。否则：有对象结果未知的，错误码必须为 `E_OUTCOME_UNKNOWN`，`lookup` 为 `operation list --state unknown --batch <operation_id>`；没有结果未知、但有对象已生效的，为 `E_PARTIAL`；都不是时，取第一个失败对象的错误码。
- `--continue-on-error` 默认必须为 `true`，危险级命令默认必须为 `false`。批量停下后，剩余对象必须报告为已跳过，并计入 `summary.skipped`。
- 会话变为 `busy`、`unresponsive`、`blocked` 或 `lost`（DCLI-204）时，不论 `--continue-on-error` 怎么设，批量都必须停下，剩余对象记为跳过。只要没有对象结果未知：已有对象生效时错误码为 `E_PARTIAL`，否则为该状态对应的错误码；`details.resume` 给出只覆盖未完成对象的命令。
- 工具禁止声称软件没有提供的「要么全成、要么全不成」。
- 同一类操作经常要对多个对象做时（如移动器件、改属性、删除），工具应该提供批量形式。

为什么：一次调用处理多个对象能省调用；逐个对象报告结果，部分失败时报告也是实话；软件不再响应时整批立刻停下，而不是一个对象一个对象地失败。
检查：🤖 驱动一致性测试 D06、D08 和 D24；运行时单元测试。

### DCLI-065 长任务

做实际工作的调用必须在 60 秒内返回。可能更久的工作必须做成长任务。等待不算工作：`job get --wait` 可以等更久（DCLI-220），真实软件行为矩阵要求的读回前等待也一样（DVER-030）。

长任务在以下两处之一运行：

- 由软件运行：命令登记了 `jobs: true`（DCLI-029），适配层的 `apply` 在软件里启动工作并报告任务，`job_status` 报告它的状态（DCLI-260）。
- 由运行时运行：命令登记了 `long: true`（DCLI-029）。调用做完全部检查，记下任务，启动一个后台进程，然后立即返回。后台进程就是工具自己的程序，以 `job get --job <id>` 和环境变量 `<PREFIX>_JOB_WORKER=<id>` 再启动一次，脱离这次调用，不弹窗口。它调用适配层时不受 60 秒限制，最长为契约里的 `jobs.worker_max_seconds`，并把结果记在状态目录里。派发前实例或设备回复 `E_BUSY` 时，什么都还没派发，后台进程就按 `retry_after_ms` 重试，最长 10 秒（契约里的 `jobs.busy_retry_seconds`），之后任务才以 `E_BUSY` 失败。工具作者不用写任何长任务代码。

可能超过一分钟的标准写命令，比如打开大工程的 `doc open`、关闭退出很慢的软件的 `session close`，工具把它列进 `long_commands`（DCLI-029）后，也按同样的方式运行；能列的标准写命令见契约的 `jobs.standard_long`。命令自己的检查在后台进程里、派发前再做一遍，工作结束后做命令自己的读回；它的结果，比如 `session open` 新开的会话，随 `job get` 返回。

写操作与读操作：

- 提交写操作的长任务是一次普通的写操作（DCLI-050），通常是动词为 `run` 的命令（DCLI-022）。它的回执报告 `execution: completed` 和 `change: not_applicable`，因为它做的只是启动任务。`data.result.job` 必须为 `{id, state, poll}`，`poll` 是查询这个任务的确切命令，如 `job get --job 7 --wait 60s`。长写操作每次调用只处理一个对象；长命令不带机密参数，因为后台进程从不接触机密。
- 长读操作，比如对大板子做设计规则检查，什么都不改：不需要授权范围和确认，也没有操作记录。调用返回 `data.job` = `{id, state, poll}`；任务结束后，`job get` 返回读操作的结果。
- 写操作长任务结束之前，目标一直锁着（DCLI-060）。
- 后台进程没有记下结果就结束时，写操作的长任务以 `unknown` 结束，读操作的长任务以 `failed` 结束。
- 任务进入终态时，运行时必须为这个任务的操作记录补上完整的回执：读回产出，任务生成的每个文件都记下大小和 SHA-256（DCLI-057）。之后 `job get` 按 DCLI-220 返回这个任务。
- 以 `unknown` 结束的任务，与其他结果未知的写操作一样，用 `operation resolve` 查清（DCLI-063）。

为什么：一次调用挂上几分钟，在 Agent 看来就和工具死掉一样；长任务的真实结果，也要和其他写操作一样认真报告。
检查：🤖 驱动一致性测试 D11 和 D26；👀 评审行为矩阵里的命令耗时（DVER-030）。

### DCLI-066 需要人处理

遇到弹窗、许可、登录、设备上的确认等必须由人处理的情况时，命令禁止等待或猜测，必须立即返回 `E_HUMAN_REQUIRED`，并在 `details` 里带：

- `action`：下表中的一个值；
- `resume`：人处理完后要运行的确切命令（DCLI-011）；
- `dispatch_state`：有没有派发。

<!-- contract:human_required_actions -->
| `action` 取值 | 人要做的事 |
|---|---|
| `dismiss_dialog` | 处理软件里的弹窗 |
| `activate_license` | 处理许可 |
| `sign_in` | 登录 |
| `install_component` | 安装组件 |
| `confirm_on_device` | 在设备上确认 |
| `restart_application` | 重启或重新加载软件实例，比如为了加载新的宿主组件（DSEC-053） |
| `other` | 其他事项 |

`resume` 是什么，取决于修改有没有派发：

- 派发前：`resume` 是要再运行一次的命令；令牌仍然有效时是确认的那条命令（DCLI-055），否则是不带标志的命令。
- 派发后，比如修改本身弹出了弹窗：操作的状态变为 `unknown`，目标保持锁定（DCLI-060）。这时 `resume` 必须是 `operation resolve --id <id>`（DCLI-063），绝不是再写一次。

弹窗的标题和文字必须放在 `details` 里，并必须列入 `meta.untrusted`。

为什么：模态弹窗可能让调用永远挂着；立即返回，Agent 才能去问人；修改本身弹出的弹窗，也不能导致同一修改被发两次。
检查：🤖 驱动一致性测试 D05；运行时单元测试（派发后出现弹窗）。

### DCLI-067 撤销与保证

工具可以提供 `doc undo`，撤销本工具最近一次修改；软件本身没有撤销功能时，禁止提供。工具禁止声明软件没有提供的事务、撤销、取消或幂等保证。

为什么：软件做不到的承诺，比不承诺更糟。
检查：👀 对照能力调查评审（DREPO-060）。

### DCLI-068 结果写明改后的样子

写操作的 `result` 必须按软件现在存着的样子，写明这次新建或修改的每个对象，以及这次修改的直接影响，让 Agent 只看这一次返回就知道改得对不对。禁止只给一个成功标志。

- 一条走线或导线：存下的端点和线宽（对齐栅格之后的值）、所在的层、所属的网络、两端各连到了哪些引脚或走线、有没有一端悬空。
- 一个建模特征：有没有再生成功、实际尺寸、引用了哪些参考。
- 结果必须只包括这次修改涉及的对象和直接影响，禁止把整个设计倒出来：走线连进了 `GND`，就写网络名，不列 `GND` 上的全部成员。
- 结果只放软件不用长时间计算就能给出的内容。要花很长时间的检查，比如设计规则检查，必须做成单独的 `check` 命令，由 Agent 在需要时调用。
- 适配层能预见的影响，作为改动放进预览（DCLI-054）；改完才知道的影响，放进 `result`。
- 软件给不出某项影响时，比如对象的连接关系，能力调查必须写明（DREPO-060），结果也禁止假装知道。

读命令同样以结构化的数据给出设计的样子：位置、尺寸和连接关系。Agent 用结果、回执和读命令核实修改，从不靠识别截图；渲染和截图是给人看的，或者用来判断纯视觉的问题，比如图纸排得挤不挤。

为什么：每画一条线都要截图的 Agent，做得慢，每张图都要花钱，还会认错。结构化的结果快、准，每次都一样。
检查：👀 对照能力调查评审每个写命令的输出 Schema；行为矩阵的 `normal` 行能看到结果（DVER-030）。

## 自更新

### DCLI-070 必须提供 update

每个工具都必须提供 `update`，即运行时自带的那个（DREPO-032），禁止自己写更新逻辑。

为什么：没有统一实现时，各工具要么不做自更新，要么各写一套。
检查：🤖 `check_project` 按哈希核对运行时副本（DREPO-034）。

### DCLI-071 选择版本

- `update` 必须从 npm 仓库查询版本。
- 使用前必须按 semver 校验每个版本号。
- 必须拒绝降级，除非同时给出 `--target-version` 和 `--allow-downgrade`。
- `update --check` 必须只查询，不安装。

为什么：格式不对的版本号或无意的降级，绝不能交给 npm 执行。
检查：🤖 运行时单元测试。

### DCLI-072 移开正在运行的程序

Windows 上正在运行的程序不能被覆盖，但可以改名移走。所以在 Windows 上，调用 npm 之前，`update` 必须把原生程序从平台包目录移到同一磁盘上的其他位置，默认是状态目录下的 `trash/`。下一次运行 `update` 时，必须删掉移到那里的文件。

为什么：不这样做，npm 就替换不了正在执行更新的那个程序。
检查：🤖 运行时单元测试。

### DCLI-073 安全地运行 npm

`update` 必须只用校验过的包名和版本号拼出 npm 的参数，禁止经过 shell 运行 npm，必须调用在 PATH 上按绝对路径找到的 npm，禁止调用当前目录里的同名文件。

为什么：精心构造的版本号，或放在工作目录里的假 npm，都不能借此执行代码。
检查：🤖 运行时单元测试。

### DCLI-074 核对结果

安装后，`update` 必须运行 `<tool> --version`，以它报告的版本为准；只有它等于目标版本，更新才算成功。接着 `update` 必须核对已安装的包的来源证明。只有核对确实做了并且通过，`provenance_status` 才可以为 `verified`；核对没法做时为 `not_checked`。做了核对但没通过，返回 `E_INTEGRITY`（DCLI-078）。

为什么：npm 可能报告成功，而实际运行的仍是旧程序。
检查：🤖 运行时单元测试。

### DCLI-075 直接运行的命令入口

经 npm 启动器启动工具，要先启动 Node.js，每次调用都多花几百毫秒甚至更多，Windows 上最明显。因此 `update` 必须把 npm 的命令入口改为直接运行平台包里的原生程序：Windows 上是 cmd、PowerShell 和 Git Bash 各用的三个入口文件，其他系统上是一个符号链接。启动器保留作后备。入口直接运行原生程序时，结果中的 `entry` 必须为 `direct`；仍经启动器时必须为 `launcher`。

为什么：Agent 调用次数多，每次的延迟在一个任务里会累积起来。
检查：🤖 运行时单元测试。

### DCLI-076 同步 Skill

安装后，`update` 必须从新包同步已安装的 Skill：在契约 `update.skill_dirs` 列出的 Agent Skill 文件夹里查找，把每个已安装的 Skill 目录换成新副本，必须保持符号链接不动，禁止新建原本没装的 Skill。

为什么：CLI 和 Skill 必须保持同一版本（DSKL-030）。
检查：🤖 运行时单元测试。

### DCLI-077 重复运行 update

已经是目标版本时，`update` 必须返回 `status: up_to_date`，并补完上次没做完的命令入口修改和 Skill 同步，所以重复运行总是安全的。装了新版本时，`status` 为 `updated`。`update --check` 什么都不装，有更新的版本时返回 `available`，否则返回 `up_to_date`。结果的 `data` 包括：

| 键 | 内容 |
|---|---|
| `status` | `up_to_date`、`updated` 或 `available` |
| `current_version` | 现在装着的版本 |
| `target_version` | `update` 要装的版本 |
| `previous_version` | 这次更新之前的版本 |
| `update_available` | 有没有比已装版本更新的版本 |
| `provenance_status` | `verified` 或 `not_checked`（DCLI-074） |
| `skill_sync_status` | `synced`、`not_needed`、`failed` 或 `skipped` |
| `entry` | `direct` 或 `launcher`（DCLI-075） |

为什么：更新中途被打断时，再运行一次同样的命令就能做完。
检查：🤖 运行时单元测试。

### DCLI-078 更新失败

`update` 必须按下表报告每种失败的错误码，并让工具处于对应的状态：

<!-- contract:update_failures -->
| 情形 | 说明 | 错误码 | 之后的状态 |
|---|---|---|---|
| `registry_unreachable` | 查版本时网络不通或被限流 | `E_NETWORK`（可重试） | 旧版本，未改变 |
| `invalid_version_or_unflagged_downgrade` | 版本号无效，或没带齐两个标志就降级 | `E_USAGE` | 未改变 |
| `npm_not_found` | PATH 上找不到 npm | `E_CONFIG` | 未改变 |
| `running_program_not_movable` | Windows 上移不走正在运行的程序 | `E_IO` | 未改变 |
| `install_failed_or_wrong_version` | npm 安装失败，或装完后程序报告的版本不对；`details` 给出手动安装命令 | `E_IO` | 旧版本 |
| `provenance_check_failed` | 来源证明核对失败 | `E_INTEGRITY`（不要重试） | 如实报告当前装着的版本 |
| `skill_sync_failed` | 工具已更新，但 Skill 同步失败；`details` 给出补救命令 | `E_PARTIAL` | 新工具、旧 Skill；再运行一次 `update` 完成同步 |

为什么：每次失败后，Agent 都必须知道自己在用哪个版本、下一步做什么。
检查：🤖 运行时单元测试。

### DCLI-079 更新提示

- 只有 `update` 和 `doctor` 访问 npm 仓库，其他命令禁止访问。`doctor` 查询时必须最多等 3 秒，失败就忽略。
- 查到的结果必须缓存在状态目录里。其他命令只读缓存，在 `meta.notices` 里附带 `update_available` 提示，每 24 小时最多一次。
- 有新的主版本，或已安装的版本在 npm 仓库上被标为弃用时，提示的 `severity` 必须为 `warning`，否则为 `info`。
- `update` 只更新 CLI 和 Skill，从不更新宿主组件（DSEC-050）。宿主组件需要重新安装时，`update` 的结果和 `doctor` 的输出都必须给出 `plugin install` 命令。

为什么：Agent 不用任何命令联网就能知道有更新，宿主组件也绝不会在用户不知情时被替换。
检查：🤖 运行时单元测试。

## 单位

### DCLI-080 标准单位

每种物理量必须使用一个标准单位，所有工具相同：

<!-- contract:units -->
| 物理量 | 单位 | 键名后缀 | 参数可接受的单位 |
|---|---|---|---|
| `length` 长度 | mm | `_mm` | nm、um、mm、cm、m、mil、in |
| `angle` 角度 | deg | `_deg` | deg、rad |
| `time` 时间 | s | `_s` | ms、s、min、h |
| `temperature` 温度 | degC | `_degc` | degC、K、degF |
| `mass` 质量 | kg | `_kg` | mg、g、kg |
| `force` 力 | N | `_n` | N、kN |
| `pressure` 压强 | Pa | `_pa` | Pa、kPa、MPa、bar |
| `speed` 速度 | mm/s | `_mm_per_s` | mm/s、mm/min、m/s |
| `angular_speed` 角速度 | deg/s | `_deg_per_s` | deg/s、rad/s、rpm |
| `frequency` 频率 | Hz | `_hz` | Hz、kHz、MHz、GHz |
| `voltage` 电压 | V | `_v` | uV、mV、V、kV |
| `current` 电流 | A | `_a` | nA、uA、mA、A |
| `resistance` 电阻 | ohm | `_ohm` | milliohm、ohm、kohm、megohm |
| `capacitance` 电容 | F | `_farad` | pF、nF、uF、mF、F |
| `inductance` 电感 | H | `_henry` | nH、uH、mH、H |
| `power` 功率 | W | `_w` | mW、W、kW |
| `acceleration` 加速度 | mm/s2 | `_mm_per_s2` | mm/s2、m/s2 |
| `torque` 力矩 | `N*m` | `_n_m` | `N*mm`、`N*m` |
| `energy` 能量 | J | `_j` | mJ、J、kJ |
| `area` 面积 | mm2 | `_mm2` | mm2、cm2、m2 |
| `volume` 体积 | mm3 | `_mm3` | mm3、cm3、l、m3 |
| `ratio` 比例 | % | `_pct` | % |

本表没有的物理量，必须先加进契约。工具禁止自创单位、后缀或物理量。

为什么：所有工具用同一个单位，Agent 在工具之间传值时不用换算。
检查：🤖 运行时单元测试（单位换算）。

### DCLI-081 输出里的单位后缀

物理量的输出键名必须带 DCLI-080 规定的单位后缀，值必须是标准单位下的数，如 `x_mm: 12.5`。错误详情和宿主协议里的计时字段（`retry_after_ms`、`deadline_ms`）以毫秒为单位，不属于领域里的物理量。

为什么：单位写在键名里，Agent 不会读错数值。
检查：👀 评审 `reference` 里的输出 Schema。

### DCLI-082 参数里的单位

物理量参数必须带单位给出，如 `--x 100mil` 或 `--x 2.54mm`，由运行时换算成标准单位。不带单位的数必须以 `E_USAGE` 拒绝，`problem` 必须列出该参数可接受的单位。只接受 DCLI-080 为该物理量列出的单位，其他单位属于无效的参数值（`E_USAGE`）。

为什么：用户和手册混用各种单位，一个光秃秃的 `100` 可能是毫米也可能是 mil；要求写明单位、在一处统一换算，每个工具都不会算错单位。
检查：🤖 驱动一致性测试 D13；运行时单元测试。

### DCLI-083 显示单位

工具禁止依赖软件当前设置的显示单位。适配层与软件交换数据时，必须自己换算。

为什么：用户随时可能把显示单位从 mm 切到 mil，相信显示单位的工具会写入错误的数值。
检查：👀 评审适配层；在真实软件上跑行为矩阵的读回行（DVER-031）。

### DCLI-084 坐标系

凡是表示坐标的参数和输出字段，`reference` 都必须在 `coordinate_system` 里写明所用的坐标系：原点、坐标轴方向，以及坐标相对哪个对象。

为什么：原点或某个轴的方向不同，同样的数字指的就是不同的点。
检查：👀 评审 `reference`。

### DCLI-085 未声明单位

已移除：这条规则已并入 DCLI-029。

## 模式与会话

### DCLI-200 驱动标准命令

运行时根据适配能力（DCLI-260）实现以下命令。工具禁止用其他名字提供同样的功能，比如 `session inspect`、`model open`、`service install`。

<!-- contract:standard_commands -->
| 命令 | 效果 | 模式 | 确认级别 | 说明 |
|---|---|---|---|---|
| `session list` | `observe` | 会话 | — | 列出所有运行中的实例：会话 ID、软件版本、状态、是否由本工具启动、打开的文档，以及被挡住时是什么挡住了它（DCLI-202） |
| `session get` | `observe` | 会话 | — | 一个实例的完整状态，包括是否忙、是否被挡住、是否没有回应 |
| `session open` | `lifecycle` | 会话 | 直接执行 | 启动新实例，返回会话 ID；需要 `open_session` |
| `session close` | `lifecycle` | 会话 | 直接执行；关闭不是本工具启动的实例为危险（DCLI-051） | 关闭实例；有未保存修改时拒绝；需要 `close_session` |
| `doc list` | `observe` | 文件、会话 | — | 列出所有实例里的文档：路径、版本、是否有未保存修改、是否只读、磁盘状态 |
| `doc get` | `observe` | 文件、会话 | — | 一份文档的状态 |
| `doc create` | `lifecycle` | 文件、会话 | 直接执行；`--path` 指定的文件已存在时两步确认 | 可选：新建文档，未命名或在 `--path`，给了 `--template` 时从模板开始（DCLI-212） |
| `doc open` | `lifecycle` | 会话 | 直接执行 | 可选：在实例里打开文件 |
| `doc save` | `persist` | 会话 | 两步确认 | 保存到原路径；预览列出上次保存以来的全部修改 |
| `doc save-as` | `artifact` | 会话 | 直接执行；目标文件已存在时两步确认 | 可选：另存为新文件 |
| `doc close` | `lifecycle` | 会话 | 直接执行；加 `--discard` 为危险（DCLI-051） | 可选：关闭；有未保存修改时拒绝，除非加 `--discard` 放弃修改 |
| `doc undo` | `change` | 会话 | 直接执行 | 可选，软件支持撤销时才提供：撤销本工具最近一次修改 |
| `job list` | `observe` | 全部 | — | 列出所有会话的长任务 |
| `job get` | `observe` | 全部 | — | 一个长任务的状态和 `poll_after_ms`；`--wait <duration>` 等到任务结束或时长用完（DCLI-220） |
| `job cancel` | `lifecycle` | 全部 | 直接执行；属于停止命令（DSEC-018） | 请求取消本工具启动的长任务；结果区分「已请求取消」和「确认已停止」 |
| `device list` | `observe` | 设备 | — | 列出设备：型号、固件、状态、是否为模拟设备 |
| `device get` | `observe` | 设备 | — | 一台设备的状态 |
| `knowledge list` | `observe` | 全部 | — | 列出知识条目（DSKL-123） |
| `knowledge get` | `observe` | 全部 | — | 内置条目返回正文；团队条目返回链接和读取工具 |
| `knowledge search` | `observe` | 全部 | — | 按关键词查找知识 |
| `knowledge create` | `configure` | 全部 | 两步确认 | 登记一份团队知识文档的链接 |
| `knowledge delete` | `configure` | 全部 | 两步确认 | 取消登记一条团队知识，不动链接指向的文档 |
| `plugin get` | `observe` | 有宿主组件时 | — | 磁盘上的版本，以及各实例已加载的版本 |
| `plugin install` | `configure` | 有宿主组件时 | 两步确认 | 安装或升级宿主组件（DSEC-050） |
| `plugin remove` | `configure` | 有宿主组件时 | 两步确认 | 卸载宿主组件 |

- 适配层缺少命令所需的能力时，命令必须返回 `E_UNSUPPORTED`（DCLI-260）；标为可选的命令，以及 `session open` 和 `session close`，依赖的都是可选能力。
- `knowledge create` 和 `knowledge delete` 的确认和授权范围见 DSKL-123。

为什么：同样的功能在每个工具里叫同样的名字，Agent 不用重新学会话、文档和长任务的用法。
检查：👀 对照本表评审命令树。

### DCLI-201 模式

工具按以下一种或几种模式工作。工具必须在每个命令的登记里声明它支持哪些模式（DCLI-029），并必须提供所支持的每种模式的必备命令。

<!-- contract:modes -->
| 模式 | 适用情况 | 必备命令 | 读回从哪里读 | 保存 |
|---|---|---|---|---|
| `file` 文件 | 不启动软件，直接读写文件 | `doc get`；`doc list` 可选 | 重新读取文件 | 写文件就是保存（`persistence: saved`） |
| `session` 会话 | 驱动一个正在运行的软件实例 | `session list`、`session get`、`doc list`、`doc get`、`doc save`；其他 `session` 和 `doc` 命令在适配层有对应能力时提供 | 运行中的实例 | 只有 `doc save` 才保存 |
| `device` 设备 | 驱动硬件或它的控制器 | `device list`、`device get` | 设备或它的传感器 | 不适用 |

文件模式下，`--doc` 必须是文件路径，`revision` 是文件内容的哈希（DCLI-210）。

一个命令同时支持文件模式和会话模式时，运行时必须为每次调用选定模式：

- 给了 `--session`，或者恰好有一个运行中的实例打开了这份文档时，用会话模式，并选中这个实例（DCLI-202）；
- 没有任何运行中的实例打开这个文件时，用文件模式。

运行中的实例已经打开了某个文件时，文件模式下修改或保存这个文件的写操作必须被拒绝，返回 `E_CONFLICT`，`conflict` 为 `file_open_in_session`。只在文件模式下工作的工具看不到实例，由适配层报告这个冲突（DCLI-279）。只是用这份文档生成别的文件的写操作，比如导出，可以照常执行；实例可能有未保存的修改时，响应必须带一条提示，说明用的是磁盘上的文件。只在文件模式下工作的工具，由适配层加这条提示（DCLI-260）。`reference` 分模式给出确认级别（DCLI-031）。

为什么：模式决定了以哪里的数据为准、保存意味着什么，每次写入因此知道该从哪里读回；同一个文件一边在内存里改、一边在磁盘上改，两边会互相抹掉对方的修改。
检查：🤖 通用一致性测试 R01 检查 `reference` 里的模式声明；驱动一致性测试 D03 检查 `file_open_in_session`。

### DCLI-202 选定会话

有多个运行中的实例、而作用于单个实例的命令没有指定 `--session` 时，命令必须返回 `E_AMBIGUOUS` 并列出候选，禁止按窗口焦点或最近使用顺序选择。只有一个实例时可以自动选中，并必须在输出中写明所选的会话。`--doc` 指定的文档恰好只在一个实例里打开时，选中这个实例（DCLI-201）。

列表读取命令 `session list`、`doc list` 和 `job list` 覆盖所有会话，禁止返回 `E_AMBIGUOUS`。`session list` 里的每个会话，以及 `E_AMBIGUOUS` 候选中的每个实例，都必须带一份摘要：`documents` = `[{id, path, dirty}]`；会话被挡住时，还有 `blocker` = `{action, title}`（适配层知道对应的知识条目时另带 `knowledge`，DSKL-124），其中 `title` 是不可信内容（DSEC-021）。适配层看不到全部实例时，`session list` 必须返回 `complete: false`。

为什么：同时开着两个工程时，按焦点选择会改错工程；看得到每个实例里的文档，Agent 才能点名正确的那个。
检查：🤖 驱动一致性测试 D01。

### DCLI-203 会话对象

会话至少必须有以下字段，完整定义见契约里的 `objects.schema.json`：

- `id`：会话 ID。运行时必须由进程号和进程启动时间推导出它，这样它在多次调用之间保持不变，也不会被之后的进程重用；
- `application`：`{name, version}`；
- `pid`：进程号；
- `owned`：实例是否由本工具启动；适配层把这一点记在适配层状态存储里（DCLI-260）；
- `generation`：连接代际。运行时发现连接断开后重新连上，或宿主组件重新加载时（DCLI-278），它加一。对同一个运行中的实例，CLI 的每次调用看到的代际相同；
- `state`：见 DCLI-204；
- `documents`：打开的文档，每份为 `{id, path, dirty}`；
- `plugin_loaded`：这个实例已加载的宿主组件版本；没有加载时为 `null`；
- `extra`：软件特有的字段。

会话、文档和设备对象中，本规范没有定义的字段都必须放在 `extra` 里。

为什么：`owned` 决定关闭是否危险；稳定的 ID 和 `generation` 让令牌能发现重连；`extra` 让软件自己的字段与标准字段互不混淆。
检查：🤖 驱动一致性测试 D02。

### DCLI-204 会话状态

写操作，以及需要软件主线程的读操作，必须按本表对各会话状态作出反应：

<!-- contract:session_states -->
| 状态 | 含义 | 命令的反应 |
|---|---|---|
| `starting` | 正在启动 | `E_BUSY`，可重试 |
| `ready` | 可以操作 | 正常执行 |
| `busy` | 软件报告主线程忙 | `E_BUSY`，可重试，附 `retry_after_ms`；保证没有派发 |
| `unresponsive` | 连接还在，但实例 2 秒内没有回复；`since_at` 说明从何时起 | `E_BUSY`，可重试；保证没有派发 |
| `blocked` | 有弹窗、许可窗口等需要人处理 | `E_HUMAN_REQUIRED`，附弹窗标题（不可信内容）和恢复命令 |
| `lost` | 发现文件和存活的进程都在，但连不上（DCLI-278） | `E_BACKEND_UNAVAILABLE`；已派发的写操作记为结果未知 |
| `unreachable` | 进程在运行，但工具驱动不了它，比如软件只让工具连上一个实例时的第二个实例 | `E_BACKEND_UNAVAILABLE`；什么都没有派发 |
| `closed` | 进程已结束 | 会话 ID 失效，`E_NOT_FOUND` |

适配层每次报告会话时都必须查看有没有弹窗，实例照常回应时也一样：有的软件弹着模态对话框时，仍然回应自动化接口的调用。工具自己正在执行的操作会自行应答的弹窗，比如导出时的选项对话框，不需要人处理；它开着时，会话报为 `busy`。适配层必须确知弹窗来自工具自己正在执行的操作，比如这个标题是那项操作预期会出现的，才把会话报为 `busy`；其他弹窗一律把会话报为 `blocked`。

标准的只读命令 `session list`、`session get`、`doc list`、`doc get`、`device list`、`device get`、`job list` 和 `job get` 必须把状态作为数据返回，禁止因为状态而失败。停止命令在 `lost`、`unreachable` 和 `closed` 之外的所有状态下都执行（DSEC-018）。

为什么：每种状态的反应都固定，软件忙或被弹窗挡住时，调用不会一直挂着；Agent 也总能看到状态。把需要人处理的弹窗错当成工具自己的，Agent 就会一直等一个不会来的应答。
检查：🤖 驱动一致性测试 D04、D05、D21 和 D22，以及行为矩阵在真实软件上的 `dialog` 行（DVER-030）；👀 审查适配层怎样认出自己操作弹出的对话框。

### DCLI-205 会话读取时限

`session list`、`session get`、`doc list` 和 `doc get` 必须在 5 秒内返回。某个实例 2 秒内没有回应时，命令必须不再等它，把它的 `state` 报为 `unresponsive`，其余实例照常返回。

5 秒里，适配层的 `sessions` 占 2.5 秒（契约里的 `session_read_deadline.adapter_sessions_ms`）；同时，运行时连接每个宿主组件并读取它的 hello，连接和读取合起来在 2 秒内完成。剩下的时间留给各实例的 `documents`，`doc list` 同时向所有实例读取。运行在 CLI 进程里的适配层必须自己守住这些时限，同时询问各个实例，并给每个实例单独的 2 秒时限，因为运行时打断不了它自己线程上的调用：超过期限的调用会结束整个进程。

为什么：一个卡住的实例，不能让 Agent 连其他实例也看不到。
检查：🤖 驱动一致性测试 D22；👀 审查 CLI 进程里适配层的 `sessions` 和 `documents`。

### DCLI-206 打开和关闭会话

`session open` 必须启动新实例并返回它的会话 ID。软件对某些种类的文档用单独的程序时，`--kind` 指明种类，由适配层启动那个程序。`session close` 必须拒绝关闭有未保存修改的实例，返回 `E_CONFLICT`，`conflict` 为 `unsaved_changes`。关闭不是本工具启动的实例（`owned: false`）属于危险级（DCLI-051）。适配层没有 `open_session` 或 `close_session` 能力时，对应的命令返回 `E_UNSUPPORTED`（DCLI-260）。

为什么：关闭会丢掉未保存的工作，而不是本工具启动的实例里可能有人正在做的工作。
检查：👀 评审行为矩阵（DVER-030）。

## 文档

### DCLI-210 文档对象

文档至少必须有以下字段，完整定义见契约里的 `objects.schema.json`：

- `id`；
- `path`：文件路径；从未保存过的新文档为 `null`；
- `key`：只用于没有路径的文档，即它的目标键 `untitled:<session-id>/<n>`（DCLI-028）。适配层必须给出，因为 `<n>` 只有适配层知道。有路径的文档没有 `key`，目标键就是 `file:///<path>`；
- `session`：文档所在的会话；文件模式下为 `null`；
- `kind`：软件有几种文档时，这份文档的种类，如 `part`、`assembly`、`drawing`。工具把这些种类列进自己的 `doc_kinds`，这时 `session open` 和 `doc create` 的 `--kind` 必须只接受这些种类，其他种类在动手之前就以 `E_USAGE` 拒绝。登记了 `doc_kinds` 的命令只能列其中的种类，遇到其他种类的文档时以 `E_VALIDATION` 拒绝（DCLI-029）；
- `revision`：由适配层给出的不透明字符串，每次修改和每次保存都会变；文件模式下是文件内容的哈希；
- `dirty`：有没有未保存的修改；软件无法判断时为 `null`，按可能有未保存的修改处理，关闭时需要 `--discard`（DCLI-212）；
- `readonly`：能不能写；
- `disk`：磁盘上的文件与文档的关系（DCLI-211）；
- `extra`：软件特有的字段（DCLI-203）。

为什么：有了 `revision` 和 `dirty`，令牌和保存预览都能发现任何修改，包括人做的修改；`disk` 则显示文件是否被别人改过。种类写错时当场拒绝，就不会被当成实例已经启动。
检查：🤖 驱动一致性测试 D03；运行时单元测试检查 `--kind` 只接受工具的种类。

### DCLI-211 文档状态

写操作必须按本表对文档的 `disk` 值作出反应：

<!-- contract:disk_states -->
| 状态 | 含义 | 命令的反应 |
|---|---|---|
| `in_sync` | 磁盘上的文件就是文档打开时或上次保存时的那个 | 正常执行 |
| `changed` | 打开或保存之后，磁盘上的文件被别人改过 | 写和保存被拒绝，返回 `E_CONFLICT`，`conflict` 为 `file_changed_on_disk` |
| `missing` | 磁盘上的文件已不存在 | 保存到原路径被拒绝，返回 `E_CONFLICT`，`conflict` 为 `file_missing` |

对 `readonly: true` 的文档写入，必须被拒绝，返回 `E_VALIDATION`。有未保存的修改（`dirty: true`）本身不拒绝任何操作；关闭或放弃这些修改按 DCLI-212 处理。`doc list` 和 `doc get` 把这些值作为数据报告（DCLI-204）。

为什么：保存绝不能悄悄覆盖别人的修改，也不能悄悄重建已被移走或删除的文件。
检查：🤖 驱动一致性测试 D03；运行时单元测试。

### DCLI-212 文档命令

文档命令的效果和确认级别见 DCLI-200。此外：

- `doc create` 新建一份 `--kind` 指定种类的文档；给了 `--template` 时，从这个模板开始。不带 `--path` 时，在实例里新建一份未命名文档；这个命令本身没有目标（DCLI-054），之后对新文档的写操作使用目标键 `untitled:<session-id>/<n>`（DCLI-028），授权范围用前缀 `untitled:` 覆盖它。
- 带 `--path` 时，目标为 `file:///<path>`，这次调用在返回之前就新建这个文件：文件模式下由工具写出，会话模式下由软件写出。只给新文档命名、要到保存时才写出文件的软件，不接受 `--path`：它新建未命名文档，之后用 `doc save-as` 写出。它直接执行，文件已存在时两步确认（DCLI-051）。文件写出后，回执报告 `persistence: saved`，读回核实文件存在（DCLI-057）。文件模式下必须带 `--path`。命令同时支持两种模式时，用 `--session` 选择会话模式。
- `doc create` 新建一份独立的文档，包括软件随之一起生成的配套文件，比如工程的原理图和电路板；回执列出每一个文件。领域命令禁止用别的名字做同样的事，比如 `model create`（DCLI-200）。往软件管理的容器里添加组成部分，比如往工程里加一块板，是领域命令，同时声明 `artifact`（DCLI-051）。
- `doc create`、`doc open`、`doc save-as` 和 `doc close` 是可选的。适配层没有命令所需的能力时，运行时必须返回 `E_UNSUPPORTED`（DCLI-260）。
- `doc close` 必须拒绝关闭有未保存修改、或 `dirty` 为 `null` 的文档，返回 `E_CONFLICT`，`conflict` 为 `unsaved_changes`。`doc close --discard` 放弃这些修改，属于危险级（DCLI-051）。
- `doc save` 写回原路径，它的预览列出上次保存以来的全部修改（DCLI-058）。
- `doc undo` 是可选的（DCLI-067）。

为什么：只有明确要求、并按危险级确认之后，未保存的工作才会被丢掉。
检查：🤖 驱动一致性测试 D10。

### DCLI-213 文件模式下的文档

已移除：这些规则已并入 DCLI-201 和 DCLI-210。

## 长任务

### DCLI-220 长任务对象与命令

长任务至少必须有以下字段：`id`、`operation_id`（读操作的长任务为 `null`，DCLI-065）、`state`、`cancel_requested`、`started_at`，以及任务结束后的 `finished_at`。软件报告进度时，还必须有 `progress` `{done, total, unit}`；总量未知时 `total` 为 `null`。

`state` 取 `planned`、`running`、`blocked`、`completed`、`failed`、`partial`、`unknown`、`cancelled` 之一。`blocked` 的任务还带 `action`：人要先做什么，任务才能继续（DCLI-066）。由运行时运行的长任务（DCLI-065），所在会话被弹窗挡住时必须报为 `blocked`，`action` 和 `knowledge`（适配层给出条目时）取自会话的 `blocker`（DCLI-202）；人处理完弹窗后，它又报为 `running`。任务处于终态（`completed`、`failed`、`partial`、`unknown` 或 `cancelled`）时，`job get` 必须同时返回它的 `receipt`、`result`，以及按 DCLI-062 由回执推导出的 `ok`（DCLI-065）。`ok` 为 `false` 时，任务还必须带上单次调用本会返回的错误：`code`、`message`，以及 `details`，比如 `conflict`、`native_message`、`knowledge` 和 `next_action`；`details` 里不带回执，因为任务自己带着回执。`details` 里重新发出这项工作的命令，无论在 `resume` 还是 `next_action` 里，都必须是这个任务自己的第一次调用，禁止是后台进程的调用；没有改动任何东西的写任务已经用掉了它的令牌，所以要从第一次调用重新开始。写操作的长任务在派发后被弹窗或其他需要人处理的情况挡住时，和单次调用一样以 `unknown` 结束（DCLI-066）：`code` 为 `E_HUMAN_REQUIRED`，`details` 里带 `action`、知道时带弹窗的 `title`、适配层给出条目时带 `knowledge`，以及 `resume`，即 `operation resolve` 命令。读操作的长任务没有回执：返回 `ok`，`ok` 为 `true` 时还返回读操作的 `result`。这些都是 `data` 里的字段：`job get` 本身是成功的，DCLI-062 所说的 `error.details.receipt` 属于写操作，不属于 `job get`。

`job list` 列出所有会话的长任务，被挡住的报为 `blocked`。`job get --job <id> --wait <duration>` 必须在任务结束、被挡住或时长用完时返回，以先到者为准。`--wait` 可以长达 600 秒，因为等待不算工作（DCLI-065）；超过时返回 `E_USAGE`。`job get` 必须返回 `poll_after_ms`，即过多久再查才有意义。

为什么：Agent 用一次次有时限的调用等待长时间的工作，而不是让一次调用一直挂着；任务的真实结果也以与其他写操作相同的形式读到。被弹窗卡住的任务会如实说出来，Agent 就去找人，而不是干等。失败的任务像单次调用一样说明原因和下一步，而不是只说失败了。
检查：🤖 驱动一致性测试 D11 和 D26；运行时单元测试检查被挡住的任务、派发后被弹窗挡住的任务，以及失败任务带的错误。

### DCLI-221 取消长任务

`job cancel` 只作用于本工具启动的长任务。它属于停止命令（DSEC-018），目标就是该任务所属操作的目标。它必须先报告 `cancel_requested: true`；状态必须等软件确认任务已停止后才变为 `cancelled`。按请求取消的任务，执行维度记为 `interrupted`，原因码为 `E_INTERRUPTED`（DCLI-062）。软件不支持取消时，`job cancel` 必须返回 `E_UNSUPPORTED`。读操作的长任务什么都不改：`job cancel` 结束它的后台进程，不需要授权范围，返回这个任务；后台进程停下后，状态为 `cancelled`；软件可能还在忙着做完这次读，之后的调用会看到它忙。由运行时运行的写操作长任务无法在中途安全停下：`job cancel` 必须返回 `E_UNSUPPORTED`，任务会一直运行到结束。

为什么：软件还没停就报告「已取消」，旧任务还在跑，Agent 就可能开始新的工作。
检查：🤖 驱动一致性测试 D11。

## 设备

### DCLI-230 设备对象与状态

设备至少必须有以下字段：`id`、`model`、`firmware`、`state`、`simulated`（是否为模拟设备）和 `extra`（DCLI-203）。动手前，运行时先读取设备状态（DSEC-061）；除停止命令外，作用于设备的每个命令都必须按本表作出反应：

<!-- contract:device_states -->
| 状态 | 含义 | 命令的反应 |
|---|---|---|
| `ready` | 可以动作 | 正常执行 |
| `busy` | 设备忙 | `E_BUSY`，可重试 |
| `fault` | 设备报告故障 | `E_HUMAN_REQUIRED`，`action` 为 `confirm_on_device` |
| `emergency_stop` | 处于急停状态 | `E_HUMAN_REQUIRED`，`action` 为 `confirm_on_device` |
| `offline` | 连不上设备 | `E_BACKEND_UNAVAILABLE` |

停止命令在 `offline` 之外的所有状态下都执行（DSEC-018）。`device list` 和 `device get` 把状态作为数据报告（DCLI-204）。模拟设备的结果按 DSEC-062 标明。

为什么：处于故障或急停的机器，在任何部件动起来之前，需要人到现场处理；而只要还连得上，就必须始终能让它停下。
检查：🤖 驱动一致性测试 D15、D23 和 D25；运行时单元测试（用模拟宿主的模拟设备逐一模拟各种状态）。

## 宿主组件

### DCLI-240 宿主组件命令

工具有宿主组件时，必须提供 `plugin get`、`plugin install` 和 `plugin remove`（DCLI-200）。`plugin get` 返回宿主组件对象：`name`、`version_on_disk`、`protocol_version`、`install_path` 和 `loaded`（各实例已加载的版本）。安装和升级必须遵守 DSEC-050 至 DSEC-053。

宿主组件的源码和清单放在 `plugin/`。清单 `plugin/manifest.json` 必须写明 `version`，即工具版本；`protocol_version`（DCLI-280）；`files`，即 `plugin install` 复制的每个文件的 SHA-256，以 `plugin/` 里的路径为键；以及 `methods`，即宿主组件实现的实例端能力（DCLI-260）。前三项由 `check_project` 根据文件写出，`methods` 由作者填写。工具把清单交给运行时，原生程序也随身带着 `plugin/`。

`plugin install` 复制 `plugin/` 里除清单、`README.md` 和 `plugin/source/` 文件夹以外的每个文件；`plugin/source/` 放的是要编译进安装文件的代码，从不安装。只有厂商的授权工具才能编译或解锁的文件，比如编译好的工具包库，可以提交在 `plugin/` 里；这时 `plugin/README.md` 必须写明是用哪个工具、哪个版本编译的，以及怎样重新编译。清单和对其他文件一样记下它的 SHA-256。

为什么：只有一条带检查的安装途径，在软件里运行的代码才可控。清单根据文件写出，就不会和文件对不上。
检查：🤖 驱动一致性测试 D12；`check_project` 重新生成清单并比对。

## 适配层与运行时

### DCLI-260 适配能力

除了领域命令，工具作者只写本表中的适配能力，其余全部由运行时处理（DREPO-032）。缺少某项能力时，运行时必须对需要它的命令返回 `E_UNSUPPORTED`，工具不用写任何代码。

<!-- contract:adapter_capabilities -->
| 能力 | 运行在哪一端 | 文件 | 会话 | 设备 | 说明 |
|---|---|---|---|---|---|
| `probe` | CLI 端 | 必需 | 必需 | 必需 | 查找软件的安装位置、版本和许可 |
| `sessions` | CLI 端 | — | 必需 | — | 列出实例及其状态 |
| `open_session` | CLI 端 | — | 可选 | — | 启动实例 |
| `close_session` | 实例端 | — | 可选 | — | 关闭实例 |
| `documents` | 实例端 | 必需 | 必需 | — | 列出文档及其状态 |
| `create_doc` | 实例端 | 可选 | 可选 | — | 新建文档：未命名，或在给定路径，给了模板时从模板开始 |
| `open_doc` | 实例端 | — | 可选 | — | 打开文档 |
| `save_doc` | 实例端 | — | 必需 | — | 把文档保存到原路径 |
| `save_doc_as` | 实例端 | — | 可选 | — | 把文档另存为新文件 |
| `close_doc` | 实例端 | — | 可选 | — | 关闭文档 |
| `undo` | 实例端 | — | 可选 | — | 撤销本工具最近一次修改（软件支持撤销时） |
| `read` | 实例端 | 必需 | 必需 | 必需 | 执行只读命令 |
| `preview` | 实例端 | 有写命令时 | 有写命令时 | 有写命令时 | 生成预览，带软件将会存下的值 |
| `apply` | 实例端 | 有写命令时 | 有写命令时 | 有写命令时 | 执行修改，并报告发生了什么 |
| `read_back` | 实例端 | 有写命令时 | 有写命令时 | 有写命令时 | 按目标键读回被改的字段 |
| `job_status` | 实例端 | 有 `jobs` 命令时 | 有 `jobs` 命令时 | 有 `jobs` 命令时 | 查询长任务的状态 |
| `cancel` | 实例端 | 有 `jobs` 命令时 | 有 `jobs` 命令时 | 有 `jobs` 命令时 | 取消长任务 |
| `devices` | CLI 端 | — | — | 必需 | 列出设备及其状态 |
| `plugin_status` | CLI 端 | 有宿主组件时 | 有宿主组件时 | 有宿主组件时 | 报告宿主组件的版本 |
| `plugin_install` | CLI 端 | 有宿主组件时 | 有宿主组件时 | 有宿主组件时 | 安装宿主组件 |
| `plugin_remove` | CLI 端 | 有宿主组件时 | 有宿主组件时 | 有宿主组件时 | 卸载宿主组件 |

- 标为「必需」的能力，支持该模式的每个工具都必须实现。「有写命令时」表示工具有写命令时必需；「有 `jobs` 命令时」「有宿主组件时」同理。「可选」的能力可以不实现。「—」表示该模式用不到这项能力。
- CLI 端的能力在 CLI 进程里运行。实例端的能力在适配层里运行，或作为宿主方法在宿主组件里运行（DCLI-262、DCLI-274）。
- `contract/schemas/adapter.schema.json` 定义每项能力的请求和结果。进程内的适配接口和宿主方法必须使用同一套定义。
- 能力只报告事实：派发状态、变化、送达、保存情况和观察到的值。由运行时把读回与预览比较，并推导回执（DCLI-057、DCLI-062）。
- 读操作的结果和写操作报告的事实可以带 `notices`，每条为 `{severity, message}`，写 Agent 应该知道的事，比如 DCLI-201 的那条提示。运行时把它们放进 `meta.notices`（DCLI-004）。
- 每次 CLI 调用都是一个独立的进程。适配层需要在两次调用之间记住的东西，比如本工具启动了哪些实例，或者没有宿主组件时上次保存以来的修改（DCLI-277），必须存放在运行时提供的适配层状态存储里，即状态目录下的 `adapter/`（DSEC-031）。

为什么：作者只写这个软件特有的部分；共用的部分，包括判定成功与否，全部来自运行时。
检查：🤖 运行时单元测试。

### DCLI-261 运行时负责的事

已移除：这条规则已并入 DREPO-032。

### DCLI-262 适配代码在哪里运行

每项适配能力的代码，必须在能调用到软件接口的地方运行，按下表决定。CLI 端的能力（DCLI-260）总是在 CLI 进程里运行。

| 软件提供的接口 | 代码在哪里运行 | 用什么语言 |
|---|---|---|
| CLI 能直接调用的接口：Python API、COM、软件自带的命令行、文件格式 | 适配层，在 CLI 进程里 | 运行时的语言 |
| 只能在软件进程里调用的接口：插件 SDK（.NET、Java、C++）、软件自带的脚本语言；或者调用必须在软件主线程上执行 | 宿主组件，在软件进程里，经宿主协议与 CLI 通信 | 软件支持的语言 |

- 两种方式可以按能力逐项混用：CLI 进程里的适配层实现了的能力就在那里运行，其余实例端能力交给宿主组件。比如读文件走适配层，改模型走宿主组件。
- 本规范提供 Python 版的驱动运行时。Python 能直接调用 COM、多数软件的 Python 接口和常见文件格式；其他接口都经宿主协议接入，所以一种运行时就能覆盖各种软件。

为什么：软件提供什么接口，决定了代码能在哪里运行；规则定好，每个作者就不用再做一遍选择。
检查：👀 对照能力调查评审（DREPO-015）。

### DCLI-263 黑盒一致性测试

已移除：这条规则已并入 DVER-011。

## 宿主协议

### DCLI-270 本机连接

CLI 与宿主组件之间必须只在本机连接：Windows 用命名管道，macOS 和 Linux 用 Unix 套接字。宿主组件禁止开放网络端口，必须只接受当前用户的连接。

为什么：宿主组件能修改用户的设计，网络上和本机上的其他人都不能连到它。
检查：👀 评审宿主组件。

### DCLI-271 发现文件

宿主组件启动后，必须在状态目录的 `hosts/` 下写一个发现文件，内容为 `address`、`pid`、`application`、`protocol_version`、`plugin_version` 和 `started_at`；退出时必须删除这个文件。`session list` 必须读取这些文件，并确认每个进程还在。为了让人从桌面启动的宿主也写到同一个目录，`plugin install` 必须把状态目录的路径写进宿主组件的配置。

为什么：崩溃的宿主会留下文件，确认进程是否还在，列表里才不会出现已经不存在的实例；共用一个目录，CLI 才能找到不是它启动的实例。
检查：🤖 运行时单元测试。

### DCLI-272 分帧

报文必须使用 JSON-RPC 2.0。每条报文必须先是一行 `Content-Length: <bytes>`，再空一行，然后是 UTF-8 编码的 JSON 正文。头部这一行和空行都以 CRLF（`\r\n`）结尾。`<bytes>` 是正文的字节数。

为什么：有了长度头，双方都能从数据流里读出完整的报文，不用猜一条报文在哪里结束。
检查：🤖 运行时单元测试。

### DCLI-273 握手

连上之后，CLI 必须先发 `hello`，带 `protocol_version` 和 `cli_version`。宿主必须回复 `protocol_version`、`plugin_version`、`application`（软件名称和版本）、`session` `{id, generation}` 和 `methods`（它支持的方法）。协议主版本不同时，CLI 必须返回 `E_PROTOCOL`，禁止发出任何修改。每个连接都必须先这样握手，协议主版本不同时，在发出任何修改之前就结束连接。宿主组件应该用宿主端小库，DCLI-270 的连接方式和这次握手它都会做（DCLI-281）。

为什么：对协议理解不一致的 CLI 和宿主，必须在误解一次写入之前就停下。
检查：🤖 驱动一致性测试 D20。

### DCLI-274 方法与期限

宿主方法必须是本表所列的方法：`hello`，以及实例端的适配能力（DCLI-260），每个方法与同名能力含义相同。方法的请求和结果，就是 `contract/schemas/adapter.schema.json` 为该能力定义的请求和结果。CLI 端的能力，如 `probe`、`sessions`、`plugin_install`，没有宿主方法，在 CLI 进程里运行。每个请求都必须带 `deadline_ms`，即 CLI 等待回复的毫秒数（DCLI-278）。

<!-- contract:host_methods -->
| 方法 | 写操作 | 含义 |
|---|---|---|
| `hello` | 否 | 握手（DCLI-273） |
| `close_session` | 是 | 关闭实例 |
| `documents` | 否 | 列出文档及其状态 |
| `create_doc` | 是 | 新建文档 |
| `open_doc` | 是 | 打开文档 |
| `save_doc` | 是 | 保存文档 |
| `save_doc_as` | 是 | 把文档另存为新文件 |
| `close_doc` | 是 | 关闭文档 |
| `undo` | 是 | 撤销本工具最近一次修改 |
| `read` | 否 | 执行读取 |
| `preview` | 否 | 生成预览；对 `doc save`，预览包含上次保存以来的外来修改（DCLI-277） |
| `apply` | 是 | 执行修改 |
| `read_back` | 否 | 读回被改的字段 |
| `job_status` | 否 | 报告长任务的状态 |
| `cancel` | 是 | 取消长任务 |

为什么：所有工具共用一套方法和一套报文定义，运行时和模拟宿主就能与任何宿主组件通信。
检查：🤖 运行时单元测试。

### DCLI-275 回复里的派发状态

写方法（DCLI-274 中标为写操作的方法）的回复必须带 `dispatch_state`。宿主在开始修改之前就失败的，必须回复 `not_started`。CLI 收不到回复时，必须把这个写操作记为结果未知（DCLI-063、DCLI-278）。

为什么：只有宿主知道修改有没有开始，CLI 不能替它猜。
检查：🤖 驱动一致性测试 D06 和 D21。

### DCLI-276 主线程

宿主组件必须在软件主线程上调用软件接口，实例侧的请求一次处理一个。`hello` 不等主线程，`cancel` 和停止命令的 `apply` 也不等（DSEC-018）：主线程忙着时，宿主按软件允许的方式把它们转交过去，比如设置一个正在运行的工作会检查的停止标志。请求到达时，如果主线程正在处理工具的上一个请求，宿主必须先等它，最多等 1.5 秒（契约里的 `host_protocol.busy_wait_ms`），且不超过请求的 `deadline_ms`，仍未等到才回复 `E_BUSY`：工具自己的请求经常同时到达，比如长任务的后台进程和 Agent 同时运行的读取命令。软件自己占着主线程时，比如人正在执行一条命令，宿主应该立即回复 `E_BUSY` 并带 `retry_after_ms`（DCLI-279），而不是把请求压着不处理。主线程忙时无法回复的宿主，过了单个实例的时限就会被报告为 `unresponsive`（DCLI-205）。

为什么：压着的请求可能很久以后才执行，那时 Agent 早已做了别的事；立即回复「忙」，Agent 才能心中有数地等待。短暂的等待让工具自己的请求不会互相拒绝。
检查：🤖 驱动一致性测试 D04；👀 审查宿主组件接收请求的方式。

### DCLI-277 外来修改

宿主从不主动向 CLI 推送消息：每次 CLI 调用都很短，需要什么就问什么。因此宿主组件必须保存上次保存以来、由本工具以外的一方对文档所做修改的清单，并在 CLI 预览 `doc save` 时通过 `preview` 返回（DCLI-058）。没有宿主组件时，由适配层把这份清单存放在适配层状态存储里（DCLI-260）。

为什么：只有在 CLI 的两次调用之间有人记下人的修改，保存预览才能把它们列出来。
检查：🤖 驱动一致性测试 D18。

### DCLI-278 连接丢失

每次 CLI 调用都建立自己的连接，调用结束时关闭；两次调用之间不保留任何连接。

- 连接在回复前关闭，或在 `deadline_ms` 内没有回复时，CLI 必须把写操作记为结果未知（DCLI-063），并必须把读操作所在的实例报告为 `unresponsive`（DCLI-204）。
- `apply` 已经回复，只是它之后的读取（比如读回）没有按时回复时，结果是已知的：运行时必须按报告的修改记录，验证为 `incomplete`，禁止记为结果未知；超过期限的调用结束了 CLI 进程或长任务的后台进程时也一样。
- 发现文件指向的进程还活着、但连不上它的地址时，会话必须报告为 `lost`（DCLI-204）。
- 运行时在连接丢失后重新连上实例，或发现宿主组件已重新加载时，会话的 `generation` 必须加一（DCLI-203）。

为什么：不再回应的宿主不能看起来还活着，它没做完的写操作也不能看起来已经完成。
检查：🤖 驱动一致性测试 D21。

### DCLI-279 宿主错误

宿主必须只返回以下错误码：

<!-- contract:host_error_codes -->
| 错误码 | 什么时候 |
|---|---|
| `E_BUSY` | 主线程忙（DCLI-276） |
| `E_NATIVE_FAILURE` | 软件拒绝了这次操作，没有留下持久改动 |
| `E_HUMAN_REQUIRED` | 有弹窗等需要人处理 |
| `E_NOT_FOUND` | 对象或文档不存在 |
| `E_AMBIGUOUS` | 一个名字匹配到多个对象，比如两个器件位号相同；带 `candidates` |
| `E_VALIDATION` | 软件拒绝了内容 |
| `E_CONFLICT` | 前置条件不满足，比如文件正被别的程序打开；带 `conflict`，取 DCLI-012 中的值，软件内部的冲突取 `application_state` |
| `E_UNSUPPORTED` | 软件做不到 |
| `E_BACKEND_UNAVAILABLE` | 软件没装、没有许可或连不上；什么都没派发 |
| `E_TIMEOUT` | 读操作超时；什么都没改 |

错误必须以 JSON-RPC 错误发送，错误码为 `-32000`。`data` 里必须有 `code` 和 `dispatch_state`，软件给出了报错原文时还必须有 `native_message`。`E_BUSY` 错误必须带 `retry_after_ms`；`E_HUMAN_REQUIRED` 错误应该带 `action`。`E_AMBIGUOUS` 错误必须带至少两个 `candidates`，`E_CONFLICT` 错误必须带 `conflict`；这两种错误和 `E_BACKEND_UNAVAILABLE`、`E_TIMEOUT` 一样，`dispatch_state` 为 `not_started`。其他错误码或缺少字段时，运行时必须以 `E_PROTOCOL` 回应。有内置知识条目讲解这个错误时，比如一个已知的弹窗，`data` 应该带 `knowledge`，即这些条目的 ID；CLI 会在 `next_action` 里写出对应的 `knowledge get` 命令（DSKL-124）。在 CLI 进程内运行的适配层报告同样的字段。DCLI-012 要求的其他 `details` 字段，如 `next_action` 和 `resume`，由 CLI 补上。`native_message` 是软件自己的报错原文，必须当作不可信内容处理（DSEC-020）。

为什么：宿主错误少而固定，就能直接对应到 Agent 已经熟悉的错误码。
检查：🤖 运行时单元测试。

### DCLI-280 协议版本

协议版本必须遵循 SemVer，并必须同时写在契约和宿主组件的清单里。本规范定义的协议版本是 1.3.0。

为什么：双方在交换任何写入之前就能比较版本。
检查：🤖 驱动一致性测试 D20。

### DCLI-281 宿主端库

本规范提供协议的 Schema（在契约里）、模拟宿主里的参考实现，以及部分宿主语言的宿主端小库。宿主组件所用的语言有对应的库时，应该使用它，不应该自己实现连接、分帧、握手和期限处理。

为什么：连接、分帧、握手和期限容易出细微的错，而且每个宿主都一样。
检查：👀 评审宿主组件。

## 检查清单

- [ ] DCLI-001 stdout 上只有一个信封
- [ ] DCLI-002 输出格式
- [ ] DCLI-003 信封的键
- [ ] DCLI-004 meta
- [ ] DCLI-005 命名与取值
- [ ] DCLI-006 列表
- [ ] DCLI-007 默认字段
- [ ] DCLI-008 响应大小
- [ ] DCLI-010 错误对象
- [ ] DCLI-011 派发状态与下一步
- [ ] DCLI-012 固定的 details 字段
- [ ] DCLI-013 错误码
- [ ] DCLI-014 退出码
- [ ] DCLI-020 先名词后动词
- [ ] DCLI-021 路径与别名
- [ ] DCLI-022 动词
- [ ] DCLI-023 名词
- [ ] DCLI-024 保留名
- [ ] DCLI-025 全局参数
- [ ] DCLI-026 共享参数
- [ ] DCLI-027 目标参数
- [ ] DCLI-028 目标键
- [ ] DCLI-029 命令登记
- [ ] DCLI-030 内置命令
- [ ] DCLI-031 reference
- [ ] DCLI-032 context
- [ ] DCLI-033 doctor
- [ ] DCLI-034 changelog
- [ ] DCLI-035 操作记录命令
- [ ] DCLI-036 版本与帮助
- [ ] DCLI-050 写操作流程
- [ ] DCLI-051 效果
- [ ] DCLI-052 确认级别
- [ ] DCLI-053 需要确认
- [ ] DCLI-054 预演与预览
- [ ] DCLI-055 确认令牌
- [ ] DCLI-056 确认时的复查
- [ ] DCLI-057 执行与读回
- [ ] DCLI-058 保存才是提交点
- [ ] DCLI-059 操作记录
- [ ] DCLI-060 目标锁
- [ ] DCLI-061 回执
- [ ] DCLI-062 推导 ok 和错误码
- [ ] DCLI-063 结果未知
- [ ] DCLI-064 批量操作
- [ ] DCLI-065 长任务
- [ ] DCLI-066 需要人处理
- [ ] DCLI-067 撤销与保证
- [ ] DCLI-068 结果写明改后的样子
- [ ] DCLI-070 必须提供 update
- [ ] DCLI-071 选择版本
- [ ] DCLI-072 移开正在运行的程序
- [ ] DCLI-073 安全地运行 npm
- [ ] DCLI-074 核对结果
- [ ] DCLI-075 直接运行的命令入口
- [ ] DCLI-076 同步 Skill
- [ ] DCLI-077 重复运行 update
- [ ] DCLI-078 更新失败
- [ ] DCLI-079 更新提示
- [ ] DCLI-080 标准单位
- [ ] DCLI-081 输出里的单位后缀
- [ ] DCLI-082 参数里的单位
- [ ] DCLI-083 显示单位
- [ ] DCLI-084 坐标系
- [ ] DCLI-085 未声明单位
- [ ] DCLI-200 驱动标准命令
- [ ] DCLI-201 模式
- [ ] DCLI-202 选定会话
- [ ] DCLI-203 会话对象
- [ ] DCLI-204 会话状态
- [ ] DCLI-205 会话读取时限
- [ ] DCLI-206 打开和关闭会话
- [ ] DCLI-210 文档对象
- [ ] DCLI-211 文档状态
- [ ] DCLI-212 文档命令
- [ ] DCLI-213 文件模式下的文档
- [ ] DCLI-220 长任务对象与命令
- [ ] DCLI-221 取消长任务
- [ ] DCLI-230 设备对象与状态
- [ ] DCLI-240 宿主组件命令
- [ ] DCLI-260 适配能力
- [ ] DCLI-261 运行时负责的事
- [ ] DCLI-262 适配代码在哪里运行
- [ ] DCLI-263 黑盒一致性测试
- [ ] DCLI-270 本机连接
- [ ] DCLI-271 发现文件
- [ ] DCLI-272 分帧
- [ ] DCLI-273 握手
- [ ] DCLI-274 方法与期限
- [ ] DCLI-275 回复里的派发状态
- [ ] DCLI-276 主线程
- [ ] DCLI-277 外来修改
- [ ] DCLI-278 连接丢失
- [ ] DCLI-279 宿主错误
- [ ] DCLI-280 协议版本
- [ ] DCLI-281 宿主端库
