# 持久化边界复核

这是开发验收入口，不是产品命令。默认只校验配置、EXE 摘要和新输出目录，
打印计划后退出，不创建工程、不启动 EXE、不暂停或终止进程。

## 依赖与入口

准备 Windows、Python 3.12、固定 SDK/spec `9607a35fe0ea33fe822e1f2ad2704eae260d4018`、
KiCad 10.0.6 原生 Python/CLI/库，以及用于 SVG 只读渲染的 PyMuPDF。
SDK/spec 和 KiCad 不包含在工程师包内。复制 [配置模板](config.example.json)，填写本机绝对路径。
`configuration` 指向独立的 KiCad 配置种子目录，内含 `10.0/kicad_common.json`；
入口复制该目录，仅改副本的工作目录。`test_root` 必须是明确授权的测试根目录。
使用包的 `CANDIDATE.json` 核对 `build_source_commit` 和 `exe_sha256`，不能用版本号替代摘要。

在仓库根目录运行下列命令。工程师包中将模块名改成 `persistence_acceptance`，
并在解压根目录运行。输出目录必须是测试根目录内一个不存在的子目录。

```powershell
python -B -m bench.persistence_acceptance run --config CONFIG.json --out NEW_OUTPUT --case F5-partial-save
```

确认具备测试副本和自有进程控制授权后，才显式追加：

```text
--execute --allow-owned-termination --authorized-by "授权来源与操作者"
```

不需要故障控制的 `--case refill-changed` 只追加 `--execute --authorized-by ...`。
无害控制器自检使用 `--case controller-selfcheck` 和上述两个执行开关；
它只控制自己创建的休眠 Python 子进程，不能计作 KiCad 故障通过。
任何失败、未命中、原生异常都保留整个输出目录；不在原目录重跑。
自发原生异常立即结束当前周期，不循环至成功。`uncovered` 返回非零退出码。

## 阶段为何实际命中

[矩阵](matrix.json) 冻结触发条件和判定条件。触发仅决定何时尝试暂停，不能证明命中。
控制器创建隐藏且初始暂停的根进程，先加入专属 Windows Job 再运行。
写入者 PID 来自本次操作的签名日志；暂停前、持有进程句柄时、暂停后均核对出生时间，
同时验证 Job 归属和 EXE 路径。控制器只终止这个 Job，并等待根进程和写入者句柄进入终态。
控制码为 `0xE0424331`，与自发原生异常分开计数。异常退出的 Job 关闭也只影响其自有子树。

| 案例 | 冻结后必须观察到的事实 |
| --- | --- |
| F0 写前 | 签名日志归属正确；所有正式目标仍为基线摘要 |
| F1 保存窗口 | 绑定的 pending 内容是候选字节前缀，或正式目标已形成部分保存；必须报告实际是哪一种 |
| F2 回执丢失 | 所有正式目标为预期摘要，签名清理阶段或终态证明已保存，调用方没有完整回执 |
| F3 清理中断 | 签名 checkpoint 的实际 `phase=cleaning`，保留 cursor、pending、清理动作和归档摘要 |
| F4 外部漂移 | 自有写入者已停止后，单独记录一字节外部变化；新进程查询仍为 unknown，保护保持 |
| F5 多文件部分保存 | 至少一个正式目标为预期摘要，另一个为基线；全部目标可归属；出生时间核验通过 |

F5 使用已有合成 U4 多文件 `sch edit`：根文件 C1 `100nF→120nF`，共享子页 R10 `2k2→3k3`。
预览前分别追加 8 MiB、48 MiB 合法尾部空白并冻结所有原件、目标、预期和脚本摘要。
只观察第一次正式替换事件，最多尝试一次。预览候选由独立 S 表达式读者核验：
只允许声明的 Value 改变，拓扑、器件身份及其他结构保持。
pending 完整但正式文件均未替换不算 F5。暂停过晚也不算命中。

等待真实租约到期后，从新进程调用公开 `operation show`。
只有读回结果为可归属的 applied/not_applied/partial 才以匹配证据 resolve。
partial 恢复合同是归档原基线、保留已落盘的部分结果并登记事实，不回滚、不补写剩余文件。
未解决的 unknown/partial 阻断新写入；unknown 不猜测 applied、不重放。
F5 从不重复原确认命令；查询、恢复和后续只读预览均检查工程字节保持。

## 真正 changed 的 refill

入口复制已保存的合成 TP-S4 RC 板，冻结意图、规则、器件和填铜摘要。
唯一准备操作是公开 `board footprint move` 的预览/确认，将独立正面测试点 TP1
从 `(55,32)` 移到 `(55,34)` mm，不编辑填铜文件数据。
独立原生读回必须看到旧填铜与新焊盘相交，然后显式选择 F.Cu 区域 UUID 执行 refill。
第一次必须 changed/saved；第二次 unchanged 仅检验稳定性。

