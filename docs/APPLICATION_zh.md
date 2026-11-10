# KiCad 能力调查

[English](APPLICATION.md) | 中文

本调查记录 KiCad 能做什么、能怎样自动化。它在设计命令之前填写，每项能力都有处置：kicad-cli 有命令提供的，处置为 `provided`。

## 软件

- 名称：KiCad
- 厂商：KiCad 开发者（https://www.kicad.org）；KiCad 是自由软件，许可为 GPL-3.0-or-later
- 调查过的版本：Windows x64 上安装的 KiCad 10.0.6（只看了程序的帮助和版本输出），以及 KiCad 10.0 源码分支到 10.0.8；kicad-cli 支持 >=10.0.6 <11

KiCad（设备模式下是设备的驱动或 SDK）能运行的操作系统和 CPU 架构；kicad-cli 的平台包按这张表决定：

| 操作系统 | CPU 架构 |
|---|---|
| Windows | x64, arm64 |
| macOS | x64, arm64 |
| Linux | x64, arm64 |

出处：https://www.kicad.org/download/windows/（Windows 10 和 11 的 x86_64 与 arm64 安装包）、https://www.kicad.org/download/macos/（一个通用磁盘映像，macOS 12 及以上）、https://www.kicad.org/download/linux/（发行版软件包和 Ubuntu PPA，支持 amd64 与 arm64；Flathub 支持 x86_64 与 aarch64），2026年11月4日查阅。

## 自动化接口

KiCad 提供的每种接口，以及调用它的代码必须在哪里运行。CLI 能直接调用的接口，由 `kicad_cli/adapter/` 里的适配层调用。只能在 KiCad 的进程里或主线程上调用的接口，需要放在 `plugin/` 里的宿主组件。

| 接口 | 从哪里调用 | 说明 |
|---|---|---|
| KiCad 的文件格式：`.kicad_sch`、`.kicad_sym`、`.kicad_pcb`、`.kicad_mod`（s-expression）、`.kicad_pro`（JSON）、`sym-lib-table`、`fp-lib-table`、`.kicad_dru` | CLI | 文档见 https://dev-docs.kicad.org/en/file-formats/。KiCad 10.0 写出的 `.kicad_pcb` 和 `.kicad_mod` 版本是 20260206，`.kicad_pro` 是第 3 版（出处：10.0 分支的 `pcb_io_kicad_sexpr.h`、`sch_file_versions.h`、`project_file.cpp`）。kicad-cli 自己读写这些文件（`kicad_cli/adapter/fileformat/`），所有修改都这样完成。 |
| KiCad 自带的命令行程序 `kicad-cli`（在 KiCad 安装目录里找，从不只凭 PATH） | CLI | `sch erc`、`pcb drc`（JSON 报告；`--schematic-parity`、`--refill-zones`）、`sch export netlist/pdf/svg/bom`、`pcb export gerbers/drill/pdf/svg/dxf/pos/step/...`、`version`（https://docs.kicad.org/10.0/en/cli/cli.html）。导出时用 `--check-zones` 在内存里重新填充铺铜。它和本工具同名。kicad-cli 的所有检查和导出都用它。 |
| IPC API（基于 NNG 的 protobuf；客户端库 kicad-python，即 `kipy`，MIT 许可） | CLI | 连接正在运行的 KiCad。默认关闭（首选项 > 插件 > 启用 KiCad API）。KiCad 10 里只覆盖 PCB 编辑器；原理图那一侧只回答 `GetOpenDocuments`。模态对话框和编辑器正忙都返回 `AS_BUSY`。出处：https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/，10.0 分支的 `pcbnew/api/api_handler_pcb.cpp`、`eeschema/api/api_handler_sch.cpp`；kipy 0.8.0。kicad-cli 1.0 没有会话模式，不用它。 |
| pcbnew Python API（SWIG），在 KiCad 自带的 Python 里运行 | CLI | 9.0 起弃用，KiCad 11 删除（https://dev-docs.kicad.org/en/apis-and-binding/pcbnew/）；只能在 KiCad 自带的解释器里运行。不用：它原来负责的绘图、钻孔文件和铺铜填充，KiCad 自带的程序都能做。 |
| PCB 编辑器里的动作插件 | 只能在进程里 | 基于已弃用的 SWIG API，需要宿主组件。不用。 |

## 对象模型

依据 KiCad 10.0 的手册和文件格式；这些名称就是命令的名词。

