"""Lets the tests here import the spike's modules by name, as the script does."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
