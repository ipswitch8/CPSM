# -*- coding: utf-8 -*-
"""Help -> About must let a user confirm which build they are running.

This dialog is the answer to "am I actually testing current code, or still on
the old one?".  That makes two properties load-bearing rather than cosmetic:
the string must carry the build identity, and it must be copyable, because a
commit hash retyped by hand is a transcription error waiting to happen.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from cpsm import __version__, version_string  # noqa: E402
from cpsm.ui.dialogs.about import AboutDialog  # noqa: E402


class TestAboutVersionLabel:
    @staticmethod
    def _label(qtbot):
        """Return (dialog, label).

        The dialog must be returned too, not just the label: dropping the last
        Python reference to the parent lets Qt delete it, and the child label
        goes with it -- "Internal C++ object already deleted" on next access.
        """
        dlg = AboutDialog()
        qtbot.addWidget(dlg)
        lbl = dlg.findChild(QLabel, "label_about_version")
        assert lbl is not None, "About dialog has no label_about_version"
        return dlg, lbl

    def test_shows_version_and_build(self, qtbot):
        _dlg, lbl = self._label(qtbot)
        text = lbl.text()
        assert __version__ in text
        # The build identity, not just the release number -- the release
        # number alone cannot distinguish two builds of the same version.
        assert version_string() in text
        assert "build" in text

    def test_is_selectable(self, qtbot):
        """A string you are asked to report back must be copyable."""
        _dlg, lbl = self._label(qtbot)
        flags = lbl.textInteractionFlags()
        assert flags & Qt.TextInteractionFlag.TextSelectableByMouse, (
            "version label is not selectable — a user would have to retype a "
            "commit hash to report which build they are on"
        )

    def test_build_identity_is_not_empty(self, qtbot):
        """Guard against rendering a bare 'Version: 0.2.0 (build )'."""
        _dlg, lbl = self._label(qtbot)
        text = lbl.text()
        inner = text.split("(build", 1)[1]
        assert inner.strip(" )").strip(), text
