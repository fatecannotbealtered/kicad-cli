# 项目局部建库

先读[共享运行规则](common.md)。符号与封装是本模块的两个流程；按所需流程加载当前`library build`和`library inspect`的reference。

先固定器件准确型号、厂商资料URL、修订号、页码、所用本地证据与SHA256。区分厂商数据与自己的工程假设；外观相似不能代替电气正确。SN74LVC1G14DBV示例采用TI的SCES218AA引脚表和DBV0005A land pattern。functional-schmitt示例另有事前连接意图、输入偏置、去耦和外部接口；CAD一致性验证不等于上电验证。

符号流程：核对每个引脚编号、名称、电气类型、单位和朝向，再规定图形与字段位置。当前build只支持单unit、矩形body、显式引脚，不支持多unit库作者功能。已有SCH多unit使用能力与新建库范围不同。

封装流程：先选准确封装变体，再核对顶视图/底视图、旋转与镜像关系、焊盘号、中心、尺寸、body和courtyard依据。当前build只支持正面矩形SMD焊盘、矩形body/courtyard，不支持THT、custom pad和自动生成高级封装。手工输入的资料事实仍需人工核实；CLI只验证结构、编号映射和证据绑定。

body_mm表示以原点为中心的名义机械本体宽/高，不包括Fab图形笔宽。courtyard_mm表示本体与全部焊盘包络的并集到Courtyard笔画内沿的最小净距。边界包括0.05mm笔宽的一半，并向外量化到整数nm；非对称焊盘不会假定对称。核对生成后的实际几何与原生读取，不能只检查资料JSON或文件存在。

封装输入的body、pad位置/尺寸和courtyard余量必须在整数nm网格上，单位仍为mm。亚纳米值会在预览阶段明确拒绝，不能依赖原生独立取整后仍满足声明余量。符号与SCH的坐标约束另读对应reference，不将两个流程的精度混用。

建库前已有同名项目PRO。创建自己的`<library>.pretty`输出目录，然后声明每个将写文件的实际hash或absent。library build创建或明确更新一个symbol/footprint asset，注册项目sym-lib-table和fp-lib-table；其他symbol、库表entry与已有版本记录保留。不要把用户全局库当临时目录。

先预览并核对明确的pin—pad双射。此能力要求编号本身一致；不隐式翻译编号，不处理一个引脚多焊盘。确认后用library inspect读取资产、版本、source和新基线。

文件存在不足以通过。用SCH当前symbol查询，按实际raw pins放入原理图；用公开sch export导出实际当前netlist，再由board from-netlist消费项目注册的本地封装。同basename板首次创建可显式初始化不存在或恰为{}的PRO；已有配置的PRO不得覆盖。准确选项以reference为准。保存后新进程重开SCH和PCB，再核对原生引脚、焊盘编号/尺寸、网络与机械边界。NC coupon不能证明实际电路功能。

修改库不会自动刷新已放置实例的cache。当前没有通用实例刷新能力；已有实例保持自己的cache与身份。更新库后必须明确选择受支持的放置或重建流程，重新做原生对照，不能宣称已同步所有实例。

评测见test-prompts.json：新库、明确asset更新、错误映射、只读查询和不支持封装均需正确处理。
