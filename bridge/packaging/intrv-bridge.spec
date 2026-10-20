# PyInstaller spec: one-file `intrv-bridge` executable for the current OS (build on each target OS).
# -*- mode: python -*-
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["../src/intrv_bridge/cli.py"],
    pathex=["../src"],
    hiddenimports=collect_submodules("intrv_bridge") + collect_submodules("websockets") + ["gguf"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="intrv-bridge",
    console=True,
    upx=False,
)
