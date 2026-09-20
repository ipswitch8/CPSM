# -*- coding: utf-8 -*-
"""
Shared pytest fixtures for the cpsm.ui test package.

Sets QT_QPA_PLATFORM=offscreen so every test in this directory runs without
a real display server.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _offscreen_platform() -> None:
    """Ensure Qt uses the offscreen platform during all UI tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
