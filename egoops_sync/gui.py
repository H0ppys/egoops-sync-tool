"""Tkinter 图形界面（状态、日志、按钮编排）（拆分自 egoops_gui.py 第 891-1137 行）"""

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
from .browser_login import EGOOPS_URL, hide_console, launch_browser, save_pasted_token, scan_token_from_profile  # noqa: F401
from .checks import DEVELOPER_CONSOLE, check_egoops, check_feishu  # noqa: F401
from .config import APP_DIR, APP_TITLE, Log, load_cfg, save_cfg  # noqa: F401
from .sync_core import sync  # noqa: F401
from .token_edge import save_token  # noqa: F401




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
