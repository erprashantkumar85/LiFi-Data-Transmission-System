# -*- mode: python ; coding: utf-8 -*-
#
# lifi_gui.spec  –  PyInstaller spec for Li-Fi BM-ES (Windows)
# =============================================================
# Run AFTER Cython build:
#   pyinstaller lifi_gui.spec
#
# Output: dist\LiFi_BM-ES\LiFi_BM-ES.exe  (folder mode — faster startup)
#      or dist\LiFi_BM-ES.exe              (onefile mode — single file)
#
# Switch between modes by toggling ONEFILE below.

ONEFILE = False          # Set True for single-file distribution

block_cipher = None

a = Analysis(
    ['lifi_gui.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Include any resource files here if you add icons/images later
        # ('assets', 'assets'),
    ],
    hiddenimports=[
        # PyQt5 plugins needed on Windows
        'PyQt5.sip',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'PyQt5.QtPrintSupport',
        # pyqtgraph
        'pyqtgraph',
        'pyqtgraph.exporters',
        'pyqtgraph.exporters.ImageExporter',
        'pyqtgraph.exporters.SVGExporter',
        'pyqtgraph.exporters.CSVExporter',
        'pyqtgraph.graphicsItems.ViewBox.axisCtrlTemplate_pyqt5',
        'pyqtgraph.graphicsItems.PlotItem.plotConfigTemplate_pyqt5',
        'pyqtgraph.imageview.ImageViewTemplate_pyqt5',
        # scipy hidden imports
        'scipy.special._ufuncs_cxx',
        'scipy.linalg.cython_blas',
        'scipy.linalg.cython_lapack',
        'scipy.integrate',
        'scipy.integrate._odepack',
        'scipy.special',
        # PIL / Pillow
        'PIL._tkinter_finder',
        'PIL.Image',
        # serial
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
        # numpy
        'numpy.core._dtype_ctypes',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',   # not used in GUI (replaced by pyqtgraph)
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'sphinx',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name            = 'LiFi_BM-ES',
        debug           = False,
        bootloader_ignore_signals = False,
        strip           = False,
        upx             = True,
        upx_exclude     = [],
        runtime_tmpdir  = None,
        console         = False,        # no console window
        disable_windowed_traceback = False,
        argv_emulation  = False,
        target_arch     = None,
        codesign_identity = None,
        entitlements_file = None,
        # icon          = 'assets/icon.ico',  # uncomment if you have an icon
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries= True,
        name            = 'LiFi_BM-ES',
        debug           = False,
        bootloader_ignore_signals = False,
        strip           = False,
        upx             = True,
        console         = False,
        disable_windowed_traceback = False,
        argv_emulation  = False,
        target_arch     = None,
        codesign_identity = None,
        entitlements_file = None,
        # icon          = 'assets/icon.ico',
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip           = False,
        upx             = True,
        upx_exclude     = [],
        name            = 'LiFi_BM-ES',
    )