独立 S 表达式读者测量面积、实际多边形、旧/新中心覆盖及边界变化范围，
剔除填充多边形中的成对桥接边后，变化顶点只能位于预先冻结的两个局部区域。
除目标区域填铜外，其余结构必须保持：非目标区域、铜线、器件、网络和规则。
原生新进程重新加载并检查区域连续轮廓、GND 焊盘/过孔接触及 TP1 隔离；
原生 DRC 单独检查连通、间距与 thermal，不更改规则或降低严重度。
全板正反面 SVG/PNG 最后由复核者目视并另写结论；自动检查不冒充目视通过。

## 现存证据与公开摘要

先审证据，禁止为补格式重放成功写入：

```powershell
python -B -m bench.persistence_acceptance audit --evidence PRIVATE_PRIOR_ROOT --out NEW_PUBLIC_JSON
```

旧根目录须包含 `FAULT-CASE-OUTPUTS.json`、各案例的原始证据及本机签名密钥。
工具只读原件，用本机密钥验证签名，再通过字段白名单导出逐文件摘要、PID/出生/冻结时间、
退出信息、公开查询状态、恢复状态、基线归档和源码/EXE 绑定。
不导出确认令牌、密钥、签名认证值、环境、命令行、绝对本机路径或转储。
已入仓摘要在 `docs/evidence/persistence-boundaries-20261013/`。
公开摘要足以重算命中谓词；本机 HMAC 审计不等于可公开验证的数字签名。
旧控制器只在冻结时记录出生时间，新交付控制器新增暂停前后强校验；不能倒推旧试验已执行新保护。

新 F5 完成后用下列只读命令导出同样的白名单格式：

```powershell
python -B -m bench.persistence_acceptance export --kind fault --case-dir EXISTING_OUTPUT --out NEW_PUBLIC_JSON
```

refill 用 `--kind refill`。导出前须在原 `visual` 目录另存 `VISUAL-REVIEW.json`，
包括 `status=passed`、`reviewer`、UTC `reviewed_at`、逐图 `observations`、`limits`，
以及从 `REVIEW-REQUIRED.json` 复制并实际核验的 `images` 摘要；不能仅将 pending 改成 passed。
本机保留原始签名记录和全部失败；公开摘要应写入新文件，不能覆盖历史结论。
F5 的 `stage_hit` 与 `partial_recovery_passed` 分别报告，两者都成立才通过。
混合目标的 unknown 安全阻断只记 `unknown_safety_passed`，不能替代 partial 登记闭环。
判据读取实际调用回执、resolution、阻断/放行和文件摘要，不强迫 SDK 的顶层 unknown 改成新终态。

refill 导出会重新核对当前工程全集、最终冻结副本、配置及已记录的外部依赖。
原件、公开移动后、refill 保存后的板身份分别绑定 before/prepared/after；repeat 必须仍为 after。
独立观察的板摘要及其调用回执也须一致。漂移拒绝导出，不修复或覆盖原工程与证据。
没有被原记录指纹化的安装库内容仍未覆盖，不能事后补称通过。

开发检查使用 `pytest tests/test_persistence_acceptance.py tests/test_persistence_binding.py
tests/test_f5_receipts.py tests/test_public_observer.py`，不默认执行真实故障。

## 公开接口的有界复跑

[public_cases.json](public_cases.json) 固定 35 个观察点的 ID、输入、输出 Schema、错误/退出码、
原生派发和工程写入边界；旧 59 项完整 EXE 用例另行绑定，没有增加 FCC 条目。
除前述依赖外，宿主 Python 需要已有的 `jsonschema` 和 `referencing`。

```powershell
python -B -m bench.persistence_acceptance run --config CONFIG.json --out NEW_OUTPUT --case public-contract
```

默认只输出计划。已有测试副本授权时追加 `--execute --authorized-by "授权来源与操作者"`。
配置可选 `public_case_ids` 指定矩阵中的准确 ID，供新目录补验失败或未执行项；成功项不必重跑。
目标工作目录应在仓库外且含中文/空格。EXE 及 SDK/spec 取显式路径，不回退借用开发工作树。
入口只使用公开参数；预览允许隔离的状态/候选写入，禁止确认工程和创建请求的导出结果。

外部 Windows `DEBUG_PROCESS` 观察完整自有进程树，记录每个进程的出生时间、镜像摘要与退出。
原生禁止项只允许候选 EXE、系统控制台宿主，以及实际命令行已确认的 `cmd /c ver` 版本查询。
未分类子进程使验收失败；不能根据进程名字猜测无害。原生异常停止整个周期，原始记录留本机。
公开摘要不含原始命令行、确认令牌或本机绝对路径。35 个观察点和复用的旧 59 项不相加宣称全量 FCC。

## 第一版有限矩阵

