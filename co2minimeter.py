#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CO2 Minimeter - Indoor Air Quality Monitor

A multi-threaded application for monitoring CO2, temperature, and humidity
using a Sensirion SCD30 sensor, displaying data on a Waveshare e-ink screen,
and serving a web interface with interactive historical data visualization.

Key Features:
    - Real-time CO2/temperature/humidity monitoring (60-second intervals)
    - E-ink display with 12-hour trend graph (updated every 15 minutes)
    - Web interface with auto-refresh and interactive plots
    - Historical data CSV logging with persistent storage
    - Manual sensor calibration (hardware button or web interface)
    - Multi-threaded architecture for optimal performance

Threads:
    1. CO2Sensor: Reads sensor data and handles calibration
    2. EInkDisplay: Updates e-ink display with current values and plots
    3. PlotGenerator: Creates SVG and PNG plots every 15 minutes
    4. WebServer: Serves web interface on port 8080
    5. CalibrationButtonMonitor: Monitors GPIO button for calibration trigger

Author: Developed by Claude Sonnet 4.5 AI
License: MIT
"""

# ============================================================================
# Configuration Constants
# ============================================================================
CO2_MEASUREMENT_INTERVAL = 60  # Measurement interval in seconds
SENSOR_WARMUP_READINGS = 2  # Number of initial sensor readings to skip
PLOT_UPDATE_INTERVAL = 900  # Plot update interval in seconds (15 minutes)
WEB_SERVER_PORT = 8080
HOURS_TO_KEEP = 12  # Keep last 12 hours of measurements
CALIBRATION_BUTTON_PIN = 21  # GPIO 21 (physical pin 40)
CALIBRATION_REFERENCE_PPM = 427  # Reference CO2 level for forced calibration
DISPLAY_UPSIDE_DOWN = True  # Set to True to rotate display 180 degrees
EINK_CO2_MAX_PPM = 2000  # Maximum CO2 value for e-ink display plot axis

# ============================================================================
# Imports
# ============================================================================
import sys
import os
import time
import random
import threading
import json
import csv
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
from queue import Queue
from PIL import Image, ImageDraw, ImageFont

# Try to import GPIO library for calibration button
HAS_GPIO = False
try:
    import gpiozero
    HAS_GPIO = True
    print("GPIO library loaded successfully")
except Exception as e:
    print(f"Warning: Could not load GPIO library: {e}")

# Try to import SCD30 sensor library
HAS_SCD30_SENSOR = False
try:
    from sensirion_i2c_driver import LinuxI2cTransceiver, I2cConnection, CrcCalculator
    from sensirion_driver_adapters.i2c_adapter.i2c_channel import I2cChannel
    from sensirion_i2c_scd30.device import Scd30Device
    HAS_SCD30_SENSOR = True
    print("SCD30 sensor library loaded successfully")
except Exception as e:
    print(f"Warning: Could not load SCD30 sensor library: {e}")
    print("Will use simulated CO2 readings")

# Import e-Paper display library with error handling
picdir = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "e-Paper",
    "RaspberryPi_JetsonNano",
    "python",
    "pic",
)
libdir = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "e-Paper",
    "RaspberryPi_JetsonNano",
    "python",
    "lib",
)

# Try to import the e-ink display library, but continue without it if not available
HAS_EINK_DISPLAY = False
if os.path.exists(libdir) and os.path.exists(picdir):
    try:
        sys.path.append(libdir)
        from waveshare_epd import epd2in13_V4

        HAS_EINK_DISPLAY = True
        print("E-ink display library loaded successfully")
    except Exception as e:
        print(f"Warning: Could not initialize e-ink display: {e}")
        print("Running in simulation mode (display updates will be printed to console)")
else:
    print(
        "E-ink display library not found. Running in simulation mode (display updates will be printed to console)"
    )

# ============================================================================
# Global Variables
# ============================================================================
data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data")
font_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "fonts")
measurements = []
measurement_lock = threading.Lock()
shutdown_event = threading.Event()
calibration_event = threading.Event()
calibration_in_progress = False
calibration_lock = threading.Lock()
sensor_warming_up = True  # True during warmup period (startup or after calibration)

# ============================================================================
# Functions
# ============================================================================

def save_to_csv(timestamp_str, co2_value, temperature, humidity):
    """Save measurement to daily CSV file.
    
    Creates a new CSV file for each day with format: data_YYYY-MM-DD.csv
    Writes header row if file is newly created.
    
    Args:
        timestamp_str: Timestamp in format "YYYY-MM-DD HH:MM:SS"
        co2_value: CO2 concentration in ppm (parts per million)
        temperature: Temperature in degrees Celsius
        humidity: Relative humidity as percentage
    
    Note:
        Creates data directory if it doesn't exist.
        Appends to existing file or creates new one for each day.
    """
    try:
        # Create data directory if it doesn't exist
        os.makedirs(data_dir, exist_ok=True)
        
        # Parse timestamp to get date
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        date_str = dt.strftime("%Y-%m-%d")
        
        # Create filename: data_YYYY-MM-DD.csv
        filename = f"data_{date_str}.csv"
        filepath = os.path.join(data_dir, filename)
        
        # Check if file exists to determine if we need to write header
        file_exists = os.path.isfile(filepath)
        
        # Write to CSV file
        with open(filepath, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                writer.writerow(['YYYY-MM-DD', 'CO2, relative (x10^6)', 'Temperature (°C)', 'Humidity (%)'])
            writer.writerow([timestamp_str, co2_value, f"{temperature:.1f}", f"{humidity:.1f}"])
            
    except Exception as e:
        print(f"Error saving to CSV: {e}")


def load_recent_measurements():
    """Load measurements from the last 12 hours from CSV files.
    
    Reads CSV files from today and yesterday to gather all measurements
    within the configured HOURS_TO_KEEP window.
    
    Returns:
        list: List of tuples (timestamp_str, co2_value, temperature, humidity)
              sorted by timestamp, containing only measurements from last 12 hours.
    
    Note:
        Used during application startup to restore recent history.
    """
    loaded_measurements = []
    
    try:
        if not os.path.exists(data_dir):
            print("No previous data found.")
            return loaded_measurements
        
        # Calculate cutoff time (12 hours ago)
        cutoff_time = datetime.now() - timedelta(hours=HOURS_TO_KEEP)
        
        # Get list of CSV files to check (today and yesterday)
        files_to_check = []
        for days_back in range(2):  # Check today and yesterday
            date = datetime.now() - timedelta(days=days_back)
            filename = f"data_{date.strftime('%Y-%m-%d')}.csv"
            filepath = os.path.join(data_dir, filename)
            if os.path.isfile(filepath):
                files_to_check.append(filepath)
        
        # Read measurements from files
        for filepath in files_to_check:
            try:
                with open(filepath, 'r') as csvfile:
                    reader = csv.reader(csvfile)
                    next(reader, None)  # Skip header
                    for row in reader:
                        if len(row) >= 4:
                            timestamp_str = row[0]
                            co2_value = int(row[1])
                            temperature = float(row[2])
                            humidity = float(row[3])
                            
                            # Parse timestamp and check if it's within last 12 hours
                            measurement_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                            if measurement_time >= cutoff_time:
                                loaded_measurements.append((timestamp_str, co2_value, temperature, humidity))
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
        
        # Sort by timestamp
        loaded_measurements.sort(key=lambda x: x[0])
        
        print(f"Loaded {len(loaded_measurements)} measurements from the last {HOURS_TO_KEEP} hours.")
        
    except Exception as e:
        print(f"Error loading recent measurements: {e}")
    
    return loaded_measurements


def cleanup_old_measurements():
    """Remove measurements older than HOURS_TO_KEEP hours from memory.
    
    Filters the global measurements array to keep only recent data within
    the configured rolling window. CSV files are not affected.
    
    Note:
        Called after each new measurement to maintain memory footprint.
        Thread-safe: Uses measurement_lock.
    """
    global measurements
    
    try:
        cutoff_time = datetime.now() - timedelta(hours=HOURS_TO_KEEP)
        
        with measurement_lock:
            # Filter out measurements older than cutoff time
            measurements = [
                (timestamp_str, value, temp, hum)
                for timestamp_str, value, temp, hum in measurements
                if datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S") >= cutoff_time
            ]
    except Exception as e:
        print(f"Error cleaning up old measurements: {e}")


def generate_plot():
    """Generate SVG and PNG plots of last 12 hours of measurements.
    
    Creates two plot files:
        - data_latest_plot.svg (1000x300px): Full-featured plot with grid,
          all three parameters (CO2, temperature, humidity), auto-scaled axes
        - data_latest_plot.png (245x70px): Simplified plot for e-ink display,
          CO2 only, black and white, fixed axis 400-EINK_CO2_MAX_PPM
    
    Features:
        - Gap detection: Inserts NaN values for gaps > 5 minutes
        - Auto-scaled CO2 axis (SVG): 400 ppm minimum, rounded to nearest 500
        - Fixed CO2 axis (PNG): 400 to EINK_CO2_MAX_PPM for e-ink display
        - Three separate y-axes for different parameters (SVG only)
        - Hourly time markers on x-axis
    
    Note:
        Output files saved to data/ directory.
        Thread-safe: Makes copy of measurements under lock.
    """
    try:
        with measurement_lock:
            data = measurements.copy()
        
        if not data:
            print("No data to plot")
            return
        
        # Parse timestamps and values
        timestamps = [datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts, _, _, _ in data]
        values = [val for _, val, _, _ in data]
        temperatures = [temp for _, _, temp, _ in data]
        humidities = [hum for _, _, _, hum in data]
        
        # Insert NaN values where gaps are larger than 5 minutes
        timestamps_gapped = []
        values_gapped = []
        temperatures_gapped = []
        humidities_gapped = []
        max_gap = timedelta(minutes=5)
        
        for i in range(len(timestamps)):
            timestamps_gapped.append(timestamps[i])
            values_gapped.append(values[i])
            temperatures_gapped.append(temperatures[i])
            humidities_gapped.append(humidities[i])
            
            # Check if there's a gap to the next point
            if i < len(timestamps) - 1:
                time_diff = timestamps[i + 1] - timestamps[i]
                if time_diff > max_gap:
                    # Insert NaN to create a gap in the plot
                    timestamps_gapped.append(timestamps[i] + time_diff / 2)
                    values_gapped.append(float('nan'))
                    temperatures_gapped.append(float('nan'))
                    humidities_gapped.append(float('nan'))
        
        # Set x-axis time range
        now = datetime.now()
        start_time = now - timedelta(hours=HOURS_TO_KEEP)
        
        # Generate SVG with grid and time labels
        fig_svg, ax_svg = plt.subplots(figsize=(10, 3), dpi=100)
        
        # Plot CO2 on primary y-axis (left)
        ax_svg.plot(timestamps_gapped, values_gapped, color='#2E7D32', linewidth=2, label='CO2')
        
        # Auto-scale CO2 y-axis from 400 to maximum value in data
        max_co2 = max(values) if values else 2000
        # Round up to nearest 500
        max_co2_rounded = ((max_co2 + 499) // 500) * 500
        max_co2_rounded = max(max_co2_rounded, 1000)  # Minimum 1000
        ax_svg.set_ylim(400, max_co2_rounded)
        
        ax_svg.set_ylabel('CO2 (x10⁻⁶)', fontsize=10, color='#2E7D32')
        ax_svg.tick_params(axis='y', labelcolor='#2E7D32', labelsize=10)
        
        # Create second y-axis for temperature (right)
        ax_temp = ax_svg.twinx()
        ax_temp.plot(timestamps_gapped, temperatures_gapped, color='red', linewidth=2, label='Temperature')
        ax_temp.set_ylabel('Temperature (°C)', fontsize=10, color='red')
        ax_temp.tick_params(axis='y', labelcolor='red', labelsize=10)
        
        # Create third y-axis for humidity (right, offset)
        ax_hum = ax_svg.twinx()
        # Offset the third axis to the right
        ax_hum.spines['right'].set_position(('outward', 60))
        ax_hum.plot(timestamps_gapped, humidities_gapped, color='blue', linewidth=2, label='Humidity')
        ax_hum.set_ylabel('Humidity (%)', fontsize=10, color='blue')
        ax_hum.tick_params(axis='y', labelcolor='blue', labelsize=10)
        
        # Set x-axis properties
        ax_svg.set_xlim(start_time, now)
        ax_svg.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        ax_svg.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax_svg.tick_params(axis='x', labelsize=8, rotation=45)
        ax_svg.grid(True, alpha=0.3, linewidth=0.5)
        
        plt.tight_layout()
        
        svg_path = os.path.join(data_dir, "data_latest_plot.svg")
        plt.savefig(svg_path, format='svg', dpi=100)
        print(f"Plot saved as SVG: {svg_path}")
        plt.close(fig_svg)
        
        # Generate PNG without grid (245x70 pixels for e-ink display)
        fig_png, ax_png = plt.subplots(figsize=(2.45, 0.7), dpi=100)
        ax_png.plot(timestamps_gapped, values_gapped, color="#000000", linewidth=1)
        ax_png.set_ylim(400, EINK_CO2_MAX_PPM)
        # Calculate yticks: round to 500, 1000, 2000, 5000, or 10000 to show max 4 ticks
        range_span = EINK_CO2_MAX_PPM - 400
        # Determine appropriate tick interval (500, 1000, 2000, 5000, 10000, 20000)
        if range_span <= 2000:  # e.g., max=2000: use 500 interval
            tick_interval = 500
        elif range_span <= 4000:  # e.g., max=4000: use 1000 interval
            tick_interval = 1000
        elif range_span <= 8000:  # e.g., max=8000: use 2000 interval
            tick_interval = 2000
        elif range_span <= 20000:  # e.g., max=20000: use 5000 interval
            tick_interval = 5000
        elif range_span <= 40000:  # e.g., max=40000: use 10000 interval
            tick_interval = 10000
        else:  # e.g., max=50000: use 20000 interval
            tick_interval = 20000
        # Generate ticks starting from first multiple of tick_interval above 400
        first_tick = ((400 // tick_interval) + 1) * tick_interval
        yticks = list(range(first_tick, EINK_CO2_MAX_PPM, tick_interval))
        # Format labels: "0.5 k", "1 k", "6 k", "10 k", etc.
        yticklabels = []
        for tick in yticks:
            if tick >= 1000:
                tick_k = tick / 1000
                if tick_k == int(tick_k):
                    yticklabels.append(f'{int(tick_k)} k')
                else:
                    yticklabels.append(f'{tick_k:.1f} k')
            else:
                yticklabels.append(str(tick))
        ax_png.set_yticks(yticks)
        ax_png.set_yticklabels(yticklabels)
        ax_png.tick_params(axis='y', labelsize=6)
        ax_png.set_xlim(start_time, now)
        ax_png.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        ax_png.xaxis.set_major_formatter(mdates.DateFormatter(''))
        ax_png.tick_params(axis='x', length=3, width=0.5)
        ax_png.grid(True, alpha=1, axis='x')
        ax_png.set_xlabel('')
        ax_png.set_ylabel('')
        plt.subplots_adjust(left=0.12, right=0.98, top=0.98, bottom=0.08)
        
        png_path = os.path.join(data_dir, "data_latest_plot.png")
        plt.savefig(png_path, format='png', dpi=100)
        print(f"Plot saved as PNG: {png_path}")
        plt.close(fig_png)
        
    except Exception as e:
        print(f"Error generating plot: {e}")


def load_historical_data(start_time, end_time):
    """Load measurements from CSV files within specified time range.
    
    Reads all CSV files covering the date range and filters measurements
    by timestamp.
    
    Args:
        start_time: datetime object for range start (inclusive)
        end_time: datetime object for range end (inclusive)
    
    Returns:
        list: List of tuples (timestamp_str, co2_value, temperature, humidity)
              sorted by timestamp, containing only measurements in range.
    
    Note:
        Used by history plot generation in web interface.
    """
    historical_data = []
    
    try:
        if not os.path.exists(data_dir):
            return historical_data
        
        # Get all CSV files in the date range
        current_date = start_time.date()
        end_date = end_time.date()
        
        while current_date <= end_date:
            filename = f"data_{current_date.strftime('%Y-%m-%d')}.csv"
            filepath = os.path.join(data_dir, filename)
            
            if os.path.isfile(filepath):
                try:
                    with open(filepath, 'r') as csvfile:
                        reader = csv.reader(csvfile)
                        next(reader, None)  # Skip header
                        for row in reader:
                            if len(row) >= 4:
                                timestamp_str = row[0]
                                co2_value = int(row[1])
                                temperature = float(row[2])
                                humidity = float(row[3])
                                
                                measurement_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                                if start_time <= measurement_time <= end_time:
                                    historical_data.append((timestamp_str, co2_value, temperature, humidity))
                except Exception as e:
                    print(f"Error reading {filepath}: {e}")
            
            current_date += timedelta(days=1)
        
        historical_data.sort(key=lambda x: x[0])
        
    except Exception as e:
        print(f"Error loading historical data: {e}")
    
    return historical_data


def get_data_range():
    """Get the available data range from CSV files.
    
    Scans data directory for CSV files and extracts first and last
    timestamps from the files.
    
    Returns:
        tuple: (first_timestamp, last_timestamp) as datetime objects,
               or (None, None) if no data files exist.
    
    Note:
        Used by history page to display available data range.
    """
    try:
        if not os.path.exists(data_dir):
            return None, None
        
        csv_files = sorted([f for f in os.listdir(data_dir) if f.startswith('data_') and f.endswith('.csv')])
        
        if not csv_files:
            return None, None
        
        # Get first and last dates from filenames
        first_file = csv_files[0]
        last_file = csv_files[-1]
        
        # Extract dates from filenames (format: data_YYYY-MM-DD.csv)
        first_date_str = first_file.replace('data_', '').replace('.csv', '')
        last_date_str = last_file.replace('data_', '').replace('.csv', '')
        
        # Get actual first and last timestamps from the files
        first_timestamp = None
        last_timestamp = None
        
        # Read first timestamp from first file
        try:
            with open(os.path.join(data_dir, first_file), 'r') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                first_row = next(reader, None)
                if first_row:
                    first_timestamp = datetime.strptime(first_row[0], "%Y-%m-%d %H:%M:%S")
        except:
            pass
        
        # Read last timestamp from last file
        try:
            with open(os.path.join(data_dir, last_file), 'r') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                rows = list(reader)
                if rows:
                    last_timestamp = datetime.strptime(rows[-1][0], "%Y-%m-%d %H:%M:%S")
        except:
            pass
        
        return first_timestamp, last_timestamp
        
    except Exception as e:
        print(f"Error getting data range: {e}")
        return None, None


def generate_history_plot(start_time, end_time):
    """Generate SVG plot for historical data within specified time range.
    
    Creates history_plot.svg with adaptive time formatting based on range:
        - Hourly labels for < 24 hours
        - 6-hourly labels for < 7 days
        - Daily labels for longer periods
    
    Args:
        start_time: datetime object for range start
        end_time: datetime object for range end
    
    Features:
        - Gap detection for measurement interruptions
        - Auto-scaled axes for all three parameters
        - Three separate y-axes with color coding
    
    Note:
        Output file: data/history_plot.svg
    """
    try:
        data = load_historical_data(start_time, end_time)
        
        if not data:
            print("No historical data to plot")
            return
        
        # Parse timestamps and values
        timestamps = [datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts, _, _, _ in data]
        values = [val for _, val, _, _ in data]
        temperatures = [temp for _, _, temp, _ in data]
        humidities = [hum for _, _, _, hum in data]
        
        # Insert NaN values where gaps are larger than 5 minutes
        timestamps_gapped = []
        values_gapped = []
        temperatures_gapped = []
        humidities_gapped = []
        max_gap = timedelta(minutes=5)
        
        for i in range(len(timestamps)):
            timestamps_gapped.append(timestamps[i])
            values_gapped.append(values[i])
            temperatures_gapped.append(temperatures[i])
            humidities_gapped.append(humidities[i])
            
            # Check if there's a gap to the next point
            if i < len(timestamps) - 1:
                time_diff = timestamps[i + 1] - timestamps[i]
                if time_diff > max_gap:
                    # Insert NaN to create a gap in the plot
                    timestamps_gapped.append(timestamps[i] + time_diff / 2)
                    values_gapped.append(float('nan'))
                    temperatures_gapped.append(float('nan'))
                    humidities_gapped.append(float('nan'))
        
        # Generate SVG with grid and time labels
        fig_svg, ax_svg = plt.subplots(figsize=(10, 3), dpi=100)
        
        # Plot CO2 on primary y-axis (left)
        ax_svg.plot(timestamps_gapped, values_gapped, color='#2E7D32', linewidth=2, label='CO2')
        
        # Auto-scale CO2 y-axis from 400 to maximum value in data
        max_co2 = max(values) if values else 2000
        # Round up to nearest 500
        max_co2_rounded = ((max_co2 + 499) // 500) * 500
        max_co2_rounded = max(max_co2_rounded, 1000)  # Minimum 1000
        ax_svg.set_ylim(400, max_co2_rounded)
        
        ax_svg.set_ylabel('CO2 (x10⁻⁶)', fontsize=10, color='#2E7D32')
        ax_svg.tick_params(axis='y', labelcolor='#2E7D32', labelsize=10)
        
        # Create second y-axis for temperature (right)
        ax_temp = ax_svg.twinx()
        ax_temp.plot(timestamps_gapped, temperatures_gapped, color='red', linewidth=2, label='Temperature')
        ax_temp.set_ylabel('Temperature (°C)', fontsize=10, color='red')
        ax_temp.tick_params(axis='y', labelcolor='red', labelsize=10)
        
        # Create third y-axis for humidity (right, offset)
        ax_hum = ax_svg.twinx()
        ax_hum.spines['right'].set_position(('outward', 60))
        ax_hum.plot(timestamps_gapped, humidities_gapped, color='blue', linewidth=2, label='Humidity')
        ax_hum.set_ylabel('Humidity (%)', fontsize=10, color='blue')
        ax_hum.tick_params(axis='y', labelcolor='blue', labelsize=10)
        
        # Set x-axis properties
        ax_svg.set_xlim(start_time, end_time)
        
        # Adjust time formatting based on range
        time_range = end_time - start_time
        if time_range <= timedelta(hours=24):
            ax_svg.xaxis.set_major_locator(mdates.HourLocator(interval=1))
            ax_svg.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
        elif time_range <= timedelta(days=7):
            ax_svg.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            ax_svg.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
        else:
            ax_svg.xaxis.set_major_locator(mdates.DayLocator())
            ax_svg.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        
        ax_svg.tick_params(axis='x', labelsize=8, rotation=45)
        ax_svg.grid(True, alpha=0.3, linewidth=0.5)
        
        plt.tight_layout()
        
        svg_path = os.path.join(data_dir, "history_plot.svg")
        plt.savefig(svg_path, format='svg', dpi=100)
        print(f"History plot saved: {svg_path}")
        plt.close(fig_svg)
        
    except Exception as e:
        print(f"Error generating history plot: {e}")


class CalibrationButtonMonitor(threading.Thread):
    """Thread to monitor physical calibration button on GPIO 21.
    
    Monitors a hardware button connected to GPIO 21 (physical pin 40) and
    triggers sensor calibration when held for 3 seconds.
    
    Attributes:
        button: gpiozero.Button instance or None if GPIO not available
        display_thread: Reference to EInkDisplay thread for immediate feedback
    
    Button Wiring:
        - GPIO 21 (pin 40) with internal pull-up resistor enabled
        - Button connects GPIO 21 to GND when pressed
    
    Behavior:
        - Detects 3-second press and sets calibration_event
        - Notifies display thread to show "Recalibration..." message
        - Waits for button release before returning to monitoring
    
    Note:
        Disabled if GPIO library not available (falls back to web-only calibration).
    """
    
    def __init__(self, display_thread, daemon=None):
        """Initialize calibration button monitor.
        
        Args:
            display_thread: Reference to EInkDisplay thread for notifications
            daemon: Whether to run as daemon thread (default None)
        """
        super().__init__(daemon=daemon)
        self.button = None
        self.display_thread = display_thread
        
    def run(self):
        """Main loop monitoring button presses for calibration trigger.
        
        Initializes button with pull-up resistor and monitors for 3-second press.
        When detected, sets calibration_in_progress flag and calibration_event,
        then notifies display thread.
        
        Button Configuration:
            - GPIO pin with pull-up resistor (button to GND)
            - 0.1 second debounce time
            - Requires 3.0 second continuous press
        
        Note:
            Returns immediately if GPIO library not available.
        """
        if not HAS_GPIO:
            print("GPIO library not available - calibration button disabled")
            return
        
        try:
            # Initialize button with pull-up resistor (button connects to GND)
            self.button = gpiozero.Button(CALIBRATION_BUTTON_PIN, pull_up=True, bounce_time=0.1)
            print(f"Calibration button initialized on GPIO {CALIBRATION_BUTTON_PIN} (pin 40)")
            
            while not shutdown_event.is_set():
                # Wait for button press
                if self.button.is_pressed:
                    press_start = time.time()
                    
                    # Wait to see if it's held for 3 seconds
                    while self.button.is_pressed and (time.time() - press_start) < 3.0:
                        time.sleep(0.1)
                    
                    # If still pressed after 3 seconds, trigger calibration
                    if self.button.is_pressed and (time.time() - press_start) >= 3.0:
                        print("Calibration button: 3-second press detected!")
                        with calibration_lock:
                            calibration_in_progress = True
                        calibration_event.set()
                        
                        # Notify display thread immediately
                        if hasattr(self, 'display_thread') and hasattr(self.display_thread, 'display_condition'):
                            with self.display_thread.display_condition:
                                self.display_thread.display_condition.notify()
                        
                        # Wait for button release
                        while self.button.is_pressed:
                            time.sleep(0.1)
                
                time.sleep(0.1)
                
        except Exception as e:
            print(f"Error in calibration button monitor: {e}")


class PlotGenerator(threading.Thread):
    """Thread to generate plots every 15 minutes.
    
    Runs in background to periodically create SVG and PNG plots from
    accumulated measurements. Updates display thread when new plots are ready.
    
    Attributes:
        display_thread: Reference to EInkDisplay thread for plot update notifications
    
    Timing:
        - Initial delay: 5 seconds after startup
        - Interval: 15 minutes (PLOT_UPDATE_INTERVAL)
        - First plot appears ~15 minutes after application start
    
    Behavior:
        - Calls generate_plot() to create both SVG and PNG files
        - Notifies display thread via display_condition to refresh plot
        - Continues until shutdown_event is set
    
    Note:
        Users should wait ~15 minutes after startup for first plot to appear.
    """
    
    def __init__(self, display_thread, daemon=None):
        super().__init__(daemon=daemon)
        self.display_thread = display_thread
    
    def run(self):
        """Main loop generating plots at regular intervals.
        
        Waits 5 seconds after startup, generates first plot, then continues
        generating plots every PLOT_UPDATE_INTERVAL (15 minutes). Notifies
        display thread after each plot generation.
        
        Note:
            First plot appears ~15 minutes after application start (5 seconds
            initial delay + 15 minute interval).
        """
        # Generate initial plot
        time.sleep(5)  # Wait a bit for initial measurements
        generate_plot()
        
        # Notify display thread of new plot
        if hasattr(self.display_thread, "display_condition"):
            with self.display_thread.display_condition:
                self.display_thread.new_plot = True
                self.display_thread.display_condition.notify()
        
        while not shutdown_event.is_set():
            # Wait for configured plot update interval
            if shutdown_event.wait(timeout=PLOT_UPDATE_INTERVAL):
                break
            generate_plot()
            
            # Notify display thread of new plot
            if hasattr(self.display_thread, "display_condition"):
                with self.display_thread.display_condition:
                    self.display_thread.new_plot = True
                    self.display_thread.display_condition.notify()


class CO2Sensor(threading.Thread):
    """Thread for reading CO2, temperature, and humidity from SCD30 sensor.
    
    Handles sensor initialization, continuous measurements at 60-second intervals,
    calibration events, and data storage to CSV files. Falls back to simulated
    data if hardware is unavailable.
    
    Attributes:
        display_thread: Reference to EInkDisplay thread for notifications
        sensor: Scd30Device instance or None if hardware unavailable
        use_hardware: Boolean flag indicating real sensor vs simulation
        readings_to_skip: Number of initial readings to discard after
                         startup or calibration (warmup period)
    
    Warmup Behavior:
        - Skips first SENSOR_WARMUP_READINGS (2) readings after startup
        - Skips 2 readings after calibration completion
        - Display shows "---" during warmup instead of stale values
    
    Calibration:
        - Triggered by calibration_event (from button or web interface)
        - Stops measurements, stabilizes for 2 minutes, forces calibration
          to CALIBRATION_REFERENCE_PPM (427 ppm), resumes measurements
        - Requires device to be in fresh outdoor air
    
    Note:
        Simulation mode generates random values if SCD30 sensor unavailable.
    """

    def __init__(self, display_thread, daemon=None):
        """Initialize CO2 sensor thread.
        
        Args:
            display_thread: Reference to EInkDisplay thread for notifications
            daemon: Whether to run as daemon thread (default None)
        """
        super().__init__(daemon=daemon)
        self.display_thread = display_thread
        self.sensor = None
        self.use_hardware = False
        self.readings_to_skip = 0  # Number of initial readings to skip
        
    def init_sensor(self):
        """Initialize SCD30 sensor hardware.
        
        Connects to sensor via I2C at address 0x61, reads firmware version,
        disables automatic self-calibration, and starts periodic measurements.
        
        Returns:
            bool: True if hardware initialized successfully, False otherwise
        
        Side Effects:
            - Sets self.use_hardware flag
            - Sets self.readings_to_skip for warmup period
            - Configures measurement interval to CO2_MEASUREMENT_INTERVAL
        
        Note:
            Falls back to simulation mode on failure.
        """
        if not HAS_SCD30_SENSOR:
            return False
            
        try:
            i2c_transceiver = LinuxI2cTransceiver('/dev/i2c-1')
            channel = I2cChannel(
                I2cConnection(i2c_transceiver),
                slave_address=0x61,
                crc=CrcCalculator(8, 0x31, 0xff, 0x0)
            )
            self.sensor = Scd30Device(channel)
            
            # Initialize sensor
            try:
                self.sensor.stop_periodic_measurement()
                self.sensor.soft_reset()
                time.sleep(2.0)
            except:
                pass
                
            # Read firmware version to verify connection
            major, minor = self.sensor.read_firmware_version()
            print(f"SCD30 sensor connected - Firmware: {major}.{minor}")
            
            # Disable automatic self-calibration
            self.sensor.activate_auto_calibration(1)
            print("Automatic self-calibration disabled")
            
            # Check and set measurement interval if needed
            current_interval = self.sensor.get_measurement_interval()
            print(f"Current measurement interval: {current_interval}s")
            if current_interval != CO2_MEASUREMENT_INTERVAL:
                print(f"Setting measurement interval to {CO2_MEASUREMENT_INTERVAL}s")
                self.sensor.set_measurement_interval(CO2_MEASUREMENT_INTERVAL)
                time.sleep(0.1)  # Small delay after setting
            
            # Start periodic measurements
            self.sensor.start_periodic_measurement(0)
            self.use_hardware = True
            self.readings_to_skip = SENSOR_WARMUP_READINGS
            print(f"Will skip first {SENSOR_WARMUP_READINGS} sensor readings (warm-up period)")
            return True
            
        except Exception as e:
            print(f"Failed to initialize SCD30 sensor: {e}")
            print("Falling back to simulated CO2 readings")
            return False
    
    def perform_calibration(self):
        """Perform forced recalibration of the CO2 sensor.
        
        Exposes the sensor to fresh outdoor air and calibrates to the reference
        CO2 level (427 ppm for year 2025). Pauses normal measurements during
        calibration process.
        
        Process:
            1. Stop current measurements
            2. Set 2-second measurement interval for calibration
            3. Wait 2 minutes for sensor stabilization
            4. Execute forced calibration to CALIBRATION_REFERENCE_PPM
            5. Restore normal measurement interval (60 seconds)
            6. Resume measurements with 2-reading warmup period
        
        Requirements:
            - Device must be placed in fresh outdoor air
            - Process takes approximately 2 minutes
            - User should not move device during calibration
        
        Side Effects:
            - Sets sensor_warming_up flag to True
            - Clears calibration_in_progress flag when complete
            - Notifies display thread to update and force full redraw
        
        Note:
            Thread-safe: Uses calibration_lock to update calibration status.
        """
        global calibration_in_progress, sensor_warming_up
        
        print("Starting CO2 sensor calibration...")
        
        try:
            if not self.use_hardware or not self.sensor:
                print("Cannot calibrate: sensor not available")
                return
            
            # Stop current measurements
            self.sensor.stop_periodic_measurement()
            time.sleep(0.1)
            
            # Set measurement interval to 2 seconds for calibration
            print("Setting measurement interval to 2 seconds for calibration")
            self.sensor.set_measurement_interval(2)
            time.sleep(0.1)
            
            # Start measurements
            self.sensor.start_periodic_measurement(0)
            
            # Wait 2 minutes for sensor to stabilize
            print("Waiting 2 minutes for sensor stabilization...")
            for i in range(120):
                if shutdown_event.is_set():
                    return
                time.sleep(1)
                if (i + 1) % 30 == 0:
                    print(f"  {i + 1}/120 seconds elapsed...")
            
            # Perform forced calibration
            print(f"Performing forced calibration to {CALIBRATION_REFERENCE_PPM} ppm...")
            self.sensor.force_recalibration(CALIBRATION_REFERENCE_PPM)
            time.sleep(0.5)
            
            print("Calibration complete!")
            
            # Restore normal measurement interval
            self.sensor.stop_periodic_measurement()
            time.sleep(0.1)
            self.sensor.set_measurement_interval(CO2_MEASUREMENT_INTERVAL)
            time.sleep(0.1)
            self.sensor.start_periodic_measurement(0)
            
            # Skip next few readings
            self.readings_to_skip = SENSOR_WARMUP_READINGS
            sensor_warming_up = True
            print(f"Will skip next {SENSOR_WARMUP_READINGS} readings after calibration")
            
        except Exception as e:
            print(f"Error during calibration: {e}")
        finally:
            with calibration_lock:
                calibration_in_progress = False
            
            # Notify display thread to update immediately and force full redraw
            if hasattr(self.display_thread, 'display_condition'):
                with self.display_thread.display_condition:
                    self.display_thread.force_redraw = True
                    self.display_thread.display_condition.notify()
            
            print("Resumed normal operation")

    def read_co2(self):
        """Main loop reading CO2 sensor at regular intervals.
        
        Initializes sensor hardware (or falls back to simulation), then
        continuously reads measurements every CO2_MEASUREMENT_INTERVAL seconds.
        Handles calibration events, warmup periods, data storage, and
        notifications.
        
        Process:
            1. Initialize sensor (hardware or simulation mode)
            2. Check for calibration event (blocking)
            3. Read measurement (real sensor or simulated data)
            4. Skip reading if in warmup period
            5. Append to measurements array and CSV file
            6. Clean up old measurements from memory
            7. Notify display thread of new data
        
        Warmup:
            - Skips first SENSOR_WARMUP_READINGS after startup/calibration
            - Sets sensor_warming_up flag until first valid reading
        
        Simulation Mode:
            - Random CO2: 400-2000 ppm
            - Random temperature: 18-26°C
            - Random humidity: 30-70%
            - Variable delay: 50-150% of interval
        
        Note:
            Thread-safe: Uses measurement_lock and calibration_lock.
        """
        global sensor_warming_up
        
        # Try to initialize hardware sensor
        self.init_sensor()
        
        while not shutdown_event.is_set():
            # Check if calibration is requested
            if calibration_event.is_set():
                self.perform_calibration()
                calibration_event.clear()
                continue
                
            try:
                if self.use_hardware:
                    # Read from real sensor - wait for sensor's measurement interval
                    time.sleep(CO2_MEASUREMENT_INTERVAL)
                    co2_concentration, temperature, humidity = self.sensor.blocking_read_measurement_data()
                    co2_value = int(co2_concentration)
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    # Skip first readings if needed
                    if self.readings_to_skip > 0:
                        self.readings_to_skip -= 1
                        print(f"Skipping initial reading {SENSOR_WARMUP_READINGS - self.readings_to_skip}/{SENSOR_WARMUP_READINGS}: {co2_value} ppm, {temperature:.1f}°C, {humidity:.1f}%")
                        continue
                    
                    # Clear warmup flag after first valid reading
                    if sensor_warming_up:
                        sensor_warming_up = False
                        print("Sensor warmup complete - displaying measurements")
                else:
                    # Simulate CO2 reading with randomized interval (50% to 150% of base interval)
                    co2_value = random.randint(400, 2000)
                    temperature = random.uniform(18.0, 26.0)
                    humidity = random.uniform(30.0, 70.0)
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    # Random delay: 50% to 150% of CO2_MEASUREMENT_INTERVAL
                    random_interval = random.uniform(
                        CO2_MEASUREMENT_INTERVAL * 0.05,
                        CO2_MEASUREMENT_INTERVAL * 0.15
                    )
                    time.sleep(random_interval)
                
                with measurement_lock:
                    measurements.append((timestamp, co2_value, temperature, humidity))
                
                # Clean up old measurements (older than HOURS_TO_KEEP)
                cleanup_old_measurements()

                # Save to CSV file
                save_to_csv(timestamp, co2_value, temperature, humidity)
                
                print(f"CO2: {co2_value}x10^-6, Temperature: {temperature:.1f}°C, Humidity: {humidity:.1f}% at {timestamp}")

                # Notify display thread of new measurement
                if hasattr(self.display_thread, "display_condition"):
                    with self.display_thread.display_condition:
                        self.display_thread.new_measurement = True
                        self.display_thread.display_condition.notify()
                        
            except Exception as e:
                print(f"Error reading CO2: {e}")
                if self.use_hardware:
                    print("Sensor error - switching to simulation mode")
                    self.use_hardware = False
                time.sleep(5)  # Wait before retry

    def run(self):
        """Thread entry point - delegates to read_co2 method."""
        self.read_co2()


class EInkDisplay(threading.Thread):
    """Thread to update the e-ink display with current measurements and plots.
    
    Manages a Waveshare 2.13" e-Paper V4 display (250x122 pixels) showing:
        - 12-hour CO2 trend plot (245x70 pixels) at top
        - Current CO2 value in large digits
        - Current time and date
        - Calibration status message when applicable
    
    Attributes:
        epd: Waveshare e-Paper display driver instance
        font12, font15, font24, font36: TrueType font objects
        base_image: PIL Image object for partial update base
        display_condition: Threading condition for synchronized updates
        new_measurement: Flag indicating new sensor reading available
        new_plot: Flag indicating new plot image available
        force_redraw: Flag to force complete display refresh
    
    Update Triggers:
        - Every minute (time/date change)
        - New CO2 measurement received
        - New plot generated (every 15 minutes)
        - Calibration status change
    
    Display Layout:
        - Top (0-70px): CO2 trend graph
        - Bottom left: Large CO2 value with "x10^-6 CO2" units
        - Bottom right: Time (HH:MM) and date (DD.MM.YYYY)
    
    Configuration:
        - DISPLAY_UPSIDE_DOWN: Set True to rotate display 180°
    
    Note:
        Falls back to console printing if display hardware unavailable.
        Uses partial updates for better performance and longer display life.
    """

    def __init__(self, daemon=None):
        """Initialize e-ink display thread.
        
        Args:
            daemon: Whether to run as daemon thread (default None)
        
        Attributes initialized:
            epd: Display driver instance (None until init_display)
            font12-36: TrueType font objects for different sizes
            display_condition: Threading condition for synchronized updates
            new_measurement: Flag for new sensor reading
            new_plot: Flag for new plot available
            force_redraw: Flag to force complete display refresh
        """
        super().__init__(daemon=daemon)
        self.epd = None
        self.font12 = None  # For superscript
        self.font15 = None
        self.font24 = None
        self.font36 = None
        self.last_display = None
        self.last_minute = -1
        self.last_plot_update = None
        self.display_condition = threading.Condition()
        self.new_measurement = False
        self.new_plot = False
        self.force_redraw = False

    def init_display(self):
        """Initialize the e-ink display hardware and fonts.
        
        Sets up Waveshare e-Paper display, loads TrueType fonts, creates base
        image for partial updates, and draws static elements (units text).
        
        Returns:
            bool: True if initialization successful or simulation mode active
        
        Static Elements:
            - "x10^-6" superscript notation for CO2 units
            - "CO2" label with subscript "2"
        
        Font:
            - DejaVuSansMono-Bold from fonts/ directory
            - Sizes: 12pt (superscripts), 15pt (labels), 24pt, 36pt (CO2 value)
        
        Note:
            Uses fast initialization mode for better update performance.
            Clears display to white before first use.
        """
        if not HAS_EINK_DISPLAY:
            print("Display: Running in simulation mode")
            return True

        try:
            self.epd = epd2in13_V4.EPD()
            self.epd.init_fast()  # Use fast init for better performance
            self.epd.Clear(0xFF)

            # Load fonts
            # self.font12 = ImageFont.truetype(os.path.join(picdir, "Font.ttc"), 12)
            # self.font15 = ImageFont.truetype(os.path.join(picdir, "Font.ttc"), 15)
            # self.font24 = ImageFont.truetype(os.path.join(picdir, "Font.ttc"), 24)
            # self.font36 = ImageFont.truetype(os.path.join(picdir, "Font.ttc"), 36)
            font_path = os.path.join(font_dir, "DejaVuSansMono-Bold.ttf")
            self.font12 = ImageFont.truetype(font_path, 12)
            self.font15 = ImageFont.truetype(font_path, 15)
            self.font24 = ImageFont.truetype(font_path, 24)
            self.font36 = ImageFont.truetype(font_path, 36)

            # Create base image for partial updates
            self.base_image = Image.new("1", (self.epd.height, self.epd.width), 255)
            self.draw = ImageDraw.Draw(self.base_image)

            # Display base image (rotate if needed)
            if DISPLAY_UPSIDE_DOWN:
                rotated = self.base_image.rotate(180)
                self.epd.displayPartBaseImage(self.epd.getbuffer(rotated))
            else:
                self.epd.displayPartBaseImage(self.epd.getbuffer(self.base_image))

            # Draw static elements on base image
            self.draw.rectangle([(0, 0), (self.epd.height, self.epd.width)], fill=255)

            # Draw static "10" text and superscript "-6" for units
            self.draw.text((110, 82), "x10", font=self.font15, fill=0)
            # Draw "-6" as superscript (smaller font, raised position)
            self.draw.text((134, 74), "-6", font=self.font12, fill=0)
            # Draw static "CO2" text
            self.draw.text((110, 100), "CO", font=self.font15, fill=0)
            self.draw.text((130, 105), "2", font=self.font12, fill=0)

            # # Create partial update image
            # self.partial_image = Image.new("1", (self.epd.height, self.epd.width), 255)
            # self.partial_draw = ImageDraw.Draw(self.partial_image)

            return True
        except Exception as e:
            print(f"Failed to initialize e-ink display: {e}")
            return False

    def run(self):
        """Main display update loop.
        
        Continuously updates e-ink display with current measurements, time,
        date, and plots. Handles calibration messages, warmup states, and
        efficient partial updates.
        
        Update Triggers:
            - Every minute (time change)
            - New measurement received (via display_condition)
            - New plot generated (via display_condition)
            - Calibration status change
            - Force redraw flag set
        
        Display States:
            - Calibration: Shows "Recalibration..." message
            - Warmup: Shows "---" instead of CO2 value
            - Normal: Shows CO2 value, time, date, and plot
        
        Performance:
            - Uses partial updates to minimize e-ink refresh wear
            - Only updates changed display areas
            - Waits efficiently using condition variable
        
        Note:
            Falls back to console printing if display hardware unavailable.
            Display contents preserved on shutdown (no clear).
        """
        if not self.init_display() and HAS_EINK_DISPLAY:
            return

        try:
            while not shutdown_event.is_set():
                # Check if calibration is in progress
                with calibration_lock:
                    is_calibrating = calibration_in_progress
                
                # Check if we need to force redraw after calibration
                if self.force_redraw:
                    if HAS_EINK_DISPLAY and self.epd:
                        # Redraw all static elements
                        self.draw.rectangle([(0, 70), (self.epd.height, self.epd.width)], fill=255)
                        self.draw.text((110, 82), "x10", font=self.font15, fill=0)
                        self.draw.text((134, 74), "-6", font=self.font12, fill=0)
                        self.draw.text((110, 100), "CO", font=self.font15, fill=0)
                        self.draw.text((130, 105), "2", font=self.font12, fill=0)
                    self.force_redraw = False
                    self.last_display = None  # Force update of dynamic content
                
                if is_calibrating:
                    # Show calibration message
                    if HAS_EINK_DISPLAY and self.epd:
                        # Clear the display area
                        self.draw.rectangle([(0, 70), (self.epd.height, self.epd.width)], fill=255)
                        # Draw calibration message
                        self.draw.text((10, 90), "Recalibration...", font=self.font24, fill=0)
                        if DISPLAY_UPSIDE_DOWN:
                            rotated = self.base_image.rotate(180)
                            self.epd.displayPartial(self.epd.getbuffer(rotated))
                        else:
                            self.epd.displayPartial(self.epd.getbuffer(self.base_image))
                    else:
                        print("Display: Recalibration in progress...")
                    
                    # Wait and check again
                    time.sleep(5)
                    continue
                
                current_time = datetime.now().strftime("%H:%M")
                current_date = datetime.now().strftime("%d.%m.%Y")

                # Get latest CO2 reading - show '---' if sensor is warming up
                with measurement_lock:
                    if sensor_warming_up:
                        latest_reading = "---"
                    elif not measurements:
                        latest_reading = "N/A"
                    else:
                        latest_reading = f"{measurements[-1][1]}"

                display_text = f""" Time: {current_time}, Date: {current_date}, CO2: {latest_reading} """

                # Only update if the display has changed
                if display_text != self.last_display:
                    if HAS_EINK_DISPLAY and self.epd:
                        # Check if we need to update the plot based on notification
                        if self.new_plot:
                            png_path = os.path.join(data_dir, "data_latest_plot.png")
                            if os.path.exists(png_path):
                                try:
                                    # Load the plot image
                                    plot_img = Image.open(png_path).convert('1')
                                    # Paste plot at the top of the base image (position 0, 0)
                                    self.base_image.paste(plot_img, (0, 0))
                                    self.new_plot = False
                                    print("Updated plot on e-ink display")
                                except Exception as e:
                                    print(f"Error loading plot image: {e}")
                        
                        # Clear only the dynamic areas we're about to update
                        # Clear CO2 value area (right-aligned region)
                        self.draw.rectangle(
                            [(0, 70), (110, self.epd.height)], fill=255
                        )
                        # Clear time and date area
                        self.draw.rectangle(
                            [(150, 70), (self.epd.height, self.epd.width)], fill=255
                        )

                        # Draw CO2 value right-aligned (flush right before the static "10^-6")
                        # Calculate text width to right-align
                        co2_text = str(latest_reading)
                        bbox = self.draw.textbbox((0, 0), co2_text, font=self.font36)
                        text_width = bbox[2] - bbox[0]
                        # Position it so it ends just before the static "10" at x=1
                        # Since we want it flush right, we'll position at a fixed right edge
                        x_position = 100 - text_width  # Align to right edge of CO2 area
                        self.draw.text(
                            (x_position, 80), co2_text, font=self.font36, fill=0
                        )
                        
                        # Draw time and date
                        self.draw.text(
                            (160, 80), current_time, font=self.font15, fill=0
                        )
                        self.draw.text(
                            (160, 100), current_date, font=self.font15, fill=0
                        )

                        # Update only the changed part of the display (rotate if needed)
                        if DISPLAY_UPSIDE_DOWN:
                            rotated = self.base_image.rotate(180)
                            self.epd.displayPartial(self.epd.getbuffer(rotated))
                        else:
                            self.epd.displayPartial(self.epd.getbuffer(self.base_image))
                    else:
                        # Print to console in simulation mode
                        print(display_text)

                    self.last_display = display_text

                # Wait until next minute or new measurement
                current_minute = datetime.now().minute
                if current_minute != self.last_minute or self.new_measurement:
                    if self.new_measurement:
                        self.new_measurement = False
                    self.last_minute = current_minute

                    # Calculate sleep time until next minute
                    now = datetime.now()
                    seconds_until_next_minute = (
                        60 - now.second - now.microsecond / 1_000_000.0
                    )
                    with self.display_condition:
                        self.display_condition.wait(timeout=seconds_until_next_minute)
                else:
                    # Just wait for notification of new measurement
                    with self.display_condition:
                        self.display_condition.wait()

        except Exception as e:
            print(f"Error in display thread: {e}")
        finally:
            if HAS_EINK_DISPLAY and self.epd:
                try:
                    # Don't clear the display, just put it to sleep to preserve the last shown values
                    self.epd.sleep()
                    print("Display: E-ink display put to sleep (last values preserved)")
                except Exception as e:
                    print(f"Error while putting display to sleep: {e}")


class WebServer(threading.Thread):
    """Thread to serve web interface on port 8080.
    
    Provides HTTP server with multiple endpoints for monitoring and control:
        - GET /: Main dashboard with current readings and 12-hour plot
        - GET /plot.svg: Current plot SVG image
        - GET /history: Historical data page with date range selection
        - GET /history_plot.svg: Historical plot SVG image
        - GET /calibrate: Trigger sensor calibration
    
    Attributes:
        port: HTTP port number (default 8080)
        server: HTTPServer instance
        display_thread: Reference to EInkDisplay for calibration notifications
    
    Main Page Features:
        - Auto-refresh every 10 seconds
        - Live measurement table (most recent first)
        - Interactive SVG plot with grid and auto-scaled axes
        - Calibration button with progress banner
        - "History" button to access historical data
    
    History Page Features:
        - Date/time range pickers
        - Quick select buttons (Yesterday, Last 24h, 48h, 7d, 30d)
        - Dynamic plot generation for selected range
        - Displays available data range
    
    Network Access:
        - Via IP: http://[device-ip]:8080
        - Via mDNS: http://[hostname].local:8080
    
    Note:
        Uses HTML templates: co2minimeter_webpage.html and
        co2minimeter_history.html with {{PLACEHOLDER}} substitution.
    """

    def __init__(self, port, display_thread):
        """Initialize web server thread.
        
        Args:
            port: HTTP port number (default 8080)
            display_thread: Reference to EInkDisplay for calibration notifications
        """
        super().__init__()
        self.port = port
        self.server = None
        self.display_thread = display_thread

    def run(self):
        """Start HTTP server and handle requests.
        
        Creates HTTPServer instance with custom RequestHandler and serves
        forever until stop() is called.
        
        Routes:
            GET /: Main dashboard page
            GET /plot.svg: Current 12-hour plot
            GET /history: Historical data page with date pickers
            GET /history_plot.svg: Historical plot for selected range
            GET /calibrate: Trigger calibration (redirects to /)
        
        Note:
            Blocks until server.shutdown() called from stop() method.
        """
        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(_self):
                global calibration_in_progress, sensor_warming_up
                
                # Parse URL and query parameters
                parsed_url = urlparse(_self.path)
                path = parsed_url.path
                query_params = parse_qs(parsed_url.query)
                
                # Serve SVG plot file
                if path == '/plot.svg':
                    svg_path = os.path.join(data_dir, "data_latest_plot.svg")
                    if os.path.exists(svg_path):
                        _self.send_response(200)
                        _self.send_header("Content-type", "image/svg+xml")
                        _self.end_headers()
                        with open(svg_path, 'rb') as f:
                            _self.wfile.write(f.read())
                    else:
                        _self.send_response(404)
                        _self.end_headers()
                    return
                
                # Serve history plot SVG
                if path == '/history_plot.svg':
                    svg_path = os.path.join(data_dir, "history_plot.svg")
                    if os.path.exists(svg_path):
                        _self.send_response(200)
                        _self.send_header("Content-type", "image/svg+xml")
                        _self.end_headers()
                        with open(svg_path, 'rb') as f:
                            _self.wfile.write(f.read())
                    else:
                        _self.send_response(404)
                        _self.end_headers()
                    return
                
                # Serve history page
                if path == '/history':
                    _self.send_response(200)
                    _self.send_header("Content-type", "text/html")
                    _self.end_headers()
                    
                    try:
                        # Get data range
                        first_timestamp, last_timestamp = get_data_range()
                        
                        if first_timestamp and last_timestamp:
                            data_range = f"{first_timestamp.strftime('%Y-%m-%d %H:%M')} to {last_timestamp.strftime('%Y-%m-%d %H:%M')}"
                            
                            # Get date parameters from query string or use defaults (last 12 hours)
                            now = datetime.now()
                            default_start = now - timedelta(hours=12)
                            default_end = now
                            
                            if 'start' in query_params and 'end' in query_params:
                                try:
                                    start_str = query_params['start'][0]
                                    end_str = query_params['end'][0]
                                    start_time = datetime.strptime(start_str, "%Y-%m-%dT%H:%M")
                                    end_time = datetime.strptime(end_str, "%Y-%m-%dT%H:%M")
                                except:
                                    start_time = default_start
                                    end_time = default_end
                            else:
                                start_time = default_start
                                end_time = default_end
                            
                            # Generate history plot
                            generate_history_plot(start_time, end_time)
                            
                            # Format dates for HTML input fields
                            start_date_html = start_time.strftime("%Y-%m-%dT%H:%M")
                            end_date_html = end_time.strftime("%Y-%m-%dT%H:%M")
                        else:
                            data_range = "No data available"
                            start_date_html = ""
                            end_date_html = ""
                        
                        # Read the HTML template
                        template_path = os.path.join(
                            os.path.dirname(os.path.abspath(__file__)),
                            "co2minimeter_history.html",
                        )
                        with open(template_path, "r") as f:
                            html = f.read()
                        
                        # Replace placeholders
                        html = html.replace("{{DATA_RANGE}}", data_range)
                        html = html.replace("{{START_DATE}}", start_date_html)
                        html = html.replace("{{END_DATE}}", end_date_html)
                        
                    except Exception as e:
                        html = f"<html><body><h1>Error</h1><p>Could not load history page: {e}</p></body></html>"
                    
                    _self.wfile.write(html.encode("utf-8"))
                    return
                
                # Handle calibration trigger
                if path == '/calibrate':
                    print("Web interface: Calibration triggered")
                    with calibration_lock:
                        calibration_in_progress = True
                    calibration_event.set()
                    
                    # Notify display thread immediately
                    if hasattr(self, 'display_thread') and hasattr(self.display_thread, 'display_condition'):
                        with self.display_thread.display_condition:
                            self.display_thread.display_condition.notify()
                    
                    # Redirect back to main page
                    _self.send_response(302)
                    _self.send_header("Location", "/")
                    _self.end_headers()
                    return
                
                # Serve main page
                _self.send_response(200)
                _self.send_header("Content-type", "text/html")
                _self.end_headers()

                # Get current measurements (thread-safe)
                with measurement_lock:
                    current_measurements = measurements.copy()
                
                # Check calibration status
                with calibration_lock:
                    is_calibrating = calibration_in_progress

                # Read the HTML template
                template_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "co2minimeter_webpage.html",
                )
                try:
                    with open(template_path, "r") as f:
                        html = f.read()
                    
                    # Replace calibration status
                    if is_calibrating:
                        html = html.replace("{{CALIBRATION_STATUS}}", "<div style='background-color: #fff3cd; padding: 15px; margin: 20px 0; border: 1px solid #ffc107; border-radius: 4px;'><strong>⚠️ Recalibration in progress...</strong><br>Please wait approximately 2 minutes.</div>")
                    else:
                        html = html.replace("{{CALIBRATION_STATUS}}", "")

                    # Generate measurement rows
                    measurements_html = ""
                    for timestamp, value, temp, hum in reversed(current_measurements):
                        measurements_html += (
                            f"<tr><td>{timestamp}</td><td>{value}</td><td>{temp:.1f}</td><td>{hum:.1f}</td></tr>"
                        )

                    # Replace the placeholder with actual measurements
                    html = html.replace("{{MEASUREMENTS}}", measurements_html)

                except Exception as e:
                    html = f"<html><body><h1>Error</h1><p>Could not load template: {e}</p></body></html>"

                _self.wfile.write(html.encode("utf-8"))

        self.server = HTTPServer(("", self.port), RequestHandler)
        print(f"Web server running on port {self.port}")
        self.server.serve_forever()

    def stop(self):
        """Gracefully stop the web server.
        
        Shuts down HTTP server and closes all connections.
        Called during application shutdown.
        """
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def main():
    """Main entry point for CO2 Minimeter application.
    
    Initializes all threads, loads recent measurements from CSV files,
    and starts the multi-threaded monitoring system.
    
    Threads Started:
        1. CO2Sensor: Reads sensor every 60 seconds
        2. EInkDisplay: Updates display on changes
        3. WebServer: Serves web interface on port 8080
        4. PlotGenerator: Creates plots every 15 minutes
        5. CalibrationButtonMonitor: Watches GPIO button
    
    Startup Sequence:
        1. Load last 12 hours of measurements from CSV
        2. Create and start all threads
        3. Enter main loop (Ctrl+C to exit)
    
    Shutdown:
        - Ctrl+C triggers graceful shutdown
        - Sets shutdown_event to signal all threads
        - Waits for threads to finish (2-second timeout)
        - Preserves e-ink display contents (no clear)
    
    Note:
        Main thread stays alive to keep daemon threads running.
    """
    print("Starting CO2 Monitor...")
    
    # Load recent measurements from CSV files
    global measurements
    measurements = load_recent_measurements()

    # Create the display thread first so it can be referenced by CO2 sensor
    display_thread = EInkDisplay(daemon=True)
    co2_thread = CO2Sensor(display_thread, daemon=True)
    web_thread = WebServer(WEB_SERVER_PORT, display_thread)
    plot_thread = PlotGenerator(display_thread, daemon=True)
    calibration_button_thread = CalibrationButtonMonitor(display_thread, daemon=True)

    try:
        co2_thread.start()
        display_thread.start()
        web_thread.start()
        plot_thread.start()
        calibration_button_thread.start()

        print("CO2 Monitor is running. Press Ctrl+C to exit.")
        print(f"Hardware button: Hold button on GPIO {CALIBRATION_BUTTON_PIN} (pin 40) for 3 seconds to calibrate")
        print(f"Web interface: Click 'Calibrate Sensor' button at http://localhost:{WEB_SERVER_PORT}")

        # Keep main thread alive
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print(
            "\nShutting down. Waiting for CO2 measurement to finish, this can take very long time ..."
        )
        shutdown_event.set()

        # Stop web server
        web_thread.stop()
        web_thread.join()

        # Wait for other threads to finish
        co2_thread.join(2)
        display_thread.join(2)

        print("Shutdown complete.")


if __name__ == "__main__":
    main()

