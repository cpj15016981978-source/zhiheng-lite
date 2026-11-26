# -*- coding: utf-8 -*-
"""知衡原生桌面壳（WebView2 / pywebview）。

特性：
  - 复用 webapp 的 /chat，不重写聊天逻辑
  - 深色 / 暖色主题、紧凑缩放
  - 系统托盘：显示 / 隐藏 / 退出
  - 可选开机自启（HKCU Run，用户级，不写系统级）
  - WebView2/pywebview 不可用时回退 Edge --app 或默认浏览器

用法：
  python desktop_shell.py                 # 启动窗口
  python desktop_shell.py --theme warm --compact
  python desktop_shell.py --install-autostart
  python desktop_shell.py --remove-autostart
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
HOST = "127.0.0.1"
DEFAULT_PORT = 8753
APP_NAME = "ZhihengDesktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _healthy(port):
    try:
        with urllib.request.urlopen("http://%s:%d/api/health" % (HOST, port), timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _edge_path():
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if not base:
            continue
        p = pathlib.Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if p.is_file():
            return str(p)
    return ""


def _open_external(port):
    url = "http://%s:%d/chat" % (HOST, port)
    edge = _edge_path()
    if edge:
        os.spawnl(os.P_NOWAIT, edge, edge, "--app=" + url)
        return
    webbrowser.open(url)


def _autostart_command():
    pyw = pathlib.Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.is_file() else pathlib.Path(sys.executable)
    return '"%s" "%s" --autostart' % (exe, pathlib.Path(__file__).resolve())


def install_autostart():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
    return _autostart_command()


def remove_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
    except FileNotFoundError:
        pass
    return True


def _tray_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(38, 70, 83, 255))
    d.text((22, 18), "知", fill=(235, 242, 245, 255))
    return img


class Shell:
    def __init__(self, port, theme="dark", compact=False, tray=True):
        self.port = int(port)
        self.theme = theme if theme in ("dark", "warm") else "dark"
        self.compact = bool(compact)
        self.use_tray = bool(tray)
        self.window = None
        self.icon = None
        self.httpd = None
        self.thread = None
        self._stopped = False

    def ensure_server(self):
        if _healthy(self.port):
            return False
        import webapp
        db = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "Zhiheng" / "data" / "memory.db"
        self.httpd, self.thread = webapp.start(port=self.port, db_path=str(db), quiet=True)
        for _ in range(40):
            if _healthy(self.port):
                return True
            time.sleep(0.25)
        raise RuntimeError("本机服务没有在端口 %d 起来" % self.port)

    def _apply_theme(self):
        if not self.window:
            return
        js = (
            "try{localStorage.setItem('luwu_theme',%r);"
            "document.documentElement.dataset.theme=%r;"
            "document.body.style.zoom=%r;}catch(e){}"
            % (self.theme, self.theme, "0.9" if self.compact else "1")
        )
        try:
            self.window.evaluate_js(js)
        except Exception:
            pass

    def start_tray(self):
        if not self.use_tray:
            return
        try:
            import pystray

            def show(icon, item):
                self.window.show()

            def hide(icon, item):
                self.window.hide()

            def quit_(icon, item):
                self._stopped = True
                try:
                    self.window.destroy()
                except Exception:
                    pass
                icon.stop()

            self.icon = pystray.Icon(
                "zhiheng", _tray_image(), "知衡",
                menu=pystray.Menu(
                    pystray.MenuItem("显示", show, default=True),
                    pystray.MenuItem("隐藏", hide),
                    pystray.MenuItem("退出", quit_),
                ))
            self.icon.run_detached()
        except Exception:
            self.icon = None

    def run(self):
        try:
            import webview
        except Exception:
            self.ensure_server()
            _open_external(self.port)
            return 0
        self.ensure_server()
        self.window = webview.create_window(
            "知衡", "http://%s:%d/chat" % (HOST, self.port),
            width=1280, height=860, min_size=(960, 640),
            background_color="#0b1118" if self.theme == "dark" else "#f3eee4",
            confirm_close=True,
        )
        try:
            self.window.events.loaded += self._apply_theme
        except Exception:
            pass
        self.start_tray()
        try:
            webview.start(debug=False)
        finally:
            if self.icon is not None:
                try:
                    self.icon.stop()
                except Exception:
                    pass
            if self.httpd is not None and self.thread is not None:
                try:
                    import webapp
                    webapp.stop(self.httpd, self.thread)
                except Exception:
                    pass
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="知衡原生桌面壳（WebView2/pywebview）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--theme", choices=["dark", "warm"], default="dark")
    ap.add_argument("--compact", action="store_true", help="页面缩到 90%%")
    ap.add_argument("--no-tray", action="store_true")
    ap.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--install-autostart", action="store_true")
    ap.add_argument("--remove-autostart", action="store_true")
    ns = ap.parse_args(argv)
    if ns.install_autostart:
        print("已安装开机自启：", install_autostart())
        return 0
    if ns.remove_autostart:
        remove_autostart()
        print("已移除开机自启")
        return 0
    return Shell(ns.port, ns.theme, ns.compact, not ns.no_tray).run()


if __name__ == "__main__":
    raise SystemExit(main())
