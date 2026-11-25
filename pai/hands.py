# -*- coding: utf-8 -*-
"""G1·手和眼（读屏 + 按键，只碰自家窗口）。见黑板 ADR-055 与 Backlog「G1 AI 自己开游戏机玩」。

Boss 2026-10-22 改方向：「不再看单据看现场，开发 AI 自己玩游戏」，他选的现场是
【它自己开游戏玩、我在旁边看】。这条线同时碰到两个 ADR-044 须点头档（读屏=常开、
模拟按键=写输入），他那次选择就是这一句点头；我把面限死成四条，代码里逐条钉住：
  · 默认关：PAI_HANDS 不设 = 一次屏幕也不读、一个键也不发（关 = 一个字都不改，ADR-032）。
  · 只碰自家窗口：窗口标题必须以 WINDOW_TITLE_PREFIX 开头，别的窗口连读都不读，更不发键。
  · 一次只玩一手：press_key() 一次只发一个键，不做宏、不做连发、不带循环（循环在 tools/play.py，
    它一次调用只走一步）。
  · 画面不上传、不落盘：读回来的像素当场分类成 12x12 个格子标签就丢，
    函数返回值里【没有像素缓冲】，本模块不 open 任何文件、不出网。

按键走 PostMessage 到【自家窗口的键盘目标】，不走 SendInput：
SendInput 打进的是系统当前焦点（那可能是 Boss 正在用的编辑器），PostMessage 只投递给指定句柄。
所以窗口失焦、切到别的程序，我们的键不会跑过去 —— 这条是设计出来的，不是测出来的兜底。

★棋盘尺寸/颜色不在本文件里定义，一律 import pai.game（唯一住址，ADR-030）。
"""
import ctypes
import os
import platform
from ctypes import wintypes

from pai import game

ENV_SWITCH = "PAI_HANDS"
SWITCH_ON = "on"

WINDOW_TITLE_PREFIX = "PAI-游戏-"
MAX_TITLE_CHARS = 200

KEYS = {"up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "space": 0x20, "q": 0x51}

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
SRCCOPY = 0x00CC0020
BI_RGB = 0

CAPABILITY = {
    "name": "G1 读屏+按键（只碰自家窗口）",
    "switch_env": ENV_SWITCH,
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "非 Windows / 句柄找不到 / 标题不是自家窗口 / 客户区和棋盘尺寸对不上 / GDI 调用失败 = "
               "返回 ok=False + 一句原因，绝不抛给上层、绝不退而求其次去碰别的窗口",
    "probe_cost": "probe() 只读环境变量、平台、user32/gdi32 能不能装载、自家窗口有几个："
                  "【不读像素、不发按键、不 open 文件、不起子进程、不联网】",
    "approval": "Boss 2026-10-22 改方向并选「它自己开游戏玩、我在旁边看」，读屏+按键两档"
                "随该选项点头，留底见黑板 ADR-055（同一条还记着限死的面：默认关、"
                "一次一手、只碰自家窗口、不联网、画面不上传）。"
                "★常驻/自启（X10）没批：本模块不装自动任务、不起常驻进程，游戏窗口空闲自己退出",
}


class _Rect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", _Rect)]


_ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
_API = None


class UnsupportedError(RuntimeError):
    """平台/GDI 用不了 —— 内部信号，调用方必须降级成 ok=False，不许抛到上层。"""


def switch_raw():
    return (os.environ.get(ENV_SWITCH) or "").strip().lower()


def enabled():
    """PAI_HANDS=on 才动手。写错值一律当关，不做兜底猜测（同 ear.py 的口径）。

    ★只看开关、不看平台：开关开着但不是 Windows，得让调用点如实回「未落这个平台」，
      不能拿「开关是关的」把人往错的方向支走。
    """
    return switch_raw() == SWITCH_ON


def on_windows():
    return platform.system() == "Windows"


def _api():
    """懒加载：非 Windows 或装不上就抛 UnsupportedError，由调用方降级成一句原因。

    ★为什么在函数里取而不是模块顶层：模块被 import 的场合太多（测试、契约扫描、
      界面启动），顶层碰 windll 会让【关掉的状态】也带上平台副作用。
    """
    global _API
    if _API is not None:
        return _API
    if not on_windows():
        raise UnsupportedError("本机后端只支持 Windows，未落其他平台")
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(_Rect)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_Point)]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPVOID, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GuiThreadInfo)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, _ENUM_PROC, wintypes.LPARAM]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                             wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                wintypes.LPVOID, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _API = (user32, gdi32)
    return _API


def window_title(hwnd, api=None):
    user32, _ = (api or _api())
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(min(length, MAX_TITLE_CHARS) + 1)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value or ""


