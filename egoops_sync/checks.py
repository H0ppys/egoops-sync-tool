"""EgoOps / 飞书环境自检（拆分自 egoops_gui.py 第 468-514 行）"""

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
from .feishu import Feishu  # noqa: F401
from .lark_cli import resolve_lark, run_lark  # noqa: F401
from .token_edge import load_token  # noqa: F401





# ---------------------------------------------------------------- 环境自检
DEVELOPER_CONSOLE = "https://open.feishu.cn/app/cli_aa71a647cd355c04/baseinfo"


def check_egoops(cfg):
    """返回 (ok, 说明)。"""
    tf = os.path.join(APP_DIR, "egoops_token.json")
    tok = load_token(tf)
    if tok and tok.get("exp") and tok["exp"] - time.time() > 600:
        left = (tok["exp"] - time.time()) / 3600
        return True, f"已登录：{tok['payload'].get('name', '?')}（剩 {left:.1f} 小时）"
    if tok and tok.get("exp"):
        return False, f"登录已过期（{datetime.fromtimestamp(tok['exp']):%m-%d %H:%M}），请重新登录"
    return False, "未登录"


def check_feishu(cfg):
    """返回 (ok, 说明, 方式)。"""
    app_id = (cfg.get("feishuAppId") or "").strip()
    secret = (cfg.get("feishuAppSecret") or "").strip()
    if app_id and secret:
        try:
            fs = Feishu(app_id, secret, cfg["baseToken"], cfg["tableId"])
            fs.token()
            n = len(fs.list_ids())
            return True, f"应用凭证有效（App ID {app_id}），表内现有 {n} 行", "openapi"
        except Exception as e:
            return False, f"应用凭证验证失败：{e}", "openapi"

    lark_bin = resolve_lark(cfg)
    if not lark_bin:
        return False, "未填 App ID/Secret，且本机没有 lark-cli", "none"
    j, out, err = run_lark(lark_bin, ["auth", "status", "--json"], as_identity=None)
    if not j:
        return False, f"lark-cli 调用失败：{(err or out)[:120]}", "lark"
    app = j.get("appId")
    bot = (j.get("identities") or {}).get("bot") or {}
    if not app:
        return False, "lark-cli 尚未配置应用", "lark"
    if not bot.get("available"):
        return False, f"lark-cli 已配置应用 {app}，但应用身份不可用", "lark"
    if app != "cli_aa71a647cd355c04":
        return False, f"lark-cli 绑定的是另一个应用（{app}），它没有这张表的权限", "lark"
    return True, f"lark-cli 可用（应用 {app}，bot 身份就绪）", "lark"
