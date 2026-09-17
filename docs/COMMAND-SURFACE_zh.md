# 命令面设计

这份清单从**硬件工程师的工作流**出发，不是从官方 CLI 的能力倒推。官方
`kicad-cli` 只有 drc / export / render 三类只读命令，那是它的范围，不是我们的
上限。

清单来自六个工作面的并行设计 + 对抗性复核（79 条候选，22 条被推翻或降级），
再由人工去重、统一命名、定序。每一条都要求有可信的实现路径。

## 可达性分级

| 分级 | 含义 | 长期风险 |
|------|------|----------|
| `fileformat` | 纯文本解析 `.kicad_pcb` / `.kicad_sch` / `.kicad_pro`，不依赖任何 KiCad API | **无** |
| `pcbnew` | 走 pcbnew 的 SWIG 绑定 | **高**：官方已宣布 KiCad 11.0 移除 SWIG |
| `official` | 必须调 KiCad 自己的二进制（今天只有 DRC / ERC / STEP 属于此类） | 中：需本机装 KiCad |
| `ipc` | 需要 IPC API，且要 GUI 开着 | 中：动作名不在稳定性承诺内 |
| `fork` | 需要我们自己构建的 KiCad | 高：合并负担 |

**设计原则：能用 `fileformat` 做的，不要用 `pcbnew` 做。** 这不是洁癖——
SWIG 有明确死期，而文件格式是 KiCad 的长期契约。今天写在 SWIG 上的每一行，
都是 KiCad 11 时要还的债。

## 已建成（21 条）

| 命令 | 类型 | 可达性 | 说明 |
|------|------|--------|------|
| `reference` `context` `doctor` `changelog` | read | fileformat | 自描述四件套 |
| `board audit` | read | pcbnew | 叠层、铺铜、按载流反算线宽、RF 禁布、细间距 |
| `board parity` | read | pcbnew + official | 板与原理图的元件/网络对账 |
| `board plane` | read | pcbnew | 逐段核验走线下方参考层有没有铜、有没有跨越平面分割。DRC 两样都不报 |
| `sch link` | read | **fileformat** | 封装是否还带着符号 UUID。在 5 个官方 demo、332 个封装上零误报 |
| `sch relink` | write | fileformat + official | 回填 UUID。路径取自 KiCad 网表，不是重建 |
| `sch audit` | read | official + fileformat | **ERC 报 0 ≠ 没问题**：把被静默的规则打开重跑，量出后果 |
| `sch sync-preview` | read | official + fileformat | 「从原理图更新 PCB」会说什么——无需开 GUI |
| `board route` | write | pcbnew | 栅格布线：repair / full / rewidth |
| `board stitch` | write | pcbnew | 铺铜孤岛缝合，带 DRC 自验回退 |
| `board rewidth` | write | pcbnew | 按目标线宽整条重布，如实上报实落宽度 |
| `board widen` | write | pcbnew | 就地加宽 |
| `board move` | write | pcbnew | 搬器件 + 庭院碰撞检查 |
| `fab gerber` `fab pdf` `fab svg` `fab dxf` | write | pcbnew | 进程内绘图，参数沿用工程既有设置 |
| `fab drill` | write | pcbnew | Excellon 钻带 + 钻孔图 + 报告，**自己跟 KiCad 的报告对账** |

`release_readiness` = **beta**。全部 20 条命令都有功能契约测试：开 `KICAD_CLI_STRICT`
实跑，断言输出字段与声明的契约逐字相同；写命令走完整闸门、作用在官方 demo 的副本上。
够不上 stable 的原因写在 `reference` 的 `reason` 里——没装 KiCad 的机器上测试只会跳过。

### 四条经验，适用于后续每一条命令

**一、先验自己的尺子。** `sch link` 第一版在真板上的四层误报，全部是拿官方 demo 对答案
时发现的：`#` 开头的电源符号、多单元器件、`board_only` 属性件，以及——我按目录 glob
收原理图，把 ecc83 目录里两个**互相独立的工程**合并了。同一个目录可以放不止一份设计。

**二、读操作可以推导，写操作要拿事实来源。** `sch link` 用自推的公式，在 351 个封装上
逐字验过，够用。`sch relink` 要写设计身份字段，就改用 KiCad 自己导出的网表——
同一份数据后面喂给更新器，中间少一层推导就少一类错法。

