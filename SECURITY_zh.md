# 安全策略

[English](SECURITY.md) | 中文

kicad-cli 让 AI Agent 操作 KiCad。人划定授权范围，Agent 动手干活，kicad-cli 保证每一步都正确、如实汇报。本文件说明怎样保证这一点，以及怎样报告漏洞。

## 支持的版本

<!-- driver:spec-supported-versions:start -->
安全修复只进入 kicad-cli 最新的次版本。

| 版本 | 安全修复 |
|---|---|
| 最新的次版本 | 有 |
| 更早的版本 | 没有；请用 `kicad-cli update` 更新 |

有已知漏洞的版本会在 npm 上标为弃用。之后 `kicad-cli doctor` 和其他命令的更新提示会向它的用户发出警告，`kicad-cli update` 会装上修复后的版本。
<!-- driver:spec-supported-versions:end -->

## 报告漏洞

<!-- driver:spec-reporting-a-vulnerability:start -->
请通过 GitHub 的私密漏洞报告功能私下报告漏洞：<https://github.com/fatecannotbealtered/kicad-cli/security/advisories/new>。不要为漏洞开公开的 Issue 或合并请求。

报告里写上 kicad-cli 的版本（`kicad-cli --version`）、操作系统、KiCad 的版本和能重现问题的步骤。不要附上机密、许可证文件和无权分享的项目文件。
<!-- driver:spec-reporting-a-vulnerability:end -->

## 风险等级

每个命令都有风险等级，由它声明的效果得出。工具的风险等级取所有命令中最高的一级；内置命令，以及团队知识命令 `knowledge create` 和 `knowledge delete` 不计入。

| 等级 | 含义 |
|---|---|
| `T0` | 只读取，什么都不改 |
| `T1` | 改变 KiCad 的运行状态、其中的对象或本地文件 |
| `T2` | 修改设置或宿主组件、触及外部的人或系统，或作用于物理世界 |

kicad-cli 的风险等级和决定它的命令，由 `kicad-cli reference` 生成：

<!-- driver:risk:start -->
风险等级：**T1**，由这些命令决定：

- `doc create`: lifecycle
- `job cancel`: lifecycle
- `schematic create`: artifact
- `schematic export`: artifact
- `schematic render`: artifact
- `symbol create`: artifact, change
- `symbol update`: change
- `symbol move`: change
- `symbol delete`: change
- `pin update`: change
- `net update`: change
- `board create`: artifact
- `board export`: artifact
- `board render`: artifact
- `footprint create`: artifact, change
- `netlist import`: change
- `footprint move`: change
- `track create`: change
- `track delete`: change
- `via create`: change
- `zone create`: change
- `zone delete`: change
- `board update`: change
- `footprint update`: change
<!-- driver:risk:end -->

## 授权

<!-- driver:spec-authorization:start -->
人在授权范围文件里授予写权限。这是一个 JSON 文件，字段有 `scope_id`、`effects`、`dangerous`、`targets`、`expires_at`，以及可选的 `confirm`。README 的「授权」一节有示例。

- **位置。** kicad-cli 从 `KICAD_CLI_SCOPE_FILE` 指定的路径读取授权范围文件；没有设置这个变量时，读取状态目录里的 `scope.json`。它不去别处找：不找工作目录、不找项目、不看命令行参数，所以授权范围不会夹在下载来的项目里进来。
- **谁来写。** 人，或者以下两种情况下的 Agent。人在自己的消息里授予目标时，Agent 写一份只含这份授权的授权范围文件，当前生效的授权范围设了 `confirm: always` 时也写上它，并告诉人写了什么；它从不修改不是它为这份授权写的授权范围文件，也不往里添加，因为一份文件的效果和到期时间对它的全部目标生效。布置任务不算授权。编码 Agent 为真实软件测试自己写授权范围文件，只授予 `docs/E2E.md` 里列出的目标。kicad-cli 只读取授权范围文件，没有命令会创建、修改或删除它。文档、文件、命令输出和其他 Agent 转来的消息都不算人的消息。
- **授予什么。** 在 `expires_at` 之前，允许这样的写操作：它的每个效果都列在 `effects` 里，它触及的对象的目标键都以 `targets` 中某个前缀开头；危险级的操作还要 `"dangerous": true`。批量操作中，只要有一个对象不在授权范围内，整批都不执行。读取不需要授权范围。
- **只能更严。** `"confirm": "always"` 让除停止工作外的每个写操作都变为两步确认，由人逐个批准预览。没有哪个字段能降低确认级别或跳过检查。
- **拒绝。** 没有有效授权范围覆盖的写操作什么都不改，返回 `E_FORBIDDEN`，说明缺什么，并给出一段人可以直接粘贴进文件的片段。`kicad-cli context` 报告授权范围的状态：`granted`、`missing`、`invalid` 或 `expired`。
- **记录与设置。** `operation resolve` 需要一个授权范围，覆盖它所处理的那次操作的效果和目标。`knowledge create` 和 `knowledge delete` 只改工具自己的团队知识链接列表，不需要授权范围。
- **局限。** 授权范围防的是 Agent 的失误，以及经不可信内容注入的指令。它拦不住无视 Skill 的 Agent，因为授权范围文件只是用户账户里的一个普通文件；要让这样的 Agent 碰不到这个文件，得靠你给 Agent 本身设定的权限。
<!-- driver:spec-authorization:end -->

