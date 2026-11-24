"""EgoOps 平台 HTTP 客户端（拆分自 egoops_gui.py 第 172-243 行）"""

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




# ---------------------------------------------------------------- EgoOps API
class EgoOps:
    def __init__(self, api_base, token, timeout=30):
        self.api = api_base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def get(self, path, params=None):
        url = self.api + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "egoops-sync-gui/2.0",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def stats(self):
        return self.get("/api/v1/recordings/stats")

    def by_task(self):
        return self.get("/api/v1/recordings/group-stats", {"group_by": "tet_task"})

    def ingest_map(self, log=None):
        """全量扫描，统计每个 (task_id, 执行者) 的最新入库时间 created_at。"""
        import concurrent.futures
        first = self.get("/api/v1/recordings", {"page": 1, "size": 20})
        total = int(first.get("total") or 0)
        pages = (total + 19) // 20
        items = list(first.get("items") or [])

        def fetch(p):
            for _ in range(3):
                try:
                    return self.get("/api/v1/recordings", {"page": p, "size": 20}).get("items") or []
                except Exception:
                    time.sleep(0.5)
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for chunk in pool.map(fetch, range(2, pages + 1)):
                if chunk:
                    items.extend(chunk)
        m = {}
        for it in items:
            k = (str(it.get("task_id") or ""), it.get("actor_name") or "")
            ca = it.get("created_at") or ""
            if ca > m.get(k, ""):
                m[k] = ca
        if log:
            log(f"扫描 {len(items)} 条用于入库时间（{pages} 页）")
        return m

    def by_actor_in_task(self, task_id):
        return self.get("/api/v1/recordings/group-stats",
                        {"group_by": "actor_name", "task_id": task_id})

    def tet_dict(self):
        """场景中文字典：{一级路径: 中文, 一级/二级: 中文}"""
        snap = self.get("/api/v1/tet-snapshots/active").get("snapshot_data") or {}
        names = {}
        for k1, v1 in snap.items():
            n1 = (v1.get("name_zh") or k1).strip()
            names[k1] = n1
            for k2, v2 in (v1.get("children") or {}).items():
                n2 = (v2.get("name_zh") or k2).strip()
                names[f"{k1}/{k2}"] = n2
        return names
