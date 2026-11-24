#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EgoOps 同步工具入口（与原 egoops_gui.py 行为一致）。

用法：
    python main.py                 # 图形界面
    python main.py --cli           # 跑一次刷新
    python main.py --cli --dry-run # 只抓不写
"""
import sys

from egoops_sync.cli import main

if __name__ == "__main__":
    sys.exit(main())
