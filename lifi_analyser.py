"""
Li-Fi Image Quality Analyser
==============================
Computes all requested metrics by comparing the original (transmitted)
image with the received (possibly noisy) image:

  • MSE   – Mean Squared Error
  • PSNR  – Peak Signal-to-Noise Ratio (dB)
  • BER   – Bit Error Rate
  • SNR   – Signal-to-Noise Ratio (dB)
  • Data Rate (bps)
  • Distance vs Quality graph  (simulates multiple distances)

Produces a single multi-panel PNG report and prints a summary table.
"""

import numpy as np
from PIL import Image
import matplotlib
matplotlib.use("Agg")  # headless backend
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import os
import json
import argparse
from datetime import datetime


# ═══════════════════════════════════════════════════════════════════
#  Core Metrics
# ═══════════════════════════════════════════════════════════════════

def compute_mse(original, received):
    """Mean Squared Error between two images (float64)."""
    return np.mean((original.astype(np.float64) - received.astype(np.float64)) ** 2)


def compute_psnr(original, received, max_val=255.0):
    """Peak Signal-to-Noise Ratio in dB."""
    mse = compute_mse(original, received)
    if mse == 0:
        return float("inf")
    return 10.0 * np.log10((max_val ** 2) / mse)


def compute_ber(original, received):
    """
    Bit Error Rate – compare every bit of every pixel.
    """
    orig_flat = original.flatten().astype(np.uint8)
    recv_flat = received.flatten().astype(np.uint8)
    xor = np.bitwise_xor(orig_flat, recv_flat)
    bit_errors = sum(bin(b).count("1") for b in xor)
    total_bits = len(orig_flat) * 8
    return bit_errors / total_bits if total_bits > 0 else 0.0


def compute_snr(original, received):
    """
    Signal-to-Noise Ratio in dB.
    SNR = 10·log10( Σ signal² / Σ noise² )
    """
    signal = original.astype(np.float64)
    noise = signal - received.astype(np.float64)
    signal_power = np.mean(signal ** 2)
    noise_power = np.mean(noise ** 2)
    if noise_power == 0:
        return float("inf")
    return 10.0 * np.log10(signal_power / noise_power)


def compute_ssim_simple(original, received):
    """
    Simplified SSIM (Structural Similarity Index) – single-scale, no windowing.
    Returns a value between -1 and 1  (1 = identical).
    """
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2

    img1 = original.astype(np.float64)
    img2 = received.astype(np.float64)

    mu1 = img1.mean()
    mu2 = img2.mean()
    sigma1_sq = img1.var()
    sigma2_sq = img2.var()
    sigma12 = np.mean((img1 - mu1) * (img2 - mu2))

    num = (2 * mu1 * mu2 + C1) * (2 * sigma12 + C2)
    den = (mu1 ** 2 + mu2 ** 2 + C1) * (sigma1_sq + sigma2_sq + C2)
    return num / den


# ═══════════════════════════════════════════════════════════════════
#  Distance vs Quality Simulation
# ═══════════════════════════════════════════════════════════════════

def simulate_distance_vs_quality(original, distances_cm=None, led_power_mw=5.0):
    """
    Model how image quality degrades with distance for a Li-Fi link.

    Uses a simplified inverse-square-law + noise model:
        received_SNR(d) = SNR_0 - 20·log10(d / d_ref)
    where SNR_0 is the SNR at d_ref = 5 cm.

    Returns dict with distance list and metric lists.
    """
    if distances_cm is None:
        distances_cm = [5, 10, 15, 20, 30, 40, 50, 75, 100, 150]

    from scipy.special import erfc  # for BER estimation

    d_ref = 5.0  # reference distance (cm)
    snr_0_db = 30.0 + 10 * np.log10(led_power_mw / 5.0)  # base SNR at d_ref

    results = {
        "distance_cm": [],
        "psnr": [],
        "mse": [],
        "ber": [],
        "snr_db": [],
        "ssim": [],
    }

    for d in distances_cm:
        # SNR drops with inverse-square law
        snr_db = snr_0_db - 20 * np.log10(max(d, 1) / d_ref)
        snr_db = max(snr_db, 0.0)

        # Derive BER from SNR (OOK modulation)
        snr_lin = 10 ** (snr_db / 10)
        ber = float(0.5 * erfc(np.sqrt(snr_lin / 2)))
        ber = min(ber, 0.5)  # cap at 0.5 (random)

        # Simulate noisy received image
        noisy = _add_noise_for_ber(original, ber)

        results["distance_cm"].append(d)
        results["psnr"].append(compute_psnr(original, noisy))
        results["mse"].append(compute_mse(original, noisy))
        results["ber"].append(ber)
        results["snr_db"].append(snr_db)
        results["ssim"].append(compute_ssim_simple(original, noisy))

    return results


