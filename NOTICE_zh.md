<!--
仅在本工具封装第三方产品或服务时才需要保留本文件。
若 kicad-cli 不对接任何第三方，请删除 NOTICE.md 与 NOTICE_zh.md。
-->

# 声明

> English → [NOTICE.md](NOTICE.md)

`kicad-cli` 是一个独立的开源项目，**与** KiCad 及其任何关联方**没有**从属、背书、赞助或支持关系。

“KiCad” 及相关产品名、公司名、徽标和品牌，均为各自所有者的商标或注册商标。此处提及仅用于标识 API 兼容目标——即本工具所对接的服务——并不表示存在任何关联或获得其认可。

本项目不会再分发 KiCad 的代码或资源。`kicad-cli` 读写 KiCad 有文档的文件格式，调用用户本机安装里 KiCad 自己的 `kicad-cli` 二进制和自带 Python 解释器，并通过公开的 IPC API 与正在运行的 KiCad 通信。全程不涉及任何凭据：一切都发生在用户自己的机器上，针对用户自己的 KiCad。

MIT 许可证（见 [LICENSE](LICENSE)）仅适用于 `kicad-cli` 源代码，不授予 KiCad 的商标、服务、数据、API 行为或上游可用性的任何权利。
