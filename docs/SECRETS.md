# 脱敏说明（egoops-sync-tool）

仓库内不保存任何可用于登录/写表的凭据。以下位置在拆分后被替换为占位符（`legacy/egoops_gui.py` 同步替换，因此 `legacy/` 与原始文件不再逐字节一致）：

| 文件 | 字段 | 原值类型 | 现占位符 |
| --- | --- | --- | --- |
| `egoops_sync/config.py` | `DEFAULT_CFG["baseToken"]` | 飞书多维表格 app_token | `<FILL_BASE_TOKEN>` |
| `egoops_sync/config.py` | `DEFAULT_CFG["tableId"]` | 飞书 table_id | `<FILL_TABLE_ID>` |
| `egoops_sync/config.py` | `DEFAULT_CFG["apiBase"]` | 内网接口根地址 | `<FILL_EGOOPS_API_BASE>` |
| `legacy/egoops_gui.py` | 同上三处 | — | 同上 |
| `js/egoops_sync.config.example.json` | `baseToken` / `tableId` / `apiBase` / `larkBin` | 同上 + 本机路径 | `<FILL_*>` |
| `js/egoops_sync.js` | 无硬编码凭据（读同目录 json） | — | — |

未纳入仓库的运行时文件：`egoops_gui_config.json`、`egoops_token.json`、`egoops_sync.log`、`egoops_sync.config.json`（真实配置）。

## 使用时怎么填

1. 复制 `js/egoops_sync.config.example.json` → `js/egoops_sync.config.json`，填入真实 `apiBase` / `baseToken` / `tableId`；
2. GUI 版首次启动会在 exe 旁生成 `egoops_gui_config.json`，在界面「配置」区填写同样的值（或填 App ID / App Secret 走 OpenAPI）。
