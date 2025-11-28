#!/bin/bash

# CO2 Minimeter Installation Script
# This script installs system packages and creates a virtual environment with access to them

set -e  # Exit on error

echo "=================================="
echo "CO2 Minimeter Installation Script"
echo "=================================="
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed. Please install Python 3 first."
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "Found: $PYTHON_VERSION"
echo ""

# Install system packages
echo "Installing system packages (requires sudo)..."
echo "This will install: python3-venv, python3-matplotlib, python3-pil, avahi-daemon, avahi-utils"
sudo apt-get update
sudo apt-get install -y python3-venv python3-matplotlib python3-pil avahi-daemon avahi-utils

echo ""
echo "Configuring Avahi mDNS..."
AVAHI_CONF="/etc/avahi/avahi-daemon.conf"

# Backup original config if not already backed up
if [ ! -f "${AVAHI_CONF}.backup" ]; then
    sudo cp "$AVAHI_CONF" "${AVAHI_CONF}.backup"
    echo "Backed up original Avahi config to ${AVAHI_CONF}.backup"
fi

# Update Avahi configuration
sudo tee "$AVAHI_CONF" > /dev/null << 'EOF'
[server]
use-ipv4=yes
use-ipv6=yes
allow-interfaces=wlan0,eth0
deny-interfaces=lo
enable-dbus=yes
ratelimit-interval-usec=1000000
ratelimit-burst=1000

[publish]
publish-addresses=yes
publish-hinfo=yes
publish-workstation=yes
publish-domain=yes

[wide-area]
enable-wide-area=yes

[rlimits]
EOF

echo "Avahi configuration updated."

# Enable and start Avahi daemon
echo "Enabling and starting Avahi daemon..."
sudo systemctl enable avahi-daemon
sudo systemctl restart avahi-daemon

# Check Avahi status
if sudo systemctl is-active --quiet avahi-daemon; then
    echo "✓ Avahi daemon is running"
    echo ""
    echo "Current mDNS hostname:"
    avahi-resolve -n $(hostname).local 2>/dev/null || echo "  (hostname resolution will be available shortly)"
else
    echo "✗ Warning: Avahi daemon failed to start"
fi

echo ""
echo "Verifying system packages..."
python3 -c "import matplotlib; print(f'matplotlib {matplotlib.__version__} installed')"
python3 -c "import PIL; print(f'Pillow {PIL.__version__} installed')"

# Create virtual environment with system site packages
VENV_DIR="venv"
if [ -d "$VENV_DIR" ]; then
    echo ""
    echo "Virtual environment already exists at '$VENV_DIR'."
    read -p "Do you want to remove it and create a new one? (if you are not sure, select N as no) (y/N): " response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        echo "Removing existing virtual environment..."
        rm -rf "$VENV_DIR"
    else
        echo "Keeping existing virtual environment."
    fi
fi

if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo "Creating virtual environment with system site packages access..."
    python3 -m venv --system-site-packages "$VENV_DIR"
    echo "Virtual environment created successfully."
fi

echo ""
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo ""
echo "Upgrading pip..."
pip install --upgrade pip

echo ""
echo "Installing Sensirion SCD30 sensor driver..."
if [ -d "python-i2c-scd30" ]; then
    pip install -e python-i2c-scd30/
else
    echo "Warning: python-i2c-scd30 directory not found. Skipping sensor driver installation."
fi

echo ""
echo "Setting up systemd service..."

# Get the absolute path of the installation directory
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$(whoami)"

# Create systemd service file
SERVICE_FILE="$INSTALL_DIR/co2minimeter.service"
TEMP_SERVICE="/tmp/co2minimeter.service"

# Replace placeholders in service file
sed -e "s|%USER%|$CURRENT_USER|g" \
    -e "s|%INSTALL_DIR%|$INSTALL_DIR|g" \
    "$SERVICE_FILE" > "$TEMP_SERVICE"

# Install systemd service
echo "Installing systemd service (requires sudo)..."
sudo cp "$TEMP_SERVICE" /etc/systemd/system/co2minimeter.service
sudo systemctl daemon-reload

# Ask if user wants to enable and start the service
echo ""
read -p "Do you want to enable the service to start on boot? (Y/n): " enable_response
enable_response=${enable_response:-Y}
if [[ "$enable_response" =~ ^[Yy]$ ]]; then
    sudo systemctl enable co2minimeter.service
    echo "Service enabled to start on boot."
    
    read -p "Do you want to start the service now? (y/N): " start_response
    if [[ "$start_response" =~ ^[Yy]$ ]]; then
        sudo systemctl start co2minimeter.service
        echo "Service started."
        echo ""
        echo "Check service status with: sudo systemctl status co2minimeter.service"
        echo "View logs with: sudo journalctl -u co2minimeter.service -f"
    fi
else
    echo "Service installed but not enabled."
    echo "To enable it later, run: sudo systemctl enable co2minimeter.service"
    echo "To start it later, run: sudo systemctl start co2minimeter.service"
fi

echo ""
echo "=================================="
echo "Installation completed successfully!"
echo "=================================="
echo ""
echo "mDNS Configuration:"
echo "  Your device should be accessible at: "
echo ""
echo "             /--------------------------------\"
echo "             | http://$(hostname).local:8080  |"
echo "             \--------------------------------/"
echo ""
echo "Systemd service commands:"
echo "  Start:   sudo systemctl start co2minimeter.service"
echo "  Stop:    sudo systemctl stop co2minimeter.service"
echo "  Restart: sudo systemctl restart co2minimeter.service"
echo "  Status:  sudo systemctl status co2minimeter.service"
echo "  Logs:    sudo journalctl -u co2minimeter.service -f"
echo ""
echo "To run manually:"
echo "  1. Activate the virtual environment:"
echo "     source venv/bin/activate"
echo ""
echo "  2. Run the script:"
echo "     python3 co2minimeter.py"
echo ""
echo "  3. Access the web interface at:"
echo "     http://localhost:8080 (local)"
echo "     http://$(hostname).local:8080 (network)"
echo ""
echo "To deactivate the virtual environment when done:"
echo "  deactivate"
echo ""
