"""Add the project root to sys.path so tests can import tuiradio directly."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
