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
    def run(self):
        global measurements
        while not shutdown_event.is_set():
            # Simulate sensor reading (blocking)
            time.sleep(random.uniform(*CO2_MEASUREMENT_INTERVAL))
            
            # Generate random CO2 value between 400-2000 ppm
            co2_value = random.randint(400, 2000)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            with measurement_lock:
                measurements.append((timestamp, co2_value))
                # Keep only the most recent measurements
                if len(measurements) > MAX_MEASUREMENTS:
                    measurements.pop(0)
            
            print(f"CO2: {co2_value} ppm at {timestamp}")

class EInkDisplay(threading.Thread):
    """Thread to update the e-ink display with current time"""
    def __init__(self, daemon=None):
        super().__init__(daemon=daemon)
        self.epd = None
        self.font15 = None
        self.font24 = None
        self.last_display = None
        
    def init_display(self):
        """Initialize the e-ink display or set up simulation"""
        if not HAS_EINK_DISPLAY:
            print("Display: Running in simulation mode")
            return True
            
        try:
            self.epd = epd2in13_V4.EPD()
            self.epd.init()
            self.epd.Clear(0xFF)
            
            # Load fonts
            self.font15 = ImageFont.truetype(os.path.join(picdir, 'Font.ttc'), 15)
            self.font24 = ImageFont.truetype(os.path.join(picdir, 'Font.ttc'), 24)
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
                
                # Only print if the display has changed
                if display_text != self.last_display:
                    if HAS_EINK_DISPLAY and self.epd:
                        # Update real display
                        image = Image.new('1', (self.epd.height, self.epd.width), 255)
                        draw = ImageDraw.Draw(image)
                        draw.text((10, 10), current_time, font=self.font24, fill=0)
                        draw.text((10, 40), current_date, font=self.font15, fill=0)
                        draw.text((10, 70), f"CO2: {latest_reading}", font=self.font24, fill=0)
                        self.epd.display(self.epd.getbuffer(image))
                    else:
                        # Print to console in simulation mode
                        print(display_text)
                    
                    self.last_display = display_text
                
                time.sleep(1)
                
        except Exception as e:
            print(f"Error in display thread: {e}")
        finally:
            if HAS_EINK_DISPLAY and self.epd:
                self.epd.sleep()
                print("Display: E-ink display put to sleep")

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
    
    # Create and start threads
    co2_thread = CO2Sensor(daemon=True)
    display_thread = EInkDisplay(daemon=True)
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
        print("\nShutting down...")
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