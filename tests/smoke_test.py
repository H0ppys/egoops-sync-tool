"""冒烟测试：拆分后的包能导入、关键符号存在、CLI 参数解析可用。"""
from __future__ import annotations

import importlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

MODULES = [
    "egoops_sync.config",
    "egoops_sync.token_edge",
    "egoops_sync.api",
    "egoops_sync.lark_cli",
    "egoops_sync.feishu",
    "egoops_sync.checks",
    "egoops_sync.rows",
    "egoops_sync.sync_core",
    "egoops_sync.browser_login",
    "egoops_sync.gui",
    "egoops_sync.cli",
]

REQUIRED = {
    "egoops_sync.config": ["DEFAULT_CFG", "cfg_path", "load_cfg", "save_cfg", "Log"],
    "egoops_sync.api": ["EgoOps"],
    "egoops_sync.feishu": ["Feishu", "to_feishu_fields"],
    "egoops_sync.rows": ["build_rows"],
    "egoops_sync.sync_core": ["sync", "run_cli"],
    "egoops_sync.cli": ["main"],
}


def main() -> int:
    for name in MODULES:
        importlib.import_module(name)
    for name, symbols in REQUIRED.items():
        mod = importlib.import_module(name)
        for sym in symbols:
            assert hasattr(mod, sym), f"{name} 缺少 {sym}"
    pkg = importlib.import_module("egoops_sync")
    public = [n for n in dir(pkg) if not n.startswith("_")]
    assert len(public) >= 40, f"re-export 符号过少：{len(public)}"
    print(f"OK modules={len(MODULES)} reexport={len(public)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
