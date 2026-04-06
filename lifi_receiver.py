"""
Li-Fi Image Receiver
=====================
Listens on a serial port for incoming Li-Fi data from the Arduino
photodetector circuit, reconstructs the image, and saves it.

Can also run in simulation mode: reads the transmitted .npy file and
injects configurable noise to emulate real-world optical channel errors.
"""

import serial
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


def add_channel_noise(data, ber=0.001, snr_db=None):
    """
    Simulate optical channel noise.

    Parameters
    ----------
    data : np.ndarray (uint8)  – clean pixel values
    ber  : float               – target bit error rate
    snr_db : float or None     – if given, derive BER from SNR

    Returns
    -------
    noisy : np.ndarray (uint8) – pixel values after simulated errors
    actual_ber : float         – measured BER
    """
    if snr_db is not None:
        # Approximate BER from SNR for OOK modulation: BER ≈ 0.5·erfc(√(SNR/2))
        from scipy.special import erfc
        snr_linear = 10 ** (snr_db / 10)
        ber = 0.5 * erfc(np.sqrt(snr_linear / 2))

    flat = data.flatten().astype(np.uint8)
    total_bits = len(flat) * 8

    # Generate bit-flip mask
    rng = np.random.default_rng()
    flip_mask_bits = rng.random(total_bits) < ber

    # Convert to byte-level XOR mask
    flip_bytes = np.packbits(flip_mask_bits)
    if len(flip_bytes) < len(flat):
        flip_bytes = np.pad(flip_bytes, (0, len(flat) - len(flip_bytes)))
    flip_bytes = flip_bytes[: len(flat)]

    noisy = np.bitwise_xor(flat, flip_bytes).astype(np.uint8)
    actual_ber = flip_mask_bits.sum() / total_bits

    return noisy.reshape(data.shape), actual_ber


def receive_from_serial(port, baudrate, timeout=60):
    """
    Listen on serial port, detect header, read metadata + pixel data.
    Returns (image_array, rx_metadata).
    """
    ser = serial.Serial(port, baudrate, timeout=1)
    time.sleep(2)

    print("  Listening for Li-Fi data...", flush=True)
    buf = bytearray()
    start_time = time.time()

    while (time.time() - start_time) < timeout:
        incoming = ser.read(ser.in_waiting or 1)
        if incoming:
            buf.extend(incoming)

        # Look for header
        hdr_pos = buf.find(HEADER)
        if hdr_pos == -1:
            continue

        # Check if we also have the footer
        ftr_pos = buf.find(FOOTER, hdr_pos + 2)
        if ftr_pos == -1:
            continue

        # Extract payload (between header and footer)
        payload = buf[hdr_pos + 2 : ftr_pos]
        elapsed = time.time() - start_time
        break
    else:
        ser.close()
        raise TimeoutError("No complete packet received within timeout.")

    ser.close()

    # ── Decode metadata (with checksum validation) ──
    idx = 0
    meta_bytes = []
    errors = 0
    for _ in range(5):  # 2+2+1 metadata bytes
        b = payload[idx]
        chk = payload[idx + 1]
        if (b ^ 0xFF) != chk:
            errors += 1
        meta_bytes.append(b)
        idx += 2

    width = int.from_bytes(bytes(meta_bytes[0:2]), "big")
    height = int.from_bytes(bytes(meta_bytes[2:4]), "big")
    channels = meta_bytes[4]

    print(f"  Received metadata: {width}x{height}, {channels} ch")

    # ── Decode pixel data ──
    expected_pixels = width * height * channels
    pixels = []
    for _ in range(expected_pixels):
        if idx + 1 >= len(payload):
            pixels.append(0)  # missing byte → 0
            errors += 1
            continue
        b = payload[idx]
        chk = payload[idx + 1]
        if (b ^ 0xFF) != chk:
            errors += 1
        pixels.append(b)
        idx += 2

    shape = (height, width, channels) if channels > 1 else (height, width)
    img_array = np.array(pixels, dtype=np.uint8).reshape(shape)

    meta = {
        "bytes_received": len(buf),
        "payload_bytes": len(payload),
        "time_seconds": elapsed,
        "data_rate_bps": (len(payload) * 8) / elapsed if elapsed > 0 else 0,
        "checksum_errors": errors,
        "width": width,
        "height": height,
        "channels": channels,
        "timestamp": datetime.now().isoformat(),
        "mode": "hardware",
    }

    return img_array, meta


