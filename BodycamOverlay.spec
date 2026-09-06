# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['overlay_app.py'],
    pathex=[],
    binaries=[],
    datas=[('families.json', '.'), ('maps.json', '.'), ('gamemodes.json', '.'), ('app_icon.ico', '.'), ('mod', 'mod'), ('ue4ss_bundle', 'ue4ss_bundle')],
    hiddenimports=['pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BodycamOverlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
