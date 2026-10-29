# 3.3V施密特反相输入级

intent.json在设计前固定连接、元件、布局和ECO目标；source-facts.json区分资料事实与工程假设。IC为TI SN74LVC1G14DBVR，DBV0005A：1为NC、2为A、3为GND、4为Y、5为VCC。供电3.3V处于数据表1.65～5.5V范围内；输入限定0～3.3V，输出用于高阻测量。

四个项目自定1×1mm焊接/测试接点用于外部供电、回流、输入刺激和输出观察。符号电气类型描述外部边界，不代表板载电源或信号发生器。只有IC的1脚为NC，其余12个引脚按四网真实连接。

R1的10kΩ输入下拉给出默认低输入；在3.3V高输入时负载约0.33mA、功耗1.089mW。按TI的5µA最大输入漏电估计，未驱动时偏移约50mV。ECO改成22kΩ后分别约0.15mA、0.495mW和110mV。它是低速实验输入级的设计计算；不插值宣称3.3V的精确保证翻转阈值，不宣称实际抗干扰或延时性能。

C1为标称100nF的16V X7R 0603去耦件。TI建议0.1µF靠近VCC。选用现有KiCad 0603封装，其实际几何和库上下文绑定保存；没有测量DC偏压后的有效电容或瞬态响应。Murata资料下载受限的情况记录在source-facts.json，不能当作已下载的新PDF证据。

预期逻辑为低输入时高输出、高输入时低输出。仅完成CAD连接、库映射、几何、原生检查、保存重开和修改验证。没有仿真、实物、上电或电气测量。


资料：[TI数据表](https://www.ti.com/lit/ds/symlink/sn74lvc1g14.pdf)、[YAGEO初始10kΩ](https://www.yageogroup.com/component-documentation/download/specsheet/RC0603FR-0710KL)、[YAGEO ECO 22kΩ](https://www.yageogroup.com/component-documentation/download/specsheet/RC0603FR-0722KL)。15kΩ电阻变体及Murata资料来源见 source-facts.json。