def _add_noise_for_ber(data, ber):
    """Add bit-flip noise to achieve approximate BER."""
    flat = data.flatten().astype(np.uint8)
    total_bits = len(flat) * 8
    rng = np.random.default_rng(42)  # fixed seed for reproducibility
    flip_bits = rng.random(total_bits) < ber
    flip_bytes = np.packbits(flip_bits)
    if len(flip_bytes) < len(flat):
        flip_bytes = np.pad(flip_bytes, (0, len(flat) - len(flip_bytes)))
    flip_bytes = flip_bytes[: len(flat)]
    noisy = np.bitwise_xor(flat, flip_bytes).astype(np.uint8)
    return noisy.reshape(data.shape)


# ═══════════════════════════════════════════════════════════════════
#  Report Generation
# ═══════════════════════════════════════════════════════════════════

def generate_report(original, received, rx_meta, output_dir):
    """
    Generate a comprehensive multi-panel quality report PNG + JSON summary.
    """
    os.makedirs(output_dir, exist_ok=True)

    # ── Compute metrics ──
    mse_val = compute_mse(original, received)
    psnr_val = compute_psnr(original, received)
    ber_val = compute_ber(original, received)
    snr_val = compute_snr(original, received)
    ssim_val = compute_ssim_simple(original, received)
    data_rate = rx_meta.get("data_rate_bps", 0)

    metrics = {
        "MSE": round(mse_val, 4),
        "PSNR_dB": round(psnr_val, 2),
        "BER": round(ber_val, 8),
        "SNR_dB": round(snr_val, 2),
        "SSIM": round(ssim_val, 4),
        "Data_Rate_bps": round(data_rate, 2),
    }

    # ── Distance vs Quality ──
    dist_results = simulate_distance_vs_quality(original)

    # ── Print summary ──
    print("\n" + "=" * 50)
    print("  LI-FI IMAGE QUALITY ANALYSIS REPORT")
    print("=" * 50)
    print(f"  MSE  (Mean Squared Error) : {mse_val:.4f}")
    print(f"  PSNR (Peak SNR)           : {psnr_val:.2f} dB")
    print(f"  BER  (Bit Error Rate)     : {ber_val:.8f}")
    print(f"  SNR  (Signal-to-Noise)    : {snr_val:.2f} dB")
    print(f"  SSIM (Structural Sim.)    : {ssim_val:.4f}")
    print(f"  Data Rate                 : {data_rate:.1f} bps")
    print("=" * 50)

    # ── Generate figure ──
    fig = plt.figure(figsize=(18, 14), facecolor="#0d1117")
    fig.suptitle("Li-Fi Image Transmission – Quality Analysis",
                 fontsize=20, fontweight="bold", color="white", y=0.98)

    gs = gridspec.GridSpec(3, 4, figure=fig, hspace=0.4, wspace=0.35,
                           left=0.06, right=0.96, top=0.92, bottom=0.06)

    ax_style = dict(facecolor="#161b22")
    text_color = "#c9d1d9"
    accent = "#58a6ff"
    warn_color = "#f0883e"
    good_color = "#3fb950"

    # ── Panel 1: Original image ──
    ax1 = fig.add_subplot(gs[0, 0], **ax_style)
    _show_image(ax1, original, "Original Image")

    # ── Panel 2: Received image ──
    ax2 = fig.add_subplot(gs[0, 1], **ax_style)
    _show_image(ax2, received, "Received Image")

    # ── Panel 3: Difference map ──
    ax3 = fig.add_subplot(gs[0, 2], **ax_style)
    diff = np.abs(original.astype(np.float64) - received.astype(np.float64))
    if diff.ndim == 3:
        diff = np.mean(diff, axis=2)
    im = ax3.imshow(diff, cmap="hot", vmin=0, vmax=128)
    ax3.set_title("Error Map (amplified)", color=text_color, fontsize=11)
    ax3.axis("off")
    plt.colorbar(im, ax=ax3, fraction=0.046, pad=0.04)

    # ── Panel 4: Metrics dashboard ──
    ax4 = fig.add_subplot(gs[0, 3], **ax_style)
    ax4.axis("off")
    ax4.set_title("Quality Metrics", color=text_color, fontsize=11)
    metric_lines = [
        ("MSE",       f"{mse_val:.4f}",       _quality_color(mse_val, 0, 100, True)),
        ("PSNR",      f"{psnr_val:.2f} dB",   _quality_color(psnr_val, 20, 50, False)),
        ("BER",       f"{ber_val:.2e}",        _quality_color(ber_val, 0, 0.01, True)),
        ("SNR",       f"{snr_val:.2f} dB",     _quality_color(snr_val, 10, 40, False)),
        ("SSIM",      f"{ssim_val:.4f}",       _quality_color(ssim_val, 0.5, 1.0, False)),
        ("Data Rate", f"{data_rate:.0f} bps",  accent),
    ]
    for i, (name, val, color) in enumerate(metric_lines):
        y = 0.85 - i * 0.14
        ax4.text(0.05, y, name, transform=ax4.transAxes,
                 fontsize=12, color=text_color, fontweight="bold")
        ax4.text(0.95, y, val, transform=ax4.transAxes,
                 fontsize=13, color=color, fontweight="bold", ha="right")

    # ── Panel 5: Distance vs PSNR ──
    ax5 = fig.add_subplot(gs[1, 0:2], **ax_style)
    ax5.plot(dist_results["distance_cm"], dist_results["psnr"],
             "o-", color=accent, linewidth=2, markersize=6)
    ax5.set_xlabel("Distance (cm)", color=text_color)
    ax5.set_ylabel("PSNR (dB)", color=text_color)
    ax5.set_title("Distance vs PSNR", color=text_color, fontsize=11)
    ax5.tick_params(colors=text_color)
    ax5.grid(True, alpha=0.15, color="white")
    ax5.axhline(y=30, color=good_color, linestyle="--", alpha=0.5, label="Good (30 dB)")
    ax5.axhline(y=20, color=warn_color, linestyle="--", alpha=0.5, label="Fair (20 dB)")
    ax5.legend(fontsize=9, facecolor="#161b22", edgecolor="#30363d", labelcolor=text_color)

    # ── Panel 6: Distance vs BER ──
    ax6 = fig.add_subplot(gs[1, 2:4], **ax_style)
    ax6.semilogy(dist_results["distance_cm"], dist_results["ber"],
                 "s-", color=warn_color, linewidth=2, markersize=6)
    ax6.set_xlabel("Distance (cm)", color=text_color)
    ax6.set_ylabel("BER (log scale)", color=text_color)
    ax6.set_title("Distance vs Bit Error Rate", color=text_color, fontsize=11)
    ax6.tick_params(colors=text_color)
    ax6.grid(True, alpha=0.15, color="white", which="both")
    ax6.axhline(y=1e-3, color=good_color, linestyle="--", alpha=0.5, label="Target (10⁻³)")
    ax6.legend(fontsize=9, facecolor="#161b22", edgecolor="#30363d", labelcolor=text_color)

    # ── Panel 7: Distance vs SNR ──
    ax7 = fig.add_subplot(gs[2, 0:2], **ax_style)
    ax7.plot(dist_results["distance_cm"], dist_results["snr_db"],
             "^-", color=good_color, linewidth=2, markersize=6)
    ax7.set_xlabel("Distance (cm)", color=text_color)
    ax7.set_ylabel("SNR (dB)", color=text_color)
    ax7.set_title("Distance vs SNR", color=text_color, fontsize=11)
    ax7.tick_params(colors=text_color)
    ax7.grid(True, alpha=0.15, color="white")

    # ── Panel 8: Distance vs SSIM ──
    ax8 = fig.add_subplot(gs[2, 2:4], **ax_style)
    ax8.plot(dist_results["distance_cm"], dist_results["ssim"],
             "D-", color="#bc8cff", linewidth=2, markersize=6)
    ax8.set_xlabel("Distance (cm)", color=text_color)
    ax8.set_ylabel("SSIM", color=text_color)
    ax8.set_title("Distance vs SSIM", color=text_color, fontsize=11)
    ax8.tick_params(colors=text_color)
    ax8.grid(True, alpha=0.15, color="white")
    ax8.set_ylim(-0.05, 1.05)
    ax8.axhline(y=0.9, color=good_color, linestyle="--", alpha=0.5, label="Excellent (0.9)")
    ax8.axhline(y=0.7, color=warn_color, linestyle="--", alpha=0.5, label="Acceptable (0.7)")
    ax8.legend(fontsize=9, facecolor="#161b22", edgecolor="#30363d", labelcolor=text_color)

    # ── Save ──
    report_path = os.path.join(output_dir, "lifi_quality_report.png")
    fig.savefig(report_path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"\n  Report saved: {report_path}")

    # Save JSON metrics
    full_metrics = {**metrics, "distance_analysis": dist_results}
    # Convert numpy types for JSON serialization
    full_metrics = _json_safe(full_metrics)
    json_path = os.path.join(output_dir, "quality_metrics.json")
    with open(json_path, "w") as f:
        json.dump(full_metrics, f, indent=2)
    print(f"  Metrics JSON: {json_path}")

    return metrics


