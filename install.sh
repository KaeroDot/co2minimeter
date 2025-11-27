#!/bin/bash

# CO2 Minimeter Installation Script
# This script creates a virtual environment and installs all required dependencies

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

# Check if venv module is available
if ! python3 -c "import venv" &> /dev/null; then
    echo "Error: Python venv module is not available."
    echo "Please install it using: sudo apt-get install python3-venv"
    exit 1
fi

# Create virtual environment
VENV_DIR="venv"
if [ -d "$VENV_DIR" ]; then
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
    echo "Creating virtual environment in '$VENV_DIR'..."
    python3 -m venv "$VENV_DIR"
    echo "Virtual environment created successfully."
fi

echo ""
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo ""
echo "Upgrading pip..."
pip install --upgrade pip

echo ""
echo "Installing dependencies..."
pip install matplotlib pillow

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
