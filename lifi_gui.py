"""
Li-Fi Image Transmission System  –  PyQt5 GUI  (v5)
====================================================
Single unified app — Hardware Mode with simulation built-in.

Tabs:
  1. TX  (Image / Text)
       • Hardware: converts image → 128x64 1-bpp bitmap → {0xHH...} hex → TX Arduino
       • Simulation: uses OOK protocol pipeline (no hardware needed)
       • Text mode: sends <text> frame to TX Arduino

  2. RX  (Monitor / Simulation)
       • Hardware: monitors RX Arduino serial output (57600 baud)
         → auto-parses "Count:NNN / w,h,b0,...,bN;" echo into a bitmap
         → shows 128x64 image preview + decoded CSV
       • Simulation: adds configurable noise to transmitted image
       • "Parse Echo" button: manual parse of log buffer
       • "Load Echo File" button: load TX_Data.txt / RX_Data.txt offline

  3. Analysis
       • Image comparison (TX vs RX vs error map)
       • Quality metrics: MSE, PSNR, BER, SNR, SSIM, Data Rate
       • Distance vs Quality graphs (offscreen QPainter, crash-safe)
       • "Load TX/RX Echo" buttons for offline analysis from .txt files

  4. Serial Settings  (TX port + RX port, both fixed 57600 baud)

Echo protocol (from tx.ino / rx.ino):
  "Count : 1027\n128,64,b0,b1,...,b1023;\n"
  First 2 values = width(128), height(64).  Next 1024 = column-major 1-bpp bitmap.
"""

