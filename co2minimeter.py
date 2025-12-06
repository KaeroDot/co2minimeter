#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
from datetime import datetime, timedelta
from queue import Queue
from PIL import Image, ImageDraw, ImageFont

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

# Configuration
CO2_MEASUREMENT_INTERVAL = 60  # Measurement interval in seconds
SENSOR_WARMUP_READINGS = 2  # Number of initial sensor readings to skip
PLOT_UPDATE_INTERVAL = 900  # Plot update interval in seconds (15 minutes)
WEB_SERVER_PORT = 8080
HOURS_TO_KEEP = 12  # Keep last 12 hours of measurements
DATA_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data")
FONT_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "fonts")

# Global variables
measurements = []
measurement_lock = threading.Lock()
shutdown_event = threading.Event()


def save_to_csv(timestamp_str, co2_value, temperature, humidity):
    """Save measurement to daily CSV file"""
    try:
        # Create data directory if it doesn't exist
        os.makedirs(DATA_DIR, exist_ok=True)
        
        # Parse timestamp to get date
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        date_str = dt.strftime("%Y-%m-%d")
        
        # Create filename: data_YYYY-MM-DD.csv
        filename = f"data_{date_str}.csv"
        filepath = os.path.join(DATA_DIR, filename)
        
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
    """Load measurements from the last 12 hours from CSV files"""
    loaded_measurements = []
    
    try:
        if not os.path.exists(DATA_DIR):
            print("No previous data found.")
            return loaded_measurements
        
        # Calculate cutoff time (12 hours ago)
        cutoff_time = datetime.now() - timedelta(hours=HOURS_TO_KEEP)
        
        # Get list of CSV files to check (today and yesterday)
        files_to_check = []
        for days_back in range(2):  # Check today and yesterday
            date = datetime.now() - timedelta(days=days_back)
            filename = f"data_{date.strftime('%Y-%m-%d')}.csv"
            filepath = os.path.join(DATA_DIR, filename)
            if os.path.isfile(filepath):
                files_to_check.append(filepath)
        
        # Read measurements from files
        for filepath in files_to_check:
            try:
                with open(filepath, 'r') as csvfile:
                    reader = csv.reader(csvfile)
                    next(reader, None)  # Skip header
                    for row in reader:
                        if len(row) >= 2:
                            timestamp_str = row[0]
                            co2_value = int(row[1])
                            
                            # Parse timestamp and check if it's within last 12 hours
                            measurement_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                            if measurement_time >= cutoff_time:
                                loaded_measurements.append((timestamp_str, co2_value))
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
        
        # Sort by timestamp
        loaded_measurements.sort(key=lambda x: x[0])
        
        print(f"Loaded {len(loaded_measurements)} measurements from the last {HOURS_TO_KEEP} hours.")
        
    except Exception as e:
        print(f"Error loading recent measurements: {e}")
    
    return loaded_measurements


def cleanup_old_measurements():
    """Remove measurements older than HOURS_TO_KEEP hours from the measurements array"""
    global measurements
    
    try:
        cutoff_time = datetime.now() - timedelta(hours=HOURS_TO_KEEP)
        
        with measurement_lock:
            # Filter out measurements older than cutoff time
            # Handle both old format (2 values) and new format (4 values)
            filtered = []
            for item in measurements:
                if len(item) == 2:
                    timestamp_str, value = item
                    # Convert old format to new format with default values
                    item = (timestamp_str, value, 0.0, 0.0)
                elif len(item) == 4:
                    timestamp_str, value, temp, hum = item
                else:
                    continue
                
                if datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S") >= cutoff_time:
                    filtered.append(item)
            measurements = filtered
    except Exception as e:
        print(f"Error cleaning up old measurements: {e}")


def generate_plot():
    """Generate SVG and bitmap plot of last 12 hours of CO2 measurements"""
    try:
        with measurement_lock:
            data = measurements.copy()
        
        if not data:
            print("No data to plot")
            return
        
        # Parse timestamps and values, handling both old (2-value) and new (4-value) formats
        timestamps = []
        values = []
        temperatures = []
        humidities = []
        
        for item in data:
            if len(item) == 2:
                ts, val = item
                timestamps.append(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                values.append(val)
                temperatures.append(0.0)
                humidities.append(0.0)
            elif len(item) == 4:
                ts, val, temp, hum = item
                timestamps.append(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                values.append(val)
                temperatures.append(temp)
                humidities.append(hum)
        
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
        ax_svg.set_ylim(400, 2000)
        ax_svg.set_yticks([500, 1000, 1500])
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
        
        svg_path = os.path.join(DATA_DIR, "data_latest_plot.svg")
        plt.savefig(svg_path, format='svg', dpi=100)
        print(f"Plot saved as SVG: {svg_path}")
        plt.close(fig_svg)
        
        # Generate PNG without grid (245x70 pixels for e-ink display)
        fig_png, ax_png = plt.subplots(figsize=(2.45, 0.7), dpi=100)
        ax_png.plot(timestamps_gapped, values_gapped, color="#000000", linewidth=1)
        ax_png.set_ylim(400, 2000)
        ax_png.set_yticks([500, 1000, 1500])
        ax_png.set_yticklabels(['0.5k', '1k', '1.5k'])
        ax_png.tick_params(axis='y', labelsize=6)
        ax_png.set_xlim(start_time, now)
        ax_png.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        ax_png.xaxis.set_major_formatter(mdates.DateFormatter(''))
        ax_png.tick_params(axis='x', length=3, width=0.5)
        ax_png.grid(True, alpha=1, axis='x')
        ax_png.set_xlabel('')
        ax_png.set_ylabel('')
        plt.subplots_adjust(left=0.12, right=0.98, top=0.98, bottom=0.08)
        
        png_path = os.path.join(DATA_DIR, "data_latest_plot.png")
        plt.savefig(png_path, format='png', dpi=100)
        print(f"Plot saved as PNG: {png_path}")
        plt.close(fig_png)
        
    except Exception as e:
        print(f"Error generating plot: {e}")


class PlotGenerator(threading.Thread):
    """Thread to generate plots every 15 minutes"""
    
    def __init__(self, display_thread, daemon=None):
        super().__init__(daemon=daemon)
        self.display_thread = display_thread
    
    def run(self):
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
    """Thread to read CO2 sensor (real hardware or simulated)"""

    def __init__(self, display_thread, daemon=None):
        super().__init__(daemon=daemon)
        self.display_thread = display_thread
        self.sensor = None
        self.use_hardware = False
        self.readings_to_skip = 0  # Number of initial readings to skip
        
    def init_sensor(self):
        """Initialize SCD30 sensor hardware"""
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

    def read_co2(self):
        # Try to initialize hardware sensor
        self.init_sensor()
        
        while not shutdown_event.is_set():
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
        self.read_co2()


class EInkDisplay(threading.Thread):
    """Thread to update the e-ink display with current time"""

    def __init__(self, daemon=None):
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

    def init_display(self):
        """Initialize the e-ink display or set up simulation"""
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
            font_path = os.path.join(FONT_DIR, "DejaVuSansMono-Bold.ttf")
            self.font12 = ImageFont.truetype(font_path, 12)
            self.font15 = ImageFont.truetype(font_path, 15)
            self.font24 = ImageFont.truetype(font_path, 24)
            self.font36 = ImageFont.truetype(font_path, 36)

            # Create base image for partial updates
            self.base_image = Image.new("1", (self.epd.height, self.epd.width), 255)
            self.draw = ImageDraw.Draw(self.base_image)

            # Display base image
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
        if not self.init_display() and HAS_EINK_DISPLAY:
            return

        try:
            while not shutdown_event.is_set():
                current_time = datetime.now().strftime("%H:%M")
                current_date = datetime.now().strftime("%d.%m.%Y")

                # Get latest CO2 reading
                with measurement_lock:
                    latest_reading = (
                        "N/A" if not measurements else f"{measurements[-1][1]}"
                    )

                display_text = f""" Time: {current_time}, Date: {current_date}, CO2: {latest_reading} """

                # Only update if the display has changed
                if display_text != self.last_display:
                    if HAS_EINK_DISPLAY and self.epd:
                        # Check if we need to update the plot based on notification
                        if self.new_plot:
                            png_path = os.path.join(DATA_DIR, "data_latest_plot.png")
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

                        # Update only the changed part of the display
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
    """Thread to serve a simple web interface"""

    def __init__(self, port):
        super().__init__()
        self.port = port
        self.server = None

    def run(self):
        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(_self):
                # Serve SVG plot file
                if _self.path == '/plot.svg':
                    svg_path = os.path.join(DATA_DIR, "data_latest_plot.svg")
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
                
                # Serve HTML page
                _self.send_response(200)
                _self.send_header("Content-type", "text/html")
                _self.end_headers()

                # Get current measurements (thread-safe)
                with measurement_lock:
                    current_measurements = measurements.copy()

                # Read the HTML template
                template_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "co2minimeter_webpage.html",
                )
                try:
                    with open(template_path, "r") as f:
                        html = f.read()

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
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def main():
    print("Starting CO2 Monitor...")
    
    # Load recent measurements from CSV files
    global measurements
    measurements = load_recent_measurements()

    # Create the display thread first so it can be referenced by CO2 sensor
    display_thread = EInkDisplay(daemon=True)
    co2_thread = CO2Sensor(display_thread, daemon=True)
    web_thread = WebServer(WEB_SERVER_PORT)
    plot_thread = PlotGenerator(display_thread, daemon=True)

    try:
        co2_thread.start()
        display_thread.start()
        web_thread.start()
        plot_thread.start()

        print("CO2 Monitor is running. Press Ctrl+C to exit.")

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

