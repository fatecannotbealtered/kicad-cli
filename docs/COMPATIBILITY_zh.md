# 兼容性

产品 1.0.0 提供 Windows x64、macOS arm64/x64、Linux x64 目标包，用户自备合法
KiCad 10.0 patch 6 或以上版本。构建基线为 Ubuntu 24.04（glibc 2.39）、macOS 14
arm64 和 macOS 15 Intel。精确原生版本和构件摘要以该包验收记录为准，不含 Linux arm64。

board sync 的 candidate-out 路线，以及新增源文件的原理图层级增量批处理，仍依赖
Windows NTFS handle 后端，基本跨平台 smoke 不能外推这两项支持。其它操作遵循
当前 selected reference 的范围与限制，不宣称所有命令三端完全一致。

文件解析、KiCad 自带 pcbnew Python、官方 CLI 各有职责。产品与官方 CLI 同名，
执行时使用准确绝对路径。自定义检查不是官方 ERC/DRC，零错误不是制造签核。
直接二进制含 Python 和另有许可的固定 SDK，外部 KiCad 不再分发。
见[安装](INSTALLATION.md)、[交付](modules/DELIVERY.md)与[知识配置](KNOWLEDGE.md)。
