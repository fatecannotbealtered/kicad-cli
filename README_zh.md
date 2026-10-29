# kicad-cli 1.0.1

面向 AI 助手的 KiCad 工程命令行工具，支持项目建库、原理图编辑、PCB 创建与局部修改、只读检查，以及 Gerber、钻孔和图面导出。

## 安装和依赖

从 [Releases](https://github.com/fatecannotbealtered/kicad-cli/releases/latest) 下载与系统、处理器匹配的完整包。另需安装 KiCad 10.0.6 或更新的 10.0 补丁版本，以及符号库和封装库。

| 系统 | 架构 | 系统基线 |
|---|---|---|
| Windows | x64 | 可运行 KiCad 10.0.6 或更新的 10.0 补丁版本 |
| macOS，Apple Silicon | arm64 | macOS 14 |
| macOS，Intel | x64 | macOS 15 |
| Linux | x64 | Ubuntu 24.04 / glibc 2.39 |

直接运行包内二进制即可，已包含 Python 和运行依赖，无需开发用 Python、Node、编译器或 SDK；可选的 Node 安装壳另需 Node。Windows 为 `bin/kicad-cli.exe`，macOS/Linux 为 `bin/kicad-cli`。暂不提供 Windows ARM64 或 Linux ARM64 包。

在 `runtime-config.json` 中选择已有 KiCad 和自有 profile，运行 `--check-environment --compact`。完整命令与升级步骤见[安装说明](docs/INSTALLATION.md)。升级时保留配置、知识库路径和 CAD 工程，先核对新包再切换现有命令入口。

## 选择六模块

| 工程任务 | Skill安装名 | CLI逻辑模块 |
|---|---|---|
| 环境、能力发现、跨阶段分流 | kicad-cli | kicad-cli |
| 局部symbol/footprint、pin/pad映射及来源 | kicad-library | kicad-library |
| 空白/精确接线、层级复用、属性、变换、单页导出 | kicad-sch | kicad-sch |
| 首板生成、同步、有限布局、显式直线铜修改 | kicad-layout | kicad-pcb |
| 只读SCH/PCB/跨图检查、重复铜和路径诊断 | kicad-review | kicad-review |
| Gerber、钻孔及PCB SVG/PDF导出 | kicad-fabrication | kicad-fabrication |

完整原生包包含六模块；也可单独安装所需 Skill，例如：

```sh
npx skills add fatecannotbealtered/kicad-cli --skill kicad-cli -y -g
```

每个 Skill 均需保留完整目录和资源。PCB 操作加载 `kicad-layout`，运行时模块 ID 为 `kicad-pcb`。先运行 `reference --index`，再按任务读取 `reference --command "<命令路径>"`；用 `modules`、`context` 和 `doctor` 核对命令与环境。

## 最短工程路线

1. 从`reference --index`选择所需命令，再读`modules`、`context`及selected reference。按模块内共享规则建立本次已授权的有限scope，profile和输出也放在自有范围中。只读请求不自动变为修改。
2. 从事前电气意图和资料建立项目局部库。用公开library build/inspect核对来源、编号/类型、pad尺寸和映射；封装参数用整数nm网格，单位仍为mm。全局库不作为临时目录。
3. 公开sch create初始化，再查询实际symbol/pin和snapshot，用sch edit建立真实电源、地、输入、输出及外围连接。保存后sch export导出当前单根页网表和完整图面；初始blank的空网表不会随编辑自动更新。
4. board from-netlist创建新板。匹配PRO不存在或恰为{}时，只有明确选择初始化选项才建立默认板规则；不覆盖非空配置。先布局，再按明确端点、层、线宽、矩形和允许UUID做route patch。此入口不做自动路径搜索。
5. 首次PCB先核对并绑定SCH身份，完整同步所需字段后才建立ECO基线。目标修改预览必须只涉及声明对象；每次确认后新进程重开，核对连接及非目标内容。
6. board check/sch check是自定义saved-file检查，与官方DRC/ERC不同。route patch候选执行官方DRC比较。保留检查范围、已有忽略/排除项和finding身份，不能只比较数量。
7. 用fabrication模块导出，核对实际文件、层与钻孔。导出成功、原生无finding或图面可读不等于制造或上电签核。

## 本版承诺与限制

准确命令和参数以当前 `modules`、`reference` 及[兼容性说明](docs/COMPATIBILITY.md)为准。

已支持单unit矩形symbol/正面矩形SMD建库、真实接线SCH、scalar层级复用、单根页网表/SVG/PDF、首板/字段同步、零度正面SMD有限平移、明确直线patch/重复铜子集、保存重开及基本制造文件。本版没有自动布线、密集逃线、Push/Shove、新via/arc或via删除、任意板框/层叠编辑、THT/custom pad、通用实例cache刷新、层级SCH导出或装配BOM/贴片坐标新能力。

macOS/Linux 暂不支持带 `candidate-out` 的 board sync，以及会发布新源文件的增量原理图层级批次。修改库文件不等于刷新既有实例。多文件保存逐个完成，没有跨文件原子回滚。遇到unknown/partial先查原operation和实际文件，不换ID重放。电气功能、SI/EMI、制造和上电仍需独立工程检查。

## 常见故障

- E_CONFIG：核对format_version=1、合法KiCad路径及完整匹配包，运行--check-environment；不要绕开SDK PIN。
- E_NOT_FOUND：检查JSON输入相对路径。library sources.file相对请求JSON目录；导出目标目录要先存在，正式输出文件须不存在。
- E_FORBIDDEN：按公开scope schema修正本次有限根目录、输出及UTC时间格式，不扩大到其它现场。
- E_CONFLICT：重新查询当前文件、原operation和snapshot，再取得新预览。不得复用旧token。
- 单独安装模块时必须复制整个目录；manifest不符或CLI绑定不符就停止，重新取得匹配版本。缺fp-lib-table时只从已选合法KiCad模板复制到自己的profile，不修改全局库。

## 安装迁移和维护

按[安装说明](docs/INSTALLATION.md)更新现有命令入口和六模块，保留原配置与知识索引。不要把旧操作状态、scope 或确认 token 当成新版授权重放；CAD 工程不会因工具升级自动修改或回滚。

## 知识配置与仓库结构

知识通过 AI 显式读取配置的文件/URL 索引接入；升级保留已有知识库路径。
参见[知识配置](docs/KNOWLEDGE.md)与[维护边界](docs/MAINTENANCE.md)。
产品代码在 kicad_cli，Skill 单源在 docs/modules，生成模块在 skills；tests 与 bench
保留可复现测试和夹具，不是使用者安装前提。

## 许可证

自有代码与 Skills 使用 [Apache-2.0](LICENSE)。固定 SDK、继承基线及其他第三方许可见 [NOTICE_zh.md](NOTICE_zh.md)。