def receive_simulated(tx_data_path, ber=0.001, snr_db=None):
    """
    Simulate reception by loading transmitted data and adding noise.
    """
    tx_img = np.load(tx_data_path)

    # Load TX metadata if available
    meta_path = os.path.join(os.path.dirname(tx_data_path), "tx_meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            tx_meta = json.load(f)
    else:
        tx_meta = {}

    noisy_img, actual_ber = add_channel_noise(tx_img, ber=ber, snr_db=snr_db)

    h = tx_img.shape[0]
    w = tx_img.shape[1]
    ch = tx_img.shape[2] if tx_img.ndim == 3 else 1
    payload_bytes = h * w * ch

    # Simulate time based on baud rate
    baudrate = tx_meta.get("baudrate", 9600)
    sim_time = (payload_bytes * 2 * 10) / baudrate  # ×2 for checksum, ×10 bits/byte

    meta = {
        "bytes_received": payload_bytes * 2,
        "payload_bytes": payload_bytes,
        "time_seconds": sim_time,
        "data_rate_bps": (payload_bytes * 8) / sim_time if sim_time > 0 else 0,
        "target_ber": ber,
        "actual_ber": float(actual_ber),
        "snr_db": snr_db,
        "width": w,
        "height": h,
        "channels": ch,
        "timestamp": datetime.now().isoformat(),
        "mode": "simulation",
    }

    return noisy_img, meta


def save_received(img_array, meta, output_dir):
    """Save received image and metadata."""
    os.makedirs(output_dir, exist_ok=True)

    # Save raw numpy array
    np.save(os.path.join(output_dir, "received_image.npy"), img_array)

    # Save as viewable image
    if img_array.ndim == 2:
        img = Image.fromarray(img_array, mode="L")
    else:
        img = Image.fromarray(img_array, mode="RGB")
    img.save(os.path.join(output_dir, "received_image.png"))

    # Save metadata
    with open(os.path.join(output_dir, "rx_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print(f"  Received image saved to {output_dir}/")


# ── CLI Entry Point ─────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Li-Fi Image Receiver – reconstruct image from Arduino photodetector"
    )
    parser.add_argument("-p", "--port", default=None,
                        help="Serial port (e.g. COM3, /dev/ttyUSB0)")
    parser.add_argument("-b", "--baud", type=int, default=9600,
                        help="Baud rate (default 9600)")
    parser.add_argument("-o", "--output", default="lifi_data",
                        help="Output directory")
    parser.add_argument("-t", "--timeout", type=int, default=120,
                        help="Receive timeout in seconds")
    parser.add_argument("--simulate", action="store_true",
                        help="Simulate using transmitted .npy data + noise")
    parser.add_argument("--tx-data", default="lifi_data/transmitted_image.npy",
                        help="Path to transmitted .npy (simulation mode)")
    parser.add_argument("--ber", type=float, default=0.001,
                        help="Target BER for simulation (default 0.001)")
    parser.add_argument("--snr", type=float, default=None,
                        help="SNR in dB (overrides --ber if given)")

    args = parser.parse_args()

    print("\n╔══════════════════════════════════════╗")
    print("║      Li-Fi Image Receiver            ║")
    print("╚══════════════════════════════════════╝\n")

    if args.simulate:
        print(f"  [SIMULATION] BER={args.ber}, SNR={args.snr} dB")
        print(f"  Loading: {args.tx_data}")
        img_array, meta = receive_simulated(
            args.tx_data, ber=args.ber, snr_db=args.snr
        )
    else:
        if args.port is None:
            print("  Error: --port is required for hardware mode.")
            print("  Use --simulate to test without hardware.\n")
            sys.exit(1)
        img_array, meta = receive_from_serial(args.port, args.baud, args.timeout)

    print(f"\n  Image: {meta['width']}x{meta['height']}, {meta['channels']} ch")
    print(f"  Data rate: {meta['data_rate_bps']:.1f} bps")
    if "actual_ber" in meta:
        print(f"  Actual BER: {meta['actual_ber']:.6f}")

    save_received(img_array, meta, args.output)
    print("\n✓ Reception complete.\n")


if __name__ == "__main__":
    main()
