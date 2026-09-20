# -*- coding: utf-8 -*-
"""
RemoteControlAuthDialog — guided OAuth bootstrap for ``claude --remote-control``.

Pages (QStackedWidget):

    0. Preflight   — runs the three probes (uname, claude --version,
                     ANTHROPIC_API_KEY); shows pass/warn/fail per check.
    1. Instructions — explains the flow; user picks the forward port; clicks
                      "Open terminal" to spawn an SSH session with the
                      callback port tunnelled.
    2. Polling      — waits for ``~/.claude/.credentials.json`` to appear
                      on the remote.  Refresh-every-3s, user can cancel.
    3. Done         — success message; Accept closes the dialog and the
                      caller flips ``remote_control_enabled`` on.

All SSH probes happen via :class:`RemoteControlService`. The terminal
spawn uses :mod:`cpsm.platform.terminal_launcher` so it's the same code
that other CPSM session launches use.

Tests inject a fake service + a no-op terminal spawner to drive the
dialog end-to-end without touching real hosts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import (
    QObject,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cpsm.services.remote_control_service import (
    MIN_CLAUDE_VERSION,
    PreflightResult,
    RemoteControlService,
)

__all__ = ["RemoteControlAuthDialog"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker — runs preflight off the UI thread
# ---------------------------------------------------------------------------


class _PreflightWorker(QThread):
    """Run :meth:`RemoteControlService.check_preflight` off the UI thread."""

    finished_with_result: Signal = Signal(object)

    def __init__(
        self,
        service: RemoteControlService,
        host: str,
        user: str,
        port: int,
        key_path: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._host = host
        self._user = user
        self._port = port
        self._key_path = key_path

    def run(self) -> None:
        try:
            result = self._service.check_preflight(
                host=self._host,
                user=self._user,
                port=self._port,
                key_path=self._key_path,
            )
        except Exception:
            logger.exception("Preflight worker raised")
            result = PreflightResult(
                ok=False,
                os_kernel="",
                claude_version=None,
                api_key_set=False,
                errors=["Preflight check raised an unexpected error"],
            )
        self.finished_with_result.emit(result)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


# Terminal-spawner callable shape. The wizard hands it the SSH argv + a
# title; production uses the platform terminal launcher; tests pass a
# stub that records calls.
TerminalSpawnFn = Callable[[list[str], str], None]


def _default_spawn_terminal(argv: list[str], title: str) -> None:
    """Default production terminal spawn: pick the first working launcher."""
    from cpsm.platform.terminal_launcher import (
        LocalShellLauncher,
        discover_launchers,
    )

    last_err: Exception | None = None
    for launcher in discover_launchers():
        if isinstance(launcher, LocalShellLauncher):
            continue  # would hijack our own tty
        try:
            launcher.spawn(argv, title=title)
            return
        except (NotImplementedError, Exception) as exc:
            last_err = exc
            continue
    raise RuntimeError(f"Could not spawn an auth terminal; last error: {last_err}")


class RemoteControlAuthDialog(QDialog):
    """Guided OAuth bootstrap wizard for ``claude --remote-control``.

    Parameters
    ----------
    host, user, port, key_path:
        Target host details (extracted from the Connection by the caller).
    service:
        Injectable :class:`RemoteControlService`. Tests pass a fake.
    spawn_terminal:
        Injectable terminal-spawn callable.  Defaults to the platform
        terminal launcher.
    poll_interval_ms:
        How often to check for the credentials file once the user has
        opened the auth terminal.  Default 3000 ms.
    parent:
        Parent QWidget.
    """

    # Result attribute the caller reads after exec(). Mirrors AdoptSessionDialog.
    authenticated: bool

    # Polling state
    _poll_timer: QTimer | None
    _poll_attempts: int

    # Default cap to keep the wizard from polling forever if the user wanders.
    _MAX_POLL_ATTEMPTS = 200  # 200 × 3 s = 10 minutes by default

    def __init__(
        self,
        *,
        host: str,
        user: str,
        port: int = 22,
        key_path: str = "",
        service: RemoteControlService | None = None,
        spawn_terminal: TerminalSpawnFn | None = None,
        poll_interval_ms: int = 3000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("dialog_remote_control_auth")
        self.setAccessibleName("Remote Control Authentication Wizard")
        self.setAccessibleDescription(
            "Guided one-time OAuth /login flow needed before Claude Code "
            "Remote Control will work on the target box."
        )
        self.setWindowTitle("Set up Remote Control")
        self.setModal(True)
        self.resize(560, 380)

        self._host = host
        self._user = user
        self._port = port
        self._key_path = key_path
        self._service = service or RemoteControlService()
        self._spawn_terminal = spawn_terminal or _default_spawn_terminal
        self._poll_interval_ms = poll_interval_ms
        self._poll_timer = None
        self._poll_attempts = 0
        self._preflight_worker: _PreflightWorker | None = None
        self.authenticated = False

        self._build_ui()
        # Kick off preflight as soon as the dialog appears.
        QTimer.singleShot(0, self._start_preflight)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Header — visible across all pages so the user remembers what
        # they're doing.
        header = QLabel(f"<b>Target:</b> {self._user}@{self._host}:{self._port}")
        header.setObjectName("label_rc_target")
        root.addWidget(header)

        self._pages = QStackedWidget()
        self._pages.setObjectName("stack_rc_pages")
        root.addWidget(self._pages, 1)

        self._pages.addWidget(self._build_preflight_page())
        self._pages.addWidget(self._build_instructions_page())
        self._pages.addWidget(self._build_polling_page())
        self._pages.addWidget(self._build_done_page())

        # Dialog buttons. Visibility/enabled state changes per page.
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.setObjectName("buttonbox_rc")
        self._btn_next = QPushButton("Continue")
        self._btn_next.setObjectName("btn_rc_next")
        self._btn_next.setEnabled(False)  # preflight running
        self._buttons.addButton(
            self._btn_next,
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._buttons.rejected.connect(self.reject)
        self._btn_next.clicked.connect(self._on_next_clicked)
        root.addWidget(self._buttons)

    def _build_preflight_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page_rc_preflight")
        layout = QVBoxLayout(page)
        title = QLabel("<h3>Checking the target…</h3>")
        layout.addWidget(title)
        self._lbl_preflight_status = QLabel(
            "Running pre-flight checks: SSH reachability, Claude Code "
            "version, and ANTHROPIC_API_KEY.\n\n"
            "<i>This usually takes 1–2 seconds.</i>"
        )
        self._lbl_preflight_status.setObjectName("label_rc_preflight_status")
        self._lbl_preflight_status.setWordWrap(True)
        layout.addWidget(self._lbl_preflight_status)
        layout.addStretch()
        return page

    def _build_instructions_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page_rc_instructions")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("<h3>Open the authentication terminal</h3>"))
        instructions = QLabel(
            "CPSM will open a new terminal SSHed into the target with the "
            "OAuth callback port forwarded back to your workstation. In "
            "that terminal, run:\n\n"
            "  <code>unset ANTHROPIC_API_KEY</code> <i>(if printed in the warnings)</i>\n"
            "  <code>claude</code>\n"
            "  <code>/login</code>   <i>then choose the claude.ai option</i>\n\n"
            "When Claude prints a <code>http://localhost:PORT/...</code> URL, "
            "paste it into your local browser. The callback is tunnelled back "
            "and /login completes. Detach (Ctrl-b d) and close the terminal "
            "when done — CPSM will notice automatically."
        )
        instructions.setObjectName("label_rc_instructions")
        instructions.setWordWrap(True)
        instructions.setTextFormat(  # render <code>
            instructions.textFormat().__class__(1)
        )
        layout.addWidget(instructions)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Forward port:"))
        self._spin_forward_port = QSpinBox()
        self._spin_forward_port.setObjectName("spin_rc_forward_port")
        self._spin_forward_port.setRange(1024, 65535)
        self._spin_forward_port.setValue(8080)
        self._spin_forward_port.setToolTip(
            "Claude prints the port it wants to use. Default 8080 covers "
            "most setups; change here if /login complains."
        )
        port_row.addWidget(self._spin_forward_port)
        port_row.addStretch()
        self._btn_open_terminal = QPushButton("Open auth terminal")
        self._btn_open_terminal.setObjectName("btn_rc_open_terminal")
        self._btn_open_terminal.clicked.connect(self._on_open_terminal_clicked)
        port_row.addWidget(self._btn_open_terminal)
        layout.addLayout(port_row)
        layout.addStretch()
        return page

    def _build_polling_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page_rc_polling")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("<h3>Waiting for /login to finish…</h3>"))
        self._lbl_poll_status = QLabel(
            "Watching the remote for <code>~/.claude/.credentials.json</code>. "
            "Complete the /login in the auth terminal; this dialog will advance "
            "automatically when CPSM sees the credentials file appear."
        )
        self._lbl_poll_status.setObjectName("label_rc_poll_status")
        self._lbl_poll_status.setWordWrap(True)
        layout.addWidget(self._lbl_poll_status)
        layout.addStretch()
        return page

    def _build_done_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page_rc_done")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("<h3>Remote Control is set up.</h3>"))
        layout.addWidget(
            QLabel(
                "The target box can now run <code>claude --remote-control "
                "&lt;name&gt;</code> and the session will show up in your "
                "claude.ai session list.\n\nCPSM will append the flag "
                "automatically on the next launch of this connection."
            ),
        )
        layout.addStretch()
        return page

    # ------------------------------------------------------------------
    # Page transitions
    # ------------------------------------------------------------------

    def _start_preflight(self) -> None:
        self._preflight_worker = _PreflightWorker(
            self._service,
            self._host,
            self._user,
            self._port,
            self._key_path,
            parent=self,
        )
        self._preflight_worker.finished_with_result.connect(self._on_preflight_done)
        self._preflight_worker.start()

    @Slot(object)
    def _on_preflight_done(self, result: PreflightResult) -> None:
        """Update the preflight page with the probe outcome."""
        lines: list[str] = []
        if result.os_kernel:
            lines.append(f"• OS kernel: <b>{result.os_kernel}</b>")
        if result.claude_version is not None:
            ver_str = ".".join(str(v) for v in result.claude_version)
            min_str = ".".join(str(v) for v in MIN_CLAUDE_VERSION)
            ok_mark = "✓" if result.claude_version >= MIN_CLAUDE_VERSION else "✗"
            lines.append(f"• Claude Code version: <b>{ver_str}</b> (need ≥ {min_str}) {ok_mark}")
        else:
            lines.append("• Claude Code: <b>not found on PATH</b> ✗")
        lines.append(
            "• ANTHROPIC_API_KEY: "
            + (
                "<b>set (will be unset for /login)</b> ⚠"
                if result.api_key_set
                else "<b>not set</b> ✓"
            )
        )

        if result.errors:
            lines.append("")
            for err in result.errors:
                lines.append(f"<span style='color:#c33'>✗ {err}</span>")
        if result.warnings:
            lines.append("")
            for w in result.warnings:
                lines.append(f"<span style='color:#c80'>⚠ {w}</span>")

        if result.ok:
            lines.append("")
            lines.append("<b>Ready to continue.</b>")

        self._lbl_preflight_status.setText("<br>".join(lines))
        self._btn_next.setEnabled(result.ok)

    @Slot()
    def _on_next_clicked(self) -> None:
        """Advance between pages."""
        idx = self._pages.currentIndex()
        if idx == 0:
            self._pages.setCurrentIndex(1)
            self._btn_next.setEnabled(False)  # locked until terminal launched
        elif idx == 1:
            # User reached step 2 without opening the terminal — refuse.
            return
        elif idx == 2:
            return  # polling; ignore
        elif idx == 3:
            self.authenticated = True
            self.accept()

    @Slot()
    def _on_open_terminal_clicked(self) -> None:
        """Spawn the auth terminal and begin polling."""
        forward_port = self._spin_forward_port.value()
        argv = self._service.build_auth_ssh_argv(
            host=self._host,
            user=self._user,
            port=self._port,
            key_path=self._key_path,
            forward_port=forward_port,
        )
        title = f"CPSM RC auth — {self._user}@{self._host}"
        try:
            self._spawn_terminal(argv, title)
        except Exception as exc:
            logger.exception("Failed to spawn auth terminal")
            self._lbl_preflight_status.setText(
                f"<span style='color:#c33'>Could not open a terminal: {exc}</span>"
            )
            return
        self._pages.setCurrentIndex(2)
        self._start_polling()

    def _start_polling(self) -> None:
        self._poll_attempts = 0
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._poll_interval_ms)
        self._poll_timer.timeout.connect(self._check_credentials)
        # Fire once immediately so a fast user gets instant feedback.
        QTimer.singleShot(0, self._check_credentials)
        self._poll_timer.start()

    @Slot()
    def _check_credentials(self) -> None:
        if self._poll_timer is None:
            return
        self._poll_attempts += 1
        try:
            found = self._service.credentials_present(
                host=self._host,
                user=self._user,
                port=self._port,
                key_path=self._key_path,
            )
        except Exception:
            logger.exception("credentials_present probe raised")
            found = False
        if found:
            self._poll_timer.stop()
            self._poll_timer = None
            self._pages.setCurrentIndex(3)
            self._btn_next.setEnabled(True)
            self._btn_next.setText("Done")
            return
        elapsed_s = (self._poll_attempts * self._poll_interval_ms) // 1000
        self._lbl_poll_status.setText(
            "Watching the remote for <code>~/.claude/.credentials.json</code>. "
            "Complete the /login in the auth terminal; this dialog will "
            "advance automatically when CPSM sees the credentials file appear."
            f"<br><br><i>Waited {elapsed_s} s…</i>"
        )
        if self._poll_attempts >= self._MAX_POLL_ATTEMPTS:
            self._poll_timer.stop()
            self._poll_timer = None
            self._lbl_poll_status.setText(
                "<span style='color:#c33'>Gave up waiting after 10 minutes. "
                "Close this dialog and re-run when ready.</span>"
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reject(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        if self._preflight_worker is not None and self._preflight_worker.isRunning():
            self._preflight_worker.requestInterruption()
            self._preflight_worker.wait(100)
        super().reject()