def _show_image(ax, img, title):
    """Display image in axes."""
    if img.ndim == 2:
        ax.imshow(img, cmap="gray", vmin=0, vmax=255)
    else:
        ax.imshow(img)
    ax.set_title(title, color="#c9d1d9", fontsize=11)
    ax.axis("off")


def _quality_color(val, low, high, invert):
    """Return green/orange/red based on value quality."""
    if invert:  # lower is better (MSE, BER)
        ratio = 1.0 - min((val - low) / (high - low + 1e-12), 1.0)
    else:       # higher is better (PSNR, SNR, SSIM)
        ratio = min((val - low) / (high - low + 1e-12), 1.0)
    if ratio > 0.7:
        return "#3fb950"
    elif ratio > 0.3:
        return "#f0883e"
    return "#f85149"


def _json_safe(obj):
    """Recursively convert numpy types to Python natives for JSON."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


# ═══════════════════════════════════════════════════════════════════
#  CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Li-Fi Image Quality Analyser"
    )
    parser.add_argument("-d", "--data-dir", default="lifi_data",
                        help="Directory with transmitted/received .npy files")
    parser.add_argument("-o", "--output", default=None,
                        help="Output directory (default: same as --data-dir)")
    parser.add_argument("--original", default=None,
                        help="Override path to original image (.npy or image file)")
    parser.add_argument("--received", default=None,
                        help="Override path to received image (.npy or image file)")

    args = parser.parse_args()
    output_dir = args.output or args.data_dir

    print("\n╔══════════════════════════════════════╗")
    print("║    Li-Fi Quality Analyser            ║")
    print("╚══════════════════════════════════════╝\n")

    # Load images
    orig_path = args.original or os.path.join(args.data_dir, "transmitted_image.npy")
    recv_path = args.received or os.path.join(args.data_dir, "received_image.npy")

    original = _load_image(orig_path)
    received = _load_image(recv_path)

    # Ensure same shape
    if original.shape != received.shape:
        print(f"  Warning: shape mismatch {original.shape} vs {received.shape}")
        # Crop to common size
        h = min(original.shape[0], received.shape[0])
        w = min(original.shape[1], received.shape[1])
        original = original[:h, :w]
        received = received[:h, :w]

    # Load RX metadata
    rx_meta_path = os.path.join(args.data_dir, "rx_meta.json")
    if os.path.exists(rx_meta_path):
        with open(rx_meta_path) as f:
            rx_meta = json.load(f)
    else:
        rx_meta = {"data_rate_bps": 0}

    generate_report(original, received, rx_meta, output_dir)
    print("\n✓ Analysis complete.\n")


def _load_image(path):
    """Load image from .npy or standard image format."""
    if path.endswith(".npy"):
        return np.load(path)
    img = Image.open(path)
    if img.mode == "L":
        return np.array(img, dtype=np.uint8)
    return np.array(img.convert("RGB"), dtype=np.uint8)


if __name__ == "__main__":
    main()