def _visible_top_level_titles(api=None):
    """当前桌面上【可见的顶层窗口】(hwnd, title) 列表 —— 只读，一个键也不发。"""
    user32, _ = (api or _api())
    out = []

    def cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            out.append((hwnd, window_title(hwnd, api)))
        return True

    user32.EnumWindows(_ENUM_PROC(cb), 0)
    return out


def own_windows(scan=None):
    """按标题前缀找自家游戏窗口，返回 [{"hwnd","title"}]；找不到就是空列表，不猜。

    失败返回 {"ok": False, "reason": ...}（调用方按 dict 判），不抛异常。
    ★判据只有标题前缀这一个：不比类名、不看进程 —— 别人的窗口长得再像，
      没有我们那串前缀就不在候选里。scan() 供测试注入一份窗口清单。
    """
    try:
        pairs = _visible_top_level_titles() if scan is None else scan()
    except Exception as exc:
        return {"ok": False, "reason": "问不了窗口列表（已静默跳过）：%s" % exc.__class__.__name__}
    return [{"hwnd": h, "title": t} for h, t in pairs if t.startswith(WINDOW_TITLE_PREFIX)]


def is_own(hwnd, scan=None):
    found = own_windows(scan=scan)
    if isinstance(found, dict):
        return False, found["reason"]
    for item in found:
        if item["hwnd"] == int(hwnd):
            return True, ""
    return False, ("句柄 %s 不在自家窗口名单里（标题必须以 %r 开头）—— 拒绝对它动手"
                   % (int(hwnd), WINDOW_TITLE_PREFIX))


def key_target_candidates(hwnd):
    """这个窗口里该收键的句柄候选：先【焦点子窗口】，再按枚举次序试可见子窗口，最后回顶层。

    Tk 的内容不在顶层句柄上，键要投到它自己的子窗口才进得了事件循环 —— 这条次序由
    tools/play.py 的现场对照验（哪个目标真让棋盘走了一步），不是照文档猜的。
    """
    user32, _ = _api()
    out = []
    pid = wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    info = _GuiThreadInfo()
    info.cbSize = ctypes.sizeof(_GuiThreadInfo)
    if user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
        out.append(int(info.hwndFocus))
    children = []

    def cb(child, _lparam):
        if user32.IsWindowVisible(child):
            children.append(int(child))
        return True

    user32.EnumChildWindows(int(hwnd), _ENUM_PROC(cb), 0)
    out.extend(children)
    out.append(int(hwnd))
    seen, ordered = set(), []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _lparam(vk, up):
    user32, _ = _api()
    scan_code = user32.MapVirtualKeyW(vk, 0)
    base = 1 | (scan_code << 16)
    return base | (0xC0000000 if up else 0)


def press_key(hwnd, key, post=None, scan=None):
    """向【自家窗口】投一个键（down + up）。一次只发一手，不连发、不带宏。

    post(hwnd, msg, wparam, lparam) 供测试注入；默认走 PostMessageW。
    ★不用 SendInput：那会把键打进系统当前焦点，可能是 Boss 正在用的窗口。
    """
    trace = {"ok": False, "hwnd": int(hwnd) if hwnd else None, "key": key,
             "target": None, "posted": 0, "reason": ""}
    if not enabled():
        trace["reason"] = "按键是关的（设 %s=on 才动）" % ENV_SWITCH
        return trace
    if not on_windows():
        trace["reason"] = "本机后端只支持 Windows，未落其他平台"
        return trace
    if key not in KEYS:
        trace["reason"] = "只认这几个键：%s，不给别的" % "、".join(sorted(KEYS))
        return trace
    try:
        ok, why = is_own(hwnd, scan=scan)
        if not ok:
            trace["reason"] = why
            return trace
        target = key_target_candidates(int(hwnd))[0]
        vk = KEYS[key]
        send = post if post is not None else _api()[0].PostMessageW
        send(target, WM_KEYDOWN, vk, _lparam(vk, False))
        send(target, WM_KEYUP, vk, _lparam(vk, True))
    except UnsupportedError as exc:
        trace["reason"] = "发不了键：%s" % exc
        return trace
    except Exception as exc:
        trace["reason"] = "这次没按成（已静默跳过）：%s" % exc.__class__.__name__
        return trace
    trace.update(ok=True, target=int(target), posted=2, reason="ok")
    return trace


def client_rect_on_screen(hwnd, api=None):
    """自家窗口客户区在屏幕上的绝对位置 —— 读屏只圈这一块，不碰全屏。"""
    user32, _ = (api or _api())
    rect = _Rect()
    if not user32.GetClientRect(int(hwnd), ctypes.byref(rect)):
        raise UnsupportedError("拿不到客户区尺寸")
    pt = _Point(rect.left, rect.top)
    if not user32.ClientToScreen(int(hwnd), ctypes.byref(pt)):
        raise UnsupportedError("客户区坐标换算不到屏幕上")
    return pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top


