# 架构说明（egoops-sync-tool）

## 数据流

```
Edge 登录态 ──► token_edge.extract_token_from_edge ──► local token cache
                               │
                               ▼
                    api.EgoOps  ──► /api/v1/recordings/stats
                               │        /api/v1/recordings/group-stats?group_by=actor_name
                               ▼
                    rows.build_rows  ──► [{字段: 值}, …]
                               │
              ┌────────────────┴────────────────┐
              ▼                                  ▼
    feishu.Feishu（OpenAPI 直连）        lark_cli.batch_create（本机 lark-cli）
              └────────────────┬────────────────┘
                               ▼
                     飞书多维表格（覆盖式刷新）
```

`sync_core.sync()` 是唯一的编排点：先取统计 → 聚合成行 → 清空旧记录 → 批量写入 → 写日志。GUI（`gui.py`）与 CLI（`sync_core.run_cli`）都只调用它，不直接碰 HTTP。

## 分层

| 层 | 模块 | 说明 |
| --- | --- | --- |
| 基础设施 | `config`、`token_edge` | 配置、路径、日志、令牌；不做业务判断 |
| 外部系统客户端 | `api`、`feishu`、`lark_cli` | 各自封装一种外部依赖，互不调用 |
| 领域逻辑 | `rows`、`sync_core` | 统计聚合与同步编排 |
| 自检 | `checks` | 供 GUI/CLI 在正式跑前探测凭证 |
| 交互 | `browser_login`、`gui`、`cli` | 浏览器登录、界面、参数分发 |

## 依赖方向

`cli → gui → sync_core → {api, feishu, lark_cli, rows} → config/token_edge`

- 无循环依赖（拆分器 AST 检查结果：cycles = []）。
- `checks` 只被 `gui`、`sync_core` 调用，自身只依赖客户端与配置。
- `browser_login` 依赖 `config`（路径/日志）与 `token_edge`（写缓存），不依赖 UI。

## 与原单文件的差异

- 原文件所有模块级常量（`APP_TITLE`、`DEFAULT_CFG`、`CFG_NAME`、`APP_DIR`）集中在 `config.py`；
- 每个模块顶部保留原文件的公共 import 区（拆分器自动复制），因此各模块可独立编译；
- `__init__.py` 全量 re-export 原 53 个公共符号，旧代码 `from egoops_sync import build_rows` 仍然可用。
