from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
binaries = collect_dynamic_libs('av') + collect_dynamic_libs('pylibsrtp')
datas = collect_data_files('lumacapture')
for package in ('aiortc', 'av', 'aiohttp', 'cryptography', 'psutil', 'qrcode', 'numpy', 'pillow', 'PySide6-Essentials', 'PySide6-Addons'):
    datas += copy_metadata(package)
for document in ('LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md'):
    datas.append((str(root/document), '.'))
hidden = collect_submodules('av') + collect_submodules('aiortc') + ['PySide6.QtNetwork', 'PySide6.QtSvg']
if sys.platform == 'win32':
    hidden += collect_submodules('windows_capture')
    hidden += collect_submodules('pyaudiowpatch')
    datas += copy_metadata('PyAudioWPatch')
    binaries += collect_dynamic_libs('windows_capture')
    binaries += collect_dynamic_libs('pyaudiowpatch')
a = Analysis([str(root/'packaging'/'entry.py')], pathex=[str(root/'src')], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtPdf', 'matplotlib', 'tkinter'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LumaCapture', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True,
          icon=str(root/'src/lumacapture/assets/lumacapture.ico') if sys.platform == 'win32' else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LumaCapture')
