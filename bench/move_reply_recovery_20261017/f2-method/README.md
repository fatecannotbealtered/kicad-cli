# F2：服务完成后，接收方丢回复

此方法只验证一个边界：公开EXE正常完成R4平移，控制端私下完整保留响应和整个Job的自然结束记录；模拟调用方通过真实stdin管道收到EOF，响应字节数为0。随后用原operation ID和同一状态目录查询原耐久回执。它不证明写入前中断、被杀进程恢复、断电耐久性或通用隔离能力。旧F1的ID、失败、原停止阶段及真实中断资格缺口继续保留。

本目录只含外部方法，不修改产品、SDK、EXE或旧包。控制器尚未获得产品执行结果。执行前须完成独立静审及纯方法检查；原计划与确认计划分别冻结，不能执行后重冻来掩盖差异。

## 固定工程意图

源为`kicad-move-safety-20261016/frozen-engineering-input-01`中的8个文件。新副本保存在独立中文、空格目录。只移动未锁定R4，UUID为`6c5beb16-fbc7-5af2-918d-da26a4b5251c`，从`(100,110)mm`到`(99.5,110)mm`。目标中心为`(99500000,110000000)nm`，两个焊盘世界中心的x为`98587500/100412500nm`，y为`110000000nm`。姿态、层、UUID、网络、原理图路径及其他对象保持。

正式保存板的预期SHA-256是`06d9017e89e9e86533041df9d554289435df6edbe8852569d350acfeddd4176c`。源板是`59b5c3a7f5569031c2c098c01cdee5c5be2c71aed77d33eb5992126ac7b5cb1f`。其余7个文件不变。工程文件的SHA、大小、mtime和文件身份分别记录，查询或重复确认不允许再次写工程。数据库以准确操作行、已消费token行和用量行的守恒判断；SQLite物理变化另记，不把它与工程写入混同。

## 执行阶段

每阶段由父`run_owned.py`启动唯一的`Python -B -S`控制器，保留父Job终态。阶段顺序如下。

1. `validate-method`只导入外部观察器和抽取未改语义的观察策略，启动0个产品、原生或pytest收集进程，不打开状态库。
2. `prepare --cycle <新的本轮直接子目录>`复制8个源文件，创建新scope，复制此前已初始化的完整KiCad10.0.6偏好目录。只在冻结前明确重定位`kicad_common.json#/system/working_dir`；保留完整前后配置摘要。此阶段不启动产品或原生程序。
3. `freeze --cycle <同目录>`生成`ORIGINAL-PLAN.json`，冻结准确case ID、方法、全部源码、固定SDK/spec、EXE/ZIP、原输入、配置、环境、授权和后续真值证据。工作工程、状态和配置路径影响执行，冻结后不能换路径；`calls`与私有证据目录只是交付位置。
4. `preview --plan <ORIGINAL-PLAN.json> --plan-sha256 <原摘要>`只执行一次公开R4预览。其内部应有4次官方调用：准备版本、准备DRC、正式基线DRC、正式候选DRC。正式工程不变。两份正式官方报告独立核对版本、毫米单位、板名、0违规、7未连和原5项忽略key。
5. `freeze-confirm`消费同一原计划，验证原预览stdout摘要仍与终态及TRACE一致，冻结原operation ID、token、完整confirm argv、请求摘要、准确状态行、预检依赖和整个预检目录。token及状态行只在本机私有证据保存。
6. `execute-confirm --plan <CONFIRM-PLAN.json> --plan-sha256 <原摘要>`复核上述所有绑定，启动独立调用方，然后通过已完成4个无害反例验证的外部relay仅dispatch一次正常公开EXE。EXE及全部子进程自然退出，原始stdout/stderr只进入控制端证据；relay关闭调用方stdin而不发送任何响应。回调异常、超时或观察不完整均保留并停止，绝不重试。
7. `verify`仍消费同一确认计划，在新公开进程查询原ID，要求原回执准确相同，且工程文件、准确操作记录及用量不变。随后同token、同key重复confirm必须`exit=6 / E_CONFLICT / conflict=token_used / dispatch_state=not_started`，且原生调用数为0。最后用独立KiCad Python和原有`bench/saved_scene_native_read.py`新进程读回保存板；验证R4中心、焊盘、网络和其他11个封装，以及铜线、区域、图元和全部文件。

每次服务调用由schema3观察器记录不可变PID＋出生身份、调试事件及完整Job。first-chance交回应用处理；不把通知本身当崩溃，也不从exit0推断功能正确。重复确认正常拒绝仅在原已审阅的精确PyInstaller父子退出传播策略允许时解释为SDK拒绝，原TRACE不被改写。原生CLI按准确安装路径和摘要计数；自然结束的Windows系统助手另列，不能把所有非候选PE都算作KiCad调用。

## 有限复用与边界

新预览的候选官方DRC与正式保存板必须具有相同板字节；保存后的报告资格为“复用本次预览候选报告”，不声称重新执行了保存后DRC。另以原native03官方报告交叉绑定0违规、7未连、原5项忽略key；不改规则、不加忽略、不声明整板零问题或制造签核。

全板图面复用根代理本轮`RENDER-REUSE-QUALIFICATION.json`对30组依赖、36个历史方法和6张既有全图的静态资格复核。此方法只核新保存工程与既有正例工程的准确身份；新增render和全图查看计数均为0。原native03的`visual_reuse_pending`原始状态保持原样，当前资格外附。

观察策略从整个已冻结的`tests/move_safety_transport.py`按明确函数、类和常量清单抽取，未改函数语义。模块层pytest、JSON Schema第三方导入和pytest收集不执行；本方法核严格JSON及准确功能字段，不声称新做了全响应Schema验证。外部观察器源码直接编译已声明的bench文件，不读pyc，不依赖PYTHONPATH或site。独立原生消费者不调用被测产品的writer/readback作为真值。

此方法不查询F4或旧source recovery01，不访问旧F1状态库，不创建替代F1预览，不重放旧确认。任一阶段失败都写新的`<stage>-STOP.json`并保留现场；该周期禁止继续写入。普通R4成功也不会把旧F1改成通过。