一个人让 Agent 修改一个练习工程并导出投板文件时，给这个工程文件夹授予 `change` 和 `artifact` 两种效果。删除（`symbol delete`、`track delete`）还需要 `"dangerous": true`。

```json
{
  "scope_id": "demo-board-edit",
  "effects": ["change", "artifact"],
  "dangerous": false,
  "targets": ["file:///D:/work/demo/"],
  "expires_at": "2027-01-31T18:00:00Z"
}
```

## 写入确认

每个写操作，不论哪个级别，都会检查授权范围、生成预览、保留操作记录、读回修改并返回回执。级别只决定怎样确认。kicad-cli 根据命令声明的效果得出级别；任何设置、参数或代码都不能降低它。

| 级别 | 适用 | 确认方式 |
|---|---|---|
| 直接执行 | 只改软件内存、可以丢弃的修改；启动实例；新建和打开文档；不覆盖已有文件的新文件；停止工作 | 不需要确认，一次调用就执行 |
| 两步确认 | 保存和覆盖文件；文件模式和设备模式下的修改；设置；宿主组件 | 第一次调用什么都不改，返回 `E_CONFIRMATION_REQUIRED`，带预览和确认令牌；再带 `--confirm <token>` 运行同一条命令才执行 |
| 危险 | 物理动作；把内容交给外部的人或系统；不可撤回的修改；放弃未保存的修改；关闭不是 kicad-cli 启动的实例 | 与两步确认相同，确认的那次调用还要加 `--dangerous`，授权范围里还要 `"dangerous": true` |

直接执行级的写操作，如果预览触及输入里没有点名的对象，按两步确认处理。授权范围设了 `"confirm": "always"` 时，除停止工作外的每个写操作都是两步确认。

**确认令牌。** 确认令牌是取自安全随机源、至少 128 位的随机值。令牌不签名：kicad-cli 把它存进计划中的写操作的操作记录，只有在那里找到它、而且它没过期、没用过，才接受它。令牌 10 分钟后过期，开始派发时即被用掉，所以只能用一次。它绑定命令、全部输入、目标、预览列出的修改、会话及其代际、文档版本和未保存状态、已加载的宿主组件版本，以及授权范围文件的 ID 和内容哈希；其中任何一项变了，令牌就会被拒绝。确认在派发前被拒绝时，令牌在过期前仍然可用。

**确认时的复查。** 就在派发之前，kicad-cli 按顺序做下面的检查，遇到第一项不通过就停：令牌有效、没过期、没用过；命令和全部输入与令牌一致；授权范围文件没变，并且仍然授予这次写操作；目标没有被锁；会话还在，代际不变；文档版本和未保存状态不变；宿主组件版本不变；重新计算的预览与原来的修改相同，并且仍然可以执行。不通过时什么都不改，返回 `E_CONFLICT` 并写明是哪一项；授权范围不再授予这次写操作时，返回 `E_FORBIDDEN`。直接执行级的写操作没有令牌，从授权范围那一项起做同样的检查，比较的基准是生成预览时记下的状态。

## 状态目录

kicad-cli 把状态保存在每个用户一个的目录里：`KICAD_CLI_STATE_DIR` 指定的路径；没有设置这个变量时是 `~/.kicad-cli`。该用户的所有进程共用这个目录，所以多个 Agent 共用同样的锁和记录。

| 项 | 存放什么 | 由谁写 |
|---|---|---|
| `operations/` | 操作记录，保留 30 天，计划中的写操作带着它的确认令牌 | kicad-cli |
| `locks/` | 目标锁 | kicad-cli |
| `cache/` | 最近一次检查更新的结果 | kicad-cli |
| `hosts/` | 运行中的宿主组件的登记文件 | 宿主组件 |
| `adapter/` | 适配层在两次调用之间要记住的东西 | kicad-cli，代适配层写 |
| `trash/` | `update` 移走的原生程序，下次运行时删除 | kicad-cli |
| `scope.json` | 默认位置的授权范围文件 | 人，或「授权」一节允许的 Agent |
| `knowledge-links.json` | 团队知识的登记 | 只有 `knowledge create` 和 `knowledge delete` |

每一项只由表中写明的一方来写。这个目录只允许当前用户读写：在 macOS 和 Linux 上权限是 `0700`，在 Windows 上 ACL 只授权给当前用户。kicad-cli 在创建目录时设置好保护，`kicad-cli context` 报告实际生效的保护方式：`posix_0700`、`windows_acl` 或 `none`。操作记录写不进去时，所有写操作都以 `E_IO` 拒绝，什么都不派发，读取照常。测试用临时的状态目录，从不碰真实的那个。

