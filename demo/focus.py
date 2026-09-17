"""把 KiCad 的板编辑器窗口拉到最前面。

演示开始前得让它可见——脚本在后台画得再好，窗口被挡着就什么也看不到。

Windows 不允许后台进程随便抢焦点（防流氓软件），所以 SetForegroundWindow 有时
会被拒。这里用的是公开的规避手法：先把自己的线程输入队列附到目标窗口的线程上，
拿到「同一个输入上下文」的资格，再设前台，然后解除附着。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_RESTORE = 9


def find_window(needle: str) -> tuple[int, str] | None:
    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if needle.lower() in buf.value.lower():
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


def raise_window(hwnd: int) -> bool:
    user32.ShowWindow(hwnd, SW_RESTORE)
    target = user32.GetWindowThreadProcessId(hwnd, None)
    mine = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(mine, target, True)
    try:
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        user32.AttachThreadInput(mine, target, False)
    return ok


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else config.STEM
    hit = find_window(needle)
    if not hit:
        print(f"没找到标题含 {needle!r} 的窗口")
        return 1
    hwnd, title = hit
    ok = raise_window(hwnd)
    print(f"{'已置顶' if ok else '置顶被系统拒绝（窗口仍已还原）'}：{title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
