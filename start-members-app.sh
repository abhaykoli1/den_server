#!/bin/bash

# Quick start script for Rowdy's Den Members App
# This script sets up and starts the members app integrated with den_server

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🚀 Rowdy's Den Members App — Integrated with den_server"
echo "=================================================="

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8 or later."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"

# Install requirements if needed
if [ ! -d "venv" ]; then
    echo ""
    echo "📦 Installing dependencies..."
    pip install -r requirements.txt
fi

# Setup demo data (optional)
if [ "$1" = "--fresh" ]; then
    echo ""
    echo "🗑️  Clearing old demo data..."
    rm -f .devdata/snapshot.json
fi

echo ""
echo "🎬 Starting den_server..."
echo "=================================================="
echo ""
echo "📱 Members app will be available at:"
echo "   🌐 http://localhost:8000/members"
echo ""
echo "📊 API docs at:"
echo "   🌐 http://localhost:8000/docs"
echo ""
echo "Test logins (AUTH_DEV_MODE=true):"
echo "   📧 ravi@example.com"
echo "   📧 arjun@example.com"
echo ""
echo "Press Ctrl+C to stop"
echo "=================================================="
echo ""

# Start the server
MONGODB_URI="mongomock://demo" AUTH_DEV_MODE=true PORT=8000 python3 run.py
