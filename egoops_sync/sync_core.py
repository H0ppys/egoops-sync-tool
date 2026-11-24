"""一次同步主流程与命令行入口解析（拆分自 egoops_gui.py 第 586-749 行）"""

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
from .api import EgoOps  # noqa: F401
from .browser_login import auto_refresh_from_profile, save_pasted_token  # noqa: F401
from .checks import check_egoops, check_feishu  # noqa: F401
from .config import APP_DIR, Log, load_cfg  # noqa: F401
from .feishu import Feishu, to_feishu_fields  # noqa: F401
from .lark_cli import batch_create, delete_records, list_record_ids, resolve_lark  # noqa: F401
from .rows import build_rows, h  # noqa: F401
from .token_edge import extract_token_from_edge, load_token, save_token  # noqa: F401




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
