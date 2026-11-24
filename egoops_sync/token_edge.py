"""Edge 浏览器登录态令牌提取与本地 token 缓存（拆分自 egoops_gui.py 第 85-171 行）"""

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




# ---------------------------------------------------------------- 登录态（Edge）
def jwt_payload(token):
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p).decode("utf-8"))
    except Exception:
        return {}


def _utf16_value_after(data, marker_bytes):
    start = data.find(marker_bytes)
    while start >= 0:
        vstart = start + len(marker_bytes)
        end = data.find(b'"\x00', vstart)
        while end >= 0:
            if (end - vstart) % 2 == 0:
                try:
                    val = data[vstart:end].decode("utf-16-le")
                except UnicodeDecodeError:
                    val = None
                if val:
                    return val
            end = data.find(b'"\x00', end + 1)
        start = data.find(marker_bytes, start + 1)
    return None


def edge_profile_dirs():
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data")
    if not os.path.isdir(base):
        return []
    return [os.path.join(base, n) for n in os.listdir(base)
            if (n == "Default" or n.startswith("Profile ")) and os.path.isdir(os.path.join(base, n))]


def extract_token_from_edge(log=None):
    markers = ['"access_token":"'.encode("utf-16-le"), b'"access_token":"']
    best = None
    for prof in edge_profile_dirs():
        for sub in ("Session Storage", os.path.join("Local Storage", "leveldb")):
            d = os.path.join(prof, sub)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                fp = os.path.join(d, name)
                if not os.path.isfile(fp) or os.path.getsize(fp) > 200 * 1024 * 1024:
                    continue
                try:
                    with open(fp, "rb") as f:
                        data = f.read()
                except Exception:
                    continue
                if b"oidc.user" not in data and b"egoops" not in data:
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


def save_token(tok, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tok, f, ensure_ascii=False, indent=2)


def load_token(path):
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        tok = j.get("access_token") or j.get("token")
        if not tok:
            return None
        pl = j.get("payload") or jwt_payload(tok)
        return {"token": tok, "exp": j.get("exp") or pl.get("exp") or 0, "payload": pl}
    except Exception:
        return None
