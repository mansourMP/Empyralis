#!/bin/bash

echo "🚀 Initializing Agency OS Environment..."

# 1. Setup Python Engine
echo "📦 Setting up Python Virtual Environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# 2. Install Dependencies
echo "⬇️  Installing Python Dependencies..."
./venv/bin/pip install pyyaml requests

# 3. Create Temp Directories
echo "📂 Creating Execution Directories..."
mkdir -p /tmp/agency_os

# 4. Success Method
echo "✅ Agency Environment Ready!"
echo "   - Python: ./venv/bin/python"
echo "   - Logic: python_engine/agency_logic.py"
