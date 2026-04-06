"""
setup.py  –  Cython build for Li-Fi protected modules
======================================================
Compiles the core algorithm files to native binary extensions:
  lifi_analyser.pyx       → lifi_analyser.pyd   (Windows)  / .so (macOS/Linux)
  lifi_transmitter.pyx    → lifi_transmitter.pyd
  lifi_receiver.pyx       → lifi_receiver.pyd
  lifi_hardware_protocol.pyx → lifi_hardware_protocol.pyd

Usage (run in the repo root):
  pip install cython setuptools
  python setup.py build_ext --inplace

The GUI (lifi_gui.py) imports these exactly as before — no changes needed.
Original .py files are NOT included in the distribution.
"""

from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy as np
import sys

# Compiler flags for optimisation and symbol stripping
extra_compile_args = []
extra_link_args    = []

if sys.platform == "win32":
    extra_compile_args = ["/O2"]          # MSVC optimise
elif sys.platform == "darwin":
    extra_compile_args = ["-O2", "-w"]
    extra_link_args    = ["-Wl,-strip-all"]
else:
    extra_compile_args = ["-O2", "-w"]
    extra_link_args    = ["-Wl,--strip-all"]

# Modules to protect  (rename .py → .pyx before running this)
protected_modules = [
    "lifi_analyser",
    "lifi_transmitter",
    "lifi_receiver",
    "lifi_hardware_protocol",
]

extensions = [
    Extension(
        name   = mod,
        sources= [f"{mod}.pyx"],
        include_dirs       = [np.get_include()],
        extra_compile_args = extra_compile_args,
        extra_link_args    = extra_link_args,
    )
    for mod in protected_modules
]

setup(
    name    = "LiFi-BM-ES",
    version = "1.0.0",
    ext_modules = cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "boundscheck"   : False,
            "wraparound"    : False,
            "cdivision"     : True,
        },
        nthreads = 1,
    ),
)
