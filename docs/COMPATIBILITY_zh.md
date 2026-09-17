# 兼容性

[English](COMPATIBILITY.md) · [中文](COMPATIBILITY_zh.md)

`kicad-cli` 用三条不同的路子跟 KiCad 打交道，这三条路的寿命不一样。这里记录
哪些版本是真跑过的，以及每条命令压在哪条路上。

## 已验证的后端版本

| KiCad | 状态 | 说明 |
|-------|------|------|
| 10.0.6 | 已验证 | `reference` 里的每条命令都由测试套件在这个版本上实跑，平台 Windows。 |
| 10.0.x 其他补丁号 | 预期可用 | 文件格式和 `pcbnew` API 面相同。未实测。 |
| 9.x | 未验证 | 板文件格式不同。`sch relink` 刻意按文本改板而不是走 `pcbnew` 保存——用 KiCad 10 的 `pcbnew` 保存一个 KiCad 9 的文件，会顺带把它迁移成 10 的格式，而调用方要的是修复一条链接，不是迁移他的文件。 |
| 11.x | 会坏 | 见下。 |

实际用的是哪个版本，由 `kicad-cli context` 报告——它会解析出解释器和官方二进制
并把两者都打出来。不要靠推断。

## 三条路，以及每条命令走哪条

| 路 | 方式 | 谁在用 | 寿命 |
|----|------|--------|------|
| 文件格式 | 直接读写 `.kicad_pcb` / `.kicad_sch` 文本 | `sch link`、`sch relink` | 稳定。不依赖任何 KiCad API。 |
| `pcbnew`（SWIG） | 跑在 KiCad 自带的 Python 下 | `board audit`、`board plane`、`board parity`、`board route`、`board rewidth`、`board widen`、`board stitch`、`board move`、`fab *` | **KiCad 11 计划移除。** |
| 官方二进制 | 调 KiCad 自己的 `kicad-cli` | `sch audit`、`sch sync-preview` 背后的 ERC/DRC 裁判，以及每条写命令的自验 | 稳定。 |
| IPC API | 通过 `kipy` 连 KiCad 的 API 服务 | `board live` | 年轻。需要 KiCad 开着，且在「偏好设置 → KiCad API」里启用。 |

SWIG 那一行就是敞口。大多数命令压在它上面，而 KiCad 已宣布移除它。真到那天，
这些命令得迁到 IPC API——而 IPC 今天既不能新建或打开文档，也不能读 DRC 结果，
更不能从库里放置封装，所以这不是一次平移。

## 已知的上游缺口

列在这里是因为每一条看起来都像本工具的 bug，其实不是：

- **没有任何 IPC 命令能新建或打开文档。** 已注册的处理器里一个都没有。
  `board live` 操作的是用户已经打开的板。
- **`ParseAndCreateItemsFromString` 在 10.0.6 里注册了但没实现。** 它接受
  s-expression 文本，返回成功，什么也不创建。`UpdateBoardStackup` 同样如此。
  验证要靠读回，不能看状态码。
- **没有任何 IPC 命令能读 DRC 结果。** DRC 自验走官方二进制。
- **「Update PCB from Schematic」没有无头入口。** KiCad 实现了它，SWIG 没绑定，
  官方 CLI 也没有对应子命令。**预测**它的结果是纯计算，`sch sync-preview` 做的
  就是这件事；**执行**它仍然要图形界面。
- **`pcb drc --schematic-parity` 按位号匹配**，而更新对话框按 uuid 路径匹配。
  一块链接全断、位号全对的板，在它眼里完全同步。

## 平台

| 平台 | 状态 |
|------|------|
| Windows | 完整验证。开发机：整套测试对着真 KiCad 跑，冻结的二进制也做了冒烟。 |
| macOS、Linux | 部分验证。CI 在 `ubuntu-latest` 和 `macos-latest` 上跑 Python 3.10 / 3.11 / 3.12：54 过、47 跳。过的是所有不需要 KiCad 的部分——信封、参数门禁、schema、退出码，以及整套替身上游测试。跳的是每一条需要真 KiCad 的测试，因为 runner 上没有。所以工具在三个平台上都能启动、解析、正确作答；而 `pcbnew` 在那两个平台上行为是否一致，仍未证明。 |