[first_version_discovery.json](first_version_discovery.json) 从同一候选公开 reference 冻结 297 个请求。
索引逐路径比较、拒绝重复或遗漏，并比较元数据；每个叶子的 reference 另核对完整参数与 Schema 引用。
help 检查参数和字面默认值，错误项检查 Schema、错误/退出码、建议、完整进程观察及工程摘要。
33 条路径的迁移口径与这些行为断言分开。此矩阵不代表所有默认行为、参数组合或完整 FCC。

配置增加 `family_matrix` 为该文件的绝对路径，可用 `family_case_ids` 选择准确 ID。
调用前述 `run` 入口并选择 `--case public-family`。默认只输出计划；实际执行才加授权和 `--execute`。
输出目录必须全新。保留失败周期；成功项直接引用原证据，不通过整组重跑覆盖首次失败。

[first_version_environment.json](first_version_environment.json) 冻结 17 个环境观察点。
配置增加 `environment_matrix`，选择 `--case environment-family`；可用 `environment_case_ids` 选子集。
ACL 两项还要求 `allow_owned_permission_controls=true`，控制范围仅为新建自有 state 目录的 DACL。
必须保存原值、真实验证拒写和恢复；未完成控制器验证不能计为 EXE 拒绝通过。
文件占用只打开本控制器的独占句柄。两个占用项各有一次单独记录的公开状态库初始化，不算进 17 项。
doctor 的诊断完成与检查项通过分开。本机隔离目录不是异机或干净电脑验收。

[concurrency_cases.json](concurrency_cases.json) 给出十个状态与冲突判据。
选择 `--case bounded-concurrency`，执行还需 `--allow-owned-termination`；控制默认关闭。
A/B 是同一保存原理图的独立计划，C 使用不相交副本，三者共享状态库。
确认前冻结目标全集、变化输入、操作 ID 和授权。只有签名日志、PID 出生时间和摘要共同证明阶段时，
才能暂停自有写入者。实际租约过期决定 unknown，清理是文件阶段，不新增 SDK 顶层状态。
未命中清理阶段记未覆盖，不重复试验。旧 F4/F5 不在此入口的访问范围内。
`controller-resume-selfcheck` 仅用无害文件握手验证暂停、拒绝错误出生时间、恢复和自然退出，另行计数。

现存公开记录可以只读重审：

```powershell
python -B -m bench.persistence_acceptance.public_reaudit --case-dir OLD_PUBLIC_DIR --exe FINAL_EXE --system-root SYSTEM_ROOT --out NEW_AUDIT.json
```

包内执行时将模块前缀 `bench.persistence_acceptance` 改为 `persistence_acceptance`，从包根目录运行。
原记录失败不升级。进程未观察到、观察完整、工程摘要保持是三个独立字段。
异常地址与可获取的进程诊断只留本机，不上传原始转储；捕获失败也要记录。

重审先比较保留的 TRACE 文件实际 SHA-256 与原观察点的 `trace_sha256`，匹配后才读取其内部
输出摘要和进程事实。缺原始绑定或字节漂移均保持未证明，不用轨迹内部字段重新给自己建立绑定。
选定 reference 的 input／selection／request Schema 必须与矩阵冻结的完整定义相同；仅引用可解析
不够。冻结 reference 来源是已提交的公开合同，不能由当次待测输出生成预期。

## 候选执行异常的有界诊断

`candidate-diagnosis` 只比较三个既有失败参数：`reference --compact`、
`reference --command "board check" --compact`、`operation list --compact`。
各有 ordinary 与 debug 两个单元，最多六次，每个单元使用新副本；不确认工程写入。
普通与调试分支都在首条指令前加入自有 Job，继承 stdin，stdout／stderr 使用独立文件。
DEBUG_PROCESS 逐项记录后代创建、出生身份和退出；普通分支只做进程采样，不能据此宣称完整创建历史。

```powershell
python -B -m bench.persistence_acceptance run --config CONFIG.json --out NEW_DIAGNOSTIC_OUTPUT --case candidate-diagnosis
```

默认仅计划。实际执行需 `--execute --allow-owned-termination --authorized-by "授权来源与操作者"`。
可用 `diagnostic_case_ids` 选冻结 ID，如 `full-reference-ordinary`、`full-reference-debug`；
其余前缀为 `selected-reference`、`operation-list`。输出 `FROZEN-MATRIX.json`、各单元的
`LAUNCH.json`、`OUTCOME.json` 及原始轨迹；即使异常导致观察调用抛错，也保存轨迹绑定。
诊断入口的 `diagnostic_matrix_recorded` 只表示记录完毕，入口退出 1，不代表根因已关闭。

