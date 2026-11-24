#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EgoOps 执行者数据 → 飞书多维表格（自动覆盖刷新）

数据来源：/api/v1/recordings/stats + /api/v1/recordings/group-stats?group_by=actor_name
目标表：Base「项目数据汇总 MT_SY」→ egoops执行者汇总

每次运行：取最新汇总 → 清空表内旧数据 → 写入新数据（覆盖式，不做追加）。

命令行：
    EgoOps同步工具.exe --cli             # 跑一次刷新
    EgoOps同步工具.exe --cli --dry-run   # 只看结果不写表
"""
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


# ---------------------------------------------------------------- 飞书开放接口（免 lark-cli）
class Feishu:
    """直接调用飞书 OpenAPI，只需 App ID + App Secret，便于换机便携使用。"""

    OPEN = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id, app_secret, base_token, table_id, timeout=60):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_token = base_token
        self.table_id = table_id
        self.timeout = timeout
        self._token = None
        self._exp = 0

    def _req(self, method, url, payload=None, headers=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        h = {"Content-Type": "application/json; charset=utf-8"}
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        if body.get("code") not in (0, None):
            raise RuntimeError(f"飞书接口错误 code={body.get('code')} msg={body.get('msg')}")
        return body

    def token(self):
        if self._token and self._exp - time.time() > 120:
            return self._token
        body = self._req("POST", f"{self.OPEN}/auth/v3/tenant_access_token/internal",
                         {"app_id": self.app_id, "app_secret": self.app_secret})
        self._token = body["tenant_access_token"]
        self._exp = time.time() + int(body.get("expire", 7200))
        return self._token

    def _hdr(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def _records_url(self, suffix=""):
        return f"{self.OPEN}/bitable/v1/apps/{self.base_token}/tables/{self.table_id}/records{suffix}"

    def list_ids(self):
        ids = []
        page_token = None
        while True:
            q = {"page_size": "500"}
            if page_token:
                q["page_token"] = page_token
            url = self._records_url() + "?" + urllib.parse.urlencode(q)
            body = self._req("GET", url, headers=self._hdr())
            data = body.get("data") or {}
            for it in data.get("items") or []:
                ids.append(it.get("record_id"))
            page_token = data.get("page_token")
            if not data.get("has_more"):
                break
        return [i for i in ids if i]

    def batch_delete(self, ids):
        n = 0
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            self._req("POST", self._records_url("/batch_delete"),
                      {"records": chunk}, headers=self._hdr())
            n += len(chunk)
        return n

    def batch_create(self, records):
        n = 0
        for i in range(0, len(records), 500):
            chunk = records[i:i + 500]
            payload = {"records": [{"fields": r} for r in chunk]}
            self._req("POST", self._records_url("/batch_create"), payload, headers=self._hdr())
            n += len(chunk)
        return n


def to_feishu_fields(row):
    """把行数据转成飞书 CellValue（datetime 用毫秒时间戳）。"""
    out = {}
    for k, v in row.items():
        if k == "更新时间" and isinstance(v, str) and v:
            try:
                out[k] = int(datetime.strptime(v, "%Y-%m-%d %H:%M").timestamp() * 1000)
                continue
            except Exception:
                pass
        if v is None or v == "":
            continue
        out[k] = v
    return out



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


# ---------------------------------------------------------------- 主流程
def sync(cfg, log, progress=None, dry_run=False):
    def step(pct, text):
        if progress:
            progress(pct, text)

    step(5, "检查登录态…")
    token_file = os.path.join(APP_DIR, "egoops_token.json")
    tok = load_token(token_file)
    if tok and tok.get("exp") and tok["exp"] - time.time() > 600:
        log(f"使用缓存登录态：{tok['payload'].get('name', '?')}"
            f"（{datetime.fromtimestamp(tok['exp']):%m-%d %H:%M} 到期）")
    else:
        log("缓存登录态不可用/即将过期，先尝试用专用浏览器配置静默续期…")
        tok = auto_refresh_from_profile(log)
        if tok:
            save_token({"access_token": tok["token"], "exp": tok["exp"], "payload": tok["payload"]}, token_file)
            log(f"静默续期成功：{tok['payload'].get('name', '?')}（{datetime.fromtimestamp(tok['exp']):%m-%d %H:%M} 到期）")
    if not tok:
        log("再从 Edge 现有会话读取…")
        tok = extract_token_from_edge(log)
        if tok:
            save_token({"access_token": tok["token"], "exp": tok["exp"], "payload": tok["payload"]}, token_file)
            log(f"已从 Edge 读取：{tok['payload'].get('name', '?')}"
                f"（{datetime.fromtimestamp(tok['exp']):%m-%d %H:%M} 到期）")
    if not tok:
        raise RuntimeError(
            "登录已失效。请点「登录 / 更新令牌」：① 打开 egoops 并确认已登录 ② F12 → Network → "
            "任意 /api/v1 请求 → Copy as cURL ③ 粘贴保存。也可命令行：--set-token \"<cURL 或 token>\"")
    if tok["exp"] and tok["exp"] < time.time():
        raise RuntimeError(
            f"登录态已过期（{datetime.fromtimestamp(tok['exp']):%Y-%m-%d %H:%M}）。"
            "请点「登录 / 更新令牌」重新粘贴 cURL；命令行：--set-token \"<cURL>\"")

    api = EgoOps(cfg["apiBase"], tok["token"])

    step(25, "拉取总体统计…")
    stats = api.stats()
    log(f"总体：{stats.get('total_count')} 条，{h(stats.get('total_duration_secs'))} 小时，"
        f"通过率 {(float(stats.get('qc_pass_duration_secs') or 0) / max(float(stats.get('total_duration_secs') or 1), 1) * 100):.1f}%")

    step(35, "拉取任务分组…")
    groups = api.by_task()
    tasks = groups.get("items") or []
    log(f"任务 {len(tasks)} 个")

    step(45, "读取场景字典…")
    try:
        names = api.tet_dict()
        log(f"场景字典 {len(names)} 项")
    except Exception as e:
        names = {}
        log(f"场景字典读取失败（用原始路径）：{e}")

    step(50, "扫描入库时间（全量）…")
    try:
        ingest = api.ingest_map(log)
    except Exception as e:
        ingest = {}
        log(f"入库时间扫描失败：{e}")

    step(62, "按任务拉取执行者数据…")
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = build_rows(stats, tasks, names,
                      lambda tid: (api.by_actor_in_task(tid).get("items") or []),
                      ingest, updated, log)
    log(f"共 {len(rows)} 行（含合计行）")

    if dry_run:
        step(100, "预览完成")
        p = os.path.join(APP_DIR, f"egoops_preview_{datetime.now():%Y%m%d_%H%M%S}.csv")
        cols = list(rows[0].keys())
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write(",".join(cols) + "\n")
            for r in rows:
                f.write(",".join('"' + str(r.get(c, "")).replace('"', '""') + '"' for c in cols) + "\n")
        log(f"预览已导出：{p}")
        return {"rows": len(rows), "cleared": 0, "written": 0}

    app_id = (cfg.get("feishuAppId") or "").strip()
    app_secret = (cfg.get("feishuAppSecret") or "").strip()

    if app_id and app_secret:
        # 便携模式：直连飞书 OpenAPI，不需要 lark-cli
        log("写入方式：飞书开放接口（App ID/Secret）")
        fs = Feishu(app_id, app_secret, cfg["baseToken"], cfg["tableId"])

        step(60, "读取表内旧数据…")
        old = fs.list_ids()
        log(f"表内旧记录 {len(old)} 条")

        step(70, "清空旧数据…")
        cleared = fs.batch_delete(old) if old else 0
        log(f"已清空 {cleared} 条")

        step(85, f"写入 {len(rows)} 行…")
        written = fs.batch_create([to_feishu_fields(r) for r in rows])
        log(f"已写入 {written} 行")
    else:
        # 兼容模式：调用本机 lark-cli
        lark_bin = resolve_lark(cfg)
        if not lark_bin:
            raise RuntimeError("既没有配置 App ID/Secret，也找不到 lark-cli")
        as_id = cfg.get("asIdentity", "bot")
        log(f"写入方式：lark-cli（--as {as_id}）")

        step(60, "读取表内旧数据…")
        old = list_record_ids(lark_bin, cfg["baseToken"], cfg["tableId"], as_id)
        log(f"表内旧记录 {len(old)} 条")

        step(70, "清空旧数据…")
        cleared = delete_records(lark_bin, cfg["baseToken"], cfg["tableId"], old, as_id, log) if old else 0
        log(f"已清空 {cleared} 条")

        step(85, f"写入 {len(rows)} 行…")
        j, out, err = batch_create(lark_bin, cfg["baseToken"], cfg["tableId"], rows, as_id)
        if not (j and j.get("ok")):
            msg = json.dumps(j.get("error"), ensure_ascii=False) if j else (err or out)
            raise RuntimeError(f"写入失败：{msg[:300]}")
        written = len(j.get("data", {}).get("record_id_list") or rows)

    step(100, "完成")
    log(f"完成：清空 {cleared} 条，写入 {written} 行（含合计行）")
    return {"rows": len(rows), "cleared": cleared, "written": written}


def run_cli(argv):
    cfg = load_cfg()
    token_file = os.path.join(APP_DIR, "egoops_token.json")
    if "--set-token" in argv:
        i = argv.index("--set-token")
        raw = argv[i + 1] if i + 1 < len(argv) else ""
        info = save_pasted_token(raw, token_file)
        if not info:
            print("未能从内容中解析出 token")
            return 2
        print(f"token 已保存：{info['payload'].get('name', '?')}，"
              f"到期 {datetime.fromtimestamp(info['exp']):%Y-%m-%d %H:%M}")
        if "--only-save" in argv:
            return 0
    dry = "--dry-run" in argv
    if "--as-user" in argv:
        cfg["asIdentity"] = "user"
    if "--check" in argv:
        ok1, msg1 = check_egoops(cfg)
        print(f"[1] egoops 登录：{'OK' if ok1 else 'NG'}  {msg1}")
        ok2, msg2, how = check_feishu(cfg)
        print(f"[2] 飞书写入：{'OK' if ok2 else 'NG'}  {msg2}")
        print(f"    写入方式：{how}")
        return 0 if (ok1 and ok2) else 1
    if "--as-bot" in argv:
        cfg["asIdentity"] = "bot"
    log = Log(os.path.join(APP_DIR, cfg.get("logFile") or "egoops_sync.log"))
    try:
        st = sync(cfg, log, progress=lambda p, t: None, dry_run=dry)
        log(f"结果：{st}")
        return 0
    except Exception as e:
        log(f"失败：{e}")
        if os.environ.get("EGOOPS_DEBUG"):
            log(traceback.format_exc())
        return 1


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


def run_gui():
    import tkinter as tk
    from tkinter import ttk, scrolledtext

    hide_console()
    cfg = load_cfg()
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("820x560")
    root.minsize(720, 500)

    q = queue.Queue()
    log = Log(os.path.join(APP_DIR, cfg.get("logFile") or "egoops_sync.log"),
              sink=lambda line: q.put(("log", line)))

    top = ttk.Frame(root, padding=10)
    top.pack(fill="x")

    ttk.Label(top, text="数据源：").grid(row=0, column=0, sticky="w")
    ttk.Label(top, text=cfg["apiBase"] + "  →  egoops执行者汇总").grid(row=0, column=1, columnspan=3, sticky="w")

    ttk.Label(top, text="Base Token：").grid(row=1, column=0, sticky="w", pady=3)
    v_base = tk.StringVar(value=cfg["baseToken"])
    ttk.Entry(top, textvariable=v_base, width=40).grid(row=1, column=1, sticky="w")

    ttk.Label(top, text="Table ID：").grid(row=1, column=2, sticky="e", padx=6)
    v_table = tk.StringVar(value=cfg["tableId"])
    ttk.Entry(top, textvariable=v_table, width=24).grid(row=1, column=3, sticky="w")

    ttk.Label(top, text="App ID：").grid(row=2, column=0, sticky="w", pady=3)
    v_appid = tk.StringVar(value=cfg.get("feishuAppId", ""))
    ttk.Entry(top, textvariable=v_appid, width=26).grid(row=2, column=1, sticky="w")
    ttk.Label(top, text="App Secret：").grid(row=2, column=2, sticky="e", padx=6)
    v_secret = tk.StringVar(value=cfg.get("feishuAppSecret", ""))
    ttk.Entry(top, textvariable=v_secret, width=34, show="*").grid(row=2, column=3, sticky="w")
    ttk.Label(top, text="（填了 App ID/Secret 就不依赖 lark-cli；不填则用本机 lark-cli）",
              foreground="#666").grid(row=3, column=0, columnspan=4, sticky="w")

    v_dry = tk.BooleanVar(value=False)
    ttk.Checkbutton(top, text="只预览（导出 CSV，不写表）", variable=v_dry).grid(row=4, column=1, sticky="w")

    bar = ttk.Frame(root, padding=(10, 0))
    bar.pack(fill="x")
    progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
    progress.pack(fill="x", pady=(4, 0))
    v_status = tk.StringVar(value="就绪：点击「开始刷新」即自动拉取并覆盖表内数据")
    ttk.Label(bar, textvariable=v_status, anchor="w").pack(fill="x")

    btns = ttk.Frame(root, padding=10)
    btns.pack(fill="x")
    box = scrolledtext.ScrolledText(root, height=16, font=("Consolas", 9))
    box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    state = {"busy": False}

    def append(t):
        box.insert("end", t + "\n")
        box.see("end")

    def worker(fn):
        if state["busy"]:
            return
        state["busy"] = True

        def run():
            try:
                fn()
            except Exception as e:
                q.put(("log", f"失败：{e}"))
                if os.environ.get("EGOOPS_DEBUG"):
                    q.put(("log", traceback.format_exc()))
                q.put(("progress", (0, "失败")))
            finally:
                state["busy"] = False
        threading.Thread(target=run, daemon=True).start()

    def do_refresh():
        def job():
            cfg["baseToken"] = v_base.get().strip()
            cfg["tableId"] = v_table.get().strip()
            cfg["feishuAppId"] = v_appid.get().strip()
            cfg["feishuAppSecret"] = v_secret.get().strip()
            save_cfg(cfg)
            log("=" * 56)
            st = sync(cfg, log, progress=lambda p, t: q.put(("progress", (p, t))), dry_run=v_dry.get())
            q.put(("progress", (100, f"完成：写入 {st['written']} 行，清空 {st['cleared']} 行")))
        worker(job)

    def do_login():
        top = tk.Toplevel(root)
        top.title("登录 egoops")
        top.geometry("760x460")
        proc_box = {"proc": None}

        ttk.Label(top, text="方式一（推荐）：用专用浏览器登录一次，之后自动续期",
                  font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 8, 8, 0)).pack(anchor="w")

        row = ttk.Frame(top)
        row.pack(fill="x", padx=10, pady=6)

        def open_login():
            try:
                proc_box["proc"] = launch_browser(headless=False)
            except Exception as e:
                q.put(("log", f"启动浏览器失败：{e}"))
                return
            q.put(("log", "已打开浏览器，请在窗口里完成 egoops 登录，然后点『读取令牌』"))

        def read_after_login():
            proc = proc_box["proc"]
            if proc:
                try:
                    proc.terminate()
                    time.sleep(1.5)
                    proc.kill()
                except Exception:
                    pass
            tok = scan_token_from_profile()
            if not tok:
                q.put(("log", "没读到令牌：请确认浏览器里已登录到 egoops 的 /recordings 页面"))
                return
            save_token({"access_token": tok["token"], "exp": tok["exp"], "payload": tok["payload"]},
                       os.path.join(APP_DIR, "egoops_token.json"))
            q.put(("log", f"✔ 登录成功：{tok['payload'].get('name', '?')}，"
                          f"到期 {datetime.fromtimestamp(tok['exp']):%Y-%m-%d %H:%M}"))
            top.destroy()

        ttk.Button(row, text="① 打开浏览器登录", command=open_login).pack(side="left")
        ttk.Button(row, text="② 我已完成登录，读取令牌", command=read_after_login).pack(side="left", padx=8)

        ttk.Separator(top).pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="方式二：直接粘贴（F12 → Network → 任意 /api/v1 请求 → Copy as cURL）").pack(anchor="w", padx=10)
        txt = tk.Text(top, height=8, font=("Consolas", 9))
        txt.pack(fill="both", expand=True, padx=10, pady=6)

        def apply():
            info = save_pasted_token(txt.get("1.0", "end"), os.path.join(APP_DIR, "egoops_token.json"))
            if not info:
                q.put(("log", "解析失败：请确认粘贴的是 cURL 或包含 Bearer 的请求头"))
                return
            q.put(("log", f"令牌已保存：{info['payload'].get('name', '?')}，"
                          f"到期 {datetime.fromtimestamp(info['exp']):%Y-%m-%d %H:%M}"))
            top.destroy()

        bar2 = ttk.Frame(top)
        bar2.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bar2, text="保存粘贴的令牌", command=apply).pack(side="left")
        ttk.Button(bar2, text="关闭", command=top.destroy).pack(side="left", padx=8)

    ttk.Button(btns, text="首次配置 / 自检", command=lambda: do_setup()).pack(side="left", padx=(0, 8))
    ttk.Button(btns, text="登录 / 更新令牌", command=do_login).pack(side="left", padx=(0, 8))

    def do_setup():
        win = tk.Toplevel(root)
        win.title("首次配置 / 环境自检")
        win.geometry("780x560")

        txt = scrolledtext.ScrolledText(win, height=20, font=("Consolas", 9))
        txt.pack(fill="both", expand=True, padx=10, pady=8)

        def w(line=""):
            txt.insert("end", line + "\n")
            txt.see("end")

        def run_checks():
            txt.delete("1.0", "end")
            cfg2 = dict(cfg)
            cfg2["baseToken"] = v_base.get().strip()
            cfg2["tableId"] = v_table.get().strip()
            cfg2["feishuAppId"] = v_appid.get().strip()
            cfg2["feishuAppSecret"] = v_secret.get().strip()
            save_cfg(cfg2)

            w("=" * 60)
            ok1, msg1 = check_egoops(cfg2)
            w(f"[1] egoops 登录：{'✔' if ok1 else '✘'} {msg1}")

            ok2, msg2, how = check_feishu(cfg2)
            w(f"[2] 飞书写入：{'✔' if ok2 else '✘'} {msg2}")

            if ok1 and ok2:
                w("")
                w("全部就绪：点「开始刷新（自动覆盖）」即可，或直接依赖定时任务。")
            else:
                w("")
                if not ok1:
                    w("→ 点顶部「登录 / 更新令牌」完成 egoops 登录")
                if not ok2:
                    w("→ 方式 A（推荐，免登录）：在开发者后台复制 App ID / App Secret，填到主窗口后点本按钮重测")
                    w("   " + DEVELOPER_CONSOLE)
                    w("→ 方式 B：确认本机 lark-cli 已配置同一个应用 " + "cli_aa71a647cd355c04")

        def do_refresh_egoops():
            try:
                proc = launch_browser(headless=False)
            except Exception as e:
                w(f"启动浏览器失败：{e}")
                return
            w("已打开 egoops 登录窗口；登录完成后关闭该窗口，再点『重新检测』")
            w("（也可在主窗口点「登录 / 更新令牌 → ② 读取令牌」）")

        def open_console():
            try:
                os.startfile(DEVELOPER_CONSOLE)
            except Exception:
                pass

        def open_egoops():
            try:
                os.startfile(EGOOPS_URL)
            except Exception:
                pass

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bar, text="重新检测", command=run_checks).pack(side="left")
        ttk.Button(bar, text="打开 egoops 登录", command=do_refresh_egoops).pack(side="left", padx=8)
        ttk.Button(bar, text="打开开发者后台", command=open_console).pack(side="left")
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")

        w("本工具写入飞书使用的是【应用身份】，换电脑不需要扫描登录飞书；")
        w("只需要保证工具能拿到应用凭证（App ID / App Secret）或本机已配置同应用的 lark-cli。")
        w("")
        run_checks()

    ttk.Button(btns, text="开始刷新（自动覆盖）", command=do_refresh).pack(side="left")
    ttk.Button(btns, text="清空日志", command=lambda: box.delete("1.0", "end")).pack(side="right")

    def pump():
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "log":
                    append(payload)
                else:
                    pct, text = payload
                    progress["value"] = pct
                    v_status.set(text)
        except queue.Empty:
            pass
        root.after(120, pump)

    pump()
    log("就绪。每次点击都会：拉取最新汇总 → 清空旧数据 → 写入新数据。")
    root.mainloop()


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
