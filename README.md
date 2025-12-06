# CO<sub>2</sub> Mini Meter

A Raspberry Pi-based CO<sub>2</sub> concentration monitoring device with e-ink display and web interface for tracking indoor air quality.

## 1. Device Photo

![CO<sub>2</sub> Mini Meter Device](device_photo.jpg)
*Photo: CO<sub>2</sub> Mini Meter showing real-time measurements on e-ink display*

## 2. Overview

The CO<sub>2</sub> Mini Meter is a compact, air quality monitoring system designed to measure and display carbon dioxide levels, temperature, and humidity in indoor environments. The device features an easily readable e-ink display showing current CO<sub>2</sub> readings and a trend graph with last 12 hours, along with a web interface accessible from any device on your local network.

## 3. Hardware Components

The device consists of the following hardware components:

- **Raspberry Pi** (any model with I2C and GPIO support)
- **Sensirion SCD30** CO<sub>2</sub> sensor module
  - Measures CO<sub>2</sub> concentration (400-10,000 ppm)
  - Built-in temperature sensor
  - Built-in humidity sensor
  - I2C interface (address 0x61)
- **Waveshare 2.13" e-Paper Display V4** (250x122 pixels)
  - Low power consumption
  - Black and white display
  - SPI interface via GPIO
  - size of Raspberry Pi Zero/Zero W
- **Power supply** for Raspberry Pi

### 3.1. Connections

- **SCD30 Sensor**: Connected via I2C (default `/dev/i2c-1`)
- **E-ink Display**: Connected via SPI/GPIO using Waveshare e-Paper HAT

## 4. Software Features

The CO<sub>2</sub> Mini Meter software provides comprehensive monitoring and data logging capabilities:

### 4.1. Real-time Monitoring

- **Continuous Measurements**: Reads CO<sub>2</sub>, temperature, and humidity every 60 seconds
- **Sensor Warmup**: Automatically skips the first 2 readings after startup for sensor stabilization
- **E-ink Display**:
  - Shows current CO<sub>2</sub> level in large digits
  - Displays current date and time
  - Shows a 12-hour trend graph (245×70 pixels) updated every 15 minutes

### 4.2. Data Logging

- **CSV Storage**: All measurements saved to daily CSV files in the format:

  ```
  data/data_YYYY-MM-DD.csv
  ```

- **Data Fields**: Timestamp, CO<sub>2</sub> (×10⁻⁶), Temperature (°C), Humidity (%)
- **Automatic Cleanup**: Maintains rolling 12-hour history in memory
- **Persistent Storage**: Historical data remains available in CSV files

### 4.3. Web Interface

- **Real-time Dashboard**: Access via `http://[device-ip]:8080` or `http://[hostname].local:8080`
- **Auto-refresh**: Page updates every 10 seconds
- **Interactive Plots**:
  - SVG plot showing 12-hour trends for all three parameters
  - CO<sub>2</sub> (green line, left axis)
  - Temperature (red line, right axis)
  - Humidity (blue line, right axis, offset)
  - Gap detection: Shows breaks in data when measurements are interrupted (e.g. restart of the device)
- **Measurement Table**: Complete list of all measurements in the current 12-hour window

### 4.4. System Integration

- **Systemd Service**: Automatically starts on boot
- **mDNS/Avahi**: Device discoverable on network as `co2minimeter.local`
- **Fallback Mode**: For testing - runs with simulated data if sensor or display unavailable

### 4.5. Multi-threaded Architecture

The software uses four independent threads for optimal performance:

1. **CO<sub>2</sub> Sensor Thread**: Handles sensor communication and data collection
2. **E-ink Display Thread**: Updates the display based on measurement changes
3. **Plot Generator Thread**: Creates SVG and PNG plots every 15 minutes
4. **Web Server Thread**: Serves the web interface on port 8080

### 4.6. Configuration

Key configuration constants (defined in `co2minimeter.py`):

- `CO2_MEASUREMENT_INTERVAL = 60` - Measurement interval in seconds
- `SENSOR_WARMUP_READINGS = 2` - Number of initial readings to skip
- `PLOT_UPDATE_INTERVAL = 900` - Plot update interval (15 minutes)
- `WEB_SERVER_PORT = 8080` - Web server port
- `HOURS_TO_KEEP = 12` - Rolling history window

## 5. Installation

Run the automated installation script:

```bash
cd /path/to/co2minimeter
chmod +x install.sh
./install.sh
```

The installer will:

- Install required system packages (Python, matplotlib, Pillow, Avahi)
- Configure mDNS for network discovery
- Create Python virtual environment
- Install SCD30 sensor driver
- Set up systemd service for auto-start

## 6. Usage

### 6.1. Manual Start

```bash
source venv/bin/activate
python3 co2minimeter.py
```

### 6.2. Service Management

```bash
# Start the service
sudo systemctl start co2minimeter.service

# Stop the service
sudo systemctl stop co2minimeter.service

# Check status
sudo systemctl status co2minimeter.service

# View logs
sudo journalctl -u co2minimeter.service -f
```

### 6.3. Accessing the Web Interface

- **Local**: `http://localhost:8080`
- **Network**: `http://co2minimeter.local:8080`
- **IP Address**: `http://[device-ip]:8080`

## 7. File Structure

```
co2minimeter/
├── co2minimeter.py           # Main application
├── co2minimeter.service      # Systemd service file
├── co2minimeter_webpage.html # Web interface template
├── install.sh                # Installation script
├── requirements.txt          # Python dependencies
├── README.md                 # This file
├── fonts/                    # Display fonts
│   └── DejaVuSansMono-Bold.ttf
├── data/                     # Generated data (auto-created)
│   ├── data_YYYY-MM-DD.csv  # Daily CSV files
│   ├── data_latest_plot.svg # Web plot
│   └── data_latest_plot.png # Display plot
└── e-Paper/                  # Waveshare e-Paper library
```

## 8. Dependencies

### 8.1. System Packages

- `python3-venv`
- `python3-matplotlib`
- `python3-pil`
- `avahi-daemon`
- `avahi-utils`

### 8.2. Python Packages

- `sensirion-i2c-scd30` - SCD30 sensor driver
- `matplotlib` - Plotting (system package)
- `Pillow` - Image manipulation (system package)

## 9. Troubleshooting

### 9.1. No sensor detected

- Check I2C connection: `i2cdetect -y 1`
- Look for device at address 0x61
- Verify sensor power supply

### 9.2. Display not updating

- Check GPIO connections
- Verify e-Paper library installation
- Check logs: `sudo journalctl -u co2minimeter.service`

### 9.3. Web interface not accessible

- Check service status: `sudo systemctl status co2minimeter.service`
- Test mDNS: `avahi-resolve -n co2minimeter.local`
- Try IP address instead of hostname

### 9.4. Service won't start

- Check Python virtual environment: `ls -la venv/`
- Verify permissions: `ls -la co2minimeter.py`
- Review logs: `sudo journalctl -u co2minimeter.service -n 50`

## 10. License

This project uses the MIT license. The Waveshare e-Paper library got its own license, see `e-Paper/` directory. The font DejaVu Sans Mono use its own license, see `font/` directory.

## 11. 2DO
Things that has to be added or fixed:
1. mDNS is not reliable
2. webpage shows zero for temperature and humidity after restart
3. button for selfcalibration
4. plotting of historical values