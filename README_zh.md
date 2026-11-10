# kicad-cli

让 AI Agent 安全地读写 KiCad 的原理图、库和 PCB，并运行 KiCad 自己的 ERC、DRC 和投板文件导出。

<!-- driver:badges:start -->
![Agent-native](https://img.shields.io/badge/🤖-Agent--native-6f42c1)
[![CI](https://github.com/fatecannotbealtered/kicad-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/fatecannotbealtered/kicad-cli/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/@fateforge/kicad-cli)](https://www.npmjs.com/package/@fateforge/kicad-cli)
![drives](https://img.shields.io/badge/drives-KiCad_%3E%3D10.0.6_%3C11-1f6feb)
![mode](https://img.shields.io/badge/mode-file-0e8a16)
![platform](https://img.shields.io/badge/platform-Windows_%C2%B7_macOS_%C2%B7_Linux-555555)
![risk](https://img.shields.io/badge/risk-T1-d29922)
![maturity](https://img.shields.io/badge/maturity-verified-8957e5)
![spec](https://img.shields.io/badge/spec-tool--driver--cli--spec_1.2.0-0969da)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)
<!-- driver:badges:end -->

[English](README.md) | 中文

<!-- driver:info-card:start -->
| 项目 | 内容 |
|---|---|
| 软件 | KiCad >=10.0.6 <11 |
| 模式 | file |
| 系统与 CPU | Windows (x64, arm64), macOS (x64, arm64), Linux (x64, arm64) |
| 风险等级 | T1 |
| 成熟度 | verified：draft 0 个，verified 2 个，stable 32 个 |
| 规范 | tool-driver-cli-spec 1.2.0 |
| 宿主组件 | 不需要 |
<!-- driver:info-card:end -->

## 安装

1. 从 npm 安装 kicad-cli：

   ```bash
   npm install -g @fateforge/kicad-cli
   ```

2. 运行一次 `update`。刚装好的已经是最新版本，所以 `update` 只把命令入口指向原生程序；这样每次调用都不用先启动 Node.js：

   ```bash
   kicad-cli update
   ```

3. 从已安装的包里安装 Skill，Skill 从不从网络获取。把它们复制到 Agent 的 Skill 目录。用 bash：

   ```bash
   mkdir -p ~/.agents/skills
   cp -r "$(npm root -g)/@fateforge/kicad-cli/skills/"* ~/.agents/skills/
   ```

   用 PowerShell：

   ```powershell
   New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
   Copy-Item -Recurse -Force "$(npm root -g)\@fateforge/kicad-cli\skills\*" "$HOME\.agents\skills\"
   ```

   其他 Agent 的 Skill 目录做法相同。以后运行 `kicad-cli update` 时，这几个目录里的副本会跟着工具一起更新：`~/.agents/skills/`、`~/.claude/skills/`、`~/.codex/skills/`、`~/.cursor/skills/`、`~/.gemini/skills/`。放在别处的副本不会更新。

## 能力

下面是 kicad-cli 的全部命令，由命令登记生成；`(dangerous)` 表示该命令属于危险级，`kicad-cli reference --command "<path>"` 给出单个命令的完整说明。

<!-- driver:command-map:start -->
- `reference`
- `context`
- `doctor`
- `changelog`
- `operation`: get, list, resolve
- `update`
- `doc`: list, get, create
- `job`: list, get, cancel
- `knowledge`: list, get, search, create, delete
- `project`: get
- `library`: list, get
- `schematic`: create, check, export, render
- `symbol`: list, get, create, update, move, delete (dangerous)
- `net`: list, update
- `pin`: update
- `board`: create, check, export, render, get, update
- `footprint`: list, create, move, update
- `track`: list, create, delete (dangerous)
- `netlist`: import
- `via`: create
- `zone`: list, create, delete (dangerous)
<!-- driver:command-map:end -->

## 工作方式

kicad-cli 每次调用都输出一个 JSON 信封。`kicad-cli reference` 列出全部命令，`kicad-cli reference --command "<path>"` 说明单个命令，包括它的参数、效果和在每种模式下的确认级别。

**文件。** kicad-cli 直接读写 KiCad 的文件，不需要运行中的实例。`--doc <path>` 指定文件。

**确认级别。** 每次写操作都会检查授权范围、生成预览、在派发前再检查一次状态、锁定目标、读回修改并返回回执。只有确认方式随级别不同，级别由 kicad-cli 根据命令声明的效果得出：

| 级别 | 适用 | 调用方式 |
|---|---|---|
| 直接执行 | 保存之前只在内存里的修改；启动实例；新建和打开文档；不覆盖已有文件的新文件；停止工作 | 一次调用就执行 |
| 两步确认 | 保存和覆盖文件；文件模式和设备模式下的修改；设置；宿主组件 | 第一次调用什么都不改，返回 `E_CONFIRMATION_REQUIRED`，带预览和确认令牌；再带 `--confirm <token>` 运行同一条命令才执行 |
| 危险 | 物理动作；把内容交给外部的人或系统；不可撤回的修改；放弃未保存的修改；关闭不是 kicad-cli 启动的实例 | 与两步确认相同，确认的那次调用还要加 `--dangerous`；授权范围必须允许危险操作 |

`--dry-run` 显示任何写操作的预览，什么都不改。确认令牌只能用一次，10 分钟内有效，而且只对它预览过的那次修改有效。授权范围文件设了 `"confirm": "always"` 时，除停止工作外的所有写操作都改为两步确认，人可以逐个批准预览。

**文件模式下的保存。** 写文件就是保存：回执报告 `persistence: saved`，kicad-cli 会重新读取文件来验证修改。

**回执。** 每次写操作都返回回执，分别报告修改是否执行、是否生效、是否送达、是否保存、是否验证。写操作的回复丢失时，kicad-cli 返回 `E_OUTCOME_UNKNOWN` 和查明结果的命令，绝不会把写操作再发一遍。

## 授权

<!-- driver:spec-authorization:start -->
kicad-cli 读取不需要许可，但只写入人在授权范围文件里允许的内容。这个 JSON 文件由人来写；或者人在自己的消息里把目标授予 Agent，由 Agent 把这份授权写成一份自己的授权范围文件，并说明写了什么。没有命令会写这个文件，Agent 也不会因为文档里的要求去写。kicad-cli 从 `KICAD_CLI_SCOPE_FILE` 指定的路径读取它；没有设置这个变量时，读取状态目录里的 `scope.json`，不从其他任何地方读取。

```json
{
  "scope_id": "demo-rework",
  "effects": ["change", "persist"],
  "dangerous": false,
  "targets": ["file:///D:/projects/demo/"],
  "expires_at": "2027-01-31T18:00:00Z"
}
```

| 字段 | 含义 |
|---|---|
| `scope_id` | 这份授权的名称；操作记录会带上它 |
| `effects` | 写操作可以有的效果；读取不需要授权范围 |
| `dangerous` | 是否允许危险级的操作 |
| `targets` | 写操作可以触及的对象的目标键前缀：`file:///D:/projects/demo/` 覆盖这个文件夹下的所有文件，`untitled:` 覆盖新建的文档，`device:<id>` 覆盖一台设备 |
| `expires_at` | 授权到期的时间，用 UTC |
| `confirm` | 可选；设为 `always` 时，除停止工作外的每个写操作都要等人批准预览后才执行 |

授权范围不覆盖的写操作什么都不改，返回 `E_FORBIDDEN`，说明缺什么，并给出一段人可以直接粘贴进文件的片段。`kicad-cli context` 显示当前生效的授权范围，`SECURITY_zh.md` 完整说明了授权范围。
<!-- driver:spec-authorization:end -->

| 效果 | 命令 | 文件模式下的确认 |
|---|---|---|
| `observe` | `project get`、`library list`、`library get`、`schematic check`、`symbol list`、`symbol get`、`net list`、`board check`、`footprint list`、`track list` | 不用确认 |
| `change` | `symbol create`、`symbol update`、`symbol move`、`pin update`、`net update`、`netlist import`、`footprint move`、`track create`、`footprint create`（登记新库时） | 两步确认 |
| `change`，不可撤销 | `symbol delete`、`track delete` | 危险级 |
| `artifact` | `schematic create`、`schematic export`、`schematic render`、`board create`、`board export`、`board render`、`footprint create` | 直接执行；目标文件已存在时两步确认 |

一项常见任务，比如修改一个工程并导出投板文件，授权范围给工程文件夹的 `change` 和 `artifact` 即可；需要删除时才设 `dangerous: true`。

## 关于本工具

KiCad 是一套免费的电子设计软件：原理图、符号库和封装库、印制电路板和投板文件。kicad-cli 让 Agent 在工程文件上完成其中有确定结果的操作，检查和导出用 KiCad 自己的：

- 定义器件：在工程的 `.pretty` 库里定义封装，在工程的 `.kicad_sym` 库里定义符号；
- 画和改原理图：放符号、改字段、移动符号、把引脚接到网络、给网络改名，并运行 KiCad 的 ERC；
- 从原理图新建 PCB，把之后的原理图改动同步过去，设置板框，摆放和旋转封装，移动丝印位号，用走线、过孔和铺铜布线，并运行带原理图一致性检查的 KiCad DRC；
- 导出 Gerber、钻孔文件、网表，以及 PDF 或 SVG 图纸。

每次修改都先预览、再确认，写完从保存的文件读回核对。kicad-cli 在文件模式下工作：改一个文件前，先在 KiCad 里关掉它。它没有自己的布局布线算法：器件和走线放在哪里由 Agent 决定，结果由 KiCad 的 DRC 检查。

```bash
kicad-cli symbol list --doc D:/work/demo/demo.kicad_sch
kicad-cli footprint move --doc D:/work/demo/demo.kicad_pcb --id R1 --x 25mm --y 18mm
kicad-cli board check --doc D:/work/demo/demo.kicad_pcb
```

## 配置

| 变量 | 默认值 | 含义 |
|---|---|---|
| `KICAD_CLI_STATE_DIR` | `~/.kicad-cli` | 状态目录：操作记录、锁、更新检查的缓存和团队知识的登记 |
| `KICAD_CLI_SCOPE_FILE` | 状态目录里的 `scope.json` | 授权范围文件（见「授权」） |
| `HTTPS_PROXY`、`HTTP_PROXY`、`NO_PROXY` | 未设置 | `update` 和 `doctor` 用的代理，只有这两个命令访问 npm 仓库 |
| `KICAD_CLI_FAKE_HOST` | 未设置 | 只用于测试：把每次调用发给模拟宿主而不是 KiCad，并把每个结果标为模拟结果 |

### 怎样找到 KiCad

kicad-cli 自己读写 KiCad 的文件，检查、导出和生成最新网表时运行 KiCad 自带的命令行程序。这个程序和本工具同名，所以 kicad-cli 从不只凭 PATH 找它：

1. 设置了 `KICAD_CLI_KICAD_DIR` 时，它就是 KiCad 的安装文件夹（Windows 上是有 `bin/kicad-cli.exe` 的文件夹，macOS 上是 `KiCad.app`，Linux 上是 `bin/kicad-cli` 的前缀），只用这一个。
2. 否则在标准安装位置找：Windows 的 `Program Files\KiCad\<版本>` 和 `%LOCALAPPDATA%\Programs\KiCad\<版本>`，macOS 的 `/Applications/KiCad/KiCad.app`，Linux 的 `/usr` 和 `/usr/local`。
3. 最后才看 PATH 里的 `kicad-cli`，而且它必须在 KiCad 的安装目录里、不是本工具自己。

版本由 KiCad 的程序报告（`kicad-cli version`）；支持 KiCad 10.0.6 到 11 以下。kicad-cli 不使用正在运行的 KiCad。`kicad-cli doctor` 会显示找到的安装位置。

| 变量 | 默认值 | 含义 |
|---|---|---|
| `KICAD_CLI_KICAD_DIR` | 未设置 | KiCad 不在标准位置时，指向它的安装文件夹 |

## 卸载

<!-- driver:spec-uninstall:start -->
趁 npm 包还在、能列出 Skill 名称时，删除已安装的 Skill。用 bash：

```bash
for skill in "$(npm root -g)/@fateforge/kicad-cli/skills/"*; do rm -rf ~/.agents/skills/"$(basename "$skill")"; done
```

用 PowerShell：

```powershell
Get-ChildItem "$(npm root -g)\@fateforge/kicad-cli\skills" | ForEach-Object { Remove-Item -Recurse -Force "$HOME\.agents\skills\$($_.Name)" -ErrorAction SilentlyContinue }
```

复制过 Skill 的其他 Agent Skill 目录，如 `~/.claude/skills/`，也照此删除。然后卸载 npm 包：

```bash
npm uninstall -g @fateforge/kicad-cli
```

在 Windows 上，如果 npm 全局 `node_modules` 文件夹（`npm root -g`）里有 `.kicad-cli-trash`，也把它删掉；状态目录在另一块磁盘上时，`update` 会把旧的程序文件放在那里，等下次运行再删。

最后删除状态目录：`~/.kicad-cli`，或 `KICAD_CLI_STATE_DIR` 指定的文件夹。里面有操作记录、锁、默认位置的授权范围文件和团队知识的登记；想保留记录就留着它。

```bash
rm -rf ~/.kicad-cli
```

```powershell
Remove-Item -Recurse -Force "$HOME\.kicad-cli"
```
<!-- driver:spec-uninstall:end -->

## 开发

<!-- driver:spec-development:start -->
kicad-cli 用 Python 写成，建立在 `kicad_cli/_driver/` 里的驱动运行时之上；这是固定副本，从不修改。领域命令在 `kicad_cli/commands/`，适配层在 `kicad_cli/adapter/`。

```bash
python -m pip install --require-hashes --no-deps -r requirements-dev.txt
python scripts/spec.py check
python -m unittest discover -s tests
python scripts/spec.py suites
```

测试和一致性测试都用模拟宿主，从不需要 KiCad。工作流程、行为矩阵和真实软件运行见 `CONTRIBUTING_zh.md`。
<!-- driver:spec-development:end -->

## 链接

<!-- driver:spec-links:start -->
- 规范：[tool-driver-cli-spec 1.2.0](.agent/)，固定副本在 `.agent/`
- [CHANGELOG.md](CHANGELOG.md)：每个版本的改动（英文）
- [SECURITY_zh.md](SECURITY_zh.md)：安全模型，以及怎样报告漏洞
- [CONTRIBUTING_zh.md](CONTRIBUTING_zh.md)：怎样参与贡献
- [docs/APPLICATION_zh.md](docs/APPLICATION_zh.md)：KiCad 的能力调查
- [docs/COMPATIBILITY_zh.md](docs/COMPATIBILITY_zh.md)：kicad-cli 在哪里能用
- [docs/E2E_zh.md](docs/E2E_zh.md)：真实软件验收及其记录
- 问题跟踪：<https://github.com/fatecannotbealtered/kicad-cli/issues>
<!-- driver:spec-links:end -->
