# Li-Fi Data Transmission System (BM-ES)

![Li-Fi Header](https://img.shields.io/badge/Technology-Li--Fi-brightgreen)
![Python Version](https://img.shields.io/badge/Python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

A high-performance, real-time image and text transmission system using **Light Fidelity (Li-Fi)** technology. This project features a robust PyQt5-based Graphical User Interface (GUI) for hardware control, simulation, and advanced signal analysis.

---

## 🚀 Features

- **Dual Transmission Modes**:
  - **Hardware Mode**: Interface with Arduino-based TX/RX modules via serial communication.
  - **Simulation Mode**: Test protocols and analysis algorithms without physical hardware by simulating noise and signal degradation.
- **Content Types**:
  - **Image Transmission**: Converts images to 128x64 1-bpp bitmaps for transmission over light.
  - **Text Transmission**: Real-time text framing and decoding.
- **Advanced Analytics**:
  - Real-time quality metrics: **MSE, PSNR, BER, SNR, SSIM**.
  - Interactive **Distance vs. Quality** graphs (using `pyqtgraph`).
  - Signal monitoring and log parsing.
- **Cross-Platform**: Works on macOS, Windows, and Linux.
- **Secure Architecture**: Core algorithms are protected via Cython compilation for production builds.

---

## 🛠️ System Architecture

The repository is organized into modular components for transmission, reception, and analysis:

```text
.
├── lifi_gui.py            # Main GUI Application Entry Point
├── lifi_transmitter.py    # Image processing and packet building
├── lifi_receiver.py       # Signal reception and decoding logic
├── lifi_analyser.py       # Quality metrics and graph generation
├── lifi_hardware_protocol.py # Arduino serial communication handlers
├── setup.py               # Cython build script for protection
└── lifi_gui.spec          # PyInstaller configuration
```

---

## 💻 Getting Started

### Prerequisites

- Python 3.8 or higher
- Arduino Uno/Nano (for hardware mode)
- Required Python packages:
  ```bash
  pip install PyQt5 pyqtgraph numpy pillow scipy pyserial
  ```

### Installation & Run

1. **Clone the repository**:

   ```bash
   git clone https://github.com/erprashantkumar85/LiFi-Data-Transmission-System.git
   cd LiFi-Data-Transmission-System
   ```

2. **Install all dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

3. **Launch the application**:
   ```bash
   python lifi_gui.py
   ```

---

## 🔌 Hardware Setup

To use the system in **Hardware Mode**, you need two Arduinos (one for TX, one for RX) flashed with the project's firmware:

1. Connect the **TX Arduino** and **RX Arduino** to your computer.
2. Ensure they are running at **57600 baud**.
3. In the GUI, go to the **Serial Settings** tab to select and connect to the respective COM/Serial ports.

---

## 📦 Building for Windows (.exe)

This project uses **GitHub Actions** to automatically compile and package the application for Windows using Cython and PyInstaller.

1. Push your changes to the `main` branch.
2. Go to the **Actions** tab in your GitHub repository.
3. Select the **Build Windows Release** workflow.
4. Download the `LiFi-BM-ES-Windows.zip` artifact once the build completes.

For local build details, see [README_BUILD.md](file:///Users/prashantgautam/Desktop/Projects/LiFi/Github_Repo/README_BUILD.md).

---

## 📊 Analytics & Metrics

The system provides deep insights into the transmission quality:

| Metric        | Description                                         |
| ------------- | --------------------------------------------------- |
| **PSNR**      | Peak Signal-to-Noise Ratio (measures image quality) |
| **BER**       | Bit Error Rate (measures transmission reliability)  |
| **SNR**       | Signal-to-Noise Ratio                               |
| **SSIM**      | Structural Similarity Index (perceptual quality)    |
| **Data Rate** | Effective transmission speed in bps                 |

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.
