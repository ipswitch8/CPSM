# -*- coding: utf-8 -*-
"""
Tests for MonitorService and MonitorInfo.

Uses synthetic QScreen mocks via pytest-qt.  QT_QPA_PLATFORM=offscreen is
required (set in pyproject.toml env or via the fixture below).

Spec: §6.1
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

# Ensure offscreen platform before Qt is imported
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSizeF
from PySide6.QtGui import QGuiApplication

from cpsm.services.monitor_service import MonitorInfo, MonitorService, _screen_to_info

# ---------------------------------------------------------------------------
# Synthetic QScreen helper
# ---------------------------------------------------------------------------


def _make_screen(
    *,
    name: str = "TEST-1",
    manufacturer: str = "",
    model: str = "",
    serial: str = "",
    x: int = 0,
    y: int = 0,
    width: int = 1920,
    height: int = 1080,
    avail_x: int = 0,
    avail_y: int = 0,
    avail_w: int = 1920,
    avail_h: int = 1040,
    phys_w: float = 527.0,
    phys_h: float = 296.0,
    dpr: float = 1.0,
    orientation: int = 2,  # landscape
) -> MagicMock:
    """Build a MagicMock that looks like a QScreen."""
    screen = MagicMock()
    screen.name.return_value = name
    screen.manufacturer.return_value = manufacturer
    screen.model.return_value = model
    screen.serialNumber.return_value = serial
    screen.geometry.return_value = QRect(x, y, width, height)
    screen.availableGeometry.return_value = QRect(avail_x, avail_y, avail_w, avail_h)
    screen.physicalSize.return_value = QSizeF(phys_w, phys_h)
    screen.devicePixelRatio.return_value = dpr
    screen.orientation.return_value = orientation
    return screen


# ---------------------------------------------------------------------------
# _screen_to_info unit tests
# ---------------------------------------------------------------------------


def test_screen_to_info_full_edid() -> None:
    screen = _make_screen(
        name="HDMI-1",
        manufacturer="DELL",
        model="U2723QE",
        serial="SN12345",
    )
    info = _screen_to_info(screen, 0)
    assert info.identifier == "DELL-U2723QE-SN12345"
    assert info.name == "HDMI-1"
    assert info.manufacturer == "DELL"
    assert info.model == "U2723QE"
    assert info.serial == "SN12345"
    assert info.qt_index == 0


def test_screen_to_info_partial_edid_no_serial() -> None:
    screen = _make_screen(manufacturer="LG", model="27UK850")
    info = _screen_to_info(screen, 1)
    assert info.identifier == "LG-27UK850"
    assert info.qt_index == 1


def test_screen_to_info_no_edid_uses_index_fallback() -> None:
    screen = _make_screen(manufacturer="", model="", serial="")
    info = _screen_to_info(screen, 3)
    assert info.identifier == "index-3"


def test_screen_to_info_geometry() -> None:
    screen = _make_screen(x=1920, y=0, width=2560, height=1440)
    info = _screen_to_info(screen, 0)
    assert info.geometry == (1920, 0, 2560, 1440)


def test_screen_to_info_available_geometry() -> None:
    screen = _make_screen(avail_x=0, avail_y=28, avail_w=1920, avail_h=1052)
    info = _screen_to_info(screen, 0)
    assert info.available_geometry == (0, 28, 1920, 1052)


def test_screen_to_info_physical_size() -> None:
    screen = _make_screen(phys_w=600.0, phys_h=340.0)
    info = _screen_to_info(screen, 0)
    assert info.physical_size_mm == pytest.approx((600.0, 340.0))


def test_screen_to_info_dpr() -> None:
    screen = _make_screen(dpr=2.0)
    info = _screen_to_info(screen, 0)
    assert info.device_pixel_ratio == 2.0


def test_screen_to_info_orientation_landscape() -> None:
    screen = _make_screen(orientation=2)
    info = _screen_to_info(screen, 0)
    assert info.orientation == "landscape"


def test_screen_to_info_orientation_portrait() -> None:
    screen = _make_screen(orientation=1)
    info = _screen_to_info(screen, 0)
    assert info.orientation == "portrait"


def test_screen_to_info_orientation_landscape_flipped() -> None:
    screen = _make_screen(orientation=8)
    info = _screen_to_info(screen, 0)
    assert info.orientation == "landscape-flipped"


def test_screen_to_info_orientation_portrait_flipped() -> None:
    screen = _make_screen(orientation=4)
    info = _screen_to_info(screen, 0)
    assert info.orientation == "portrait-flipped"


def test_screen_to_info_orientation_unknown() -> None:
    screen = _make_screen(orientation=99)
    info = _screen_to_info(screen, 0)
    assert info.orientation == "unknown"


# ---------------------------------------------------------------------------
# MonitorService snapshot() — uses real offscreen QApplication
# ---------------------------------------------------------------------------


@pytest.fixture()
def qapp_instance(qapp: QGuiApplication) -> QGuiApplication:
    """Re-use the pytest-qt provided QApplication."""
    return qapp  # type: ignore[return-value]


def test_snapshot_returns_list(qapp_instance: QGuiApplication) -> None:
    """snapshot() returns a list — may be empty in offscreen mode."""
    svc = MonitorService(qapp_instance)
    result = svc.snapshot()
    assert isinstance(result, list)


def test_snapshot_each_item_is_monitor_info(qapp_instance: QGuiApplication) -> None:
    svc = MonitorService(qapp_instance)
    for item in svc.snapshot():
        assert isinstance(item, MonitorInfo)


# ---------------------------------------------------------------------------
# MonitorService with mocked screens
# ---------------------------------------------------------------------------


def test_snapshot_with_mock_screens(
    qapp_instance: QGuiApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """snapshot() should reflect the mocked screen list."""
    s1 = _make_screen(name="DP-1", manufacturer="DELL", model="U2723QE", serial="ABC")
    s2 = _make_screen(name="HDMI-1", manufacturer="LG", model="27UK850", serial="XYZ")
    monkeypatch.setattr(qapp_instance, "screens", lambda: [s1, s2])

    svc = MonitorService(qapp_instance)
    result = svc.snapshot()

    assert len(result) == 2
    assert result[0].name == "DP-1"
    assert result[0].identifier == "DELL-U2723QE-ABC"
    assert result[1].name == "HDMI-1"
    assert result[1].identifier == "LG-27UK850-XYZ"
    assert result[0].qt_index == 0
    assert result[1].qt_index == 1


def test_snapshot_single_monitor(
    qapp_instance: QGuiApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _make_screen(name="eDP-1", manufacturer="AU", model="B156HAN", serial="")
    monkeypatch.setattr(qapp_instance, "screens", lambda: [s])

    svc = MonitorService(qapp_instance)
    result = svc.snapshot()

    assert len(result) == 1
    assert result[0].qt_index == 0
    assert result[0].identifier == "AU-B156HAN"


def test_snapshot_no_edid_uses_fallback(
    qapp_instance: QGuiApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _make_screen(manufacturer="", model="", serial="")
    monkeypatch.setattr(qapp_instance, "screens", lambda: [s])

    svc = MonitorService(qapp_instance)
    result = svc.snapshot()

    assert result[0].identifier == "index-0"


# ---------------------------------------------------------------------------
# monitor_added / monitor_removed signal tests
#
# We call the private handler methods (_on_screen_added / _on_screen_removed)
# directly rather than emitting Qt's screenAdded/screenRemoved with a
# MagicMock QScreen, because emitting Qt signals with non-QScreen objects
# causes a segfault in the C++ layer.
# ---------------------------------------------------------------------------


def test_monitor_added_signal_fires(
    qapp_instance: QGuiApplication,
    qtbot: pytest.fixture,  # type: ignore[valid-type]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(qapp_instance, "screens", lambda: [])
    svc = MonitorService(qapp_instance)

    new_screen = _make_screen(name="DP-2", manufacturer="ASUS", model="PG32UQ", serial="S001")
    # After the screen is added, screens() should return it so the index lookup works
    monkeypatch.setattr(qapp_instance, "screens", lambda: [new_screen])

    received: list[MonitorInfo] = []
    svc.monitor_added.connect(received.append)

    # Call the private handler directly (avoids emitting Qt signal with a non-QScreen mock)
    with qtbot.waitSignal(svc.monitor_added, timeout=1000):
        svc._on_screen_added(new_screen)  # type: ignore[arg-type]

    assert len(received) == 1
    assert received[0].identifier == "ASUS-PG32UQ-S001"


def test_monitor_removed_signal_fires(
    qapp_instance: QGuiApplication,
    qtbot: pytest.fixture,  # type: ignore[valid-type]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen(name="DP-1", manufacturer="DELL", model="P2419H", serial="SN99")
    monkeypatch.setattr(qapp_instance, "screens", lambda: [screen])
    svc = MonitorService(qapp_instance)

    # Now the screen is gone
    monkeypatch.setattr(qapp_instance, "screens", lambda: [])

    received_ids: list[str] = []
    svc.monitor_removed.connect(received_ids.append)

    with qtbot.waitSignal(svc.monitor_removed, timeout=1000):
        svc._on_screen_removed(screen)  # type: ignore[arg-type]

    assert len(received_ids) == 1
    assert received_ids[0] == "DELL-P2419H-SN99"


def test_monitor_removed_unknown_screen(
    qapp_instance: QGuiApplication,
    qtbot: pytest.fixture,  # type: ignore[valid-type]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing a screen not seen at init should still emit a signal."""
    monkeypatch.setattr(qapp_instance, "screens", lambda: [])
    svc = MonitorService(qapp_instance)

    unknown_screen = _make_screen(name="UNKNOWN-1")

    received_ids: list[str] = []
    svc.monitor_removed.connect(received_ids.append)

    with qtbot.waitSignal(svc.monitor_removed, timeout=1000):
        svc._on_screen_removed(unknown_screen)  # type: ignore[arg-type]

    assert len(received_ids) == 1
    # The fallback uses screen.name()
    assert received_ids[0] == "UNKNOWN-1"


# ---------------------------------------------------------------------------
# MonitorInfo dataclass
# ---------------------------------------------------------------------------


def test_monitor_info_is_frozen() -> None:
    info = MonitorInfo(
        identifier="DELL-U2723QE-ABC",
        name="DP-1",
        geometry=(0, 0, 2560, 1440),
        available_geometry=(0, 0, 2560, 1412),
        physical_size_mm=(600.0, 340.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="DELL",
        model="U2723QE",
        serial="ABC",
        qt_index=0,
    )
    with pytest.raises((TypeError, AttributeError)):
        info.name = "changed"  # type: ignore[misc]
