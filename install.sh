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
echo "This will install: python3-venv, python3-matplotlib, python3-pil"
sudo apt-get update
sudo apt-get install -y python3-venv python3-matplotlib python3-pil

echo ""
echo "Verifying system packages..."
python3 -c "import matplotlib; print(f'matplotlib {matplotlib.__version__} installed')"
python3 -c "import PIL; print(f'Pillow {PIL.__version__} installed')"

# Create virtual environment with system site packages
VENV_DIR="venv"
if [ -d "$VENV_DIR" ]; then
    echo ""
    echo "Virtual environment already exists at '$VENV_DIR'."
    read -p "Do you want to remove it and create a new one? (y/N): " response
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
echo "=================================="
echo "Installation completed successfully!"
echo "=================================="
echo ""
echo "To run the CO2 monitor:"
echo "  1. Activate the virtual environment:"
echo "     source venv/bin/activate"
echo ""
echo "  2. Run the script:"
echo "     python3 co2minimeter.py"
echo ""
echo "  3. Access the web interface at:"
echo "     http://localhost:8080"
echo ""
echo "To deactivate the virtual environment when done:"
echo "  deactivate"
echo ""
