"""命令行入口：--cli / --dry-run / gui 分发（拆分自 egoops_gui.py 第 1138-1158 行）"""

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
from .browser_login import hide_console  # noqa: F401
from .config import APP_DIR  # noqa: F401
from .gui import run_gui  # noqa: F401
from .sync_core import run_cli  # noqa: F401




def main():
    if "--hidden" in sys.argv:
        hide_console()
    if "--cli" in sys.argv:
        sys.exit(run_cli(sys.argv))
    try:
        run_gui()
    except Exception:
        detail = traceback.format_exc()
        try:
            with open(os.path.join(APP_DIR, "egoops_crash.log"), "a", encoding="utf-8") as f:
                f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n{detail}\n")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
