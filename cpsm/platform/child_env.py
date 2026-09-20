# -*- coding: utf-8 -*-
"""Environment sanitising for spawned child processes.

Why this exists
---------------
When CPSM runs from a PyInstaller bundle, the bootloader points
``LD_LIBRARY_PATH`` and ``QT_PLUGIN_PATH`` at the bundle's ``_internal``
directory so the frozen app can find its own copies of Qt, OpenSSL and the
rest. That is correct for CPSM itself and **wrong for everything it spawns**.

CPSM spawns terminal emulators, tmux, ssh and launcher scripts. Those are
system binaries built against the system's libraries. Handed CPSM's
environment, they resolve libraries out of the bundle instead, mixing two
incompatible library sets. Measured on a machine with system Qt 6.6.2 and a
bundle carrying Qt 6.11.0::

    LD_LIBRARY_PATH=<bundle>/_internal QT_PLUGIN_PATH=<bundle>/_internal/... konsole --version
    konsole: symbol lookup error: /lib/x86_64-linux-gnu/libQt6Multimedia.so.6:
             undefined symbol: _ZN14QObjectPrivateC2Ei, version Qt_6_PRIVATE_API

Clean environment: ``konsole 24.08.1``. konsole resolves 55 libraries out of
the bundle when the variable leaks through; ssh resolves 11, including
``libcrypto.so.3``.

This is not AppImage-specific. The variables come from PyInstaller, so the same
breakage applies to a bare ``dist/cpsm/cpsm`` run and to any future packaging.
It was previously masked in the AppImage by appimage-builder's ``AppRun`` shim,
which scrubbed child environments — and that shim leaked ~413 MB/day, so it had
to go (see docs/MEMORY-LEAK-INVESTIGATION.md). Removing it exposed a bug that
was always there in non-AppImage runs.

How the restoration works
-------------------------
PyInstaller saves the pre-launch value of each variable it overrides as
``<NAME>_ORIG``. So the correct child value is:

* ``<NAME>_ORIG`` if the bootloader saved one — restore exactly what the user's
  session had, including "it was unset" (saved as an empty string);
* otherwise, drop the variable if it points into the bundle;
* otherwise, leave it alone — it is the user's own setting and not ours to
  discard.

Not frozen (development runs) means nothing was overridden, so the environment
passes through untouched.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

# Variables PyInstaller's bootloader overrides to point at the bundle. Each is
# saved as ``<NAME>_ORIG`` when it had a previous value.
#
# LD_LIBRARY_PATH and QT_PLUGIN_PATH are the two that demonstrably break real
# children. The Qt path variables are included because they steer plugin and
# QML loading the same way and would misdirect any Qt-based child.
_BUNDLE_VARS = (
    "LD_LIBRARY_PATH",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "QML_IMPORT_PATH",
)

_ORIG_SUFFIX = "_ORIG"


def bundle_dir() -> str | None:
    """Return the PyInstaller bundle directory, or None when not frozen."""
    if not getattr(sys, "frozen", False):
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    return str(meipass) if meipass else None


def _points_into(value: str, directory: str) -> bool:
    """True if any ``:``-separated entry of *value* lies under *directory*."""
    if not value or not directory:
        return False
    root = os.path.normpath(directory)
    for part in value.split(os.pathsep):
        if not part:
            continue
        candidate = os.path.normpath(part)
        if candidate == root or candidate.startswith(root + os.sep):
            return True
    return False


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment safe to hand to a spawned system binary.

    Args:
        base: Environment to sanitise.  Defaults to ``os.environ``.

    Returns:
        A new dict.  The input is never mutated.

    When CPSM is not running frozen this is a plain copy: nothing was
    overridden, so there is nothing to undo.
    """
    env = dict(os.environ if base is None else base)

    bundle = bundle_dir()
    if bundle is None:
        # Not frozen: the bootloader never ran, so nothing was overridden and
        # there is nothing to undo.
        #
        # This early return is load-bearing, not a shortcut. Without it the
        # <NAME>_ORIG restore below fires on ANY environment carrying such a
        # key -- including a development shell that happens to be a descendant
        # of an AppImage session, where a STALE LD_LIBRARY_PATH_ORIG survives
        # pointing at an AppDir mount that no longer exists. child_env() would
        # then SET LD_LIBRARY_PATH from it, injecting a dead path into children
        # that the parent did not even have set: the exact inverse of this
        # module's purpose. Confirmed reproducible on a machine whose shell was
        # launched from a previous AppImage.
        return env

    for name in _BUNDLE_VARS:
        orig_key = name + _ORIG_SUFFIX
        if orig_key in env:
            # The bootloader saved the pre-launch value. Restore it verbatim;
            # an empty saved value means the variable was unset originally, and
            # must end up unset rather than set to "".
            original = env.pop(orig_key)
            if original:
                env[name] = original
            else:
                env.pop(name, None)
        elif bundle and _points_into(env.get(name, ""), bundle):
            # No saved original, but the value points into our own bundle, so
            # it is ours and not the user's. Drop it.
            env.pop(name, None)

    # Any remaining *_ORIG keys are PyInstaller bookkeeping. Leaking them is
    # harmless but confusing in a child's environment.
    for key in [k for k in env if k.endswith(_ORIG_SUFFIX)]:
        base_name = key[: -len(_ORIG_SUFFIX)]
        if base_name in _BUNDLE_VARS:
            env.pop(key, None)

    return env