**三、只做被要求的那件事。** `sch relink` 第一版用 pcbnew 存盘，完整性检查报了
`E_INTEGRITY`：用 KiCad 10 存一块 KiCad 9 的板会顺手升级文件格式。**做多了和做错了
一样是失败。** 改成文本精确插入，pcbnew 退为读回校验者。

**四、模型错了，输出会静悄悄地空掉。** `board plane` 第一版按铺铜覆盖率把层分成
「平面层」和「信号层」，只分析信号层。这块板四层覆盖率都在 79%~93%，于是全被判成
平面层、一条也不分析，命令**返回成功、内容为空**。顶底层同时走信号和铺地是常规做法，
「是平面还是信号」本来就不是二分的。所以测试里那句断言是 `samples > 0`——
「检查了多少」和「发现了多少」是两个数，前者为零时后者毫无意义。

**五、契约要被强制，不能只是声明。** 加上严格模式的第一次运行就抓到 `board audit`
声明了三个从未发出的字段、漏了四个一直在发的字段。注册表把派发和描述放在一起是为了
防漂移，但没有校验时它们照样会漂。

## 曾经的陷阱：`--schematic-parity` 不能当预言机

`kicad-cli pcb drc --schematic-parity` 是 KiCad 官方的、headless 的、有 JSON 输出的
原理图↔PCB 比对，看起来正是我们要的东西。**它按位号配对，而更新器按 UUID 路径配对。**

- 只断链、位号没错（下面那块实测板修复前的状态）→ parity 报 0 条，**完全失明**
- 只改位号、UUID 还在（重新编号）→ parity 报 N 增 N 删，**纯误报**

而空 path 正是 KiCad 自己制造的状态：未匹配上的封装会被 `SetPath(KIID_PATH())` 清空。
所以谁用「按位号重新关联」跑过一次更新，之后 parity 就永远说板子是同步的。
`sch sync-preview` 因此只用重实现的 UUID 匹配，parity 仅作旁证、永不参与计数。

## 原理图 `sch`

| 优先级 | 命令 | 类型 | 可达性 | 解决什么 |
|--------|------|------|--------|----------|
| ✅ | `sch audit` | read | official | ERC 之外的那一层：被静默的规则、被当消音器用的 no-connect、单节点网络。核心断言是「ERC 报 0 条 ≠ 没问题」 |
| ✅ | `sch link` | read | **fileformat** | 板上封装是否带着原理图符号的 UUID。断链后「从原理图更新 PCB」会删光重建，一次性毁掉布局布线 |
| ✅ | `sch relink` | write | fileformat + official | 按位号回填 UUID 恢复绑定。落盘后仍需在 GUI 里验证一次增删为 0——这一步不能假装闭环 |
| P0 | `sch diff` | read | official | 两版原理图的**语义**差异。`git diff` 对 `.kicad_sch` 无用：挪一个器件就是几十行坐标噪声 |
| P1 | `sch bom` | read | fileformat | BOM 可采购性门禁，不是导出 BOM。填满 ≠ 能下单 |
| P1 | `sch derate` | read | fileformat | 无源器件电应力：电容额定电压 vs 轨电压。轨电压推断要标 inferred/declared |
| P2 | `sch renumber` | write | fileformat + pcbnew | 位号重排，两侧同步。硬前置：断链时必须拒绝执行 |
| P2 | `sch policy` | write | fileformat | ERC 严重度矩阵对齐到可版本化基线：哪些规则不许关 |
| P2 | `sch intent` | read | **fileformat** | 抽取图纸里的设计意图文本，与实测量并排摆出。只给证据不下结论；输出进 `untrusted_fields` |

## 板级检查 `board`（只读）

