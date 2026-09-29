import sys
import os

# Ensure backend directory is in Python path
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(base_dir, "backend")

if base_dir not in sys.path:
    sys.path.insert(0, base_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from backend.main import app