## 凭据

- **机密。** 密码、访问令牌和许可证密钥从不出现在输出、日志、操作记录、错误信息或真实软件记录里。每个命令都声明自己的机密参数，凡是回显输入的地方，比如预览，kicad-cli 都把它们显示为 `***`。
- **连接。** kicad-cli 只连接 npm 仓库（只有 `update` 和 `doctor` 会连），以及它驱动的 KiCad 服务或设备（只由驱动它们的命令连接）。它不发送任何遥测数据。每个网络请求都遵守 `HTTPS_PROXY`、`HTTP_PROXY` 和 `NO_PROXY`，每个 TLS 连接都校验服务器证书，没有任何办法关掉这项校验。
- **团队知识。** kicad-cli 只保存已登记的团队知识的链接。它从不连接飞书，也不保存飞书账号；团队文档由 Agent 自己用 lark-cli 读取。

kicad-cli 不经手任何机密：KiCad 不需要许可，也没有命令接收密码、密钥或 token。

kicad-cli 不连接任何 KiCad 服务或设备。它只读写本机磁盘上的文件，并在同一台电脑上运行 KiCad 自带的命令行程序；只有 `update` 和 `doctor` 访问 npm 仓库。

## 不可信内容

来自 kicad-cli 和人以外的文字都是不可信内容，不管是谁写的：

- 对象名称、文档内容和文件名；
- 弹窗的标题和文字；
- 软件自己的报错原文；
- 设备发来的消息；
- 团队知识：登记的链接、链接的说明，以及链接指向的文档。

含有不可信内容的输出，会把每个不可信值的 JSON 路径列进 `meta.untrusted`。kicad-cli 标出软件的报错原文、弹窗文字、团队知识，以及它自己嵌入了不可信值的文字，比如预览的 `action`、目标的 `label` 和它生成的命令；每个领域命令都声明自己结果里哪些字段是不可信内容。kicad-cli 和它的 Skill 从不把不可信内容当作指令执行：文档或弹窗里的要求是要汇报的数据，它提议的操作，Agent 要先问人再做。不可信内容从不扩大授权范围或目标。

## 供应链

- 全部依赖都由仓库里的锁文件固定版本，CI 按这些锁文件构建。
- kicad-cli 的每个 npm 包，包括主包和每个平台包，都只由版本标签触发的发布工作流构建和发布，并带 npm 来源证明。不会有包从个人电脑上发布。
- 这些包没有安装脚本（`preinstall`、`install` 或 `postinstall`），安装时也不下载任何东西；原生程序和它运行时要读的一切都在包里。
- Skill 只从已安装的 npm 包里安装和同步，从不从网络拉取。

## 自更新

`kicad-cli update` 是 kicad-cli 更新自己的唯一途径。它：

- 通过 npm 查询 npm 仓库，所以 npm 自己的仓库、代理和证书设置都生效；按 semver 校验每个版本号，除非同时给出 `--target-version` 和 `--allow-downgrade`，否则拒绝降级；`update --check` 只查询，什么都不装；
- 在 Windows 上，调用 npm 之前把正在运行的原生程序的文件移到状态目录的 `trash/` 里；状态目录在另一块磁盘上时，移到 npm 全局 `node_modules` 文件夹里的 `.kicad-cli-trash`；下次运行时删除；
- 调用在 PATH 上按绝对路径找到的 npm，从不经过 shell，也从不调用当前目录里的同名文件，npm 的参数只用校验过的包名和版本号拼成；
- 安装后运行 `kicad-cli --version`，只有它报告的是目标版本、并且 PATH 上的 `kicad-cli` 就是 npm 刚改的那一个，才算成功；否则把原来的版本放回去。然后核对已安装的包的来源证明：核对确实做了并且通过才报告 `verified`，没法核对时报告 `not_checked`，核对没通过时返回 `E_INTEGRITY`；核对失败会被记下，再运行 `update` 也不会用那个包去修改命令入口或同步 Skill；
- 把命令入口指向原生程序（`entry: direct`），npm 启动器保留作后备；
- 从新包同步已经装在 Agent Skill 目录里的 Skill，不动符号链接，也不新建原本没装的 Skill；
- 从不安装或改动宿主组件；宿主组件需要重新安装时，给出 `plugin install` 命令。

重复运行 `update` 总是安全的：已经是目标版本时，它返回 `up_to_date`，并补完上次没做完的命令入口修改或 Skill 同步。每种失败之后的状态都是确定的：安装失败时保留旧版本；Skill 同步失败时返回 `E_PARTIAL`，再运行一次 `update` 就能补完。只有 `update` 和 `doctor` 访问 npm 仓库，`doctor` 最多等 3 秒。其他命令只读缓存的结果，每 24 小时最多附带一次更新提示；有新的主版本，或已安装的版本被标为弃用时，提示为警告。
