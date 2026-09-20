# -*- coding: utf-8 -*-
"""Real-render visual UI smoke tests.

These tests run the Qt app against a real display (xcb) and capture screenshots,
unlike `tests/ui/` which uses `QT_QPA_PLATFORM=offscreen`.

Skipped automatically if no display is available (e.g. in CI without Xvfb).
"""

from __future__ import annotations

import os

import pytest
from PIL import Image

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")


def has_display() -> bool:
    """Return True if a real X display is available."""
    return bool(os.environ.get("DISPLAY"))


def pytest_collection_modifyitems(config, items):
    """Skip all visual tests when no display is available."""
    if has_display():
        return
    skip_marker = pytest.mark.skip(reason="No DISPLAY — visual tests require xcb/wayland")
    for item in items:
        item.add_marker(skip_marker)


@pytest.fixture(autouse=True, scope="session")
def _require_xcb_platform():
    """Force the xcb platform plugin for real rendering."""
    if has_display():
        # Override any pytest-qt-set offscreen
        os.environ["QT_QPA_PLATFORM"] = "xcb"


@pytest.fixture
def screenshot_dir() -> str:
    """Path where test screenshots are saved."""
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    return SCREENSHOTS_DIR


def assert_pixmap_nonblank(pixmap, *, threshold: int = 100) -> None:
    """Assert a QPixmap has at least `threshold` non-white pixels.

    A blank rendering (all white or transparent) typically means the widget
    didn't actually paint to the surface — useful for catching offscreen
    fallbacks or driver issues even with a real display.
    """
    img = pixmap.toImage()
    assert not img.isNull(), "Pixmap is null — widget did not render"
    assert img.width() > 0 and img.height() > 0, "Pixmap has zero dimension"

    # Convert to PIL for fast pixel inspection
    buf = img.bits().tobytes()
    pil = Image.frombytes("RGBA", (img.width(), img.height()), buf, "raw", "BGRA")
    pixels = pil.getdata()
    non_white = sum(1 for r, g, b, _ in pixels if (r, g, b) != (255, 255, 255))
    assert non_white >= threshold, (
        f"Rendering looks blank: only {non_white} non-white pixels "
        f"(threshold {threshold}) in {img.width()}x{img.height()} surface"
    )
