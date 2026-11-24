# 拆分映射表（egoops_sync）

源文件：`C:\Users\Administrator\Documents\deepseek-harness\default-workspace\egoops_gui.py`（共 1158 行，公共 import 区 26 行）

| 原文件 | 行段 | 新模块 | 行数 | 依赖的包内模块 |
| --- | --- | --- | --- | --- |
| egoops_gui.py | 28-84 | egoops_sync/config.py | 57 | - |
| egoops_gui.py | 85-171 | egoops_sync/token_edge.py | 87 | - |
| egoops_gui.py | 172-243 | egoops_sync/api.py | 72 | - |
| egoops_gui.py | 244-374 | egoops_sync/lark_cli.py | 131 | config |
| egoops_gui.py | 375-467 | egoops_sync/feishu.py | 93 | - |
| egoops_gui.py | 468-514 | egoops_sync/checks.py | 47 | config, feishu, lark_cli, token_edge |
| egoops_gui.py | 515-585 | egoops_sync/rows.py | 71 | - |
| egoops_gui.py | 586-749 | egoops_sync/sync_core.py | 164 | api, browser_login, checks, config, feishu, lark_cli, rows, token_edge |
| egoops_gui.py | 750-890 | egoops_sync/browser_login.py | 141 | config, token_edge |
| egoops_gui.py | 891-1137 | egoops_sync/gui.py | 247 | browser_login, checks, config, sync_core, token_edge |
| egoops_gui.py | 1138-1158 | egoops_sync/cli.py | 21 | browser_login, config, gui, sync_core |

## 校验

- 每个模块 `py_compile` 通过
- 包 `__init__.py` 全量 re-export 原符号
- 循环依赖：无

## 未在包内解析的名字（需人工确认来源）

```json
{
 "config": [
  "datetime",
  "json",
  "os",
  "sys"
 ],
 "token_edge": [
  "base64",
  "json",
  "os"
 ],
 "api": [
  "json",
  "time",
  "urllib"
 ],
 "lark_cli": [
  "json",
  "os",
  "shutil",
  "time"
 ],
 "feishu": [
  "datetime",
  "json",
  "time",
  "urllib"
 ],
 "checks": [
  "datetime",
  "os",
  "time"
 ],
 "sync_core": [
  "datetime",
  "json",
  "os",
  "tid",
  "time",
  "traceback"
 ],
 "browser_login": [
  "os",
  "time"
 ],
 "gui": [
  "datetime",
  "os",
  "p",
  "queue",
  "threading",
  "time",
  "traceback"
 ],
 "cli": [
  "datetime",
  "os",
  "sys",
  "traceback"
 ]
}
```