- 工程（project）：`.kicad_pro` 文件，连同它的根原理图、PCB、工程库表（`sym-lib-table`、`fp-lib-table`）、设计规则（`.kicad_dru`）和网络类。
- 原理图（schematic）：一张或多张图纸（`.kicad_sch`），组成层次结构；图纸上有符号、导线、结点、标签（本地、全局、层次）、不连接标记、子图符号、文字和图形。
- 符号（symbol）：放在图上的库符号实例，有位号（`R1`）、字段（Value、Footprint、Datasheet 和自定义字段）、单元和引脚；在符号库里则是符号的定义。
- 引脚（pin）：符号的连接点，按编号区分，有名称和电气类型。
- 网络（net）：一组连在一起的引脚，由标签或电源符号命名，没有时由某个引脚生成名字。
- 库（library）：符号库（`.kicad_sym`，一个文件放多个符号）或封装库（`.pretty` 文件夹，每个封装一个 `.kicad_mod`），在用户或工程的库表里按昵称登记。
- PCB（board）：`.kicad_pcb` 文件，有层、板框（Edge.Cuts）、封装、走线、圆弧、过孔、铺铜、文字和图形。
- 网表（netlist）：原理图的连接关系写成的文件，用来新建和更新 PCB。
- 封装（footprint）：板上放置的封装（位号、位置、角度、正反面、锁定、带网络的焊盘），或库里的封装。
- 走线（track）：铜层上的直线段或圆弧，有线宽和网络；过孔连接不同的层。
- 铺铜（zone）：有外形、网络和已存填充多边形的铜区域。

## 单位与坐标

- PCB 和封装：内部单位 1 nm；文件里存毫米，最多 6 位小数。原理图和符号：内部单位 100 nm；文件里存毫米，最多 4 位小数。出处：`include/base_units.h`、https://dev-docs.kicad.org/en/file-formats/sexpr-intro/。
- 原点在页面左上角；x 向右增大，y 向下增大。符号库里的图形按 y 向上存。绘图和钻孔文件可以用钻孔/放置原点（辅助原点）。
- 角度用度，屏幕上逆时针为正；符号只能转 0、90、180 或 270 度。
- 原理图的连接格点是 1.27 mm；不在格点上的引脚或导线端点接不上。
- 显示单位（mm、mil、inch）是用户设置，从不写进文件；kicad-cli 要求每个物理量带单位，输出用毫米和度（DCLI-080 到 DCLI-085）。

## 线程与阻塞

- kicad-cli 操作的是文件：不在 KiCad 主线程上运行任何东西，也不涉及 KiCad 的窗口。
- KiCad 自带的程序跑完就退出。中小设计的 ERC 和 DRC 只要几秒；大板子可能要几分钟。kicad-cli 给检查 25 秒、给导出 40 秒，保证每次调用在 60 秒内返回（DCLI-065），超时报 `E_NATIVE_FAILURE`。
- KiCad 自带的程序不弹对话框。用户配置不存在时（从没启动过 KiCad），全局库表是空的，检查会报库相关的警告。

## 读回

- 修改后重新解析保存的文件，逐个字段和预览比对：PCB 坐标按整数纳米比（容差 1 nm），原理图坐标按文件的 100 nm 精度比，角度和文字要完全一致。
- ERC 和 DRC 的结果是 KiCad 的 JSON 报告，按类型、严重程度、对象 UUID 和位置区分。退出码为 0 只说明检查跑完了。
- 导出：产出的文件存在；运行时记下大小和 SHA-256。文件内容由 KiCad 生成，工具不解析。

## 保存与撤销

- 写文件就是保存（DCLI-213）。kicad-cli 整个替换文件（写临时文件再改名覆盖）。一次修改写多个文件时（库和库表、几张子图），按顺序写，不是原子操作。
- 在 KiCad 编辑器之外写的文件，KiCad 没法撤销。kicad-cli 没有撤销；移动或改字段可以用同一条命令改回旧值。
- 没有另存为。导出写的是新文件。

## 实例与并发

- 可以同时运行多个 KiCad。打开了某个文件的编辑器会在文件旁放一个锁文件 `~<文件名>.lck`（KiCad 10.0 源码 `include/lockfile.h`）。
- 编辑器开着某个文件时，磁盘上对它的改动会在编辑器下次保存时被覆盖。所以 kicad-cli 拒绝写有锁文件的文件（`E_CONFLICT`，`file_open_in_session`：要人先关掉它），读不受影响。
- 工程师可以同时在 KiCad 里编辑别的文件。

## 许可

- KiCad 是自由软件，许可为 GPL-3.0-or-later。自动化不需要许可，KiCad 也不弹许可对话框。
- kicad-cli 把 KiCad 自带的程序作为独立进程运行，不链接 KiCad 的代码。

## 能力

每项能力占一行，有一个在本调查内唯一的编号 `CAP-nnn`，以及一个处置：`provided`（有领域命令提供）、`deferred`（值得提供，留到以后的版本）、`excluded`（不会提供，比如不安全或无法可靠地自动化）或 `unknown`（还不知道 KiCad 是否允许）。每行写明能用到这项能力的接口；处置不是 `provided` 的，用一句话说明原因。

