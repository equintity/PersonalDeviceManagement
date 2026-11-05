# -*- mode: python ; coding: utf-8 -*-
# PersonalDeviceManagement 桌面版 PyInstaller 打包配置(onedir)。
# 由 scripts/build_desktop.py 调用;手动构建:
#   pyinstaller --noconfirm --clean PersonalDeviceManagement.spec
from PyInstaller.utils.hooks import (
    collect_submodules,
    collect_data_files,
    collect_dynamic_libs,
)

datas = [
    ("pdl/static", "pdl/static"),              # 前端资源
    ("firmware/template", "firmware/template"),  # 固件代码层模板
    ("LICENSE", "."),
    ("README.md", "."),
]
datas += collect_data_files("webview")          # WebView2 互操作 DLL 等

binaries = []
binaries += collect_dynamic_libs("webview")
binaries += collect_dynamic_libs("pythonnet")   # Python.Runtime.dll

a = Analysis(
    ["desktop/launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("webview") + ["clr"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pydoc_data", "test", "lib2to3",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="PersonalDeviceManagement",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 不加壳:降低杀软误报,保证可审计
    console=False,           # 桌面应用无控制台;--selftest 结果写报告文件
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PersonalDeviceManagement",
)
