#!/bin/bash

# JF-Resolve Setup Script
# This script creates a virtual environment and installs dependencies.

echo "Starting JF-Resolve Setup..."

# Prefer the supported Python release when it is installed.
if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN=python3.12
else
    PYTHON_BIN=python3
fi
python3_version=$($PYTHON_BIN -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "Found Python $python3_version ($PYTHON_BIN)"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    "$PYTHON_BIN" -m venv venv
else
    echo "Virtual environment already exists."
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing dependencies from requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "Setup complete!"
echo "-------------------------------------------------------"
echo "To start JF-Resolve:"
echo "1. Activate the environment: source venv/bin/activate"
echo "2. Run the launcher: python start.py"
echo "-------------------------------------------------------"
