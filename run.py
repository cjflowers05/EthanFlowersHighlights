"""
run.py - Start Ethan's Soccer Highlights viewer.
Just double-click this file or run: python run.py
"""
import subprocess
import sys
import webbrowser
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent

print("=" * 50)
print("  Ethan's Soccer Highlights")
print("=" * 50)

# Step 1: scan for new clips
print("\n[1/2] Scanning for clips...")
result = subprocess.run([sys.executable, str(BASE_DIR / "tools" / "scan.py")], cwd=str(BASE_DIR))

# Step 2: start the server (opens browser automatically)
print("\n[2/2] Starting viewer at http://localhost:8080")
print("      Press Ctrl+C to stop.\n")
subprocess.run([sys.executable, str(BASE_DIR / "tools" / "serve.py")], cwd=str(BASE_DIR))