| 编号 | 能力 | 接口 | 处置 | 原因 |
|---|---|---|---|---|
| CAP-001 | 读工程：文件、库表、设计规则和网络类 | 文件格式 | provided | - |
| CAP-002 | 读原理图上的符号：位号、字段、引脚、单元、所在图纸 | 文件格式 | provided | - |
| CAP-003 | 读连接关系：网络及其上的引脚或焊盘 | 文件格式 | provided | - |
| CAP-004 | 新建空原理图（没有工程文件时一起建） | 文件格式 | provided | - |
| CAP-005 | 在图纸上放置库符号 | 文件格式 | provided | - |
| CAP-006 | 改符号的位号、值、封装或其他字段 | 文件格式 | provided | - |
| CAP-007 | 移动或旋转符号，并保持连接 | 文件格式 | provided | - |
| CAP-008 | 删除符号 | 文件格式 | provided | - |
| CAP-009 | 用导线和标签把引脚接到网络，或标成不接 | 文件格式 | provided | - |
| CAP-010 | 建层次子图、复用子图 | 文件格式 | deferred | 以前的实现没做完 |
| CAP-011 | 用 KiCad 自己的检查器做电气规则检查 | KiCad 的 `kicad-cli sch erc` | provided | - |
| CAP-012 | 把原理图导出为网表、PDF 或 SVG | KiCad 的 `kicad-cli sch export` | provided | - |
| CAP-013 | 导出物料清单 | KiCad 的 `kicad-cli sch export bom` | deferred | 还没有提供；需要单独的真机验证 |
| CAP-014 | 列出工程能用的库及库里的内容 | 文件格式 | provided | - |
| CAP-015 | 新建工程库并登记到工程库表 | 文件格式 | provided | - |
| CAP-016 | 定义库符号：一个单元、矩形外框、逐个列出的引脚 | 文件格式 | provided | - |
| CAP-017 | 定义库封装：正面矩形 SMD 焊盘、外形和占位框 | 文件格式 | provided | - |
| CAP-018 | 用最新网表和库里的封装新建 PCB | KiCad 的 `kicad-cli sch export netlist` 加文件格式 | provided | - |
| CAP-019 | 按原理图更新 PCB：封装、值、网络、关联 | KiCad 的 `kicad-cli sch export netlist` 加文件格式 | provided | - |
| CAP-020 | 读板上的封装：位置、角度、正反面、锁定、占位框、焊盘及其类型和大小、位号文字 | 文件格式 | provided | - |
| CAP-021 | 移动和旋转封装，写入前用 KiCad 的 DRC 检查 | 文件格式加 KiCad 的 `kicad-cli pcb drc` | provided | - |
| CAP-022 | 读走线和过孔 | 文件格式 | provided | - |
| CAP-023 | 加直线走线、多段路径和通孔过孔，写入前用 KiCad 的 DRC 检查；删除直线段和过孔 | 文件格式加 KiCad 的 `kicad-cli pcb drc` | provided | - |
| CAP-024 | 用 KiCad 自己的检查器做设计规则检查 | KiCad 的 `kicad-cli pcb drc` | provided | - |
| CAP-025 | 导出 Gerber 和钻孔文件 | KiCad 的 `kicad-cli pcb export gerbers/drill` | provided | - |
| CAP-026 | 把 PCB 绘制成 PDF 或 SVG | KiCad 的 `kicad-cli pcb export pdf/svg` | provided | - |
| CAP-027 | 改网络类及其网络分配 | 文件格式（`.kicad_pro`） | deferred | 还没有提供 |
| CAP-028 | 比对原理图和 PCB | KiCad 的 `kicad-cli pcb drc --schematic-parity` | provided | - |
| CAP-029 | 检查和绘图前在内存里重新填充铺铜 | KiCad 的 `kicad-cli`（`--refill-zones`、`--check-zones`） | provided | - |
| CAP-030 | 列出、新建和删除铺铜区域的轮廓；检查和绘图时由 KiCad 在内存里填充 | 文件格式加 KiCad 的 `kicad-cli pcb drc` | provided | - |
| CAP-031 | 自动布局 | 旧工具自写的算法 | excluded | 不是 KiCad 的功能；agent 用 footprint move 摆放器件 |
| CAP-032 | 自动布线（网格布线器、Freerouting） | 旧工具自写的布线器；Freerouting | excluded | 不是 KiCad 的功能；基准测试多数次回滚 |
| CAP-033 | 加宽走线、缩颈 | 旧工具自写的算法 | excluded | 不是 KiCad 的功能；从没验证过 |
| CAP-034 | 自动摆放丝印位号 | 旧工具自写的算法 | excluded | 15 次基准测试里有 9 到 10 次位号离别的器件更近 |
| CAP-035 | 缝合过孔 | 旧工具自写的算法 | excluded | 不是 KiCad 10 的功能；从没验证过 |
| CAP-036 | 载流和参考平面估算 | 自写的计算 | excluded | 是工程估算，不是 KiCad 的能力 |
| CAP-037 | 列出正在运行的 PCB 编辑器及其打开的板子；保存板子 | IPC API | deferred | kicad-cli 还没有会话模式 |
| CAP-038 | 修改打开的板子上的对象，每次修改一步撤销 | IPC API | deferred | kicad-cli 还没有会话模式 |
| CAP-039 | 读改正在运行的原理图编辑器里打开的原理图 | IPC API | excluded | KiCad 10 没有这个接口 |
| CAP-040 | 在运行中的 KiCad 里打开或关闭文件 | IPC API | excluded | KiCad 10 的 API 没有这个命令 |
| CAP-041 | 启动或关闭 KiCad | 启动进程 | deferred | 文件模式用不到 |
| CAP-042 | 在运行中的 KiCad 里撤销工具的上一次修改 | IPC API | excluded | API 没有撤销命令 |
| CAP-043 | 坐标文件、STEP、DXF 等其他输出 | KiCad 的 `kicad-cli pcb export` | deferred | 还没有提供；每种都要单独做真机验证 |
| CAP-044 | 把文件升级到当前格式 | KiCad 的 `kicad-cli sch/pcb upgrade` | excluded | 会原地改写用户文件；工程师保存时 KiCad 会自己升级 |
| CAP-045 | 改全局（非工程）库 | 文件格式 | excluded | 全局库所有工程共用；只改工程自己的库 |
| CAP-046 | 给网络改名 | 文件格式 | provided | - |
| CAP-047 | 找重复或重叠的走线段 | 旧工具自写的分析 | excluded | 不是 KiCad 的检查；KiCad 的 DRC 会报开口的走线 |
| CAP-049 | 读板框，把板框设成矩形，写入前用 KiCad 的 DRC 检查 | 文件格式加 KiCad 的 `kicad-cli pcb drc` | provided | - |
| CAP-050 | 移动和旋转封装的位号文字 | 文件格式 | provided | - |
| CAP-051 | 把封装翻到另一面 | 文件格式 | deferred | 还没有任务需要；翻面要镜像每个焊盘、图形和文字 |
| CAP-048 | 重新填充铺铜并把填充存进板子文件 | KiCad 的 `kicad-cli pcb drc --refill-zones --save-board` | deferred | 检查和导出已在内存里重新填充，用不到 |

