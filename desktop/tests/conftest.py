"""Run desktop model/widget checks without requiring a visible display."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