| 优先级 | 命令 | 可达性 | 解决什么 |
|--------|------|--------|----------|
| P0 | `board signoff` | 混合 | 投板放行判决：聚合 audit / parity / DRC / 平面 / 去耦，一条命令给 go 或 no-go |
| ✅ | `board plane` | pcbnew | 已建成。在一块真实的四层功放板上实测：88.9% 长度有铜支撑，462 段缺口归并成 20 个位置，6 段跨越 +3V3/VSYS 分割 |
| P0 | `board width` | pcbnew | 按 IPC-2221 反算所需线宽，与**实际落下的**宽度比对。网络类写着 1.2 mm 不等于板上是 1.2 mm |
| P0 | `board topology` | pcbnew | 比对「引脚的物理顺序」与「它要连的器件的物理顺序」。顺序颠倒 = 走线必须互相交叉 = 单层上无解 |
| P0 | `board routing` | pcbnew | 布线收敛度量**加归因**：每条未连接项给出原因分类，而不只是报个数 |
| P0 | `board via` | pcbnew | 过孔策略：换层孔到最近伴地孔的距离、过孔载流、缝合密度 |
| P0 | `board decouple` | pcbnew | 每个 IC 电源脚到最近去耦电容的环路面积 |
| P0 | `board diff` | pcbnew | 两版板的语义差异。板文件 70% 的字符是铺铜多边形，文本比对彻底失效 |
| P1 | `board dfm` | pcbnew | 按厂商能力档案预检：环宽、孔径比、最小线宽线距 |
| P1 | `board fanout` | pcbnew | 细间距出脚可行性：在「只有焊盘、没有走线」的空板上测每根脚能否出得来 |
| P1 | `board place` | pcbnew | 庭院重叠、器件朝向、去耦距离 |
| P1 | `board impedance` | pcbnew + fileformat | 从物理叠层读介质厚度与 εr，闭式解算特征阻抗 |
| P1 | `board length` | pcbnew | 逐网络实际长度（含过孔 z 向）、差分偏斜 |
| P2 | `board coupling` | pcbnew | 同层平行走线耦合与跨层广播 |
| P2 | `board thermal` | pcbnew | 散热焊盘实连铜面积、过孔阵列 |
| P2 | `board copper` | pcbnew | 逐层铜箔占比与象限分布，报层间失衡（影响翘曲） |

## 板级修改 `board`（写，全部带 DRC 自验回退）

| 优先级 | 命令 | 可达性 | 状态 |
|--------|------|--------|------|
| ✅ | `board route` | pcbnew | 已迁入，带契约测试 |
| ✅ | `board stitch` | pcbnew | 已迁入。真实案例里 13 座孤岛上坐着焊盘却无过孔 |
| ✅ | `board rewidth` | pcbnew | 已迁入，实落宽度如实上报 |
| ✅ | `board widen` | pcbnew | 已迁入 |
| ✅ | `board move` | pcbnew | 已迁入 |
| P1 | `board viafix` | pcbnew | 为信号换层孔补伴地回流孔 |
| P2 | `board ripup` | pcbnew | 事务性撕线：按网络 / 区域 / DRC 违规 |

**所有写命令的共同约定**：dry-run → confirm（**预览必须跟着 token 一起返回**——
让人确认一件他看不见的事，比没有闸门更糟）；执行后跑 DRC；凡是引入新 error 的部分
按粒度回退并如实上报「没做到什么」；**目标工程有 `~*.lck` 锁文件时直接拒绝**——
KiCad 开着时它内存里是整块板，下一次 Ctrl+S 会把我们写的东西无声盖掉。
这几条是这套工具敢在无人值守下跑破坏性操作的唯一依据。

## 制造 `fab` / `panel`

| 优先级 | 命令 | 类型 | 可达性 | 解决什么 |
|--------|------|------|--------|----------|
| ✅ | `fab drill` | write | pcbnew | 已建成，并与 KiCad 自己的钻孔报告对账后才放行 |
| P0 | `fab check` | read | pcbnew | 制造域体检。设计域有 DRC 兜底，制造域今天完全没有 |
| P0 | `fab release` | write | 混合 | 一条命令产出自洽的完整投产包 + 可验证 manifest，产出前先过门禁 |
| P0 | `fab verify` | read | pcbnew | 拿一个已存在的包跟板文件对账：我手上这包，是不是这块板 |
| P1 | `fab place` | write | pcbnew | 贴片坐标，按目标板厂的列名与原点约定 |
| P1 | `fab stencil` | read | pcbnew | 按 IPC-7525 核算钢网开孔面积比 |
| P1 | `fab quote` | read | pcbnew | 询价单要填的每一项直接从板文件抽出来 |
| P1 | `fab vendor` | read | fileformat | 板厂能力档案（原名 `fab profile`，按 §12.4 改为名词一致） |
| P1 | `panel build` | write | pcbnew | 拼板：阵列、工艺边、基准点、连接方式 |
| P2 | `panel check` | read | pcbnew | 拼板级 DFM |
| P2 | `fab etest` | write | pcbnew | IPC-D-356 裸板电测网表与可测性报告 |
| P2 | `fab silk` | write | pcbnew | 修丝印可制造性：压在阻焊开窗上的丝印移开 |

