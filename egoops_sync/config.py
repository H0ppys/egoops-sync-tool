"""配置常量、配置文件读写、日志器（拆分自 egoops_gui.py 第 28-84 行）"""

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


APP_TITLE = "EgoOps 任务合格率 → 飞书表格（任务 × 执行者）"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CFG_NAME = "egoops_gui_config.json"

DEFAULT_CFG = {
    "apiBase": "<FILL_EGOOPS_API_BASE>",
    "baseToken": "<FILL_BASE_TOKEN>",
    "tableId": "<FILL_TABLE_ID>",
    "larkBin": "",
    "asIdentity": "bot",
    "logFile": "egoops_sync.log",
}


# ---------------------------------------------------------------- 配置
def cfg_path():
    return os.path.join(APP_DIR, CFG_NAME)


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(cfg_path(), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(cfg_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class Log:
    def __init__(self, path, sink=None):
        self.path = path
        self.sink = sink

    def __call__(self, msg):
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        if self.sink:
            self.sink(line)
        else:
            try:
                if sys.stdout is not None:
                    print(line, flush=True)
            except Exception:
                pass
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
        except Exception:
            pass
