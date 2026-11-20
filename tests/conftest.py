"""Tests run without the models: they import the app's code (app/) and check the parts that are pure Python:
permission rules, the Python guard, time parsing, speech-speed words and the honesty helpers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
