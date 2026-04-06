"""
Li-Fi Hardware Protocol
========================
Handles the specific protocol used by the BM-ES Li-Fi hardware:

TX Arduino (tx.ino):
  - USB Serial @ 57600 baud  ←→  PC
  - SoftwareSerial @ 400 baud  →  optical link
  - Image protocol : PC sends  {0xHH0xHH...}  (1024 hex-encoded bytes)
  - Text  protocol : PC sends  <text_string>

RX Arduino (rx.ino):
  - SoftwareSerial @ 400 baud  ←  optical link
  - Renders bitmap on 128×64 GLCD (openGLCD / KS0108)
  - Echos debug over USB Serial @ 57600

Bitmap format (column-major, page-packed 1-bpp):
  byte 0 = width  (128)
  byte 1 = height ( 64)
  bytes 2..1025 = pixel data, 128 columns × 8 pages (64/8) = 1024 bytes
  Each byte: bits 0..7 = rows 0..7 of that page, bit 0 = top row
"""

import numpy as np
from PIL import Image, ImageOps
import serial
import time
from datetime import datetime
import threading
from PyQt5.QtCore import QThread, pyqtSignal


# ── Constants ──────────────────────────────────────────────────────
HW_WIDTH  = 128
HW_HEIGHT = 64
HW_BAUD   = 57600          # PC ↔ TX/RX Arduino USB serial
OOK_BAUD  = 400            # optical link (SoftwareSerial, handled by Arduino)
MAX_IMAGE_BYTES = HW_WIDTH * (HW_HEIGHT // 8)   # 1024


# ═══════════════════════════════════════════════════════════════════
#  Image Conversion Helpers
# ═══════════════════════════════════════════════════════════════════

def image_to_glcd_bytes(img_path_or_array, dither=True):
    """
    Convert any image → 128×64 1-bpp column-major page-packed byte array.

    Parameters
    ----------
    img_path_or_array : str | PIL.Image | np.ndarray
    dither            : bool  – use Floyd-Steinberg dithering (True) or
                                simple threshold (False)

    Returns
    -------
    pixels : np.ndarray, shape=(1024,), dtype=uint8
             Same format as `2.h` bitmap array (without the width/height header bytes)
    preview : np.ndarray, shape=(64,128), dtype=uint8
              1bpp image as 8-bit grayscale for display (0=black, 255=white)
    """
    # ── Load ──
    if isinstance(img_path_or_array, str):
        img = Image.open(img_path_or_array)
    elif isinstance(img_path_or_array, np.ndarray):
        img = Image.fromarray(img_path_or_array)
    else:
        img = img_path_or_array

    # ── Resize to 128×64, letterbox / pad ──
    img = img.convert("L")                         # grayscale
    img.thumbnail((HW_WIDTH, HW_HEIGHT), Image.LANCZOS)
    # Pad to exact 128×64 with white background
    padded = Image.new("L", (HW_WIDTH, HW_HEIGHT), 255)
    x_off  = (HW_WIDTH  - img.width)  // 2
    y_off  = (HW_HEIGHT - img.height) // 2
    padded.paste(img, (x_off, y_off))
    img = padded

    # ── 1-bpp conversion ──
    if dither:
        img_1bpp = img.convert("1", dither=Image.FLOYDSTEINBERG)
    else:
        img_arr  = np.array(img)
        img_1bpp = Image.fromarray((img_arr > 127).astype(np.uint8) * 255).convert("1")

    # Preview (8-bit grayscale, 0=black 255=white)
    preview = np.array(img_1bpp, dtype=np.uint8) * 255   # shape (64,128)

    # ── Column-major page packing ──
    # openGLCD uses: col-major, each byte = 8 vertical pixels, LSB = topmost row
    pixels_bool = np.array(img_1bpp, dtype=bool)          # (64,128), True=black
    pages        = HW_HEIGHT // 8                          # 8 pages
    glcd_bytes   = np.zeros(HW_WIDTH * pages, dtype=np.uint8)

    for page in range(pages):
        for col in range(HW_WIDTH):
            byte_val = 0
            for bit in range(8):
                row = page * 8 + bit
                if row < HW_HEIGHT:
                    # In openGLCD BLACK means pixel ON (bit=1 in data)
                    # pixels_bool True = black pixel = ON
                    if pixels_bool[row, col]:
                        byte_val |= (1 << bit)
            glcd_bytes[page * HW_WIDTH + col] = byte_val

    return glcd_bytes, preview


def glcd_bytes_to_hex_string(glcd_bytes):
    """
    Encode 1024 raw bytes into the Arduino-expected hex string format:
      {0xHH0xHH0xHH...0xHH}

    Returns the full string including braces.
    """
    inner = "".join(f"0x{b:02X}" for b in glcd_bytes)
    return "{" + inner + "}"


def encode_text_for_hardware(text):
    """
    Encode text string for hardware TX Arduino:
      <text_string>
    Max ~98 characters (Arduino cmd_arr2[100]).
    """
    text = text[:98]   # hard limit
    return f"<{text}>"


def glcd_bytes_from_h_file(h_content):
    """
    Parse a .h file (like 2.h) and extract the bitmap byte array.
    Returns np.ndarray of uint8 values (skips first 2 width/height bytes if present).
    """
    import re
    # Find all hex literals 0xHH
    matches = re.findall(r'0x([0-9A-Fa-f]{2})', h_content)
    raw = np.array([int(m, 16) for m in matches], dtype=np.uint8)
    return raw


def glcd_bytes_to_preview(glcd_bytes):
    """
    Decode column-major page-packed bytes back to a viewable (64,128) uint8 image.
    """
    pages   = HW_HEIGHT // 8
    preview = np.zeros((HW_HEIGHT, HW_WIDTH), dtype=np.uint8)
    for page in range(pages):
        for col in range(HW_WIDTH):
            byte_val = glcd_bytes[page * HW_WIDTH + col]
            for bit in range(8):
                row = page * 8 + bit
                if byte_val & (1 << bit):
                    preview[row, col] = 0    # black pixel
                else:
                    preview[row, col] = 255  # white pixel
    return preview


# ═══════════════════════════════════════════════════════════════════
#  Serial Workers
# ═══════════════════════════════════════════════════════════════════

class HardwareTXWorker(QThread):
    """
    Sends image or text data to the TX Arduino over USB serial.
    QThread + pyqtSignal: all UI updates dispatched to main thread safely.
    Fixes the macOS SIGSEGV caused by calling Qt widgets from a background thread.
    """
    progress = pyqtSignal(int)
    status   = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, port, data_string, baudrate=HW_BAUD, parent=None):
        super().__init__(parent)
        self.port        = port
        self.data_string = data_string
        self.baudrate    = baudrate

    def run(self):
        try:
            self.status.emit("Opening serial port...")
            self.progress.emit(5)

            ser = serial.Serial(self.port, self.baudrate, timeout=3)
            time.sleep(2)

            total = len(self.data_string)
            self.status.emit(f"Sending {total} chars...")
            chunk = 64
            sent  = 0
            start = time.time()

            for i in range(0, total, chunk):
                seg = self.data_string[i : i + chunk].encode("ascii")
                ser.write(seg)
                sent += len(seg)
                self.progress.emit(int(10 + (sent / total) * 75))
                time.sleep(0.01)

            self.status.emit("Waiting for Arduino...")
            self.progress.emit(90)

            response_lines = []
            deadline = time.time() + 5
            while time.time() < deadline:
                line = ser.readline().decode("ascii", errors="replace").strip()
                if line:
                    response_lines.append(line)
                    if "Count" in line or "Received" in line or "EE" in line:
                        break

            elapsed = time.time() - start
            ser.close()
            self.progress.emit(100)
            self.status.emit("Done")

            meta = {
                "bytes_sent"      : sent,
                "time_seconds"    : round(elapsed, 3),
                "data_rate_bps"   : round((sent * 8) / elapsed, 1) if elapsed > 0 else 0,
                "arduino_response": response_lines,
                "timestamp"       : datetime.now().isoformat(),
            }
            self.finished.emit(meta)

        except Exception as e:
            self.error.emit(str(e))


class HardwareRXMonitor(threading.Thread):
    """
    Monitors the RX Arduino's USB serial output.
    The RX Arduino (rx.ino) doesn't echo image data back; it just prints
    debug messages.  This worker reads whatever the Arduino sends.
    Signals:
      on_line(str)   – each line received
      on_stopped()   – when monitoring ends
    """

    def __init__(self, port, baudrate=HW_BAUD):
        super().__init__(daemon=True)
        self.port     = port
        self.baudrate = baudrate
        self._stop    = threading.Event()
        self.on_line  = None     # set by caller
        self.on_stopped = None

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=1)
            time.sleep(1)
            while not self._stop.is_set():
                line = ser.readline().decode("ascii", errors="replace").strip()
                if line and self.on_line:
                    self.on_line(line)
            ser.close()
        except Exception as e:
            if self.on_line:
                self.on_line(f"[ERROR] {e}")
        finally:
            if self.on_stopped:
                self.on_stopped()


# ── Convenience: list serial ports ─────────────────────────────────
def list_serial_ports():
    """Return list of (device, description) tuples."""
    try:
        import serial.tools.list_ports
        return [(p.device, p.description) for p in serial.tools.list_ports.comports()]
    except Exception:
        return []