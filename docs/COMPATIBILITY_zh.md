# 兼容性

[English](COMPATIBILITY.md) | 中文

kicad-cli 在哪里能用，能配合哪些版本的 KiCad。

## 支持矩阵

KiCad 版本、操作系统和模式的每种组合占一行，由 `python scripts/spec.py generate` 根据 `docs/evidence/` 里的真实软件记录和 `package.json` 里的平台包生成，从不手改：✅ verified 表示已在真实软件上验证，⏳ untested 表示还没测试，⛔ unsupported 表示不支持。

<!-- driver:support-matrix:start -->
| 软件版本 | 操作系统 | 模式 | 状态 |
|---|---|---|---|
| 10.0 | Windows | file | ✅ verified |
| >=10.0.6 <11 | macOS | file | ⏳ untested |
| >=10.0.6 <11 | Linux | file | ⏳ untested |
<!-- driver:support-matrix:end -->

## 已知限制

- kicad-cli 只有文件模式；不修改正在运行的 KiCad 里打开的设计（KiCad 10.0.6 及以上）。
- `footprint move` 移动和旋转未锁定的封装，但不把封装翻到另一面，走线也不跟着走；含铺铜区域、文本框或尺寸标注的封装既不移动也不旋转（kicad-cli 1.1.0，所有 KiCad 版本）。
- `track create` 只画直线段，不画圆弧；`via create` 只加通孔过孔，不加盲孔、埋孔和微孔。`track delete` 拒绝圆弧和锁定的走线（kicad-cli 1.1.0）。
- `zone create` 按 KiCad 的默认铺铜设置写入轮廓，不写填充；KiCad 重新填充并保存后，文件里才有填充（kicad-cli 1.1.0）。
- `board update` 只设矩形板框，而且只替换由一个矩形构成的板框，或者没有板框的情况（kicad-cli 1.1.0）。
- 改板子的命令拒绝 KiCad 9 及更早版本保存的板子；请先用 KiCad 10 打开并保存一次（kicad-cli 1.1.0）。
- `symbol move` 拒绝多单元符号，以及直接连到其他器件的符号；不能新建子图或复用子图（kicad-cli 1.1.0）。
- 检查限时 25 秒、导出限时 40 秒；大板子上 KiCad 的 DRC 可能更久（kicad-cli 1.1.0，KiCad 10.0.6 及以上）。
- Linux 上找不到 Flatpak 版 KiCad（kicad-cli 1.1.0）。
