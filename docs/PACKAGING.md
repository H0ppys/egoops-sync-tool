# 打包说明（egoops-sync-tool）

## 原打包方式

PyInstaller onefile、`runtime_tmpdir='.'`、`upx=True`、`console=True`（15 个 spec 变体，差异只在 `name` 与是否 `--windowed`）。

代表性 spec（原文）：

```python
a = Analysis(
    ['egoops_gui.py'],
    pathex=[], binaries=[], datas=[], hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='EgoOps任务合格率',
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=True, upx_exclude=[], runtime_tmpdir='.',
    console=True, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)
```

## 拆分后如何打包

入口从 `egoops_gui.py` 换成 `main.py`：

```bash
python -m PyInstaller --onefile --console --runtime-tmpdir . \
  --name EgoOps同步工具 main.py

# 图形版（无控制台）
python -m PyInstaller --onefile --windowed --runtime-tmpdir . \
  --name EgoOps同步工具_gui main.py
```

要点：
- `--runtime-tmpdir .` 让 onefile 解包到 exe 同目录，`APP_DIR`（`config.APP_DIR`）仍按 exe 位置取，配置文件与日志继续落在 exe 旁。
- 模块化后无隐式导入需求：`egoops_sync/*.py` 均被 `__init__.py` 静态 re-export，PyInstaller 的静态分析可完整收集。
- tkinter 由 PyInstaller hook 自动带上；无需 `hiddenimports`。

## 已验证的产物结构（本仓库 `docs/UNPACK.md` 有更细的提取结果）

onefile exe 内部为 PyInstaller CArchive：入口脚本 `egoops_gui`（pyc）+ `PYZ.pyz`（381 个模块，含标准库）。PYZ 头部布局：`b'PYZ\0'` + 4B pyc magic + 4B TOC offset(BE)，TOC 是 `marshal` 序列化的 `[(name, (pos, len, ulen)), …]`。
