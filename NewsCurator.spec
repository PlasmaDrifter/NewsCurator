# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

datas = [
    ('app/templates', 'app/templates'),
    ('app/templates', 'templates'),
    ('app/static', 'app/static'),
    ('app/static', 'static'),
    ('app/app', 'app/app'),
]

a = Analysis(
    ['desktop.py'],
    pathex=['.', 'app'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'sqlite3',
        '_sqlite3',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'starlette',
        'starlette.routing',
        'starlette.middleware',
        'starlette.staticfiles',
        'starlette.templating',
        'starlette.responses',
        'feedparser',
        'jinja2',
        'webview',
        'gi',
        'gi.repository.Gtk',
        'gi.repository.WebKit2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio', 'tensorflow', 'scipy',
        'matplotlib', 'pandas', 'numpy', 'pygame', 'PyQt5', 'PyQt6',
        'PySide6', 'PySide2', 'cv2', 'sklearn', 'tkinter', 'IPython',
        'jupyter', 'notebook', 'unittest', 'pydoc', 'doctest', 'test',
        'pdb', 'curses', 'xmlrpc', 'multiprocessing.test'
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Strip out unnecessary OS icon themes/locales/fonts
a.datas = [
    d for d in a.datas
    if not (
        d[1].startswith('/usr/share/icons')
        or d[1].startswith('/usr/share/locale')
        or d[1].startswith('/usr/share/fonts')
        or d[1].startswith('/usr/share/doc')
    )
]

# Exclude unnecessary heavy shared libraries (keep libsqlite3 for database)
excluded_bin_patterns = (
    'libsystemd',
    'libreadline',
    'libtinfo',
    'libdbus',
    'fips.so',
)
a.binaries = [
    b for b in a.binaries
    if not any(p in b[0] for p in excluded_bin_patterns)
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='NewsCurator',
    icon='app/static/favicons/newspaper.svg',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[
        'libgnutls.so.30',
        'libgmp.so.10',
        'libhogweed.so.6',
        'libnettle.so.8',
        'libcrypto.so.3',
        'libssl.so.3'
    ],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
