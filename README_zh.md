# kicad-cli 1.0.0

CLI、原生可执行文件、六模块 Skill、CHANGELOG 和交付元数据统一为 **1.0.0**。提供 Windows x64、macOS arm64/x64、Linux x64 原生包；外部前提为合法安装的 KiCad 10.0 系列 patch 6 或更新版本。按包记录基本安装、启动及库/SCH/PCB smoke，不把完整 FCC 或所有 CAD 场景作为本轮三端出包门槛。

## 安装和依赖

从内部发布入口选择匹配 OS/CPU 的完整包。直接二进制已带 Python、校验依赖及授权的固定 SDK/spec，无需开发 SDK；Node 安装壳另需 Node。Windows 二进制为 bin/kicad-cli.exe，macOS/Linux 为 bin/kicad-cli。配置 runtime-config.json 的 kicad_root 与新的 profile_dir：macOS 选择 KiCad.app，Linux 通常选择 /usr，Windows 选择安装版本根目录。

不要覆盖工程师已有环境。首次验证可解包到新目录，直接运行其中的EXE；Hub安装请使用新的自有安装前缀。创建UTF-8的runtime-config.json，格式为：

```json
{"format_version":1,"kicad_root":"D:/已有合法KiCad根目录","profile_dir":"D:/自有工程工作区/profile"}
```

通过 KICAD_CLI_RUNTIME_CONFIG 选择配置，以新包的绝对路径运行 --check-environment --compact、reference --index 和 modules。Node 壳核对包内 OS/CPU、版本和二进制 hash；错误平台会被拒绝。既有安装不会被自动覆盖。

## 选择六模块

| 工程任务 | Skill安装名 | CLI逻辑模块 |
|---|---|---|
| 环境、能力发现、跨阶段分流 | kicad-cli | kicad-cli |
| 局部symbol/footprint、pin/pad映射及来源 | kicad-library | kicad-library |
| 空白/精确接线、层级复用、属性、变换、单页导出 | kicad-sch | kicad-sch |
| 首板生成、同步、有限布局、显式直线铜修改 | kicad-layout | kicad-pcb |
| 只读SCH/PCB/跨图检查、重复铜和路径诊断 | kicad-review | kicad-review |
| Gerber、钻孔及PCB SVG/PDF导出 | kicad-fabrication | kicad-fabrication |

安装名统一由release/SKILL-NAMES.json生成。kicad-pcb已被第三方占用，本产品PCB操作加载kicad-layout；运行时逻辑模块ID仍为kicad-pcb。已有安装的显式迁移见[安装说明](docs/INSTALLATION.md)，不自动覆盖环境。每个完整Skill目录均带自己的共享资源、manifest和binding，不依赖开发仓库的../../路径。只加载当前任务正文；先用selected reference读取实际参数/schema和分页上限。

## 最短工程路线

1. 从`reference --index`选择所需命令，再读`modules`、`context`及selected reference。按模块内共享规则建立本次已授权的有限scope，profile和试验输出也放在自有范围中。只读请求不自动变为修改。
2. 从事前电气意图和资料建立项目局部库。用公开library build/inspect核对来源、编号/类型、pad尺寸和映射；封装参数用整数nm网格，单位仍为mm。全局库不作为临时目录。
3. 公开sch create初始化，再查询实际symbol/pin和snapshot，用sch edit建立真实电源、地、输入、输出及外围连接。保存后sch export导出当前单根页网表和完整图面；初始blank的空网表不会随编辑自动更新。
4. board from-netlist创建新板。匹配PRO不存在或恰为{}时，只有明确选择初始化选项才建立默认板规则；不覆盖非空配置。先布局，再按明确端点、层、线宽、矩形和允许UUID做route patch。此入口不做自动路径搜索。
5. 首次PCB先核对并绑定SCH身份，完整同步所需字段后才建立ECO基线。目标修改预览必须只涉及声明对象；每次确认后新进程重开，核对连接及非目标内容。示例工程已真实接线，可从修改R1值开始继续编辑。
6. board check/sch check是自定义saved-file检查，不能冒充官方DRC/ERC。route patch候选有官方DRC比较；正式验收同时提供独立原生报告。保留检查范围、已有忽略/排除项和finding身份，不能只比较数量。
7. 用fabrication模块导出，读取实际文件。示例零钻孔只验证文件路线，未证明非零孔加工。不要把导出成功、原生无finding或图面可读当作制造/上电签核。

## 本版承诺与限制

准确命令范围由[release/PROFILE.json](release/PROFILE.json)和公开modules中的internal_1_0_supported_commands给出。保留在reference中的历史grid routing、自动放置、zone和功率处理命令不因本次发布自动获得资格。

已支持单unit矩形symbol/正面矩形SMD建库、真实接线SCH、scalar层级复用、单根页网表/SVG/PDF、首板/字段同步、零度正面SMD有限平移、明确直线patch/重复铜子集、保存重开及基本制造文件。本版没有自动布线、密集逃线、Push/Shove、新via/arc或via删除、任意板框/层叠编辑、THT/custom pad、通用实例cache刷新、层级SCH导出或装配BOM/贴片坐标新能力。

修改库文件不等于刷新既有实例。多文件保存逐个完成，没有跨文件原子回滚。遇到unknown/partial先查原operation和实际文件，不换ID重放。未做完整FCC、所有恢复故障、全部跨平台命令/干净新机、仿真、上电、SI/EMI或制造签核；本版是限定范围的内部beta交付。

## 常见故障

- E_CONFIG：核对format_version=1、合法KiCad路径及完整匹配包，运行--check-environment；不要绕开SDK PIN。
- E_NOT_FOUND：检查JSON输入相对路径。library sources.file相对请求JSON目录；导出目标目录要先存在，正式输出文件须不存在。
- E_FORBIDDEN：按公开scope schema修正本次有限根目录、试验输出及UTC时间格式，不扩大到其它现场。
- E_CONFLICT：重新查询当前文件、原operation和snapshot，再取得新预览。不得复用旧token。
- 单独安装模块时必须复制整个目录；manifest不符或CLI绑定不符就停止，重新取得匹配版本。缺fp-lib-table时只从已选合法KiCad模板复制到自己的profile，不修改全局库。

## 安装迁移和维护

已有内部试用安装应按[安装说明](docs/INSTALLATION.md)显式迁移，在新前缀和新 profile 验证后再切换入口；不复制旧 scope、状态或 token，不自动回滚 CAD。正式分发使用明确 allowlist，不携带大型开发证据、个人状态或临时日志。

后续按真实反馈迭代安装、诊断和工程能力。公共稳定版门禁保留，普通版本 tag 只运行验收 CI，不自动发布公共 npm 或公共 Release。

## 知识配置与仓库结构

知识通过 AI 显式读取文件/URL 索引接入，不是写死的飞书链接，也不自动登录任何平台。
参见[知识配置](docs/KNOWLEDGE.md)与[维护边界](docs/MAINTENANCE.md)。
产品代码在 kicad_cli，Skill 单源在 docs/modules，生成模块在 skills；tests 与 bench
保留可复现测试和夹具，不是工程师安装前提。公司部署配置不属于产品仓库。

## 许可证

自有代码与 Skills 使用 [Apache-2.0](LICENSE)。固定 SDK、继承基线及其他第三方许可见 [NOTICE_zh.md](NOTICE_zh.md)。