import sys, os, platform, time, math, re, json
import numpy as np
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QFileDialog, QProgressBar,
    QGroupBox, QGridLayout, QComboBox, QSpinBox, QFrame,
    QTextEdit, QScrollArea, QDoubleSpinBox, QMessageBox,
    QRadioButton, QLineEdit, QCheckBox, QSizePolicy, QInputDialog,
)
from PyQt5.QtCore  import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui   import (
    QPixmap, QImage, QFont, QPainter, QPen, QBrush, QPainterPath, QPalette,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lifi_transmitter import prepare_image, build_packet, save_transmission_log
from lifi_receiver    import receive_simulated, save_received
from lifi_analyser    import (
    compute_mse, compute_psnr, compute_ber, compute_snr,
    compute_ssim_simple, simulate_distance_vs_quality,
)
from lifi_hardware_protocol import (
    image_to_glcd_bytes, glcd_bytes_to_hex_string,
    encode_text_for_hardware, glcd_bytes_from_h_file,
    glcd_bytes_to_preview, HardwareTXWorker,
    list_serial_ports, HW_BAUD,
)

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

HW_W, HW_H = 128, 64
OUTPUT_DIR  = "lifi_data"


# ═══════════════════════════════════════════════════════════════════
#  Utilities
# ═══════════════════════════════════════════════════════════════════

def numpy_to_pixmap(arr, max_w=320, max_h=320):
    if arr is None: return None
    arr = np.ascontiguousarray(arr)
    if arr.ndim == 2:
        h, w = arr.shape
        img = QImage(arr.tobytes(), w, h, w, QImage.Format_Grayscale8)
    else:
        h, w, _ = arr.shape
        img = QImage(arr.tobytes(), w, h, 3*w, QImage.Format_RGB888)
    return QPixmap.fromImage(img).scaled(
        max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

def ts():
    return datetime.now().strftime("%H:%M:%S")

def parse_arduino_echo(text):
    """
    Parse the Arduino serial echo:
      "Count : 1027\n128,64,b0,b1,...,b1023;\n"
    Returns np.ndarray shape (64,128) uint8 (grayscale, 0=black 255=white),
    or None on failure.
    """
    try:
        match = re.search(r'([\d ,]+);', text, re.DOTALL)
        if not match: return None
        raw_str = match.group(1)
        vals = [int(v.strip()) for v in raw_str.split(',') if v.strip().isdigit()]
        if len(vals) < 3: return None
        if vals[0] == 128 and vals[1] == 64:
            vals = vals[2:]          # strip width/height header
        if len(vals) < 1024: vals += [0] * (1024 - len(vals))
        glcd_bytes = np.array(vals[:1024], dtype=np.uint8)
        return glcd_bytes_to_preview(glcd_bytes)   # (64,128) uint8
    except Exception:
        return None

def _save_rx(img_arr, meta):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    np.save(os.path.join(OUTPUT_DIR, "received_image.npy"), img_arr)
    with open(os.path.join(OUTPUT_DIR, "rx_meta.json"), "w") as f:
        json.dump({k: (float(v) if isinstance(v, (np.floating, float)) else
                       int(v)  if isinstance(v, (np.integer, int)) else v)
                   for k, v in meta.items()}, f, indent=2)


# ═══════════════════════════════════════════════════════════════════
#  Interactive graphs using pyqtgraph
#  - Native Qt: no matplotlib/Quartz conflict, no macOS crash
#  - Built-in zoom/pan (mouse wheel + drag), export to PNG/SVG/CSV
# ═══════════════════════════════════════════════════════════════════

import pyqtgraph as pg
import pyqtgraph.exporters

# pg.setConfigOption calls moved into main() — must run after QApplication exists

def _finite(vals, cap=None):
    """Replace inf/nan; optionally cap at a maximum value."""
    out = []
    for v in vals:
        if not math.isfinite(v):
            out.append(cap if cap is not None else 0.0)
        else:
            out.append(min(v, cap) if cap is not None else v)
    return out


class InteractiveGraph(pg.PlotWidget):
    """
    Single interactive pyqtgraph plot.
    Features: zoom (wheel), pan (drag), right-click menu with
    Export (PNG / SVG / CSV), auto-range button, log-Y toggle.
    """
    def __init__(self, title, x_label, y_label,
                 log_y=False, h_lines=None, parent=None):
        super().__init__(parent, title=title)
        self._log_y  = log_y
        self._hlines = h_lines or []   # [(value, label), ...]

        pi = self.getPlotItem()
        pi.setLabel('bottom', x_label)
        pi.setLabel('left',   y_label)
        pi.showGrid(x=True, y=True, alpha=0.3)
        pi.addLegend(offset=(-10, 10))

        if log_y:
            pi.setLogMode(y=True)

        # Draw reference horizontal lines
        styles = ['dash', 'dot', 'dashdot']
        colors = [(0, 180, 0), (220, 130, 0), (200, 0, 0)]
        for i, (val, label) in enumerate(self._hlines):
            col  = colors[i % len(colors)]
            pen  = pg.mkPen(color=col, width=1,
                            style=Qt.DashLine if i==0 else Qt.DotLine)
            inf_line = pg.InfiniteLine(
                pos=math.log10(max(val, 1e-15)) if log_y else val,
                angle=0, pen=pen, label=label,
                labelOpts={'color': col, 'position': 0.05,
                           'anchors': [(0,1),(0,1)]})
            self.addItem(inf_line)

        # Main data curve
        self._curve = self.plot([], [], pen=pg.mkPen(width=2),
                                 symbol='o', symbolSize=7,
                                 symbolBrush=pg.mkBrush('b'),
                                 symbolPen=pg.mkPen('w', width=1))

    def set_data(self, xs, ys):
        ys_safe = _finite(list(ys))
        if self._log_y:
            ys_safe = [max(v, 1e-15) for v in ys_safe]
        self._curve.setData(list(xs), ys_safe)
        self.getPlotItem().enableAutoRange()

    def export_png(self):
        exp = pg.exporters.ImageExporter(self.getPlotItem())
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", "graph.png", "PNG Images (*.png)")
        if path:
            exp.export(path)

    def export_svg(self):
        exp = pg.exporters.SVGExporter(self.getPlotItem())
        path, _ = QFileDialog.getSaveFileName(
            self, "Export SVG", "graph.svg", "SVG Files (*.svg)")
        if path:
            exp.export(path)

    def export_csv(self):
        exp = pg.exporters.CSVExporter(self.getPlotItem())
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "graph.csv", "CSV Files (*.csv)")
        if path:
            exp.export(path)


class GraphPanel(QWidget):
    """
    2x2 grid of interactive pyqtgraph plots.
    Right-click any chart for pyqtgraph's built-in export/view menu.
    Extra export buttons are added above the grid.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        main = QVBoxLayout(self); main.setContentsMargins(0,0,0,0); main.setSpacing(4)

        # Export toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Export:"))
        for label, slot in [("PNG", self._exp_png),
                             ("SVG", self._exp_svg),
                             ("CSV", self._exp_csv)]:
            btn = QPushButton(label); btn.setMaximumWidth(60)
            btn.clicked.connect(slot); toolbar.addWidget(btn)
        toolbar.addStretch()
        hint = QLabel("  Zoom: scroll wheel  |  Pan: drag  |  Reset: A key or right-click")
        toolbar.addWidget(hint)
        main.addLayout(toolbar)

        # 2x2 chart grid
        grid = QGridLayout(); grid.setSpacing(6)

        self.psnr = InteractiveGraph(
            "Distance vs PSNR", "Distance (cm)", "PSNR (dB)",
            h_lines=[(30, "Good 30dB"), (20, "Fair 20dB")])
        self.ber  = InteractiveGraph(
            "Distance vs BER",  "Distance (cm)", "BER", log_y=True,
            h_lines=[(1e-3, "Target 1e-3")])
        self.snr  = InteractiveGraph(
            "Distance vs SNR",  "Distance (cm)", "SNR (dB)")
        self.ssim = InteractiveGraph(
            "Distance vs SSIM", "Distance (cm)", "SSIM",
            h_lines=[(0.9, "Excellent 0.9"), (0.7, "OK 0.7")])

        for w in [self.psnr, self.ber, self.snr, self.ssim]:
            w.setMinimumHeight(180)

        grid.addWidget(self.psnr, 0, 0)
        grid.addWidget(self.ber,  0, 1)
        grid.addWidget(self.snr,  1, 0)
        grid.addWidget(self.ssim, 1, 1)
        main.addLayout(grid)
        self.setMinimumHeight(400)

    def plot(self, d):
        # Cap inf values before plotting
        self.psnr.set_data(d["distance_cm"], _finite(d["psnr"],  cap=80.0))
        self.ber.set_data( d["distance_cm"], _finite(d["ber"]))
        self.snr.set_data( d["distance_cm"], _finite(d["snr_db"], cap=60.0))
        self.ssim.set_data(d["distance_cm"], _finite(d["ssim"]))

    def _active(self):
        """Return whichever chart was last clicked, or PSNR as default."""
        return self.psnr   # default; user can right-click any chart for its own menu

    def _exp_png(self):
        g,_ = self._pick_graph(); g.export_png() if g else None
    def _exp_svg(self):
        g,_ = self._pick_graph(); g.export_svg() if g else None
    def _exp_csv(self):
        g,_ = self._pick_graph(); g.export_csv() if g else None

    def _pick_graph(self):
        graphs = [("PSNR", self.psnr), ("BER", self.ber),
                  ("SNR",  self.snr),  ("SSIM", self.ssim)]
        items  = [name for name,_ in graphs]
        name, ok = QInputDialog.getItem(
            self, "Select Graph", "Which graph to export?", items, 0, False)
        if not ok: return None, None
        return dict(graphs)[name], name
# ═══════════════════════════════════════════════════════════════════
#  Shared widgets
# ═══════════════════════════════════════════════════════════════════

class ImagePreview(QLabel):
    def __init__(self,placeholder="No image",parent=None):
        super().__init__(placeholder,parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFrameStyle(QFrame.StyledPanel|QFrame.Sunken)
        self.setMinimumSize(180,140); self._ph=placeholder
    def set_image(self,px,info=""):
        if px: self.setPixmap(px); self.setToolTip(info) if info else None
        else: self.clear_image()
    def clear_image(self): self.setPixmap(QPixmap()); self.setText(self._ph); self.setToolTip("")

class MetricCard(QGroupBox):
    def __init__(self,title,unit="",parent=None):
        super().__init__(title,parent)
        lay=QVBoxLayout(self); lay.setContentsMargins(8,4,8,6)
        self._v=QLabel("—"); f=QFont(); f.setPointSize(18); f.setBold(True)
        self._v.setFont(f); self._v.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._v); lay.addWidget(QLabel(unit,alignment=Qt.AlignCenter))
    def set_value(self,val,_=None): self._v.setText(str(val))

class LogBox(QTextEdit):
    _sig=pyqtSignal(str)
    def __init__(self,placeholder="Log...",parent=None):
        super().__init__(parent); self.setReadOnly(True); self.setPlaceholderText(placeholder)
        f=QFont()
        if sys.platform=="darwin": f.setFamily("Menlo")
        elif sys.platform=="win32": f.setFamily("Courier New")
        else: f.setFamily("DejaVu Sans Mono")
        f.setPointSize(10); self.setFont(f); self._sig.connect(self.append)
    def log(self,msg,_=None): self._sig.emit(f"[{ts()}] {msg}")


# ═══════════════════════════════════════════════════════════════════
#  Port selector + Serial Settings tab
# ═══════════════════════════════════════════════════════════════════

class PortSelector(QWidget):
    connected_changed=pyqtSignal(bool,str)
    def __init__(self,label="Port",parent=None):
        super().__init__(parent); self._ser=None
        lay=QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)
        lay.addWidget(QLabel(label+":"))
        self.port_cb=QComboBox(); self.port_cb.setMinimumWidth(160); lay.addWidget(self.port_cb)
        lay.addWidget(QLabel("Baud: 57600"))
        self.stat=QLabel("Disconnected"); lay.addWidget(self.stat)
        self.btn=QPushButton("Connect"); self.btn.clicked.connect(self.toggle)
        lay.addWidget(self.btn); lay.addStretch()
    def port(self): return self.port_cb.currentData() or ""
    @property
    def connected(self): return self._ser is not None
    def refresh(self,ports):
        self.port_cb.clear()
        if ports:
            for dev,desc in ports: self.port_cb.addItem(f"{dev}  -  {desc[:28]}",dev)
        else: self.port_cb.addItem("No ports detected")
    def toggle(self):
        if not self.connected:
            p=self.port()
            if not p: QMessageBox.warning(self.window(),"No Port","Select a port first."); return
            try:
                self._ser=serial.Serial(p,HW_BAUD,timeout=2)
                self.stat.setText("Connected"); self.btn.setText("Disconnect")
                self.connected_changed.emit(True,p)
            except Exception as e: QMessageBox.critical(self.window(),"Failed",str(e))
        else:
            try: self._ser.close()
            except: pass
            self._ser=None; self.stat.setText("Disconnected"); self.btn.setText("Connect")
            self.connected_changed.emit(False,"")
    def serial_handle(self): return self._ser

class SerialSettingsTab(QWidget):
    ports_refreshed=pyqtSignal()
    def __init__(self,parent=None):
        super().__init__(parent)
        main=QVBoxLayout(self); main.setContentsMargins(10,10,10,10); main.setSpacing(10)
        rb=QPushButton("Refresh Ports"); rb.clicked.connect(self.refresh)
        rl=QHBoxLayout(); rl.addWidget(rb); rl.addStretch(); main.addLayout(rl)
        txg=QGroupBox("TX Arduino Port  (tx.ino)"); tl=QVBoxLayout(txg)
        self.tx_sel=PortSelector("TX"); tl.addWidget(self.tx_sel); main.addWidget(txg)
        rxg=QGroupBox("RX Arduino Port  (rx.ino)"); rl2=QVBoxLayout(rxg)
        self.rx_sel=PortSelector("RX"); rl2.addWidget(self.rx_sel); main.addWidget(rxg)
        ig=QGroupBox("System Info"); il=QVBoxLayout(ig)
        info=QTextEdit(); info.setReadOnly(True); info.setMaximumHeight(90)
        info.setPlainText(
            f"PySerial: {'Yes' if SERIAL_AVAILABLE else 'No – simulation only'}\n"
            f"Python: {sys.version.split()[0]}\n"
            f"Platform: {platform.system()} {platform.release()}\n"
            f"Baud rate: 57600 (fixed, matches tx.ino/rx.ino firmware)")
        il.addWidget(info); main.addWidget(ig); main.addStretch()
        QTimer.singleShot(400,self.refresh)
    def refresh(self):
        ports=list_serial_ports() if SERIAL_AVAILABLE else []
        self.tx_sel.refresh(ports); self.rx_sel.refresh(ports); self.ports_refreshed.emit()
    def tx_port(self):       return self.tx_sel.port()
    def rx_port(self):       return self.rx_sel.port()
    def tx_connected(self):  return self.tx_sel.connected
    def rx_connected(self):  return self.rx_sel.connected


# ═══════════════════════════════════════════════════════════════════
#  Workers
# ═══════════════════════════════════════════════════════════════════

class SimTXWorker(QThread):
    progress=pyqtSignal(int); status=pyqtSignal(str)
    finished=pyqtSignal(dict); error=pyqtSignal(str)
    def __init__(self,img_array,baudrate=HW_BAUD):
        super().__init__(); self.img_array=img_array; self.baudrate=baudrate
    def run(self):
        try:
            h,w=self.img_array.shape[:2]
            ch=self.img_array.shape[2] if self.img_array.ndim==3 else 1
            payload=w*h*ch
            self.status.emit("Building packet..."); self.progress.emit(5)
            packet=build_packet(self.img_array,w,h,ch)
            total=len(packet); sim_time=(total*10)/self.baudrate
            self.status.emit("Simulating TX...")
            for i in range(50): time.sleep(sim_time/50); self.progress.emit(int(10+i/50*85))
            self.progress.emit(95); self.status.emit("Saving...")
            meta=dict(bytes_sent=total,time_seconds=sim_time,
                      data_rate_bps=(payload*8)/sim_time,baudrate=self.baudrate,
                      image_width=w,image_height=h,image_channels=ch,
                      mode="simulation",timestamp=datetime.now().isoformat())
            save_transmission_log(meta,self.img_array,OUTPUT_DIR)
            self.progress.emit(100); self.status.emit("Complete"); self.finished.emit(meta)
        except Exception as e: self.error.emit(str(e))

class SimRXWorker(QThread):
    progress=pyqtSignal(int); status=pyqtSignal(str)
    finished=pyqtSignal(object,dict); error=pyqtSignal(str)
    def __init__(self,ber,snr_db):
        super().__init__(); self.ber=ber; self.snr_db=snr_db
    def run(self):
        try:
            tp=os.path.join(OUTPUT_DIR,"transmitted_image.npy")
            if not os.path.exists(tp): self.error.emit("No transmitted image. TX first!"); return
            self.status.emit("Simulating reception..."); self.progress.emit(10)
            for i in range(20): time.sleep(0.04); self.progress.emit(10+i*3)
            img,meta=receive_simulated(tp,ber=self.ber,
                                       snr_db=self.snr_db if self.snr_db>0 else None)
            self.progress.emit(85); self.status.emit("Saving...")
            save_received(img,meta,OUTPUT_DIR)
            self.progress.emit(100); self.status.emit("Complete"); self.finished.emit(img,meta)
        except Exception as e: self.error.emit(str(e))

class RXMonitorWorker(QThread):
    line_received=pyqtSignal(str); stopped=pyqtSignal()
    def __init__(self,port,baudrate):
        super().__init__(); self.port=port; self.baudrate=baudrate; self._running=True
    def stop(self): self._running=False
    def run(self):
        try:
            ser=serial.Serial(self.port,self.baudrate,timeout=1); time.sleep(1)
            while self._running:
                line=ser.readline().decode("ascii",errors="replace").strip()
                if line: self.line_received.emit(line)
            ser.close()
        except Exception as e: self.line_received.emit(f"[ERROR] {e}")
        finally: self.stopped.emit()


# ═══════════════════════════════════════════════════════════════════
#  Tab 1: TX
# ═══════════════════════════════════════════════════════════════════

class TXTab(QWidget):
    tx_image_ready=pyqtSignal(np.ndarray)

    def __init__(self,settings:SerialSettingsTab,parent=None):
        super().__init__(parent)
        self.settings=settings; self.glcd_bytes=None
        self._raw_img_path=None; self._orig_img=None
        self.hw_worker=None; self.sim_worker=None
        self._build()

    def _build(self):
        main=QVBoxLayout(self); main.setContentsMargins(10,10,10,10); main.setSpacing(8)

        top_mode=QGroupBox("Content Type"); ml=QHBoxLayout(top_mode)
        self.m_img=QRadioButton("Image (128x64 bitmap)"); self.m_img.setChecked(True)
        self.m_txt=QRadioButton("Text (max 98 chars)")
        ml.addWidget(self.m_img); ml.addWidget(self.m_txt); ml.addStretch()
        main.addWidget(top_mode)

        self.img_sec=self._img_section()
        self.txt_sec=self._txt_section(); self.txt_sec.hide()
        main.addWidget(self.img_sec); main.addWidget(self.txt_sec)

        cg=QGroupBox("Transmit"); cl=QVBoxLayout(cg)
        sim_row=QHBoxLayout()
        self.sim_chk=QCheckBox("Simulation mode  (no hardware required)")
        self.sim_chk.setChecked(not SERIAL_AVAILABLE)
        sim_row.addWidget(self.sim_chk); sim_row.addStretch(); cl.addLayout(sim_row)
        self.send_btn=QPushButton("Send to TX Arduino / Simulate")
        self.send_btn.setEnabled(False); self.send_btn.clicked.connect(self.send)
        cl.addWidget(self.send_btn)
        self.prog=QProgressBar(); self.prog.setFormat("%p%  -  Ready"); cl.addWidget(self.prog)
        self.log=LogBox("TX log..."); self.log.setMaximumHeight(120); cl.addWidget(self.log)
        main.addWidget(cg)

        sg=QGroupBox("Transmission Statistics"); sgl=QGridLayout(sg); sgl.setSpacing(6)
        self.cards={}
        for i,(n,u) in enumerate([("Bytes Sent","bytes"),("TX Time","s"),
                                   ("Data Rate","bps"),("Arduino Response","")]):
            c=MetricCard(n,u); self.cards[n]=c; sgl.addWidget(c,i//2,i%2)
        main.addWidget(sg)
        self.m_img.toggled.connect(self._mode)

    def _img_section(self):
        w=QWidget(); lay=QHBoxLayout(w); lay.setContentsMargins(0,0,0,0); lay.setSpacing(8)
        ctrl=QGroupBox("Image Input"); cl=QVBoxLayout(ctrl)
        br=QHBoxLayout()
        self.img_path=QLabel("No image selected"); self.img_path.setWordWrap(True)
        self.img_btn=QPushButton("Browse Image"); self.img_btn.clicked.connect(self.browse_image)
        br.addWidget(self.img_path,1); br.addWidget(self.img_btn); cl.addLayout(br)
        self.dither=QCheckBox("Floyd-Steinberg dithering"); self.dither.setChecked(True)
        cl.addWidget(self.dither)
        sz_row=QHBoxLayout(); sz_row.addWidget(QLabel("Sim max size (px):"))
        self.size_spin=QSpinBox(); self.size_spin.setRange(16,256); self.size_spin.setValue(64)
        sz_row.addWidget(self.size_spin); sz_row.addStretch(); cl.addLayout(sz_row)
        hr=QHBoxLayout()
        self.h_btn=QPushButton("Load .h File"); self.h_btn.clicked.connect(self.browse_h)
        hr.addWidget(QLabel("Or:")); hr.addWidget(self.h_btn); hr.addStretch(); cl.addLayout(hr)
        self.conv_btn=QPushButton("Convert to 128x64 Bitmap")
        self.conv_btn.setEnabled(False); self.conv_btn.clicked.connect(self.convert)
        cl.addWidget(self.conv_btn)
        self.byte_lbl=QLabel("Bitmap: not loaded"); cl.addWidget(self.byte_lbl)
        cl.addStretch(); lay.addWidget(ctrl,1)

        pg=QGroupBox("128x64 Preview (3x scale)"); pl=QVBoxLayout(pg)
        self.glcd_prev=ImagePreview("No bitmap\nBrowse then Convert")
        self.glcd_prev.setMinimumSize(384,192); self.glcd_prev.setMaximumSize(512,260)
        pl.addWidget(self.glcd_prev); lay.addWidget(pg,2)

        hg=QGroupBox("Hex String  {0xHH...}  (sent to Arduino)"); hl=QVBoxLayout(hg)
        self.hex_box=QTextEdit(); self.hex_box.setReadOnly(True)
        self.hex_box.setPlaceholderText("Appears after Convert...")
        f=QFont()
        if sys.platform=="darwin": f.setFamily("Menlo")
        elif sys.platform=="win32": f.setFamily("Courier New")
        else: f.setFamily("DejaVu Sans Mono")
        f.setPointSize(9); self.hex_box.setFont(f)
        hl.addWidget(self.hex_box,1); lay.addWidget(hg,2)
        return w

    def _txt_section(self):
        w=QWidget(); lay=QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        tg=QGroupBox("Text Input (max 98 chars)"); tgl=QVBoxLayout(tg)
        tgl.addWidget(QLabel("Text to transmit via Li-Fi hardware:"))
        self.txt_ed=QLineEdit(); self.txt_ed.setPlaceholderText("Enter text...")
        self.txt_ed.setMaxLength(98); self.txt_ed.textChanged.connect(self._txt_changed)
        tgl.addWidget(self.txt_ed)
        self.txt_prev=QLabel("Frame: -"); self.txt_prev.setWordWrap(True)
        tgl.addWidget(self.txt_prev); lay.addWidget(tg); lay.addStretch()
        return w

    def _mode(self):
        img=self.m_img.isChecked()
        self.img_sec.setVisible(img); self.txt_sec.setVisible(not img)
        self.send_btn.setEnabled(self.glcd_bytes is not None if img
                                 else bool(self.txt_ed.text().strip()))

    def _txt_changed(self,t):
        if self.m_txt.isChecked(): self.send_btn.setEnabled(bool(t.strip()))
        f=encode_text_for_hardware(t) if t.strip() else "-"
        self.txt_prev.setText(f"Frame: {f[:80]}{'...' if len(f)>80 else ''}")

    def browse_image(self):
        p,_=QFileDialog.getOpenFileName(self,"Select Image","",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff);;All (*)")
        if not p: return
        self._raw_img_path=p; self.img_path.setText(os.path.basename(p))
        self.conv_btn.setEnabled(True); self.log.log(f"Selected: {os.path.basename(p)}")

    def browse_h(self):
        p,_=QFileDialog.getOpenFileName(self,"Select .h File","","Header Files (*.h);;All (*)")
        if not p: return
        try:
            raw=glcd_bytes_from_h_file(open(p).read())
            if len(raw)>=2 and raw[0]==128 and raw[1]==64: raw=raw[2:]
            if len(raw)!=1024: raw=raw[:1024] if len(raw)>1024 else np.pad(raw,(0,1024-len(raw)))
            self.glcd_bytes=raw
            prev=glcd_bytes_to_preview(raw)
            self.glcd_prev.set_image(numpy_to_pixmap(prev,384,192),"128x64  1-bpp")
            hs=glcd_bytes_to_hex_string(raw)
            self.hex_box.setPlainText(hs)
            self.byte_lbl.setText(f"{len(raw)} bytes  ->  {len(hs)} chars hex")
            self.send_btn.setEnabled(True)
            self.log.log(f".h loaded: {os.path.basename(p)}")
            # Save as reference TX image
            os.makedirs(OUTPUT_DIR,exist_ok=True)
            np.save(os.path.join(OUTPUT_DIR,"transmitted_image.npy"),prev)
            self._orig_img=prev
        except Exception as e: self.log.log(f"Error: {e}")

    def convert(self):
        if not self._raw_img_path: return
        try:
            glcd,prev=image_to_glcd_bytes(self._raw_img_path,self.dither.isChecked())
            self.glcd_bytes=glcd
            arr,w,h,ch=prepare_image(self._raw_img_path,self.size_spin.value())
            self._orig_img=arr
            self.glcd_prev.set_image(numpy_to_pixmap(prev,384,192),"128x64  1-bpp")
            hs=glcd_bytes_to_hex_string(glcd)
            self.hex_box.setPlainText(hs)
            self.byte_lbl.setText(f"{len(glcd)} bytes  ->  {len(hs)} chars hex")
            self.send_btn.setEnabled(True)
            self.log.log(f"Converted 128x64 1-bpp  "
                         f"({'dithered' if self.dither.isChecked() else 'threshold'})")
        except Exception as e: self.log.log(f"Conversion error: {e}")

    def send(self):
        simulate=self.sim_chk.isChecked() or not self.settings.tx_connected()
        port=self.settings.tx_port()

        if self.m_img.isChecked():
            if self.glcd_bytes is None: return
            self.send_btn.setEnabled(False); self.prog.setValue(0)
            if simulate:
                if self._orig_img is None:
                    tp=os.path.join(OUTPUT_DIR,"transmitted_image.npy")
                    if os.path.exists(tp): self._orig_img=np.load(tp)
                    else:
                        self.log.log("ERROR: Convert image first.")
                        self.send_btn.setEnabled(True); return
                self.prog.setFormat("%p%  -  Simulating...")
                self.log.log(f"SIM TX | shape={self._orig_img.shape}")
                self.sim_worker=SimTXWorker(self._orig_img)
                self.sim_worker.progress.connect(self.prog.setValue)
                self.sim_worker.status.connect(lambda s: self.prog.setFormat(f"%p%  -  {s}"))
                self.sim_worker.finished.connect(self._sim_done)
                self.sim_worker.error.connect(self._err)
                self.sim_worker.start()
            else:
                data=glcd_bytes_to_hex_string(self.glcd_bytes)
                self.prog.setFormat("%p%  -  Sending...")
                self.log.log(f"HW TX | {port} @ {HW_BAUD} | {len(data)} chars")
                # Save GLCD preview as TX reference
                os.makedirs(OUTPUT_DIR,exist_ok=True)
                tx_prev=glcd_bytes_to_preview(self.glcd_bytes)
                np.save(os.path.join(OUTPUT_DIR,"transmitted_image.npy"),tx_prev)
                self._orig_img=tx_prev
                self.hw_worker=HardwareTXWorker(port,data,HW_BAUD)
                self.hw_worker.progress.connect(self.prog.setValue)
                self.hw_worker.status.connect(lambda s: self.prog.setFormat(f"%p%  -  {s}"))
                self.hw_worker.finished.connect(self._hw_done)
                self.hw_worker.error.connect(self._err)
                self.hw_worker.start()
        else:
            t=self.txt_ed.text().strip()
            if not t: return
            if simulate:
                self.log.log(f"SIM Text TX: '{t[:40]}'"); self.log.log("Sent (simulation).")
                self.prog.setValue(100); self.prog.setFormat("%p%  -  Sent")
            else:
                if not port:
                    QMessageBox.warning(self,"No Port","Connect TX Arduino in Serial Settings.")
                    return
                self.send_btn.setEnabled(False)
                data=encode_text_for_hardware(t)
                self.hw_worker=HardwareTXWorker(port,data,HW_BAUD)
                self.hw_worker.progress.connect(self.prog.setValue)
                self.hw_worker.status.connect(lambda s: self.prog.setFormat(f"%p%  -  {s}"))
                self.hw_worker.finished.connect(self._hw_done)
                self.hw_worker.error.connect(self._err)
                self.hw_worker.start()

    def _sim_done(self,meta):
        self.send_btn.setEnabled(True); self.prog.setFormat("%p%  -  Complete (SIM)")
        self.log.log(f"SIM TX: {meta['bytes_sent']} bytes  {meta['time_seconds']:.2f}s  "
                     f"{meta['data_rate_bps']:.0f} bps")
        for k,v in [("Bytes Sent",f"{meta['bytes_sent']:,}"),
                    ("TX Time",f"{meta['time_seconds']:.2f}"),
                    ("Data Rate",f"{meta['data_rate_bps']:.0f}"),
                    ("Arduino Response","(simulation)")]:
            self.cards[k].set_value(v)
        if self._orig_img is not None: self.tx_image_ready.emit(self._orig_img)

    def _hw_done(self,meta):
        self.send_btn.setEnabled(True); self.prog.setFormat("%p%  -  Sent")
        resp="; ".join(meta.get("arduino_response",["(no response)"]))
        self.log.log(f"HW TX: {meta['bytes_sent']} bytes  {meta['time_seconds']:.2f}s")
        self.log.log(f"Arduino echo: {resp}")
        for k,v in [("Bytes Sent",f"{meta['bytes_sent']:,}"),
                    ("TX Time",f"{meta['time_seconds']:.2f}"),
                    ("Data Rate",f"{meta['data_rate_bps']:.0f}"),
                    ("Arduino Response",resp[:25] or "-")]:
            self.cards[k].set_value(v)
        tp=os.path.join(OUTPUT_DIR,"transmitted_image.npy")
        if os.path.exists(tp): self.tx_image_ready.emit(np.load(tp))

    def _err(self,msg):
        self.send_btn.setEnabled(True); self.prog.setFormat("Error!")
        self.log.log(f"ERROR: {msg}"); QMessageBox.critical(self,"TX Error",msg)


# ═══════════════════════════════════════════════════════════════════
#  Tab 2: RX (Monitor / Simulation)
# ═══════════════════════════════════════════════════════════════════

class RXTab(QWidget):
    rx_image_ready=pyqtSignal(np.ndarray,dict)

    def __init__(self,settings:SerialSettingsTab,parent=None):
        super().__init__(parent)
        self.settings=settings; self._worker=None; self._sim_worker=None
        self._echo_buf=""; self._lc=self._ec=self._ic=self._tc=0
        self._build()

    def _build(self):
        main=QVBoxLayout(self); main.setContentsMargins(10,10,10,10); main.setSpacing(8)

        # ── Simulation ──
        sim_grp=QGroupBox("Simulation Mode  (applies noise to transmitted image)")
        sl=QVBoxLayout(sim_grp)
        self.sim_chk=QCheckBox("Enable simulation  (no hardware required)")
        self.sim_chk.setChecked(not SERIAL_AVAILABLE); sl.addWidget(self.sim_chk)
        nr=QHBoxLayout(); nr.addWidget(QLabel("BER:"))
        self.ber=QDoubleSpinBox(); self.ber.setDecimals(6); self.ber.setRange(0,0.5)
        self.ber.setValue(0.005); self.ber.setSingleStep(0.001); nr.addWidget(self.ber)
        nr.addWidget(QLabel("  SNR (dB):"))
        self.snr=QDoubleSpinBox(); self.snr.setRange(0,60); self.snr.setValue(0)
        self.snr.setToolTip("Set > 0 to override BER"); nr.addWidget(self.snr); nr.addStretch()
        sl.addLayout(nr)
        self.sim_btn=QPushButton("Run Simulation Reception")
        self.sim_btn.clicked.connect(self.run_sim); sl.addWidget(self.sim_btn)
        self.sim_prog=QProgressBar(); self.sim_prog.setFormat("%p%  -  Ready")
        sl.addWidget(self.sim_prog); main.addWidget(sim_grp)

        # ── Hardware monitor ──
        hw_grp=QGroupBox("Hardware Monitor  (RX Arduino serial output @ 57600 baud)")
        hl=QVBoxLayout(hw_grp)
        cl=QHBoxLayout()
        self.start_btn=QPushButton("Start Monitoring"); self.start_btn.clicked.connect(self.start_hw)
        self.stop_btn=QPushButton("Stop"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_hw)
        self.clr_btn=QPushButton("Clear Log"); self.clr_btn.clicked.connect(self._clear)
        self.parse_btn=QPushButton("Parse Echo -> Image")
        self.parse_btn.setToolTip(
            "Parse 'Count:N / 128,64,b0,...,bN;' echo\ninto a 128x64 bitmap for analysis")
        self.parse_btn.clicked.connect(self.parse_echo)
        self.load_btn=QPushButton("Load Echo File (.txt)")
        self.load_btn.clicked.connect(self.load_file)
        cl.addWidget(self.start_btn); cl.addWidget(self.stop_btn)
        cl.addWidget(self.clr_btn);  cl.addWidget(self.parse_btn)
        cl.addWidget(self.load_btn); cl.addStretch()
        hl.addLayout(cl)
        self.stat_lbl=QLabel("Not monitoring"); hl.addWidget(self.stat_lbl)
        main.addWidget(hw_grp)

        # ── Content: log | preview side-by-side with decoded data ──
        content=QHBoxLayout()
        lg=QGroupBox("Serial Output  (Arduino echo)"); ll=QVBoxLayout(lg)
        self.log=LogBox("Serial monitor..."); self.log.setMinimumHeight(200)
        ll.addWidget(self.log); content.addWidget(lg,2)

        # Right side: preview and decoded data SIDE BY SIDE
        right=QVBoxLayout()
        preview_row=QHBoxLayout()

        ig=QGroupBox("Received Bitmap Preview  (128x64)"); il2=QVBoxLayout(ig)
        self.rx_prev=ImagePreview("No image\nParse echo or run simulation")
        self.rx_prev.setMinimumSize(384,192); self.rx_prev.setMaximumSize(512,260)
        il2.addWidget(self.rx_prev)
        preview_row.addWidget(ig,3)

        dg=QGroupBox("Decoded Data  (decimal CSV)"); dl=QVBoxLayout(dg)
        self.dec_box=QTextEdit(); self.dec_box.setReadOnly(True)
        self.dec_box.setPlaceholderText("Decoded byte values appear here after Parse Echo...")
        f=QFont()
        if sys.platform=="darwin": f.setFamily("Menlo")
        elif sys.platform=="win32": f.setFamily("Courier New")
        else: f.setFamily("DejaVu Sans Mono")
        f.setPointSize(9); self.dec_box.setFont(f)
        dl.addWidget(self.dec_box)
        preview_row.addWidget(dg,2)

        right.addLayout(preview_row)
        content.addLayout(right,5); main.addLayout(content,1)

        # ── Stats ──
        sg=QGroupBox("Stats"); sgl=QHBoxLayout(sg)
        self.lc=MetricCard("Lines",""); self.ec=MetricCard("Errors","")
        self.ic=MetricCard("Images rx",""); self.tc=MetricCard("Texts rx","")
        for c in [self.lc,self.ec,self.ic,self.tc]: c.set_value("0"); sgl.addWidget(c)
        main.addWidget(sg)

    # Simulation
    def run_sim(self):
        self.sim_btn.setEnabled(False); self.sim_prog.setValue(0)
        self.sim_prog.setFormat("%p%  -  Running...")
        self._sim_worker=SimRXWorker(self.ber.value(),self.snr.value())
        self._sim_worker.progress.connect(self.sim_prog.setValue)
        self._sim_worker.status.connect(lambda s: self.sim_prog.setFormat(f"%p%  -  {s}"))
        self._sim_worker.finished.connect(self._sim_done)
        self._sim_worker.error.connect(self._sim_err)
        self._sim_worker.start()

    def _sim_done(self,img,meta):
        self.sim_btn.setEnabled(True); self.sim_prog.setFormat("%p%  -  Complete")
        bv=meta.get("actual_ber",meta.get("target_ber",0))
        self.log.log(f"SIM RX done: BER={bv:.6f}")
        self._show_image(img,meta)

    def _sim_err(self,msg):
        self.sim_btn.setEnabled(True); self.sim_prog.setFormat("Error!")
        self.log.log(f"SIM ERROR: {msg}"); QMessageBox.critical(self,"Simulation Error",msg)

    # Hardware monitor
    def start_hw(self):
        port=self.settings.rx_port()
        if not port:
            QMessageBox.warning(self,"No Port","Connect RX Arduino in Serial Settings."); return
        self._echo_buf=""
        self._worker=RXMonitorWorker(port,HW_BAUD)
        self._worker.line_received.connect(self._on_line)
        self._worker.stopped.connect(self._on_stop)
        self._worker.start()
        self.start_btn.setEnabled(False); self.stop_btn.setEnabled(True)
        self.stat_lbl.setText(f"Monitoring {port} @ {HW_BAUD} baud")
        self.log.log(f"Monitoring {port} @ {HW_BAUD} baud")

    def stop_hw(self):
        if self._worker: self._worker.stop()
        self.stop_btn.setEnabled(False)

    def _on_line(self,line):
        self._lc+=1; self._echo_buf+=line+"\n"
        if "EE04" in line or "error" in line.lower():
            self._ec+=1; self.ec.set_value(str(self._ec))
        elif "Received...Image" in line:
            self._ic+=1; self.ic.set_value(str(self._ic))
        elif "Received...Text" in line:
            self._tc+=1; self.tc.set_value(str(self._tc))
        self.lc.set_value(str(self._lc)); self.log.log(line)
        # Auto-parse when a data dump line (ending with ;) is detected
        if line.endswith(";") and line.count(",")>10:
            img=parse_arduino_echo(self._echo_buf)
            if img is not None: self.log.log("Auto-parsed bitmap."); self._show_image(img,{})

    def _on_stop(self):
        self.start_btn.setEnabled(True); self.stop_btn.setEnabled(False)
        self.stat_lbl.setText("Not monitoring"); self.log.log("Stopped.")

    def _clear(self):
        self.log.clear(); self._echo_buf=""

    def parse_echo(self):
        text=self._echo_buf or self.log.toPlainText()
        img=parse_arduino_echo(text)
        if img is None:
            QMessageBox.information(self,"Parse Failed",
                "Could not find a complete data dump.\n\n"
                "Expected format in log:\n  Count : 1027\n  128,64,b0,b1,...,b1023;\n\n"
                "Ensure RX Arduino has echoed the received data."); return
        self.log.log("Parsed bitmap from echo."); self._show_image(img,{})

    def load_file(self):
        p,_=QFileDialog.getOpenFileName(self,"Load Echo File","","Text Files (*.txt);;All (*)")
        if not p: return
        try:
            text=open(p).read()
            img=parse_arduino_echo(text)
            if img is None:
                QMessageBox.warning(self,"Parse Failed","Could not parse the echo file."); return
            self._echo_buf=text; self.log.log(f"Loaded: {os.path.basename(p)}")
            self._show_image(img,{})
        except Exception as e: self.log.log(f"Load error: {e}")

    def _show_image(self,img_arr,meta):
        """Display received image and emit for analysis."""
        self.rx_prev.set_image(numpy_to_pixmap(img_arr,384,192),"128x64  1-bpp")
        flat=img_arr.flatten()
        self.dec_box.setPlainText(f"128,64,{','.join(str(v) for v in flat)};")
        # Build/complete meta
        if not meta:
            meta={"width":HW_W,"height":HW_H,"channels":1,
                  "payload_bytes":1024,"time_seconds":0,"data_rate_bps":0,
                  "actual_ber":0.0,"mode":"hardware_echo",
                  "timestamp":datetime.now().isoformat()}
        _save_rx(img_arr,meta)
        self.rx_image_ready.emit(img_arr,meta)


# ═══════════════════════════════════════════════════════════════════
#  Tab 3: Analysis
# ═══════════════════════════════════════════════════════════════════

class AnalysisTab(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.tx_img=None; self.rx_img=None; self.rx_meta=None; self._build()

    def _build(self):
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        cont=QWidget(); main=QVBoxLayout(cont); main.setContentsMargins(10,10,10,10); main.setSpacing(8)

        cg=QGroupBox("Image Comparison"); cl=QHBoxLayout(cg)
        self.tx_cmp=ImagePreview("Transmitted"); self.rx_cmp=ImagePreview("Received")
        self.df_cmp=ImagePreview("Error Map (x4)")
        cl.addWidget(self.tx_cmp); cl.addWidget(self.rx_cmp); cl.addWidget(self.df_cmp)
        main.addWidget(cg)

        mg=QGroupBox("Quality Metrics"); mgl=QGridLayout(mg); mgl.setSpacing(6)
        self.mc={}
        for i,(k,t,u) in enumerate([
                ("MSE","Mean Squared Error",""),("PSNR","Peak SNR","dB"),
                ("BER","Bit Error Rate",""),   ("SNR","Signal-to-Noise","dB"),
                ("SSIM","Struct. Similarity",""),("Data Rate","Throughput","bps")]):
            c=MetricCard(t,u); self.mc[k]=c; mgl.addWidget(c,i//3,i%3)
        main.addWidget(mg)

        btn_row=QHBoxLayout()
        self.run_btn=QPushButton("Run Analysis"); self.run_btn.clicked.connect(self.run_analysis)
        btn_row.addWidget(self.run_btn)

        # Offline echo file loaders
        self.load_tx_btn=QPushButton("Load TX Echo (.txt)")
        self.load_tx_btn.clicked.connect(self.load_tx_file)
        self.load_rx_btn=QPushButton("Load RX Echo (.txt)")
        self.load_rx_btn.clicked.connect(self.load_rx_file)
        self.tx_file_lbl=QLabel("TX: —"); self.rx_file_lbl=QLabel("RX: —")
        btn_row.addWidget(self.load_tx_btn); btn_row.addWidget(self.tx_file_lbl)
        btn_row.addWidget(self.load_rx_btn); btn_row.addWidget(self.rx_file_lbl)
        btn_row.addStretch(); main.addLayout(btn_row)

        gg=QGroupBox("Distance vs Quality Graphs"); ggl=QVBoxLayout(gg)
        self.graphs=GraphPanel(); ggl.addWidget(self.graphs)
        main.addWidget(gg); main.addStretch()
        scroll.setWidget(cont)
        outer=QVBoxLayout(self); outer.setContentsMargins(0,0,0,0); outer.addWidget(scroll)

    def _parse_and_set(self,kind,path):
        try:
            img=parse_arduino_echo(open(path).read())
            if img is None:
                QMessageBox.warning(self,"Parse Failed",f"Could not parse {kind} echo file."); return
            os.makedirs(OUTPUT_DIR,exist_ok=True)
            fname="transmitted_image.npy" if kind=="TX" else "received_image.npy"
            np.save(os.path.join(OUTPUT_DIR,fname),img)
            if kind=="TX":
                self.tx_img=img; self.tx_cmp.set_image(numpy_to_pixmap(img,240),f"TX Echo")
                self.tx_file_lbl.setText(f"TX: {os.path.basename(path)}")
            else:
                self.rx_img=img; self.rx_cmp.set_image(numpy_to_pixmap(img,240),f"RX Echo")
                self.rx_file_lbl.setText(f"RX: {os.path.basename(path)}")
                self.rx_meta={"data_rate_bps":0,"mode":"file"}
        except Exception as e: QMessageBox.critical(self,"Error",str(e))

    def load_tx_file(self):
        p,_=QFileDialog.getOpenFileName(self,"Load TX Echo","","Text Files (*.txt);;All (*)")
        if p: self._parse_and_set("TX",p)

    def load_rx_file(self):
        p,_=QFileDialog.getOpenFileName(self,"Load RX Echo","","Text Files (*.txt);;All (*)")
        if p: self._parse_and_set("RX",p)

    def run_analysis(self):
        # Auto-load from disk if not already set via signals
        def _try_load(attr,fname):
            if getattr(self,attr) is None:
                fp=os.path.join(OUTPUT_DIR,fname)
                if os.path.exists(fp): setattr(self,attr,np.load(fp))
        _try_load("tx_img","transmitted_image.npy")
        _try_load("rx_img","received_image.npy")
        if self.rx_meta is None:
            mp=os.path.join(OUTPUT_DIR,"rx_meta.json")
            if os.path.exists(mp):
                with open(mp) as f: self.rx_meta=json.load(f)

        if self.tx_img is None or self.rx_img is None:
            QMessageBox.warning(self,"No Data",
                "Need both TX and RX images.\n\n"
                "Options:\n"
                "  1. Run TX then RX simulation in their tabs\n"
                "  2. Monitor hardware and Parse Echo in the RX tab\n"
                "  3. Use 'Load TX/RX Echo' buttons to load .txt files directly"); return

        tx=self.tx_img; rx=self.rx_img
        if tx.shape!=rx.shape:
            h=min(tx.shape[0],rx.shape[0]); w=min(tx.shape[1],rx.shape[1])
            tx=tx[:h,:w]; rx=rx[:h,:w]

        self.tx_cmp.set_image(numpy_to_pixmap(tx,240),"Transmitted")
        self.rx_cmp.set_image(numpy_to_pixmap(rx,240),"Received")
        diff=np.abs(tx.astype(np.float64)-rx.astype(np.float64))
        if diff.ndim==3: diff=np.mean(diff,axis=2)
        self.df_cmp.set_image(numpy_to_pixmap(np.clip(diff*4,0,255).astype(np.uint8),240),"Error x4")

        mse=compute_mse(tx,rx); psnr=compute_psnr(tx,rx)
        ber=compute_ber(tx,rx);  snr=compute_snr(tx,rx)
        ssim=compute_ssim_simple(tx,rx)
        dr=self.rx_meta.get("data_rate_bps",0) if self.rx_meta else 0

        # Display inf as a clear label rather than "inf"
        def _fmt_metric(v, fmt, inf_label="Perfect (inf)"):
            if not math.isfinite(v): return inf_label
            return fmt.format(v)

        self.mc["MSE"].set_value(f"{mse:.2f}")
        self.mc["PSNR"].set_value(_fmt_metric(psnr, "{:.2f}"))
        self.mc["BER"].set_value(f"{ber:.2e}")
        self.mc["SNR"].set_value(_fmt_metric(snr, "{:.2f}"))
        self.mc["SSIM"].set_value(f"{ssim:.4f}")
        self.mc["Data Rate"].set_value(f"{dr:.0f}")
        # Cap inf/nan values from simulate_distance_vs_quality before plotting
        # (inf PSNR occurs when simulated distance noise is zero, i.e. very close range)
        _dist = simulate_distance_vs_quality(tx)
        import math as _math
        for _k in ("psnr","snr_db"):
            _dist[_k] = [min(v, 80.0) if _math.isfinite(v) else 80.0 for v in _dist[_k]]
        self.graphs.plot(_dist)


# ═══════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════

class LiFiMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Li-Fi Transmission System  -  BM-ES")
        self.setMinimumSize(1050,740); self.resize(1280,900)
        self.tabs=QTabWidget()
        self.settings=SerialSettingsTab()
        self.tx_tab=TXTab(self.settings)
        self.rx_tab=RXTab(self.settings)
        self.an_tab=AnalysisTab()
        self.tabs.addTab(self.tx_tab,"TX  (Image / Text)")
        self.tabs.addTab(self.rx_tab,"RX  (Monitor / Simulation)")
        self.tabs.addTab(self.an_tab,"Analysis")
        self.tabs.addTab(self.settings,"Serial Settings")
        self.setCentralWidget(self.tabs)
        self.tx_tab.tx_image_ready.connect(self._on_tx)
        self.rx_tab.rx_image_ready.connect(self._on_rx)
        self.statusBar().showMessage(
            "Ready  |  Simulation available without hardware  |  "
            "Connect TX/RX Arduinos in Serial Settings for hardware mode")

    def _on_tx(self,img):
        self.an_tab.tx_img=img
        self.an_tab.tx_cmp.set_image(numpy_to_pixmap(img,240),"Transmitted")

    def _on_rx(self,img,meta):
        self.an_tab.rx_img=img; self.an_tab.rx_meta=meta
        self.an_tab.rx_cmp.set_image(numpy_to_pixmap(img,240),"Received")
        self.statusBar().showMessage(
            f"RX ready  |  {img.shape}  |  Go to Analysis tab")


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling,True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,True)
    app=QApplication(sys.argv)
    # Configure pyqtgraph after QApplication is created
    pg.setConfigOption('background', 'w')
    pg.setConfigOption('foreground', 'k')
    pg.setConfigOption('antialias', True)
    w=LiFiMainWindow(); w.show()
    sys.exit(app.exec_())

if __name__=="__main__":
    main()