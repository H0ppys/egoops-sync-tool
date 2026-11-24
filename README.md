# egoops-sync-tool

EgoOps 平台录制统计 → 飞书多维表格的全量刷新同步工具（Tkinter 图形界面 + CLI），单文件程序已按职责拆成模块包。

## 1. 原始来源

| 项 | 值 |
| --- | --- |
| 来源路径 | `default-workspace/egoops_gui.py` |
| 原始单文件 | `egoops_gui.py`（1158 行） |
| 配套 JS CLI | `egoops_sync.js`（264 行，headless 增量同步） |
| 打包配置 | `EgoOps同步工具.spec`、`EgoOps任务合格率.spec` 等 15 个 PyInstaller spec |
| 版本 | 内部工具，无显式版本号 |

## 2. 目录结构

```
egoops-sync-tool/
  main.py                     # 入口（等价原 egoops_gui.py）
  egoops_sync/                # 拆分后的包（11 个模块）
    config.py                 # 配置常量 / 读写 / 日志器
    token_edge.py             # Edge 登录态令牌提取与缓存
    api.py                    # EgoOps HTTP 客户端
    lark_cli.py               # lark-cli 子进程封装
    feishu.py                 # 飞书 OpenAPI 直连 + 字段映射
    checks.py                 # 环境自检
    rows.py                   # 统计 → 表格行聚合
    sync_core.py              # 同步主流程 + CLI 解析
    browser_login.py          # 专用浏览器登录/令牌扫描
    gui.py                    # Tkinter 界面
    cli.py                    # 入口分发
  js/                         # Node 侧 headless 同步器
    egoops_sync.js
    egoops_sync.config.example.json
  legacy/egoops_gui.py        # 原单文件（已脱敏）
  docs/                       # 架构、拆分映射、打包、脱敏说明
  tests/smoke_test.py
```

## 3. 安装与运行

```bash
# 仅需 Python 3.10+，无第三方依赖（GUI 用 tkinter，HTTP 用 urllib）
python main.py                  # 图形界面
python main.py --cli            # 跑一次刷新
python main.py --cli --dry-run  # 只抓取不写表
python main.py --hidden         # 隐藏控制台窗口（计划任务用）
```

Node 侧增量同步：

```bash
cd js
cp egoops_sync.config.example.json egoops_sync.config.json   # 填入真实配置
node egoops_sync.js --days 3
node egoops_sync.js --since 2026-11-17
node egoops_sync.js --full --dry-run
```

## 4. 模块说明

| 模块 | 职责 | 关键符号 |
| --- | --- | --- |
| `config` | 默认配置、`egoops_gui_config.json` 读写、`Log` 日志器 | `DEFAULT_CFG`、`load_cfg`、`save_cfg`、`Log` |
| `token_edge` | 从 Edge 配置目录提取 EgoOps JWT，本地缓存读写 | `extract_token_from_edge`、`save_token`、`load_token` |
| `api` | `/api/v1/recordings/stats` 等接口调用、分页、重试 | `EgoOps` |
| `lark_cli` | `lark-cli` 子进程封装：列行、删行、批量创建 | `run_lark`、`list_record_ids`、`delete_records`、`batch_create` |
| `feishu` | 飞书 OpenAPI 直连（tenant_access_token + records CRUD） | `Feishu`、`to_feishu_fields` |
| `checks` | EgoOps / 飞书连通性与凭证自检 | `check_egoops`、`check_feishu` |
| `rows` | 按任务 × 执行者聚合，生成表格行 | `build_rows` |
| `sync_core` | 一次完整同步（清表 + 写入）、`--cli` 参数解析 | `sync`、`run_cli` |
| `browser_login` | 启动专用浏览器、扫描 profile 令牌、粘贴令牌 | `launch_browser`、`scan_token_from_profile` |
| `gui` | 主窗口、按钮流程、进度与日志显示 | `run_gui` |
| `cli` | 入口分发（GUI / CLI / 隐藏控制台） | `main` |

## 5. 配置

运行时配置写在可执行文件旁的 `egoops_gui_config.json`，由 GUI「配置」区维护：

```json
{
  "apiBase": "<FILL_EGOOPS_API_BASE>",
  "baseToken": "<FILL_BASE_TOKEN>",
  "tableId": "<FILL_TABLE_ID>",
  "larkBin": "",
  "asIdentity": "bot",
  "logFile": "egoops_sync.log",
  "feishuAppId": "",
  "feishuAppSecret": ""
}
```

两种写表方式：
1. 填 `feishuAppId` + `feishuAppSecret` → 走飞书 OpenAPI，免登录、可换机；
2. 留空 → 调用本机已授权的 `lark-cli`（`larkBin` 可指定路径）。

## 6. 打包

原项目用 PyInstaller `--onefile --windowed`，spec 见 `docs/PACKAGING.md`。拆分后打包只需把入口从 `egoops_gui.py` 换成 `main.py`，隐藏导入无需额外配置（模块依赖已显式）。

## 7. 拆分说明

- 拆分为纯搬运：函数体逐字保留，仅新增模块 docstring、`from .x import ...` 依赖导入与 `__init__.py` 全量 re-export。
- 详细行段映射见 [docs/SPLIT.md](docs/SPLIT.md)，模块职责与数据流见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
- 原单文件保留在 `legacy/egoops_gui.py`，便于对照（其中 token/表 ID/接口域名已脱敏，见 [docs/SECRETS.md](docs/SECRETS.md)）。

## 8. 校验

```bash
python -c "import compileall,sys; sys.exit(0 if compileall.compile_dir('.',quiet=1) else 1)"
python tests/smoke_test.py
node --check js/egoops_sync.js
```
