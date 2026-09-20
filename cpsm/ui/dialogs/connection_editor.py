# -*- coding: utf-8 -*-
"""
cpsm.ui.dialogs.connection_editor — Connection Editor dialog.

Spec sections: §4.5, §2.3

Layout (top → bottom):
  - Identity group: name, id (auto-suggested; locked after create)
  - ConnectionForm (wraps all profile-conditional fields)
  - Test Connection button + result area
  - Member of: <read-only label with group links>
  - Bottom button bar: Save / Cancel

Test Connection behaviour per profile:
  - claude-remote / ssh-shell: SshTestConnectionTask via QThreadPool
  - claude-local / local-shell: Path(project_folder).expanduser().is_dir()
  - custom: TemplateService.render preview
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cpsm.services.template_service import IdentityKeyNotFoundError
from cpsm.ui.widgets.connection_form import ConnectionForm

if TYPE_CHECKING:
    from cpsm.services.key_discovery import KeyCandidate

__all__ = ["ConnectionEditorDialog"]

_ID_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


class _FormConnection:
    """Attribute view over the editor's form dict, for template rendering."""

    def __init__(self, data: dict[str, Any]) -> None:
        for k, v in data.items():
            setattr(self, k, v)
        self.launch_profile = data.get("launch_profile", "custom")


