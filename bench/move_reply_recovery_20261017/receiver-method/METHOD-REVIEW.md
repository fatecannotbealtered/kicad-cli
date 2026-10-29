# 接收方丢回复方法：待执行审查稿

本目录只提供有限的同步CLI流转发实验。它不修改产品、SDK或EXE，不证明断电、写盘前中断或进程被杀后的恢复，也不是操作系统安全沙箱。

`receiver_probe.py`是实际独立的无害Python调用方。它先向控制端发出operation ID和冻结请求摘要，然后在自己的stdin pipe上阻塞读取。它不知道服务命令、服务输出、证据文件路径或canary。服务正常结束后，转发器不写入任何响应字节，只关闭pipe；调用方实际读到EOF，并记录收到0字节及空内容摘要。调用方不能从这个结果推断服务成功。

`response_relay.py`只允许一次服务callback。真实服务由外部控制端使用既有观察器执行，完整保留stdout、stderr、实际退出与完整Job。callback返回的描述只进入私有证据，不传给调用方。服务失败也按实际失败保留；空回复不会把服务失败变成成功。callback异常不自动重试。

无害矩阵有四个准确ID：`chunked-ascii`、`unicode`、`nonzero`、`empty-stdout`。它们分别验证分块输出、UTF-8字节、真实exit7及空stdout。服务stdout/stderr分别有独立canary。每项必须同时满足：服务原始字节准确、真实退出准确、全Job自然结束、调用方实际EOF和0字节、canary未进入调用方stdout/stderr/argv/env、服务只dispatch一次。`nonzero`通过只表示转发边界保留了服务失败，并不表示服务业务成功。

`validate_receiver.py freeze`只生成新的冻结计划，不启动子进程；`execute`必须消费原计划及摘要，在启动任意子进程前核对方法根目录、控制器自身路径、四份方法源码的准确路径及字节、源码成员、Python镜像、WindowsJob源码、准确ID、环境及超时。每项前后再次核对。方法模块和WindowsJob模块使用同一次核验得到的原始字节`compile/exec`载入，不重新读取同名路径，也不读取其pyc。控制端与子进程都要求`-B -S`。输出目录是交付位置，可以在新目录接续；复制或迁移方法输入目录须拒绝。

调用方argv和环境逐项解码检查，不通过JSON转义后的文本搜索判断路径隔离。私有case、relay、service目录不得进入调用方argv或环境。调用方自己的空工作目录明确允许；它不是服务证据目录。四项矩阵不执行callback抛出异常这一分支；该分支目前仅有实现，不能记为已验证，也不能从exit7案例推断它通过。

示例接口（实际路径与原计划摘要由控制端冻结，不是产品命令答案）：

```text
python -B -S validate_receiver.py freeze --plan <new-plan.json> --python <pinned-python.exe> --owned-process-source <pinned-owned_process.py>
python -B -S validate_receiver.py execute --plan <original-plan.json> --plan-sha256 <original-sha256> --output <fresh-output>
```

执行计划收集也会运行一个控制端Python进程；“未启动测试子进程”不等于零进程。正式执行另有1个控制端、4个无害调用方、4个无害服务。观察器自身可能启动元数据查询进程，必须按真实完整Job记录另列，不能从矩阵数推断总进程数。

调用方和无害服务在恢复控制失败时，只能停止其各自明确归属的WindowsJob，并按不完整保留；该分支不算自然成功。真实产品服务不使用此处的无害服务callback，必须使用外部已有的完整观察器，且已开始调用不能自动重试。控制端整体也必须由父级完整Job观察，单凭本目录RESULT不能认定整个测试周期自然结束。

执行前仍需独立静审：真实请求身份如何取得、实际服务callback的输入/输出和全Job如何绑定、冻结计划与父控制器的准确源码摘要，以及矩阵检查代码是否按预定边界完成。此稿不表示已经执行或通过。