## 库与元件 `lib`

| 优先级 | 命令 | 类型 | 可达性 | 解决什么 |
|--------|------|------|--------|----------|
| P0 | `lib drift` | read | pcbnew | 板上封装与库母本的**分类型分严重度** diff。DRC 那 47 条 `lib_footprint_mismatch` 今天没有分辨率，全是一个级别 |
| P0 | `lib model` | read | pcbnew | 3D 模型引用审计：环境变量展开后文件是否真实存在 |
| P0 | `lib padstack` | read | pcbnew | 焊盘栈可制造性：逐轴环宽、孔到铜、钻孔规格清点 |
| P1 | `lib pinmap` | read | fileformat + pcbnew | 符号引脚号集合 vs 封装焊盘号集合。这类错 ERC 过、DRC 过、网表自洽，但实物上那根脚没连 |
| P1 | `lib upstream` | read | pcbnew | 三方漂移：工程快照库 vs 上游官方库 vs 板上实例 |
| P1 | `lib sync` | write | pcbnew | 按位号 / 类别选择性地把库母本更新进板上封装 |
| P2 | `lib snapshot` | write | pcbnew | 生成可往返的自包含封装快照库 |

## 工程与协作 `proj`

| 优先级 | 命令 | 类型 | 可达性 | 解决什么 |
|--------|------|------|--------|----------|
| P0 | `proj report` | read | 混合 | 把板差异与原理图网表差异合成一份评审用变更清单，按影响面分组 |
| P1 | `proj gate` | read | 混合 | 发布前机器判定：DRC / ERC / audit / parity 一次跑完给结论 |
| P1 | `proj check` | read | fileformat | 交接可打开性：lib-table、相对路径、缺失依赖 |
| P1 | `proj mcad` | write | 混合 | 给结构的一次性交付：STEP、板框 DXF、安装孔表、限高 |
| P1 | `proj mcad-check` | read | pcbnew | 拿结构给的 DXF 回头校板：板框、安装孔、限高是否一致 |
| P2 | `proj markup` | write | pcbnew | 把差异画成评审图（内存副本的注释层），不改设计数据 |

## 被推翻或降级的

对抗性复核推翻了 22 条，多数不是「想法不好」而是「证据或标签不实」。值得记住的几类：

- **`board merge`**（多人改同一块板的三方合并）：只读冲突报告可行，**写路径今天不可能**。
- **`board waive` / `board baseline`**（评审豁免与基线冻结）：想法成立，但当时的写门禁设计不符合
  §7，需要先补签名与过期机制，否则豁免本身会变成绕过检查的后门。
- **`length tune`**（自动蛇形绕线补长度）：无实现、无原型，标 unverified 是诚实的。
- **`lib snapshot` 的「单个 .pretty 内保留原始 nickname 身份」**：`not_possible`。

## 实现顺序

1. ~~**`sch link`**~~ ✅ 在一块真实的四层功放板上实测 **113/113 全部断链**，
   这是悬在现有布线成果上的一把刀。
2. ~~**`sch relink`**~~ ✅ 刀已拆掉：113/113 回填，DRC 仍为 0 error / 64 warning /
   3 未连接，与交付时一致。剩 `fab drill` 补齐制造包的缺口。
3. **迁入五个已验证的写命令**（route / stitch / rewidth / widen / move），全部带 DRC 自验回退。
4. **`board topology` / `board plane` / `board width`**——这三条是我在真实板子上
   吃过亏才知道要有的：拓扑交叉、参考平面断裂、线宽名不副实，全都是 DRC 不报的。
5. 补命令级测试，把 `release_readiness` 从 `unpublishable` 提到 `beta`。
6. 规划 SWIG → IPC 迁移，优先把 `fileformat` 能做的从 pcbnew 上摘下来。
