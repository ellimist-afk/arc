import sys
from pathlib import Path

# Modules under src/ import each other rootless (e.g. `from database.session
# import ...`), same as start_web_ui.py sets up.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
