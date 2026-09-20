# -*- coding: utf-8 -*-
"""
Unit tests for cpsm.ui.main_window._derive_external_status.

This is the pure function that maps (PaneStatus, launch_profile) to the
sidebar/canvas color: connected / dropped / disconnected / error.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cpsm.workers.status_poller import PaneState, PaneStatus

# Import the helper directly. We avoid spinning up a QApplication just to
# test pure-function logic.
from cpsm.ui.main_window import _derive_external_status

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _status(
    *,
    state: PaneState = PaneState.CONNECTED,
    current_command: str = "ssh",
    attached: bool = True,
    exit_code: int | None = None,
) -> PaneStatus:
    return PaneStatus(
        pane_id="%1",
        session="cpsm-group-x-mon-0",
        state=state,
        last_seen=_NOW,
        exit_code=exit_code,
        last_output_tail=None,
        window_index=0,
        pane_index=0,
        current_command=current_command,
        attached=attached,
    )


class TestDeriveExternalStatus:
    @pytest.mark.parametrize(
        "profile",
        ["ssh-shell", "claude-remote"],
    )
    def test_remote_ssh_running_is_connected(self, profile: str) -> None:
        st = _status(current_command="ssh", attached=True)
        assert _derive_external_status(st, profile) == "connected"

    @pytest.mark.parametrize(
        "fg_cmd",
        ["bash", "zsh", "sh", "dash", "fish", "sleep", "read"],
    )
    def test_remote_shell_foreground_is_dropped(self, fg_cmd: str) -> None:
        """ssh-shell/claude-remote profile + shell foreground = SSH dropped."""
        st = _status(current_command=fg_cmd, attached=True)
        assert _derive_external_status(st, "claude-remote") == "dropped"

    def test_local_shell_with_bash_foreground_is_connected(self) -> None:
        """For local-shell, bash IS the expected foreground."""
        st = _status(current_command="bash", attached=True)
        assert _derive_external_status(st, "local-shell") == "connected"

    def test_claude_local_is_connected_regardless_of_command(self) -> None:
        st = _status(current_command="bash", attached=True)
        assert _derive_external_status(st, "claude-local") == "connected"

    def test_detached_session_is_disconnected_even_when_ssh_running(self) -> None:
        """Terminal app crashed: pane alive, ssh running, but no client attached."""
        st = _status(current_command="ssh", attached=False)
        assert _derive_external_status(st, "ssh-shell") == "disconnected"

    def test_error_state_overrides_everything(self) -> None:
        st = _status(state=PaneState.ERROR, current_command="ssh", exit_code=1)
        assert _derive_external_status(st, "ssh-shell") == "error"

    def test_clean_exit_is_disconnected(self) -> None:
        st = _status(state=PaneState.DISCONNECTED_CLEAN, current_command="bash", exit_code=0)
        assert _derive_external_status(st, "ssh-shell") == "disconnected"

    def test_unknown_state_is_disconnected(self) -> None:
        st = _status(state=PaneState.UNKNOWN, current_command="")
        assert _derive_external_status(st, "ssh-shell") == "disconnected"

    def test_empty_slot_is_disconnected(self) -> None:
        st = _status(state=PaneState.EMPTY_SLOT, current_command="_placeholder.sh")
        assert _derive_external_status(st, "ssh-shell") == "disconnected"

    def test_stale_alive_remote_with_ssh_is_connected(self) -> None:
        """STALE on a remote profile with ssh foreground stays connected
        (poller heartbeat hiccup, not a real connection drop)."""
        st = _status(state=PaneState.STALE, current_command="ssh", attached=True)
        assert _derive_external_status(st, "ssh-shell") == "connected"

    def test_stale_alive_remote_with_bash_is_dropped(self) -> None:
        """STALE + bash foreground on remote profile is still dropped — the
        SSH process really isn't there, regardless of poller heartbeat."""
        st = _status(state=PaneState.STALE, current_command="bash", attached=True)
        assert _derive_external_status(st, "ssh-shell") == "dropped"

    def test_custom_profile_with_bash_is_connected(self) -> None:
        """Custom profile: we don't know what the user expects the
        foreground to be, so don't speculate. Treat as connected."""
        st = _status(current_command="bash", attached=True)
        assert _derive_external_status(st, "custom") == "connected"

    def test_empty_profile_treated_as_non_remote(self) -> None:
        """Defensive: empty profile string falls through to 'connected'
        for alive+attached panes (matches the custom-profile policy)."""
        st = _status(current_command="bash", attached=True)
        assert _derive_external_status(st, "") == "connected"

    def test_mosh_is_recognized_as_live_command(self) -> None:
        st = _status(current_command="mosh", attached=True)
        assert _derive_external_status(st, "ssh-shell") == "connected"
