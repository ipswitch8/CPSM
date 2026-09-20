# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for CPSM — one-folder build.
# Generated for PyInstaller >= 6.5
# Usage: pyinstaller --noconfirm packaging/cpsm.spec

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate the project root (parent of this spec's directory)
# ---------------------------------------------------------------------------
spec_dir = Path(SPECPATH)          # noqa: F821  (SPECPATH injected by PyInstaller)
project_root = spec_dir.parent

# ---------------------------------------------------------------------------
# Build stamp — record which commit this artifact was built from
# ---------------------------------------------------------------------------
# A packaged binary carries no git metadata, so `cpsm --version` could only
# ever print "0.1.0" -- identical for a build made today and one made months
# ago. That made "is the installed binary the one with the fix?" unanswerable
# without unpacking the archive. Writing the commit in at package time makes
# it a one-line question.
#
# The file is gitignored and rewritten on every build; it is an artifact of
# packaging, not source.
import subprocess as _sp


def _git(*args):
    try:
        r = _sp.run(["git", *args], cwd=str(project_root),
                    capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


_commit = _git("rev-parse", "--short", "HEAD") or "unknown"
if _git("status", "--porcelain"):
    _commit += "-dirty"
_built_at = __import__("datetime").datetime.now(
    __import__("datetime").timezone.utc
).strftime("%Y-%m-%dT%H:%M:%SZ")

(project_root / "cpsm" / "_build_stamp.py").write_text(
    "# -*- coding: utf-8 -*-\n"
    '"""Generated at package time by packaging/cpsm.spec. Do not edit."""\n'
    f'COMMIT = "{_commit}"\n'
    f'BUILT_AT = "{_built_at}"\n',
    encoding="utf-8",
)
print(f"build stamp: {_commit} @ {_built_at}")

# ---------------------------------------------------------------------------
# Data files — resources bundled alongside the binary
# ---------------------------------------------------------------------------
datas = [
    (str(project_root / "cpsm" / "resources" / "launcher_templates"), "cpsm/resources/launcher_templates"),
    (str(project_root / "cpsm" / "resources" / "icons"),               "cpsm/resources/icons"),
    (str(project_root / "cpsm" / "resources" / "translations"),        "cpsm/resources/translations"),
]

# ---------------------------------------------------------------------------
# Hidden imports needed at runtime but not detected by static analysis
# ---------------------------------------------------------------------------
hiddenimports = [
    # Pydantic v2 internals
    "pydantic.deprecated.decorator",
    "pydantic.deprecated.class_validators",
    "pydantic.v1",
    # ruamel.yaml
    "ruamel.yaml.comments",
    "ruamel.yaml.representer",
    "ruamel.yaml.constructor",
    # cryptography
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cryptography.hazmat.backends.openssl.backend",
    # keyring backends
    "keyring.backends",
    "keyring.backends.fail",
    "keyring.backends.null",
]

if sys.platform == "linux":
    hiddenimports += [
        "keyring.backends.SecretService",
        "keyring.backends.libsecret",
    ]
elif sys.platform == "win32":
    hiddenimports += [
        "keyring.backends.Windows",
        "keyring.backends.chainer",
    ]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(project_root / "cpsm" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "_tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "IPython",
        "notebook",
        "PIL",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)  # noqa: F821

# ---------------------------------------------------------------------------
# EXE — the thin launcher / entry-point binary
# ---------------------------------------------------------------------------
exe = EXE(           # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # binaries go into the folder, not the exe
    name="cpsm",                    # produces  dist/cpsm/cpsm  (or cpsm.exe on Windows)
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,                   # CLI app — keep console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---------------------------------------------------------------------------
# COLLECT — assemble the one-folder dist
# ---------------------------------------------------------------------------
coll = COLLECT(      # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="cpsm",                    # → dist/cpsm/
)
