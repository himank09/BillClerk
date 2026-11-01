"""Draws the preview picture of each example invoice ahead of time.

Render runs this while building the app, so a visitor never waits for a
preview to be drawn (it takes a few seconds each on the free server)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.server import INVOICES, _render_preview  # noqa: E402

for f in sorted(INVOICES.iterdir()):
    _render_preview(f)
    print("preview ready:", f.name)
