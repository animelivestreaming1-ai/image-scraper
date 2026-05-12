#!/bin/bash
# Build script for Render deployment
# Installs Playwright Chromium browser for scraping

set -e

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Installing Playwright Chromium browser..."
playwright install chromium

echo "Build completed successfully!"