def _grab_screen_bits(x, y, w, h):
    """从屏幕 DC 拷一块客户区，返回 BGRA 字节串。只在真要读屏时才被调用。"""
    user32, gdi32 = _api()
    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY):
            raise UnsupportedError("BitBlt 失败（屏幕拷贝没成功）")
        bmi = _BitmapInfoHeader()
        bmi.biSize = ctypes.sizeof(_BitmapInfoHeader)
        bmi.biWidth, bmi.biHeight = w, -h           # 负高 = 自上而下的行序
        bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0):
            raise UnsupportedError("GetDIBits 失败（拿不到像素）")
        return buf.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, screen)


def screen_pixels(hwnd):
    """默认取像素：返回 (BGRA 字节串, 宽, 高)。窗口被缩小时宽高能对上棋盘吗？对不上就拒读。"""
    x, y, w, h = client_rect_on_screen(hwnd)
    return _grab_screen_bits(x, y, w, h), w, h


def cell_kinds(pixels, w, h, cols=None, rows=None):
    """像素 → 格子标签。每格取【中心 3x3 的中位色】，避开格子边线那一圈混色。"""
    cols = cols or game.COLS
    rows = rows or game.ROWS
    out = []
    for r in range(rows):
        for c in range(cols):
            cx = int((c + 0.5) * (w / float(cols)))
            cy = int((r + 0.5) * (h / float(rows)))
            rs, gs, bs = [], [], []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < w and 0 <= y < h:
                        i = (y * w + x) * 4
                        bs.append(pixels[i])
                        gs.append(pixels[i + 1])
                        rs.append(pixels[i + 2])
            mid = len(rs) // 2
            out.append(game.classify_pixel((sorted(rs)[mid], sorted(gs)[mid], sorted(bs)[mid])))
    return out


def capture_grid(hwnd, cols=None, rows=None, read_pixels=None, scan=None):
    """读【自家窗口客户区】的屏幕像素，分类成格子标签就丢掉像素。

    返回值里没有像素缓冲，也不落盘、不上传（Boss 那四条限制里的两条）。
    read_pixels(hwnd) -> (bytes, w, h) 供测试注入一份合成画面，让分类这段真跑一遍。
    """
    cols = cols or game.COLS
    rows = rows or game.ROWS
    want_w, want_h = cols * game.CELL_PX, rows * game.CELL_PX
    trace = {"ok": False, "grid": [], "counts": {}, "w": 0, "h": 0, "reason": ""}
    if not enabled():
        trace["reason"] = "读屏是关的（设 %s=on 才读）" % ENV_SWITCH
        return trace
    try:
        ok, why = is_own(hwnd, scan=scan)
        if not ok:
            trace["reason"] = why
            return trace
        pixels, w, h = (screen_pixels(int(hwnd)) if read_pixels is None else read_pixels(int(hwnd)))
        if (w, h) != (want_w, want_h):
            trace["reason"] = ("客户区 %dx%d 和棋盘 %dx%d 对不上（窗口被缩放或 DPI 缩放了？）"
                               "—— 不猜，拒读" % (w, h, want_w, want_h))
            return trace
        grid = cell_kinds(pixels, w, h, cols, rows)
        counts = {}
        for kind in grid:
            counts[kind] = counts.get(kind, 0) + 1
    except UnsupportedError as exc:
        trace["reason"] = "读不了：%s" % exc
        return trace
    except Exception as exc:
        trace["reason"] = "这次没看成（已静默跳过）：%s" % exc.__class__.__name__
        return trace
    if counts.get(game.KIND_HEAD, 0) != 1:
        trace["counts"] = counts
        trace["reason"] = ("画面上蛇头认出 %d 个（应为 1 个）：不是我们的棋盘、被别的窗口压住了，"
                           "或者游戏已经死了 —— 不猜" % counts.get(game.KIND_HEAD, 0))
        return trace
    trace.update(ok=True, grid=grid, counts=counts, w=w, h=h, reason="ok")
    return trace


def probe(scan=None):
    """只读体检：开关、平台、GDI 能不能装、自家窗口有几个。【不读像素、不发键、不起进程】。"""
    out = {"switch": ENV_SWITCH, "on": enabled(), "windows": platform.system(),
           "gdi_ready": False, "own_count": 0, "titles": [], "reason": ""}
    try:
        _api()
        out["gdi_ready"] = True
    except Exception as exc:
        out["reason"] = "GDI 装不起来：%s" % exc.__class__.__name__
        return out
    found = own_windows(scan=scan)
    if isinstance(found, dict):
        out["reason"] = found["reason"]
        return out
    out["own_count"] = len(found)
    out["titles"] = [item["title"][:MAX_TITLE_CHARS] for item in found]
    return out
