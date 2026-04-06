"""
Li-Fi Image Transmitter
========================
Reads an image, converts to binary, and sends to Arduino via serial port.
The Arduino drives an LED/laser to transmit the data optically.

Protocol:
  - START header: 0xAA 0x55
  - Image width (2 bytes, big-endian)
  - Image height (2 bytes, big-endian)
  - Channels (1 byte)
  - Pixel data (row-major, channel-interleaved)
  - END footer: 0x55 0xAA
  - Each byte is sent with a 1-byte XOR checksum immediately after
"""

import serial
import serial.tools.list_ports
import numpy as np
from PIL import Image
import time
import sys
import os
import argparse
import json
from datetime import datetime


# ── Protocol Constants ──────────────────────────────────────────────
HEADER = bytes([0xAA, 0x55])
FOOTER = bytes([0x55, 0xAA])
CHUNK_SIZE = 64  # bytes per serial write burst


def list_serial_ports():
    """List all available serial ports."""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("  No serial ports found.")
    for p in ports:
        print(f"  {p.device}  –  {p.description}")
    return [p.device for p in ports]


def prepare_image(image_path, max_size=128):
    """
    Load image, resize to fit max_size, return numpy array + metadata.
    Supports grayscale and RGB.
    """
    img = Image.open(image_path)

    # Resize keeping aspect ratio
    img.thumbnail((max_size, max_size), Image.LANCZOS)

    # Convert to RGB or grayscale
    if img.mode == "L":
        channels = 1
    elif img.mode in ("RGB", "RGBA"):
        img = img.convert("RGB")
        channels = 3
    else:
        img = img.convert("RGB")
        channels = 3

    arr = np.array(img, dtype=np.uint8)
    h, w = arr.shape[:2]
    print(f"  Image prepared: {w}x{h}, {channels} channel(s), "
          f"{w * h * channels} bytes payload")
    return arr, w, h, channels


def build_packet(img_array, width, height, channels):
    """
    Build the full transmission packet with checksums.
    Format per data byte:  [byte] [xor_checksum]
    """
    packet = bytearray()

    # ── Header ──
    packet.extend(HEADER)

    # ── Metadata (width, height, channels) ──
    meta = bytearray()
    meta.extend(width.to_bytes(2, "big"))
    meta.extend(height.to_bytes(2, "big"))
    meta.append(channels)

    for b in meta:
        packet.append(b)
        packet.append(b ^ 0xFF)  # simple XOR checksum

    # ── Pixel data ──
    flat = img_array.flatten()
    for b in flat:
        packet.append(int(b))
        packet.append(int(b) ^ 0xFF)

    # ── Footer ──
    packet.extend(FOOTER)

    return bytes(packet)


def transmit(port, baudrate, packet, simulate=False):
    """
    Send packet over serial to Arduino.
    Returns transmission metadata (time, data_rate, etc.)
    """
    total_bytes = len(packet)
    print(f"  Packet size: {total_bytes} bytes (including checksums & framing)")

    if simulate:
        # ── Simulation mode (no Arduino needed) ──
        print("  [SIMULATION] No serial port used.")
        sim_time = total_bytes / (baudrate / 10)  # approximate
        return {
            "bytes_sent": total_bytes,
            "time_seconds": sim_time,
            "data_rate_bps": (total_bytes * 8) / sim_time,
            "baudrate": baudrate,
            "timestamp": datetime.now().isoformat(),
            "mode": "simulation",
        }

    # ── Real transmission ──
    ser = serial.Serial(port, baudrate, timeout=2)
    time.sleep(2)  # wait for Arduino reset

    print("  Transmitting", end="", flush=True)
    start = time.time()
    sent = 0

    for i in range(0, total_bytes, CHUNK_SIZE):
        chunk = packet[i : i + CHUNK_SIZE]
        ser.write(chunk)
        sent += len(chunk)
        # Print progress every 10%
        if sent % max(1, total_bytes // 10) < CHUNK_SIZE:
            print(".", end="", flush=True)
        # Small delay to avoid overrun on Arduino side
        time.sleep(0.002)

    elapsed = time.time() - start
    ser.flush()
    ser.close()

    print(f"\n  Done! {sent} bytes in {elapsed:.3f}s")

    return {
        "bytes_sent": sent,
        "time_seconds": elapsed,
        "data_rate_bps": (sent * 8) / elapsed,
        "baudrate": baudrate,
        "timestamp": datetime.now().isoformat(),
        "mode": "hardware",
    }


def save_transmission_log(meta, img_array, output_dir):
    """Save raw transmitted data and metadata for the receiver / analyser."""
    os.makedirs(output_dir, exist_ok=True)

    # Save raw pixel data (ground truth for quality comparison)
    np.save(os.path.join(output_dir, "transmitted_image.npy"), img_array)

    # Save metadata
    with open(os.path.join(output_dir, "tx_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print(f"  Transmission log saved to {output_dir}/")


# ── CLI Entry Point ─────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Li-Fi Image Transmitter – send image via Arduino LED/laser"
    )
    parser.add_argument("image", help="Path to source image")
    parser.add_argument("-p", "--port", default=None,
                        help="Serial port (e.g. COM3, /dev/ttyUSB0)")
    parser.add_argument("-b", "--baud", type=int, default=9600,
                        help="Baud rate (default 9600)")
    parser.add_argument("-s", "--size", type=int, default=64,
                        help="Max image dimension in pixels (default 64)")
    parser.add_argument("-o", "--output", default="lifi_data",
                        help="Output directory for logs")
    parser.add_argument("--simulate", action="store_true",
                        help="Simulate without real hardware")

    args = parser.parse_args()

    print("\n╔══════════════════════════════════════╗")
    print("║      Li-Fi Image Transmitter         ║")
    print("╚══════════════════════════════════════╝\n")

    # List ports
    if args.port is None and not args.simulate:
        print("Available serial ports:")
        ports = list_serial_ports()
        if not ports:
            print("\n  No ports found. Use --simulate to test without hardware.")
            sys.exit(1)
        args.port = ports[0]
        print(f"\n  Auto-selected: {args.port}")

    # Prepare image
    print(f"\nLoading image: {args.image}")
    img_array, w, h, ch = prepare_image(args.image, max_size=args.size)

    # Build packet
    print("\nBuilding packet...")
    packet = build_packet(img_array, w, h, ch)

    # Transmit
    print(f"\nTransmitting at {args.baud} baud...")
    meta = transmit(args.port, args.baud, packet, simulate=args.simulate)
    meta["image_width"] = w
    meta["image_height"] = h
    meta["image_channels"] = ch
    meta["source_image"] = os.path.abspath(args.image)

    print(f"\n  Effective data rate: {meta['data_rate_bps']:.1f} bps")

    # Save logs
    print("\nSaving transmission log...")
    save_transmission_log(meta, img_array, args.output)

    print("\n✓ Transmission complete.\n")


if __name__ == "__main__":
    main()