class ConnectionEditorDialog(QDialog):
    """Dialog for creating or editing a Connection record.

    Signals:
        open_group_editor(str): emitted when the user clicks a group link in
            the "Member of" line; carries the group_id.
    """

    open_group_editor: Signal = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        connection_data: dict[str, Any] | None = None,
        groups: list[dict[str, Any]] | None = None,
        available_key_ids: list[str] | None = None,
        available_template_ids: list[str] | None = None,
        available_connection_ids: list[str] | None = None,
        available_layout_ids: list[str] | None = None,
        is_new: bool = True,
        # Injected for tests
        ssh_test_factory: Any | None = None,
        template_service: Any | None = None,
        ssh_keys: list[Any] | None = None,
        key_service: Any | None = None,
        generate_key_dialog_cls: Any | None = None,
        # Injected for tests — see _run_discovery_and_probe / _on_key_probe_result.
        key_discovery_service: Any | None = None,
        key_probe_factory: Any | None = None,
        confirm_pin_fn: Any | None = None,
    ) -> None:
        super().__init__(parent)

        self._is_new = is_new
        self._groups = groups or []
        self._ssh_test_factory = ssh_test_factory
        self._template_service = template_service
        # Needed so the preview can resolve identity_file_ref.  Without it the
        # renderer sees no keys, cannot produce a path for a chosen key, and
        # reports a render error for a connection that is perfectly fine.
        #
        # This list is ALSO the single source of truth for the identity-key
        # combo (see _build_ui below): it is the only key data that stays
        # live across a "+ New Key…" click within this dialog session, so
        # anything else (e.g. a bare list of ids) would silently drift from
        # it the moment a key is generated — which is exactly how the
        # original "dropdown always empty" bug came about (two parallel,
        # independently-populated key lists that disagreed).
        self._ssh_keys: list[Any] = list(ssh_keys or [])
        # Keys generated via "+ New Key…" during this dialog session, not
        # yet part of the document. Exposed via `new_ssh_keys` so the caller
        # (main_window) can append them to doc.ssh_keys before saving —
        # otherwise the saved connection would reference a key id the
        # document never learned about.
        self._new_ssh_keys: list[Any] = []
        # Injectable for tests, same spirit as ssh_test_factory /
        # template_service: avoids ever touching the real ~/.ssh or KeyService.
        self._key_service = key_service
        self._generate_key_dialog_cls = generate_key_dialog_cls
        # Injectable so tests never invoke the real KeyDiscoveryService
        # (which defaults to scanning the real ~/.ssh) or launch a real ssh
        # probe subprocess. See _run_discovery_and_probe.
        self._key_discovery_service = key_discovery_service
        self._key_probe_factory = key_probe_factory
        self._confirm_pin_fn = confirm_pin_fn
        self._connection_data: dict[str, Any] = dict(connection_data or {})
        self._closed = False  # Fix #11 — guard against post-close slot calls

        self.setObjectName("dlg_connection_editor")
        self.setAccessibleName("Connection Editor Dialog")
        self.setAccessibleDescription("Dialog for creating or editing a CPSM connection")
        self.setWindowTitle("New Connection" if is_new else "Edit Connection")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(640)
        self.setMinimumHeight(600)

        self._build_ui(
            available_key_ids=available_key_ids or [],
            available_template_ids=available_template_ids or [],
            available_connection_ids=available_connection_ids or [],
        )

        # Populate with existing data
        if connection_data:
            self._populate(connection_data)
        else:
            self._update_id_from_name("")

        self._update_member_of_label()
        self._on_form_validation_changed(self._form.is_valid())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(
        self,
        available_key_ids: list[str],
        available_template_ids: list[str],
        available_connection_ids: list[str],
    ) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # ---- Identity group ----
        grp_identity = QGroupBox("Identity")
        grp_identity.setObjectName("grp_identity")
        grp_identity.setAccessibleName("Identity group box")
        id_form = QFormLayout(grp_identity)
        id_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._edit_name = QLineEdit()
        self._edit_name.setObjectName("edit_name")
        self._edit_name.setAccessibleName("Connection name field")
        self._edit_name.setAccessibleDescription("Human-readable display name for this connection")
        self._edit_name.setPlaceholderText("e.g. WebApp Frontend (prod)")
        self._err_name = QLabel()
        self._err_name.setObjectName("error_name")
        self._err_name.setAccessibleName("Name error label")
        self._err_name.setStyleSheet("color: red;")
        self._err_name.setVisible(False)
        id_form.addRow("Name *", self._edit_name)
        id_form.addRow("", self._err_name)

        self._edit_id = QLineEdit()
        self._edit_id.setObjectName("edit_id")
        self._edit_id.setAccessibleName("Connection ID field")
        self._edit_id.setAccessibleDescription(
            "Unique slug ID; auto-suggested from name; locked after first save"
        )
        self._edit_id.setPlaceholderText("e.g. webapp-frontend-prod")
        if not self._is_new:
            self._edit_id.setReadOnly(True)
            self._edit_id.setToolTip("ID is locked after creation")
        self._err_id = QLabel()
        self._err_id.setObjectName("error_id")
        self._err_id.setAccessibleName("ID error label")
        self._err_id.setStyleSheet("color: red;")
        self._err_id.setVisible(False)
        id_form.addRow("ID *", self._edit_id)
        id_form.addRow("", self._err_id)

        root.addWidget(grp_identity)

        # ---- ConnectionForm ----
        # Key ids for the identity combo: prefer self._ssh_keys (see the
        # comment in __init__ for why it is the source of truth). The
        # `available_key_ids` ctor param is kept only as a fallback for
        # call sites that pass bare ids without the full SshKey objects —
        # it is never used *alongside* ssh_keys, only instead of it, so
        # there is exactly one answer for "what ids does the combo show"
        # at any given time.
        key_ids = (
            [k.id for k in self._ssh_keys] if self._ssh_keys else list(available_key_ids)
        )
        self._form = ConnectionForm(
            self,
            available_key_ids=key_ids,
            available_template_ids=available_template_ids,
        )
        self._form.populate_connections(available_connection_ids)
        self._form.setObjectName("connection_form_embed")
        root.addWidget(self._form, stretch=1)

        # ---- Test Connection row ----
        test_row = QHBoxLayout()

        self._btn_test = QPushButton("Test Connection")
        self._btn_test.setObjectName("btn_test_connection")
        self._btn_test.setAccessibleName("Test connection button")
        self._btn_test.setAccessibleDescription(
            "Test the connection using the current profile settings"
        )
        self._btn_test.clicked.connect(self._on_test_connection)
        test_row.addWidget(self._btn_test)

        self._lbl_test_result = QLabel()
        self._lbl_test_result.setObjectName("lbl_test_result")
        self._lbl_test_result.setAccessibleName("Test result label")
        self._lbl_test_result.setAccessibleDescription("Shows the result of the connection test")
        self._lbl_test_result.setWordWrap(True)
        test_row.addWidget(self._lbl_test_result, stretch=1)
        root.addLayout(test_row)

        # Preview area for custom profile test
        self._txt_test_preview = QPlainTextEdit()
        self._txt_test_preview.setObjectName("txt_test_preview")
        self._txt_test_preview.setAccessibleName("Test preview text area")
        self._txt_test_preview.setAccessibleDescription(
            "Shows rendered template preview for custom profile"
        )
        self._txt_test_preview.setReadOnly(True)
        font = self._txt_test_preview.font()
        font.setFamily("Monospace")
        self._txt_test_preview.setFont(font)
        self._txt_test_preview.setMaximumHeight(120)
        self._txt_test_preview.setVisible(False)
        root.addWidget(self._txt_test_preview)

        # ---- Member of line ----
        member_row = QHBoxLayout()
        lbl_member_title = QLabel("Member of:")
        lbl_member_title.setObjectName("lbl_member_of_title")
        lbl_member_title.setAccessibleName("Member of title label")
        member_row.addWidget(lbl_member_title)

        self._lbl_member_of = QLabel()
        self._lbl_member_of.setObjectName("lbl_member_of")
        self._lbl_member_of.setAccessibleName("Member of groups label")
        self._lbl_member_of.setAccessibleDescription(
            "Groups that contain this connection; click to open the group editor"
        )
        self._lbl_member_of.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_member_of.setOpenExternalLinks(False)
        self._lbl_member_of.linkActivated.connect(self._on_group_link_clicked)
        member_row.addWidget(self._lbl_member_of, stretch=1)
        root.addLayout(member_row)

        # ---- Bottom buttons ----
        self._btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self._btn_box.setObjectName("btn_box")

        self._btn_save = self._btn_box.button(QDialogButtonBox.StandardButton.Save)
        assert self._btn_save is not None
        self._btn_save.setObjectName("btn_save")
        self._btn_save.setAccessibleName("Save button")
        self._btn_save.setAccessibleDescription("Save the connection and close the dialog")

        btn_cancel = self._btn_box.button(QDialogButtonBox.StandardButton.Cancel)
        assert btn_cancel is not None
        btn_cancel.setObjectName("btn_cancel")
        btn_cancel.setAccessibleName("Cancel button")
        btn_cancel.setAccessibleDescription("Discard changes and close the dialog")

        self._btn_box.accepted.connect(self._on_save)
        self._btn_box.rejected.connect(self.reject)
        root.addWidget(self._btn_box)

        # Wire signals
        self._edit_name.textChanged.connect(self._on_name_changed)
        self._edit_name.editingFinished.connect(self._validate_name)
        self._edit_id.editingFinished.connect(self._validate_id)
        self._form.validation_changed.connect(self._on_form_validation_changed)
        self._form.profile_changed.connect(self._on_profile_changed)
        self._form.remote_control_auth_requested.connect(
            self._on_remote_control_auth_requested
        )
        self._form.new_key_requested.connect(self._on_new_key_requested)

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _populate(self, data: dict[str, Any]) -> None:
        """Fill in identity fields and the embedded form."""
        self._edit_name.setText(str(data.get("name", "") or ""))
        self._edit_id.setText(str(data.get("id", "") or ""))
        self._form.load_data(data)
        self._validate_name()
        self._validate_id()

    def _update_id_from_name(self, name: str) -> None:
        """Auto-suggest an ID slug from name (only when editing a new connection)."""
        if not self._is_new or self._edit_id.isReadOnly():
            return
        slug = name.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        if slug and len(slug) >= 2:
            # Truncate to 63 chars
            self._edit_id.setText(slug[:63])
        else:
            self._edit_id.setText("")

    def _update_member_of_label(self) -> None:
        """Update the Member of label based on current connection ID."""
        conn_id = self._edit_id.text().strip()
        member_groups = [g for g in self._groups if conn_id in g.get("members", [])]
        if member_groups:
            links = []
            for g in member_groups:
                gid = g.get("id", "")
                gname = g.get("name", gid)
                links.append(f'<a href="{gid}">{gname}</a>')
            self._lbl_member_of.setText(", ".join(links))
        else:
            self._lbl_member_of.setText("(none)")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_name(self) -> None:
        name = self._edit_name.text().strip()
        if not name:
            self._set_widget_error(self._edit_name, self._err_name, "Name is required")
        else:
            self._set_widget_error(self._edit_name, self._err_name, "")
        self._update_save_button()

    def _validate_id(self) -> None:
        conn_id = self._edit_id.text().strip()
        if not conn_id:
            self._set_widget_error(self._edit_id, self._err_id, "ID is required")
        elif not _ID_SLUG_RE.match(conn_id):
            self._set_widget_error(
                self._edit_id,
                self._err_id,
                "ID must match ^[a-z0-9][a-z0-9-]{1,62}$",
            )
        else:
            self._set_widget_error(self._edit_id, self._err_id, "")
        self._update_save_button()

    def _set_widget_error(self, widget: QWidget, err_label: QLabel, error: str) -> None:
        if error:
            widget.setStyleSheet("border: 1px solid red;")
            err_label.setText(error)
            err_label.setVisible(True)
        else:
            widget.setStyleSheet("")
            err_label.setText("")
            err_label.setVisible(False)

    def _has_identity_errors(self) -> bool:
        return bool(self._err_name.text()) or bool(self._err_id.text())

    def _update_save_button(self) -> None:
        """Enable/disable Save and update tooltip."""
        form_valid = self._form.is_valid()
        identity_ok = not self._has_identity_errors()
        name_ok = bool(self._edit_name.text().strip())
        id_ok = bool(self._edit_id.text().strip())
        can_save = form_valid and identity_ok and name_ok and id_ok

        self._btn_save.setEnabled(can_save)

        if not can_save:
            issues: list[str] = []
            if not name_ok or self._err_name.text():
                issues.append(f"Name: {self._err_name.text() or 'required'}")
            if not id_ok or self._err_id.text():
                issues.append(f"ID: {self._err_id.text() or 'required'}")
            for field, err in self._form.errors().items():
                issues.append(f"{field}: {err}")
            self._btn_save.setToolTip("Outstanding issues:\n" + "\n".join(issues))
        else:
            self._btn_save.setToolTip("")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_name_changed(self, text: str) -> None:
        self._update_id_from_name(text)
        self._validate_name()

    @Slot(bool)
    def _on_form_validation_changed(self, valid: bool) -> None:
        self._update_save_button()

    @Slot(str)
    def _on_profile_changed(self, profile: str) -> None:
        # Show/hide preview area based on profile
        self._txt_test_preview.setVisible(False)
        self._lbl_test_result.setText("")

    @Slot()
    def _on_remote_control_auth_requested(self) -> None:
        """Open the Remote Control auth wizard for the connection currently
        being edited. Pulls host/user/port/key from the form (not the saved
        document) so the user can run the wizard before saving."""
        data = self._form.collect_data()
        host = str(data.get("host") or "").strip()
        user = str(data.get("user") or "").strip()
        port = int(data.get("port") or 22)
        if not host or not user:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Set up Remote Control",
                "Fill in the host and user fields first — the wizard needs "
                "them to SSH into the target.",
            )
            return

        # Key path: resolve identity_file_ref through self._ssh_keys the same
        # way Test Connection does (see _resolve_identity_path). Falls back
        # to "" — letting ssh use its default key resolution (~/.ssh/config,
        # ssh-agent, etc.) — when no key is chosen yet, or the chosen ref
        # does not resolve to a usable path; either way the wizard's
        # bootstrap probe still has *something* to try.
        identity_ref = str(data.get("identity_file_ref") or "").strip()
        key_path = ""
        if identity_ref:
            try:
                key_path = str(self._resolve_identity_path(identity_ref))
            except IdentityKeyNotFoundError:
                key_path = ""

        from cpsm.ui.dialogs.remote_control_auth import RemoteControlAuthDialog
        dlg = RemoteControlAuthDialog(
            host=host, user=user, port=port, key_path=key_path,
            parent=self,
        )
        dlg.exec()
        # If the user completed auth, optimistically flip the form's
        # checkbox on so saving the connection persists the intent.
        if getattr(dlg, "authenticated", False):
            self._form._chk_remote_control_enabled.setChecked(True)

    @Slot()
    def _on_new_key_requested(self) -> None:
        """Open GenerateKeyDialog to create a new SSH key for this
        connection.  On success the key is appended to self._ssh_keys (the
        single source of truth for the identity combo — see __init__), the
        combo is repopulated, and the new key is selected.  On cancel
        nothing changes.

        The dialog class and KeyService are both injectable via the
        constructor (`generate_key_dialog_cls`, `key_service`) so tests
        never generate a real key pair or touch the real ~/.ssh.
        """
        key_service = self._key_service
        if key_service is None:
            from cpsm.services.key_service import KeyService

            key_service = KeyService()

        dialog_cls = self._generate_key_dialog_cls
        if dialog_cls is None:
            from cpsm.ui.dialogs.generate_key import GenerateKeyDialog

            dialog_cls = GenerateKeyDialog

        default_dir = Path("~/.ssh").expanduser()
        dlg = dialog_cls(key_service, default_dir, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_key = getattr(dlg, "created_key", None)
        if new_key is None:
            return

        existing_ids = {k.id for k in self._ssh_keys}
        if new_key.id not in existing_ids:
            self._ssh_keys.append(new_key)
            self._new_ssh_keys.append(new_key)

        self._form.populate_keys([k.id for k in self._ssh_keys])
        self._form._combo_identity_file_ref.setCurrentText(new_key.id)

    @Slot(str)
    def _on_group_link_clicked(self, group_id: str) -> None:
        self.open_group_editor.emit(group_id)

    @Slot()
    def _on_save(self) -> None:
        self._validate_name()
        self._validate_id()
        if self._has_identity_errors():
            return
        if not self._form.is_valid():
            return
        self.accept()

    @Slot()
    def _on_test_connection(self) -> None:
        """Run the appropriate test for the current profile."""
        profile = self._form.current_profile
        data = self._form.collect_data()

        self._lbl_test_result.setText("Testing…")
        self._txt_test_preview.setVisible(False)

        if profile in {"claude-remote", "ssh-shell"}:
            self._run_ssh_test(data)
        elif profile in {"claude-local", "local-shell"}:
            self._run_local_test(data)
        elif profile == "custom":
            self._run_custom_preview(data)

    def _resolve_identity_path(self, ref: str) -> Path:
        """Resolve ``identity_file_ref`` (an ``SshKey`` id slug) to its
        on-disk private key path via ``self._ssh_keys``, expanding ``~``.

        Mirrors ``cpsm.services.template_service.TemplateService.render``
        (see template_service.py:391-410): the id itself is never a
        filesystem path, so ``Path(ref)`` directly on the slug is always
        wrong. Deliberately keys off the resolved PATH rather than the id
        lookup, so an empty/None ``ssh_keys`` and a matching key with a
        blank ``private_path`` are treated as the same failure as a ref that
        matches nothing — the id lookup alone let those two degrade to an
        unpinned connection instead of raising.

        Raises:
            IdentityKeyNotFoundError: *ref* resolves to no usable private
                key path. Same exception class ``template_service`` raises,
                so callers can handle both call sites identically.
        """
        private_path = ""
        for k in self._ssh_keys:
            if getattr(k, "id", None) == ref:
                private_path = str(getattr(k, "private_path", "") or "")
                break
        if not private_path:
            available = (
                ", ".join(sorted(str(getattr(k, "id", "")) for k in self._ssh_keys))
                if self._ssh_keys
                else "(none defined)"
            )
            raise IdentityKeyNotFoundError(
                f"Connection references SSH key id {ref!r}, which has no "
                f"usable private key path. Available key ids: {available}."
            )
        return Path(private_path).expanduser()

    def _run_ssh_test(self, data: dict[str, Any]) -> None:
        """Test SSH connectivity using SshTestConnectionTask.

        ``identity_file_ref`` is an id slug into ``self._ssh_keys`` (see
        cpsm/data/schema.py's ``_validate_id_slug``), never a filesystem path
        directly — resolve it via ``_resolve_identity_path`` first. When
        there is no ref, or the ref does not resolve to a usable key, fall
        through to key discovery + probing (_run_discovery_and_probe) rather
        than testing unpinned or crashing.
        """
        host = data.get("host") or ""
        user = data.get("user") or ""
        port = int(data.get("port") or 22)
        identity_ref = str(data.get("identity_file_ref") or "").strip()

        if not host or not user:
            self._lbl_test_result.setText("⚠  Host and User are required for SSH test")
            return

        identity_file: Path | None = None
        if identity_ref:
            try:
                identity_file = self._resolve_identity_path(identity_ref)
            except IdentityKeyNotFoundError:
                identity_file = None

        if identity_file is None:
            # No ref at all, or a ref that does not resolve — both mean
            # "no usable pinned key": try to discover and probe one instead
            # of testing unpinned (which would defeat IdentitiesOnly=yes,
            # see ssh_binary.py:205) or crashing on a bogus Path.
            self._run_discovery_and_probe(host, user, port)
            return

        if self._ssh_test_factory is not None:
            # Injected factory for testing
            self._ssh_test_factory(
                host=host,
                user=user,
                port=port,
                identity_file=identity_file,
                on_result=self._on_ssh_test_result,
            )
            return

        try:
            from PySide6.QtCore import QThreadPool

            from cpsm.platform.ssh_binary import SshBinary
            from cpsm.workers.ssh_worker import SshTestConnectionTask

            ssh_bin = SshBinary.detect()
            task = SshTestConnectionTask(
                ssh_binary=ssh_bin,
                host=host,
                user=user,
                port=port,
                identity_file=identity_file,
            )
            task.signals.finished.connect(
                self._on_ssh_test_result, Qt.ConnectionType.QueuedConnection
            )
            QThreadPool.globalInstance().start(task)
        except Exception as exc:
            self._lbl_test_result.setText(f"✗  Error: {exc}")

    # ------------------------------------------------------------------
    # Key discovery + probe-based auto-pin
    # ------------------------------------------------------------------

    def _run_discovery_and_probe(self, host: str, user: str, port: int) -> None:
        """Discover candidate keys for *host* and probe them in ranked order.

        Discovery (``KeyDiscoveryService.discover``) is pure filesystem logic
        with no network I/O; probing each candidate over ssh is blocking
        network I/O and MUST run off the GUI thread — see
        ``KeyDiscoveryProbeTask`` in ``cpsm.workers.ssh_worker``.

        Both the discovery service and the probe task are injectable
        (``key_discovery_service`` / ``key_probe_factory``) so tests never
        reach the real ``~/.ssh`` or launch a real ssh subprocess.
        """
        discovery = self._key_discovery_service
        if discovery is None:
            from cpsm.services.key_discovery import KeyDiscoveryService

            discovery = KeyDiscoveryService()

        candidates = discovery.discover(host, user=user or None)
        if not candidates:
            self._lbl_test_result.setStyleSheet("color: red;")
            self._lbl_test_result.setText(
                "✗  No pinned key, and no candidate SSH key found for this host"
            )
            return

        self._lbl_test_result.setText("Testing… (looking for a working SSH key)")

        def _handle_result(candidate: KeyCandidate | None) -> None:
            self._on_key_probe_result(candidate, host, user, port)

        if self._key_probe_factory is not None:
            # Injected factory for testing
            self._key_probe_factory(
                host=host,
                user=user,
                port=port,
                candidates=candidates,
                on_result=_handle_result,
            )
            return

        try:
            from PySide6.QtCore import QThreadPool

            from cpsm.platform.ssh_binary import SshBinary
            from cpsm.workers.ssh_worker import KeyDiscoveryProbeTask

            ssh_bin = SshBinary.detect()
            task = KeyDiscoveryProbeTask(
                ssh_binary=ssh_bin,
                host=host,
                user=user,
                port=port,
                candidates=candidates,
            )
            task.signals.finished.connect(
                _handle_result, Qt.ConnectionType.QueuedConnection
            )
            QThreadPool.globalInstance().start(task)
        except Exception as exc:
            self._lbl_test_result.setStyleSheet("color: red;")
            self._lbl_test_result.setText(f"✗  Error: {exc}")

    @Slot(object)
    def _on_key_probe_result(
        self, candidate: "KeyCandidate | None", host: str, user: str, port: int
    ) -> None:
        """Handle the outcome of a key-discovery probe.

        A ``None`` candidate means no candidate authenticated. Otherwise the
        user is prompted (see _show_pin_confirm_dialog) before anything is
        written to the in-memory document — declining leaves it untouched.
        """
        try:
            if not self.isVisible() or self._closed:
                return
        except RuntimeError:
            # C++ widget already deleted
            return

        if candidate is None:
            self._lbl_test_result.setStyleSheet("color: red;")
            self._lbl_test_result.setText(
                "✗  No working SSH key found for this host among the "
                "candidates CPSM could discover"
            )
            return

        if not self._show_pin_confirm_dialog(candidate):
            self._lbl_test_result.setStyleSheet("color: orange;")
            self._lbl_test_result.setText(
                f"⚠  Found a working key ({candidate.private_path}) but "
                "declined to pin it"
            )
            return

        key_id = self._pin_discovered_key(candidate)
        self._lbl_test_result.setStyleSheet("color: green;")
        self._lbl_test_result.setText(
            f"✓  Connection successful — pinned discovered key '{key_id}'"
        )

    def _show_pin_confirm_dialog(self, candidate: "KeyCandidate") -> bool:
        """Ask the user whether to pin *candidate* to this connection.

        Injectable via ``confirm_pin_fn`` (constructor) for tests. The real
        dialog carries a stable objectName/accessible name+description per
        the project's automation-friendliness rule (pywinauto/FlaUI parity
        for native UI), and states how the key was identified
        (``candidate.source`` / ``candidate.comment``) so the user is not
        asked to trust an opaque decision.
        """
        if self._confirm_pin_fn is not None:
            return self._confirm_pin_fn(self, candidate)

        box = QMessageBox(self)
        box.setObjectName("dlg_confirm_pin_discovered_key")
        box.setAccessibleName("Confirm pin discovered SSH key dialog")
        box.setAccessibleDescription(
            "Asks whether to pin the SSH key CPSM discovered works for this "
            "connection's host, identifying how the key was found"
        )
        box.setWindowTitle("Use discovered SSH key?")
        box.setIcon(QMessageBox.Icon.Question)
        source_desc = {
            "ssh_config": "your ~/.ssh/config",
            "pub_comment_user_host": "a public key comment matching user@host",
            "pub_comment_host": "a public key comment matching the host",
            "default": "a conventional default key filename",
        }.get(candidate.source, candidate.source)
        comment_line = f"\nComment: {candidate.comment}" if candidate.comment else ""
        box.setText(
            "CPSM found a working SSH key for this connection by checking "
            f"{source_desc}:\n\n{candidate.private_path}{comment_line}\n\n"
            "Pin this key to the connection?"
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == int(QMessageBox.StandardButton.Yes)

    def _pin_discovered_key(self, candidate: "KeyCandidate") -> str:
        """Ensure an ``ssh_keys`` entry exists for *candidate* and pin it.

        Reuses an existing entry (matched by resolved private_path) instead
        of creating a duplicate. A newly-created entry is appended to both
        ``self._ssh_keys`` (source of truth for the identity combo — see
        __init__) and ``self._new_ssh_keys`` (so the caller persists it into
        the document, mirroring ``_on_new_key_requested``).
        """
        from cpsm.data.schema import SshKey

        target = str(candidate.private_path)
        existing = next(
            (k for k in self._ssh_keys if str(getattr(k, "private_path", "")) == target),
            None,
        )
        if existing is not None:
            key_id = str(existing.id)
        else:
            key_id = self._suggest_key_id(candidate)
            public_path = (
                str(candidate.public_path) if candidate.public_path else target + ".pub"
            )
            new_key = SshKey(
                id=key_id,
                name=candidate.comment or candidate.private_path.name,
                type=self._infer_key_type(candidate),
                private_path=target,
                public_path=public_path,
            )
            self._ssh_keys.append(new_key)
            self._new_ssh_keys.append(new_key)
            self._form.populate_keys([k.id for k in self._ssh_keys])

        self._form._combo_identity_file_ref.setCurrentText(key_id)
        return key_id

    @staticmethod
    def _infer_key_type(candidate: "KeyCandidate") -> str:
        """Best-effort algorithm for a discovered key: ed25519 | rsa | ecdsa.

        This used to be hardcoded to "ed25519" for every discovered key,
        which quietly mislabels an RSA key in the user's config — and an old
        RSA key is a very likely thing to find, since the whole reason this
        feature exists is hosts whose working key predates the current
        convention.

        The public key file states its own algorithm in its first field
        (``ssh-ed25519 AAAA...``, ``ssh-rsa AAAA...``,
        ``ecdsa-sha2-nistp256 AAAA...``), which is authoritative, so prefer
        that. Only the .pub is read — never the private key. Fall back to
        the filename heuristic already used for system-key import in
        main_window when the .pub is missing or unreadable, and to ed25519
        (the type CPSM itself generates) when the name says nothing.
        """
        pub = candidate.public_path
        if pub is not None:
            try:
                first_line = pub.read_text(encoding="utf-8", errors="replace").split(
                    "\n", 1
                )[0]
            except OSError:
                first_line = ""
            algo = first_line.split(None, 1)[0].lower() if first_line.strip() else ""
            if algo.startswith("ecdsa"):
                return "ecdsa"
            if algo == "ssh-rsa" or algo.startswith("rsa"):
                return "rsa"
            if "ed25519" in algo:
                return "ed25519"

        name = candidate.private_path.name.lower()
        if "ecdsa" in name:
            return "ecdsa"
        if "rsa" in name:
            return "rsa"
        return "ed25519"

    def _suggest_key_id(self, candidate: "KeyCandidate") -> str:
        """Derive a unique id slug (``_ID_SLUG_RE``) for a discovered key."""
        base = candidate.comment or candidate.private_path.stem
        slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:63]
        if len(slug) < 2 or not _ID_SLUG_RE.match(slug):
            slug = "discovered-key"

        existing_ids = {k.id for k in self._ssh_keys}
        if slug not in existing_ids:
            return slug
        n = 2
        while True:
            suffix = f"-{n}"
            candidate_id = slug[: 63 - len(suffix)] + suffix
            if candidate_id not in existing_ids:
                return candidate_id
            n += 1

    @Slot(bool, str)
    def _on_ssh_test_result(self, success: bool, message: str) -> None:
        try:
            if not self.isVisible() or self._closed:
                return
            if success:
                self._lbl_test_result.setStyleSheet("color: green;")
                self._lbl_test_result.setText("✓  Connection successful")
            else:
                self._lbl_test_result.setStyleSheet("color: red;")
                self._lbl_test_result.setText(f"✗  {message}")
        except RuntimeError:
            # C++ widget already deleted
            pass

    def closeEvent(self, event: Any) -> None:
        """Mark dialog as closed to guard async slot callbacks."""
        self._closed = True
        super().closeEvent(event)

    def _run_local_test(self, data: dict[str, Any]) -> None:
        """Test that project_folder exists locally."""
        folder = data.get("project_folder") or ""
        if not folder:
            self._lbl_test_result.setStyleSheet("color: orange;")
            self._lbl_test_result.setText("⚠  No project folder specified")
            return
        path = Path(folder).expanduser()
        if path.is_dir():
            self._lbl_test_result.setStyleSheet("color: green;")
            self._lbl_test_result.setText(f"✓  Directory exists: {path}")
        else:
            self._lbl_test_result.setStyleSheet("color: red;")
            self._lbl_test_result.setText(f"✗  Directory not found: {path}")

    def _run_custom_preview(self, data: dict[str, Any]) -> None:
        """Render the template and show it in the preview area."""
        template_id = data.get("custom_template_id") or ""
        if not template_id:
            self._lbl_test_result.setStyleSheet("color: orange;")
            self._lbl_test_result.setText("⚠  No template selected")
            return

        if self._template_service is not None:
            try:
                rendered = self._template_service.render(
                    data.get("launch_profile", "custom"),
                    _FormConnection(data),
                    ssh_keys=self._ssh_keys,
                )
                self._txt_test_preview.setPlainText(rendered)
                self._txt_test_preview.setVisible(True)
                self._lbl_test_result.setStyleSheet("color: green;")
                self._lbl_test_result.setText("✓  Template rendered (preview above)")
            except Exception as exc:
                self._lbl_test_result.setStyleSheet("color: red;")
                self._lbl_test_result.setText(f"✗  Render error: {exc}")
            return

        try:
            from cpsm.services.template_service import TemplateService

            svc = TemplateService()

            # Build a minimal connection-like object
            class _FakeConn:
                def __init__(self, d: dict[str, Any]) -> None:
                    for k, v in d.items():
                        setattr(self, k, v)
                    self.launch_profile = d.get("launch_profile", "custom")

            rendered = svc.render(
                profile=data.get("launch_profile", "custom"),
                connection=_FakeConn(data),
                ssh_keys=self._ssh_keys,
            )
            self._txt_test_preview.setPlainText(rendered)
            self._txt_test_preview.setVisible(True)
            self._lbl_test_result.setStyleSheet("color: green;")
            self._lbl_test_result.setText("✓  Template rendered (preview above)")
        except Exception as exc:
            self._lbl_test_result.setStyleSheet("color: red;")
            self._lbl_test_result.setText(f"✗  Render error: {exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_connection_data(self) -> dict[str, Any]:
        """Return the collected connection data including name and id."""
        data = self._form.collect_data()
        data["name"] = self._edit_name.text().strip() or None
        data["id"] = self._edit_id.text().strip()
        return data

    @property
    def new_ssh_keys(self) -> list[Any]:
        """SSH keys generated via "+ New Key…" during this dialog session
        that are not yet part of the document. The caller (main_window)
        must append these to document.ssh_keys before saving — otherwise
        the saved connection would reference a key id the document never
        learned about."""
        return list(self._new_ssh_keys)

    @property
    def connection_name(self) -> str:
        return self._edit_name.text().strip()

    @property
    def connection_id(self) -> str:
        return self._edit_id.text().strip()
