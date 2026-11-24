"""lark-cli 子进程封装（拉表、删行、批量写入）（拆分自 egoops_gui.py 第 244-374 行）"""

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




# ---------------------------------------------------------------- 飞书
def resolve_lark(cfg):
    p = (cfg.get("larkBin") or "").strip()
    if p and os.path.isfile(p):
        return p
    for cand in (
        os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules",
                     "@larksuite", "cli", "bin", "lark-cli.exe"),
        shutil.which("lark-cli"),
        "/usr/local/bin/lark-cli",
    ):
        if cand and os.path.isfile(cand):
            return cand
    return None


def work_tmpdir(prefix="egoops_"):
    """临时文件放在程序目录（打包后无法写系统 %TEMP%）。"""
    base = os.path.join(APP_DIR, ".tmp")
    os.makedirs(base, exist_ok=True)
    d = os.path.join(base, f"{prefix}{os.getpid()}_{int(time.time() * 1000) % 100000}")
    os.makedirs(d, exist_ok=True)
    return d


def run_lark(lark_bin, args, timeout=180, as_identity="bot"):
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("_PYI") and k not in ("_MEIPASS", "_MEIPASS2")}
    if env.get("PATH"):
        env["PATH"] = os.pathsep.join(
            p for p in env["PATH"].split(os.pathsep) if p and "_MEI" not in p.upper())
    cmd = [lark_bin] + list(args)
    if as_identity:
        cmd += ["--as", as_identity]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace", close_fds=True,
                       stdin=subprocess.DEVNULL, env=env, cwd=os.path.expanduser("~"))
    out = r.stdout or ""
    try:
        j = json.loads(out)
    except Exception:
        j = None
    return j, out, (r.stderr or "")


def list_record_ids(lark_bin, base_token, table_id, as_id):
    """读出表内所有 record_id（用于清空）。"""
    ids = []
    tmpdir = work_tmpdir("ids_")
    try:
        offset = 0
        for rnd in range(50):
            p = os.path.join(tmpdir, f"ids_{rnd}.ndjson")
            j, out, err = run_lark(lark_bin, [
                "base", "+record-list", "--base-token", base_token,
                "--table-id", table_id, "--limit", "2000", "--offset", str(offset),
                "--format", "ndjson", "--output", p, "--overwrite",
            ], as_identity=as_id)
            if not j or not os.path.exists(p):
                raise RuntimeError(f"读取记录失败: {(json.dumps(j.get('error'), ensure_ascii=False) if j else err)[:300]}")
            cnt = j.get("records_count") or 0
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rid = json.loads(line).get("record_id") or json.loads(line).get("_record_id")
                    except Exception:
                        rid = None
                    if rid:
                        ids.append(rid)
            if not j.get("has_more") or cnt == 0:
                break
            offset += cnt
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return ids


def delete_records(lark_bin, base_token, table_id, ids, as_id, log):
    """并发删除（bot 身份不依赖本机密钥，可安全并发；user 身份串行）。"""
    from concurrent.futures import ThreadPoolExecutor

    if not ids:
        return 0
    ok = 0
    errors = []
    workers = 1 if as_id == "user" else 4

    def one(rid):
        j, out, err = run_lark(lark_bin, [
            "base", "+record-delete", "--base-token", base_token,
            "--table-id", table_id, "--record-id", rid, "--yes",
        ], as_identity=as_id)
        return j, out, err

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (j, out, err) in enumerate(pool.map(one, ids), 1):
            if j and j.get("ok"):
                ok += 1
            else:
                msg = json.dumps(j.get("error"), ensure_ascii=False) if j else (err or out)
                errors.append(msg)
                if "app_scope_not_applied" in msg:
                    raise RuntimeError(
                        "应用缺少删除记录权限（base:record:delete）。"
                        "请在开发者后台开通后重试，或用 --as-user 以个人身份运行。")
            if i % 10 == 0:
                log(f"  已清空 {i}/{len(ids)}")
    if errors:
        log(f"  {len(errors)} 条删除失败，示例：{errors[0][:160]}")
    return ok


def batch_create(lark_bin, base_token, table_id, records, as_id):
    tmpdir = work_tmpdir("batch_")
    try:
        p = os.path.join(tmpdir, "batch.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"create_records": records}, f, ensure_ascii=False)
        j, out, err = run_lark(lark_bin, [
            "base", "+record-batch-create", "--base-token", base_token,
            "--table-id", table_id, "--json", "@" + p,
        ], timeout=300, as_identity=as_id)
        return j, out, err
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
