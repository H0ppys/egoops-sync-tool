"""专用浏览器登录、令牌扫描与本地登录页控制（拆分自 egoops_gui.py 第 750-890 行）"""

# --- 原文件公共 import（拆分保留） ---
import base64
import json
import os
import queue
import shutil
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime

# --- 跨模块依赖（拆分自动生成） ---
from .config import APP_DIR  # noqa: F401
from .token_edge import _utf16_value_after, jwt_payload, save_token  # noqa: F401




# ---------------------------------------------------------------- GUI
# ---------------------------------------------------------------- 专用浏览器登录
BROWSER_PROFILE = os.path.join(APP_DIR, "browser_profile")
EGOOPS_URL = "https://egoops.meituan.com/recordings"


def find_browser():
    for p in (
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     "Google", "Chrome", "Application", "chrome.exe"),
    ):
        if os.path.isfile(p):
            return p
    return None


def scan_token_from_profile():
    """登录后从专用配置的 Session Storage 里读 oidc 令牌（无需 Cookie 数据库）。"""
    markers = ['"access_token":"'.encode("utf-16-le"), b'"access_token":"']
    base = os.path.join(BROWSER_PROFILE, "Default", "Session Storage")
    if not os.path.isdir(base):
        return None
    best = None
    for name in sorted(os.listdir(base)):
        fp = os.path.join(base, name)
        if not os.path.isfile(fp):
            continue
        try:
            data = open(fp, "rb").read()
        except Exception:
            continue
        for mk in markers:
            tok = _utf16_value_after(data, mk)
            if not tok:
                continue
            tok = tok.strip()
            if tok.count(".") != 2 or len(tok) < 100:
                continue
            pl = jwt_payload(tok)
            exp = pl.get("exp") or 0
            if best is None or exp > best["exp"]:
                best = {"token": tok, "exp": exp, "payload": pl}
    return best


def auto_refresh_from_profile(log=None):
    """用专用浏览器配置静默完成 SSO 登录并读取新令牌。"""
    if not os.path.isdir(BROWSER_PROFILE):
        return None
    try:
        proc = launch_browser(headless=True)
    except Exception as e:
        if log:
            log(f"启动浏览器失败：{e}")
        return None
    try:
        for _ in range(15):
            time.sleep(2)
            tok = scan_token_from_profile()
            if tok and tok["exp"] - time.time() > 600:
                return tok
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(1)
        try:
            proc.kill()
        except Exception:
            pass
    return scan_token_from_profile()


def launch_browser(headless=False, wait_close=None):
    exe = find_browser()
    if not exe:
        raise RuntimeError("找不到 Edge/Chrome 浏览器")
    os.makedirs(BROWSER_PROFILE, exist_ok=True)
    args = [
        f"--user-data-dir={BROWSER_PROFILE}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-features=msEdgeSplashScreen",
    ]
    if headless:
        args += ["--headless=new", "--disable-gpu"]
    args.append(EGOOPS_URL)
    import subprocess
    proc = subprocess.Popen([exe] + args, close_fds=True)
    return proc


def parse_token_input(text):
    """从粘贴内容中提取 access_token：支持纯 JWT、Authorization 头、整段 cURL。"""
    import re
    if not text:
        return None
    text = text.strip().strip('"').strip("'")
    m = re.search(r"[Bb]earer\s+([A-Za-z0-9_\-\.]{60,})", text)
    if m:
        return m.group(1)
    m = re.search(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", text)
    if m:
        return m.group(0)
    if text.count(".") == 2 and len(text) > 100 and " " not in text:
        return text
    return None


def save_pasted_token(text, path):
    tok = parse_token_input(text)
    if not tok:
        return None
    pl = jwt_payload(tok)
    save_token({"access_token": tok, "exp": pl.get("exp") or 0, "payload": pl}, path)
    return {"token": tok, "exp": pl.get("exp") or 0, "payload": pl}


def open_login_page():
    try:
        os.startfile("https://egoops.meituan.com/recordings")
    except Exception:
        pass


def hide_console():
    """GUI 模式下隐藏控制台窗口（打包为 console 版时用）。"""
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass
