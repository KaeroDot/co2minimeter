#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import time
import random
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from queue import Queue
from PIL import Image, ImageDraw, ImageFont

# Import e-Paper display library with error handling
picdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'e-Paper', 'RaspberryPi_JetsonNano', 'python', 'pic')
libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'e-Paper', 'RaspberryPi_JetsonNano', 'python', 'lib')

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
    print("E-ink display library not found. Running in simulation mode (display updates will be printed to console)")

# Configuration
CO2_MEASUREMENT_INTERVAL = (2, 10)  # Random interval between 2-10 seconds
WEB_SERVER_PORT = 8080
MAX_MEASUREMENTS = 100

# Global variables
measurements = []
measurement_lock = threading.Lock()
shutdown_event = threading.Event()

class CO2Sensor(threading.Thread):
    """Thread to simulate CO2 sensor readings"""
    def __init__(self, display_thread, daemon=None):
        super().__init__(daemon=daemon)
        self.display_thread = display_thread

    def read_co2(self):
        while not shutdown_event.is_set():
            # Simulate CO2 reading (400-2000 ppm)
            co2_value = random.randint(400, 2000)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            with measurement_lock:
                measurements.append((timestamp, co2_value))
                # Keep only the last MAX_MEASUREMENTS
                if len(measurements) > MAX_MEASUREMENTS:
                    measurements.pop(0)
            
            print(f"CO2: {co2_value} ppm at {timestamp}")
            
            # Notify display thread of new measurement
            if hasattr(self.display_thread, 'display_condition'):
                with self.display_thread.display_condition:
                    self.display_thread.new_measurement = True
                    self.display_thread.display_condition.notify()
            
            # Random delay between 2-10 seconds
            time.sleep(random.uniform(2, 10))

    def run(self):
        self.read_co2()

class EInkDisplay(threading.Thread):
    """Thread to update the e-ink display with current time"""
    def __init__(self, daemon=None):
        super().__init__(daemon=daemon)
        self.epd = None
        self.font15 = None
        self.font24 = None
        self.last_display = None
        self.last_minute = -1
        self.display_condition = threading.Condition()
        self.new_measurement = False
        
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
            self.font15 = ImageFont.truetype(os.path.join(picdir, 'Font.ttc'), 15)
            self.font24 = ImageFont.truetype(os.path.join(picdir, 'Font.ttc'), 24)
            
            # Create base image for partial updates
            self.base_image = Image.new('1', (self.epd.height, self.epd.width), 255)
            self.draw = ImageDraw.Draw(self.base_image)
            
            # Draw static elements on base image
            self.draw.rectangle([(0, 0), (self.epd.height, self.epd.width)], fill=255)
            
            # Display base image
            self.epd.displayPartBaseImage(self.epd.getbuffer(self.base_image))
            
            # Create partial update image
            self.partial_image = Image.new('1', (self.epd.height, self.epd.width), 255)
            self.partial_draw = ImageDraw.Draw(self.partial_image)
            
            return True
        except Exception as e:
            print(f"Failed to initialize e-ink display: {e}")
            return False
    
    def run(self):
        if not self.init_display() and HAS_EINK_DISPLAY:
            return
            
        try:
            while not shutdown_event.is_set():
                current_time = datetime.now().strftime('%H:%M:%S')
                current_date = datetime.now().strftime('%Y-%m-%d')
                
                # Get latest CO2 reading
                with measurement_lock:
                    latest_reading = "N/A" if not measurements else f"{measurements[-1][1]} ppm"
                
                display_text = f""" Time: {current_time}, Date: {current_date}, CO2:  {latest_reading} """
                
                # Only update if the display has changed
                if display_text != self.last_display:
                    if HAS_EINK_DISPLAY and self.epd:
                        # Clear only the areas we're about to update
                        self.partial_draw.rectangle([(0, 0), (self.epd.height, 100)], fill=255)
                        
                        # Draw new content
                        self.partial_draw.text((10, 10), current_time, font=self.font24, fill=0)
                        self.partial_draw.text((10, 40), current_date, font=self.font15, fill=0)
                        self.partial_draw.text((10, 70), f"CO2: {latest_reading}", font=self.font24, fill=0)
                        
                        # Update only the changed part of the display
                        self.epd.displayPartial(self.epd.getbuffer(self.partial_image))
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
                    seconds_until_next_minute = 60 - now.second - now.microsecond / 1_000_000.0
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
                _self.send_response(200)
                _self.send_header('Content-type', 'text/html')
                _self.end_headers()
                
                # Get current measurements (thread-safe)
                with measurement_lock:
                    current_measurements = measurements.copy()
                
                # Read the HTML template
                template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'co2minimeter_webpage.html')
                try:
                    with open(template_path, 'r') as f:
                        html = f.read()
                    
                    # Generate measurement rows
                    measurements_html = ''
                    for timestamp, value in reversed(current_measurements):
                        measurements_html += f'<tr><td>{timestamp}</td><td>{value}</td></tr>'
                    
                    # Replace the placeholder with actual measurements
                    html = html.replace('{{MEASUREMENTS}}', measurements_html)
                    
                except Exception as e:
                    html = f"<html><body><h1>Error</h1><p>Could not load template: {e}</p></body></html>"
                
                _self.wfile.write(html.encode('utf-8'))
        
        self.server = HTTPServer(('', self.port), RequestHandler)
        print(f"Web server running on port {self.port}")
        self.server.serve_forever()
    
    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

def main():
    print("Starting CO2 Monitor...")
    
    # Create the display thread first so it can be referenced by CO2 sensor
    display_thread = EInkDisplay(daemon=True)
    co2_thread = CO2Sensor(display_thread, daemon=True)
    web_thread = WebServer(WEB_SERVER_PORT)
    
    try:
        co2_thread.start()
        display_thread.start()
        web_thread.start()
        
        print("CO2 Monitor is running. Press Ctrl+C to exit.")
        
        # Keep main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nShutting down. Waiting for CO2 measurement to finish can take long time ...")
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