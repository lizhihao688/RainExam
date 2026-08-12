# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for RainExam
打包命令（在项目根目录执行）：
    pip install pyinstaller
    pyinstaller RainExam.spec
生成的 exe 在 dist/RainExam.exe
"""

block_cipher = None

a = Analysis(
    ['src/gui.py'],
    pathex=['.', 'src'],
    binaries=[],
    datas=[
        # 把 .env.example 和 static/ 一并打进去
        ('.env.example', '.'),
        ('static', 'static'),
    ],
    hiddenimports=[
        'extract_questions',
        'openai',
        'httpx',
        'tkinter',
        'tkinter.ttk',
        'tkinter.scrolledtext',
        'tkinter.messagebox',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RainExam',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # False = 不弹黑色命令窗口，只显示 GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # 可在此填 icon.ico 路径
)