## 风险

调查中发现的每项风险和限制，都写成 `knowledge/` 里的一个条目，或者在这里说明为什么不写。

- 本工具和 KiCad 自带的程序都叫 `kicad-cli`。→ `kicad-cli-name-clash`
- KiCad 装在标准目录以外时，不做设置就找不到。→ `kicad-not-found`
- KiCad 开着某个文件时写它，KiCad 下次保存会把改动覆盖掉。→ `files-open-in-kicad`
- 改库不会刷新已经放上去的符号和封装。→ `library-changes-do-not-refresh-instances`
- 一次写多个文件不是原子操作。→ `multi-file-writes-are-not-atomic`
- 文件里存的铺铜填充可能过时。→ `zone-fills`
- 检查需要工程目录及其中的规则和库表。→ `run-checks-in-the-project-folder`
- 大设计的检查或导出可能超出一次调用允许的时间。→ `checks-time-limit`
- 设计规则在 `.kicad_pro` 和 `.kicad_dru` 里，不在板子文件里。→ `design-rules-files`
- 网络名来自标签，除全局标签外只在本图纸内有效。→ `net-names-and-labels`
- 不在 1.27 mm 格点上的引脚或导线端点接不上。→ `schematic-grid`
- 绘图原点决定图形和钻孔落在哪里。→ `plot-origin`
- KiCad 10.0.6 在连接筋只连到孤岛时报 `starved_thermal`。→ `starved-thermal-in-10-0-6`
- 检查通过、导出成功都不等于可以投板。→ `checks-are-not-sign-off`
- 不支持 KiCad 11。→ `kicad-11-not-supported`
- Linux 上找不到 Flatpak 版 KiCad。→ `flatpak-kicad`
- IPC API 默认关闭，`AS_BUSY` 分不清对话框和正忙：不写条目；kicad-cli 不用 IPC API。
- pcbnew 的断言对话框和控制台杂讯：不写条目；kicad-cli 不用 pcbnew API。
- 在真实项目上做真机测试可能损坏它们：不写条目；`docs/E2E.md` 只允许授权的练习目标。
