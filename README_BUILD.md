# Li-Fi BM-ES  —  Build & Distribution Guide

## Repo layout expected by the build system

```
your-repo/
├── lifi_gui.py                    ← entry point (not protected, kept as .py)
├── lifi_hardware_protocol.py      ← protected: compiled to .pyd
├── lifi_analyser.py               ← protected: compiled to .pyd
├── lifi_transmitter.py            ← protected: compiled to .pyd
├── lifi_receiver.py               ← protected: compiled to .pyd
├── setup.py                       ← Cython build script
├── lifi_gui.spec                  ← PyInstaller spec
└── .github/
    └── workflows/
        └── build_windows.yml      ← GitHub Actions CI
```

---

## Step 1 — Create a GitHub repository

```bash
git init
git remote add origin https://github.com/YOUR_USERNAME/lifi-bm-es.git
```

Add all files and push:

```bash
git add lifi_gui.py lifi_hardware_protocol.py lifi_analyser.py \
        lifi_transmitter.py lifi_receiver.py \
        setup.py lifi_gui.spec \
        .github/workflows/build_windows.yml
git commit -m "Initial commit"
git push -u origin main
```

---

## Step 2 — Watch the build

1. Go to your repo on GitHub  
2. Click the **Actions** tab  
3. You will see **"Build Windows Release"** running automatically  
4. Click into it to watch each step live  

The full build takes **3–5 minutes**.

---

## Step 3 — Download the Windows exe

When the workflow completes (green tick):

1. Click the workflow run  
2. Scroll to **Artifacts** at the bottom  
3. Download **LiFi-BM-ES-Windows.zip**  
4. Unzip → run `LiFi_BM-ES.exe`

The zip contains a folder with the exe + required Qt/Python DLLs.  
No Python installation needed on the target Windows machine.

---

## What gets protected

| File | In repo | In dist |
|---|---|---|
| `lifi_gui.py` | ✓ Python source | bundled by PyInstaller |
| `lifi_analyser.py` | ✓ Python source | **compiled → .pyd, source deleted** |
| `lifi_transmitter.py` | ✓ Python source | **compiled → .pyd, source deleted** |
| `lifi_receiver.py` | ✓ Python source | **compiled → .pyd, source deleted** |
| `lifi_hardware_protocol.py` | ✓ Python source | **compiled → .pyd, source deleted** |

The `.pyd` files are native Windows DLLs — they cannot be decompiled back  
to Python. The algorithms are completely hidden from end users.

---

## Triggering a build manually (without pushing)

1. Go to **Actions → Build Windows Release**  
2. Click **"Run workflow"** → **"Run workflow"**  
3. Download artifact when done  

---

## Switching to single-file .exe

Edit `lifi_gui.spec`, change:

```python
ONEFILE = False   →   ONEFILE = True
```

Push the change. The next build produces a single `LiFi_BM-ES.exe`  
(larger file, slower first launch, but easier to share).

---

## Local macOS development (no compilation needed)

```bash
pip install PyQt5 pyqtgraph numpy pillow scipy pyserial
python lifi_gui.py
```

The `.py` source files are used directly on macOS — compilation is only  
done on the Windows GitHub runner.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: lifi_hardware_protocol` | Check the `.pyd` files exist in the dist folder |
| Workflow fails at "Compile Cython" | Check `setup.py` lists all four module names |
| Workflow fails at "Prepare .pyx" | Ensure all four `.py` files are committed to the repo |
| App crashes on launch (Windows) | Run from command prompt to see the error: `LiFi_BM-ES.exe` |
| Qt platform plugin missing | The entire `dist\LiFi_BM-ES\` folder must stay together — do not move just the .exe |
