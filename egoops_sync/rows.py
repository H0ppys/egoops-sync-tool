"""统计结果转飞书表格行（按任务 × 执行者聚合）（拆分自 egoops_gui.py 第 515-585 行）"""

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





# ---------------------------------------------------------------- 组表
def h(x):
    return round(float(x or 0) / 3600.0, 2)


def gb(x):
    return round(float(x or 0) / (1024 ** 3), 1)


def build_rows(stats, tasks, names, fetch_actors, ingest, updated_at, log):
    """一行 = 一个任务下的一个执行者（任务 × 执行者）。"""
    rows = []

    def zh(path):
        if not path:
            return ""
        return names.get(path) or path.split("/")[-1]

    total_dur = float(stats.get("total_duration_secs") or 0)
    total_pass = float(stats.get("qc_pass_duration_secs") or 0)
    rows.append({
        "任务": "＝合计＝",
        "任务ID": "",
        "一级场景": "",
        "二级场景": "",
        "执行者": "",
        "录制总数": int(stats.get("total_count") or 0),
        "总时长（小时）": h(total_dur),
        "质检通过（小时）": h(total_pass),
        "质检拒绝（小时）": h(total_dur - total_pass),
        "合格率（%）": round(total_pass / total_dur * 100) if total_dur else 0,
        "大小（GB）": gb(stats.get("total_size_bytes")),
        "最近录制": "",
        "更新时间": updated_at,
    })

    tasks = sorted(tasks, key=lambda x: -(float(x.get("total_duration_secs") or 0)))
    for t in tasks:
        tid = str(t.get("task_id") or "")
        l1 = zh(t.get("level1_tet_path"))
        l2 = zh(t.get("level2_tet_path"))
        task_label = "-".join(x for x in (l1, l2, t.get("task_name") or "") if x)
        try:
            actors = fetch_actors(tid)
        except Exception as e:
            log(f"  任务 {tid} 的执行者数据读取失败：{e}")
            continue
        actors.sort(key=lambda x: -(float(x.get("total_duration_secs") or 0)))
        for a in actors:
            d = float(a.get("total_duration_secs") or 0)
            pss = float(a.get("qc_pass_duration_secs") or 0)
            rows.append({
                "任务": task_label,
                "任务ID": tid,
                "一级场景": l1,
                "二级场景": l2,
                "执行者": a.get("group_name") or a.get("group_key") or "",
                "录制总数": int(a.get("recording_count") or 0),
                "总时长（小时）": h(d),
                "质检通过（小时）": h(pss),
                "质检拒绝（小时）": h(d - pss),
                "合格率（%）": round(pss / d * 100) if d else 0,
                "大小（GB）": gb(a.get("total_size_bytes")),
                "最近录制": (a.get("latest_record_time") or "")[:10],
                "最近入库": (ingest.get((tid, a.get("group_name") or "")) or "")[:10],
                "更新时间": updated_at,
            })
    return rows
