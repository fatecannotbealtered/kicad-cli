"""kicad-layout 公共库：信封、退出码、安全板操作、占用栅格。

被同目录其它脚本 import，不单独执行。

三件事在这里统一，别在各脚本里重写：
  1  输出信封与退出码（ai-native-cli-spec CLI-SPEC §3/§6）
  2  pcbnew 的已知陷阱规避（见 reference/pcbnew-api.md）
  3  网络类判定：板文件内嵌的类名可能过期，一律以 .kicad_pro 的 pattern 为准
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
import time

if __package__:
    from . import confirm_store, drc_runner
else:
    import confirm_store
    import drc_runner

SCHEMA_VERSION = "1.0"
TOOL_VERSION = "1.0.0"

# ---- 退出码（CLI-SPEC §6）---------------------------------------------------
EXIT = {
    "E_USAGE": 2,
    "E_VALIDATION": 2,
    "E_NOT_FOUND": 3,
    "E_CONFIG": 4,
    "E_CONFIRMATION_REQUIRED": 5,
    "E_CONFLICT": 6,
    "E_TIMEOUT": 8,
    "E_IO": 1,
    "E_INTERNAL": 1,
}
RETRYABLE = {"E_CONFLICT": True, "E_TIMEOUT": True}

_T0 = time.time()


def _emit(doc, code=0):
    doc["schema_version"] = SCHEMA_VERSION
    doc.setdefault("meta", {})["duration_ms"] = int((time.time() - _T0) * 1000)
    doc["meta"]["tool_version"] = TOOL_VERSION
    # The host decodes this stream as UTF-8, so write UTF-8 rather than
    # whatever locale KiCad's own interpreter happens to start with. Most of
    # this tool's operator-facing text is Chinese, and a mismatch here does not
    # fail loudly: the host decodes with errors="replace" and the note arrives
    # as replacement characters attached to an otherwise successful write.
    text = json.dumps(doc, ensure_ascii=False, default=str) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(text)
        sys.stdout.flush()
    else:
        sys.stdout.flush()
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    sys.exit(code)


def ok(data, **meta):
    _emit({"ok": True, "data": data, "meta": meta}, 0)


def fail(code, message, details=None):
    _emit(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "retryable": RETRYABLE.get(code, False),
            },
        },
        EXIT.get(code, 1),
    )


def need_confirm(preview, op_desc, target=None):
    """写操作的 dry-run 出口：返回 confirm_token，退出码 5。

    token 由 confirm_store 记录后签发：随机、限时、一次性。原先是
    sha256(op_desc + preview)，是公开输入的纯函数——每次 dry-run 都得到同一个
    token，永不过期，可以无限重放。那样的闸门只值一次额外调用，不值一个决定。
    """
    try:
        token = confirm_store.issue(op_desc, preview, target)
    except confirm_store.ConfirmError as exc:
        fail(exc.code, str(exc), exc.details)
    _emit(
        {
            "ok": False,
            "error": {
                "code": "E_CONFIRMATION_REQUIRED",
                "message": f"写操作需确认，{confirm_store.TTL_SECONDS}s 内用 "
                "--confirm <token> 重跑同一命令",
                # 预览必须跟着 token 一起出去。只给 token 不给预览，等于让调用方
                # 确认一件它看不见的事——那比没有闸门更糟，把决定变成了反射。
                "details": {
                    "confirm_token": token,
                    "operation": op_desc,
                    "preview": preview,
                    "expires_in_s": confirm_store.TTL_SECONDS,
                    "single_use": True,
                },
                "retryable": False,
            },
            "data": {"preview": preview, "confirm_token": token},
        },
        EXIT["E_CONFIRMATION_REQUIRED"],
    )


def check_confirm(args_confirm, preview, op_desc, target=None):
    """写脚本统一入口：无 token 走 dry-run 出口，token 不可兑付报 E_CONFLICT。

    target 传板文件路径时，token 还绑定该文件的内容哈希——预览只是摘要，
    两块板可能摘要相同，同一块板也可能被改成摘要不再描述的状态。
    """
    if not args_confirm:
        need_confirm(preview, op_desc, target)
    try:
        confirm_store.redeem(args_confirm, op_desc, preview, target)
    except confirm_store.ConfirmError as exc:
        fail(exc.code, str(exc), exc.details)


# ---- pcbnew 导入 ------------------------------------------------------------
def import_pcbnew():
    """pcbnew 只存在于 KiCad 自带的 Python 里，系统 python 没有。

    顺带把 wxWidgets 的断言弹窗关掉。KiCad 的 C++ 层有一批 wxASSERT，
    误用 API 会弹模态对话框——在无人值守环境下它**永久阻塞进程**，
    表现是脚本不输出也不退出，用户那边则是一个接一个的弹框。
    最常见的触发点是 PCB_VIA::GetWidth() 不带层参数（见第 6 条）。
    正确的写法当然是不去误用，但工具链必须对误用免疫，不能靠人记得。
    """
    try:
        import wx

        wx.DisableAsserts()
    except Exception:  # noqa: BLE001
        pass  # 没有 wx 也无妨，只是少一层保险
    try:
        import pcbnew  # noqa

        return pcbnew
    except ImportError:
        fail(
            "E_CONFIG",
            "当前解释器没有 pcbnew。载荷必须跑在 KiCad 自带的 Python 下，"
            "用 kicad-cli context 查它解析到了哪一个",
            {"executable": sys.executable},
        )


# ---- 板文件安全操作 ---------------------------------------------------------
def load_board(pcbnew, path):
    if not os.path.exists(path):
        fail("E_NOT_FOUND", "板文件不存在", {"path": path})
    try:
        return pcbnew.LoadBoard(path)
    except Exception as e:  # noqa: BLE001
        fail("E_IO", f"板文件加载失败: {e}", {"path": path})


def disown(obj):
    """把对象交给板子之后放弃 Python 侧所有权。

    不做这一步，SWIG 会在解释器退出时往 C 层 stdout 打一堆
    "detected a memory leak of type 'PCB_TRACK *'"，那些字节排在信封后面，
    让 stdout 出现第二段非 JSON 内容，违反「一个 JSON 文档」的约定。
    C 层缓冲 Python 的 flush 管不到，所以要从源头消除，不能靠重定向。
    """
    try:
        obj.thisown = False
    except Exception:  # noqa: BLE001
        pass
    return obj


def zones_of(board):
    """必须先快照成 list。board.Zones() 是活容器，边遍历边增删会段错误。"""
    return list(board.Zones())


def tracks_of(board):
    return list(board.GetTracks())


def footprints_of(board):
    return list(board.GetFootprints())


def all_pads(board):
    """在任何 Remove 之前取完。Remove 之后 GetFootprints() 返回的代理会失效。"""
    out = []
    for f in board.GetFootprints():
        for p in f.Pads():
            out.append(p)
    return out


def fill_and_save(pcbnew, board, path):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.BuildConnectivity()
    board.Save(path)


def unconnected(board):
    board.BuildConnectivity()
    # GetUnconnectedCount 在 KiCad 9+ 需要一个 aVisibleOnly 参数
    return board.GetConnectivity().GetUnconnectedCount(False)


def zone_filled_area(pcbnew, zone, board):
    """返回 (多边形数, 面积 mm2)。多边形数为 0 意味着铺铜从未真正填充。"""
    lay = zone.GetLayerSet().Seq()[0]
    poly = zone.GetFilledPolysList(lay)
    n = poly.OutlineCount()
    a = sum(poly.Outline(k).Area() for k in range(n)) / 1e12
    return n, a, board.GetLayerName(lay)


def via_drill(track):
    """过孔钻径，单位 mm。

    两个坑合在这一个函数里：
      1  PCB_VIA.GetWidth() 不带层参数会弹断言对话框卡死进程，所以用 GetDrillValue。
      2  GetDrillValue() 返回的是内部单位（纳米），不是毫米。当毫米用会让
         paint_via 把半径算成上百万格，一个过孔就把整层涂成它的网络，
         后续所有布线全部失效且不报错。
    """
    import pcbnew

    return pcbnew.ToMM(track.GetDrillValue())


# ---- 项目文件与网络类 -------------------------------------------------------
def official_cli():
    """Resolve the configured KiCad executable; never fall back to our own shim."""
    executable = drc_runner.find_official_cli(interpreter=sys.executable)
    if not executable:
        fail("E_CONFIG", "KiCad official binary not found; set KICAD_CLI_OFFICIAL")
    return executable


def run_drc(path, timeout=1800):
    """Shared fail-closed report boundary; this does not roll back the caller."""
    try:
        return drc_runner.run(
            path, drc_runner.find_official_cli(interpreter=sys.executable), timeout
        ).report
    except drc_runner.DrcError as exc:
        fail(
            exc.code,
            str(exc),
            {
                **exc.details,
                "write_state": "unknown",
                "next_action": "Inspect the board and backup before retrying the enclosing write; "
                "a failed verification does not prove the write was rolled back.",
            },
        )


def project_path(pcb_path):
    return os.path.splitext(pcb_path)[0] + ".kicad_pro"


def load_project(pcb_path):
    p = project_path(pcb_path)
    if not os.path.exists(p):
        fail("E_NOT_FOUND", "找不到工程文件，设计规则与网络类都存在其中", {"path": p})
    with open(p, encoding="utf-8") as f:
        return json.load(f), p


class NetClasses:
    """网络类判定。

    板文件里内嵌的类名可能是旧的（改过 .kicad_pro 之后不会自动同步），
    所以一律按 .kicad_pro 的 netclass_patterns 重新判定，不要用
    net.GetNetClassName()。
    """

    def __init__(self, pro):
        ns = pro.get("net_settings", {})
        self.classes = {c["name"]: c for c in ns.get("classes", [])}
        self.patterns = ns.get("netclass_patterns", []) or []
        if "Default" not in self.classes:
            self.classes["Default"] = {
                "name": "Default",
                "track_width": 0.2,
                "clearance": 0.2,
                "via_diameter": 0.6,
                "via_drill": 0.3,
            }

    def name_for(self, netname):
        for p in self.patterns:
            if fnmatch.fnmatch(netname, p["pattern"]):
                return p["netclass"]
        return "Default"

    def params(self, netname):
        c = self.classes.get(self.name_for(netname), self.classes["Default"])
        return (
            c.get("track_width", 0.2),
            c.get("clearance", 0.2),
            c.get("via_diameter", 0.6),
            c.get("via_drill", 0.3),
        )

    def max_clearance(self):
        return max([c.get("clearance", 0.2) for c in self.classes.values()] or [0.2])


# ---- 载流核算（IPC-2221）----------------------------------------------------
def ampacity(width_mm, oz=1.0, dT=10.0, external=True):
    """IPC-2221 载流估算。I = k * dT^0.44 * A^0.725，A 为 mil^2。

    k 取 0.048（外层）/ 0.024（内层）。返回安培。
    """
    area_mil2 = (width_mm / 0.0254) * (1.378 * oz)
    k = 0.048 if external else 0.024
    return k * (dT**0.44) * (area_mil2**0.725)


def width_for(current_a, oz=1.0, dT=10.0, external=True):
    """反解满足给定电流的最小线宽（mm）。"""
    k = 0.048 if external else 0.024
    area = (current_a / (k * (dT**0.44))) ** (1.0 / 0.725)
    return area / (1.378 * oz) * 0.0254


# ---- 参数解析 ---------------------------------------------------------------
def parse_args(argv, required=(), flags=(), opts=()):
    """极简参数解析。--key value 与 --flag。"""
    out = {"_pos": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            k = a[2:]
            if k in flags:
                out[k] = True
                i += 1
            else:
                if i + 1 >= len(argv):
                    fail("E_USAGE", f"参数 --{k} 缺少取值")
                out[k] = argv[i + 1]
                i += 2
        else:
            out["_pos"].append(a)
            i += 1
    for r in required:
        if r not in out and not out["_pos"]:
            fail("E_USAGE", f"缺少必需参数 --{r}")
    return out


def board_arg(args):
    p = args.get("board") or (args["_pos"][0] if args["_pos"] else None)
    if not p:
        fail("E_USAGE", "必须给出板文件：--board <path.kicad_pcb>")
    return os.path.abspath(p)