`PRIVATE-ENVIRONMENT.json`、模块／线程上下文、完整内存及句柄转储只留本机，可能含凭据。
只调用系统诊断 API，不安装调试器、不配置符号服务器，不调整系统调试、安全或许可设置。
无异常时没有对应候选转储，不能拿另一个无害程序的转储或成功对照证明候选根因。
每次异常停止该单元；另一个单元必须有预先声明的诊断目的，不为取得通过无限重试。

`controller-tree-selfcheck` 使用无害文件握手验证两种收尾：根进程退出但后代仍活着时，
控制器记录 Job 成员、出生身份和显式终止，`natural_tree_complete=false`；释放后代且整个 Job
实际为空时，才接受自然完成。入口需显式授权及 `--allow-owned-termination`。
控制器不按进程名杀进程，不因根退出直接关闭仍有成员的 KILL_ON_JOB_CLOSE Job。
旧并发记录缺少此见证时保持未证明；不能推定旧周期发生过隐式终止，也不能归因候选异常。

ACL 实机验收前先选择 `permission-selfcheck`，配置显式设置
`allow_owned_permission_controls=true`，并提供 `--execute --authorized-by`。
它只在新空目录中做一次 deny Write／原 DACL 恢复与写入探针，不启动候选 EXE。
准备或恢复失败时保留结果，候选 ACL 拒绝项不计通过；域或策略阻止时停止，不绕过。
SQLite 独占场景仍使用 `environment-family` 的冻结 ID，句柄占用和释放须各有实测见证。

新的租约实验须在控制操作前记录 raw lease epoch、UTC 采样时间和写入者存活／出生身份。
旧时间字段缺失不从后续时间推算为原始实测，旧操作不为补字段重放。

`minimal-saved-recovery` 是构建修正后的单次工程／状态回归：新 U4 副本中只将 J1 的 Value
从 `IN_PWR` 改为 `PWR_IN`。预览前冻结 2 MiB 尾部空白准备及全部工程摘要，独立语法树只允许
该一个字段变化。确认时只持有本次操作备份的共享句柄以拒绝删除，检查真实 cleaning、保存摘要、
签名归属与自然进程树收尾，再通过新进程 show／resolve 和重复登记拒绝核对恢复；不重放确认。
调用仍默认计划，执行需显式自有副本授权。窗口未命中时不补跑；原生检查及完整图面须另验，
脚本的 `saved_recovery_passed_native_visual_pending` 不等于工程验收完成。

## 原生异常的显式取证

本次Windows AMD64环境可在配置中设置`native_debug: true`，让`Scene.raw_call`仅对明确声明的
`native_root/bin/python.exe`和`native_root/bin/kicad-cli.exe`启用已有的自有进程调试器。
默认关闭；同名候选EXE、其他Python及相对程序路径不会被列为原生调试目标。
它不增加产品命令，不附加已有PID，不改注册表、系统调试、安全、符号服务器或许可设置。

源码测试使用已有`tests/native_process.py`入口时，维护者可显式设置
`KICAD_BENCH_NATIVE_DEBUG=1`和绝对的`KICAD_BENCH_NATIVE_ROOT`。
控制值仅接受0／1，控制变量在启动子进程前移除；它们不是产品配置。
调用记录中的`debugger`及`debug_evidence`说明实际观察方式，不能只凭配置声称已取得诊断。

调试器仅观察新启动的目标根进程。后代是否结束仍由整个自有Job单独核对，不能宣称已观察所有后代异常。
首次引导断点之外保留程序原有异常处理；第二次机会异常及C409／C602会尝试记录地址、上下文、模块和转储，
失败也保留原因。原始转储、堆栈及环境只留本机，公共摘要仅收录允许分享的事实和摘要。

观察方式改变应单列：调试运行成功不能关闭普通启动的原异常，也不能重写旧结果。
原pic_programmer失败测试ID未再选择，但共享helper曾通过其他测试重复调用同一入口。
两次调试成功不能关闭原C409；完整纠正见候选证据目录的`native-alias-correction-96.json`。
后续调用须先按实际镜像、输入及调用路径审查，再为声明的新周期准备取证。
新原生异常仍停止当次周期，不为取得成功循环执行。

源码测试的`tests/native_process.py`还可显式读取失败隔离清单：
`KICAD_BENCH_NATIVE_QUARANTINE`必须是绝对路径，
`KICAD_BENCH_NATIVE_QUARANTINE_SHA256`必须绑定其原始字节。
清单按原生镜像与原理图摘要识别已保留的失败入口；匹配时在启动前写入`QUARANTINED.json`并跳过，不能计为通过。
它只覆盖该测试助手的直接`sch export netlist`调用，不是进程防火墙，不能拦截其他程序内部的嵌套导出。
新计划仍须审查剩余调用路径。更改文件名、输出路径或格式不解除隔离；需要诊断重试时另行声明目的与有限矩阵。
这两个控制变量在子进程启动前移除，不是产品配置，也不增加产品命令。
