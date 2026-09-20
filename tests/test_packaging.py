# -*- coding: utf-8 -*-
"""
Tests for Phase 21 — Packaging artefacts.

Validates that:
  - packaging/cpsm.spec is syntactically valid Python (PyInstaller can load it).
  - packaging/wix/cpsm.wxs is valid XML.
  - packaging/AppImageBuilder.yml is valid YAML.

These are fast unit tests; no actual build is performed here.
"""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PACKAGING_DIR = Path(__file__).parent.parent / "packaging"


# ---------------------------------------------------------------------------
# cpsm.spec — PyInstaller spec
# ---------------------------------------------------------------------------


class TestCpsmSpec:
    """The .spec file must be syntactically valid Python and contain the
    expected PyInstaller building-block names."""

    SPEC_PATH = PACKAGING_DIR / "cpsm.spec"

    def test_spec_file_exists(self) -> None:
        assert self.SPEC_PATH.is_file(), f"Spec not found: {self.SPEC_PATH}"

    def test_spec_is_valid_python_syntax(self) -> None:
        """ast.parse raises SyntaxError on malformed Python."""
        source = self.SPEC_PATH.read_text(encoding="utf-8")
        try:
            ast.parse(source)
        except SyntaxError as exc:
            pytest.fail(f"cpsm.spec has a syntax error: {exc}")

    def test_spec_references_main_entrypoint(self) -> None:
        """The spec must reference cpsm/__main__.py as the analysis target."""
        source = self.SPEC_PATH.read_text(encoding="utf-8")
        assert "__main__.py" in source, "Spec must list cpsm/__main__.py as entrypoint"

    def test_spec_references_resources(self) -> None:
        """The spec must bundle all three resource directories."""
        source = self.SPEC_PATH.read_text(encoding="utf-8")
        for resource in ("launcher_templates", "icons", "translations"):
            assert resource in source, f"Spec missing resource: {resource}"

    def test_spec_has_analysis_exe_collect(self) -> None:
        """The spec must contain Analysis, EXE and COLLECT calls."""
        source = self.SPEC_PATH.read_text(encoding="utf-8")
        for symbol in ("Analysis", "EXE", "COLLECT", "PYZ"):
            assert symbol in source, f"Spec missing PyInstaller symbol: {symbol}"

    def test_spec_output_name_is_cpsm(self) -> None:
        """EXE and COLLECT must both use name='cpsm'."""
        source = self.SPEC_PATH.read_text(encoding="utf-8")
        assert 'name="cpsm"' in source or "name='cpsm'" in source, (
            "Spec EXE/COLLECT must use name='cpsm'"
        )

    def test_spec_has_pydantic_hidden_import(self) -> None:
        source = self.SPEC_PATH.read_text(encoding="utf-8")
        assert "pydantic" in source, "Spec must list pydantic hidden imports"

    def test_spec_has_keyring_hidden_import(self) -> None:
        source = self.SPEC_PATH.read_text(encoding="utf-8")
        assert "keyring" in source, "Spec must list keyring hidden imports"

    def test_spec_is_one_folder_build(self) -> None:
        """exclude_binaries=True in EXE + COLLECT present = one-folder mode."""
        source = self.SPEC_PATH.read_text(encoding="utf-8")
        assert "exclude_binaries=True" in source, (
            "Spec must use exclude_binaries=True for one-folder build"
        )
        assert "COLLECT" in source, "Spec must have COLLECT for one-folder build"


# ---------------------------------------------------------------------------
# cpsm.wxs — WiX MSI source
# ---------------------------------------------------------------------------


class TestCpsmWxs:
    """The .wxs file must be valid XML with expected WiX elements."""

    WXS_PATH = PACKAGING_DIR / "wix" / "cpsm.wxs"

    def test_wxs_file_exists(self) -> None:
        assert self.WXS_PATH.is_file(), f"WXS not found: {self.WXS_PATH}"

    # ------------------------------------------------------------------
    # WiX .wxs files contain WiX-preprocessor processing instructions
    # such as  <?define Foo = "Bar" ?> that Python's xml.etree parser
    # rejects (the = sign inside the PI target is not well-formed per
    # the strict XML 1.0 PI syntax it implements).  Strip them before
    # handing the source to ET so we can still validate the document
    # structure.
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_wix_pis(text: str) -> str:
        """Remove WiX preprocessor processing instructions (<?define … ?>)."""
        import re

        return re.sub(r"<\?[a-z]+[^?]*\?>", "", text, flags=re.DOTALL)

    def _parse_wxs(self) -> ET.ElementTree:
        raw = self.WXS_PATH.read_text(encoding="utf-8")
        clean = self._strip_wix_pis(raw)
        return ET.ElementTree(ET.fromstring(clean))

    def test_wxs_is_valid_xml(self) -> None:
        """WXS must be valid XML after stripping WiX preprocessor PIs."""
        try:
            self._parse_wxs()
        except ET.ParseError as exc:
            pytest.fail(f"cpsm.wxs is not valid XML: {exc}")

    def test_wxs_root_element_is_wix(self) -> None:
        tree = self._parse_wxs()
        root = tree.getroot()
        # WiX XML namespace may be prefixed
        assert "Wix" in root.tag or root.tag == "Wix", f"Root element must be Wix, got: {root.tag}"

    def test_wxs_has_product_element(self) -> None:
        tree = self._parse_wxs()
        root = tree.getroot()
        # Elements may have namespace prefix
        found = any("Product" in child.tag for child in root.iter())
        assert found, "wxs must contain a <Product> element"

    def test_wxs_has_start_menu_shortcut(self) -> None:
        text = self.WXS_PATH.read_text(encoding="utf-8")
        assert "Shortcut" in text, "wxs must include a Start Menu <Shortcut> element"

    def test_wxs_install_scope_per_user(self) -> None:
        text = self.WXS_PATH.read_text(encoding="utf-8")
        assert "perUser" in text, "wxs Package must set InstallScope='perUser'"

    def test_wxs_localappdata_install_path(self) -> None:
        text = self.WXS_PATH.read_text(encoding="utf-8")
        assert "LocalAppDataFolder" in text, (
            "wxs must install under LocalAppDataFolder (%LOCALAPPDATA%)"
        )

    def test_wxs_has_codesigning_placeholder(self) -> None:
        text = self.WXS_PATH.read_text(encoding="utf-8")
        assert "sign" in text.lower(), "wxs must contain a code-signing comment or placeholder"


# ---------------------------------------------------------------------------
# AppImageBuilder.yml — appimagetool config
# ---------------------------------------------------------------------------


class TestAppImageBuilderYml:
    """The AppImageBuilder.yml must be valid YAML with required keys."""

    YML_PATH = PACKAGING_DIR / "AppImageBuilder.yml"

    def test_yml_file_exists(self) -> None:
        assert self.YML_PATH.is_file(), f"AppImageBuilder.yml not found: {self.YML_PATH}"

    def test_yml_is_valid_yaml(self) -> None:
        """ruamel.yaml raises on malformed YAML."""
        try:
            from ruamel.yaml import YAML  # type: ignore[import-untyped]

            yaml = YAML()
            data = yaml.load(self.YML_PATH.read_text(encoding="utf-8"))
            assert data is not None, "AppImageBuilder.yml parsed as empty/null"
        except Exception as exc:
            pytest.fail(f"AppImageBuilder.yml is not valid YAML: {exc}")

    def test_yml_has_app_info(self) -> None:
        from ruamel.yaml import YAML

        yaml = YAML()
        data = yaml.load(self.YML_PATH.read_text(encoding="utf-8"))
        assert "AppDir" in data, "AppImageBuilder.yml must have top-level 'AppDir' key"
        app_dir = data["AppDir"]
        assert "app_info" in app_dir, "AppDir must contain 'app_info'"

    def test_yml_app_id(self) -> None:
        from ruamel.yaml import YAML

        yaml = YAML()
        data = yaml.load(self.YML_PATH.read_text(encoding="utf-8"))
        app_info = data["AppDir"]["app_info"]
        assert "id" in app_info, "app_info must have 'id'"
        assert "cpsm" in str(app_info["id"]), "app_info id must reference cpsm"

    def test_yml_has_appimage_section(self) -> None:
        from ruamel.yaml import YAML

        yaml = YAML()
        data = yaml.load(self.YML_PATH.read_text(encoding="utf-8"))
        assert "AppImage" in data, "AppImageBuilder.yml must have top-level 'AppImage' key"

    def test_yml_arch_x86_64(self) -> None:
        from ruamel.yaml import YAML

        yaml = YAML()
        data = yaml.load(self.YML_PATH.read_text(encoding="utf-8"))
        arch = data.get("AppImage", {}).get("arch", "")
        assert arch == "x86_64", f"AppImage arch must be x86_64, got: {arch!r}"

    def test_yml_exec_points_to_cpsm(self) -> None:
        from ruamel.yaml import YAML

        yaml = YAML()
        data = yaml.load(self.YML_PATH.read_text(encoding="utf-8"))
        exec_val = data["AppDir"]["app_info"].get("exec", "")
        assert "cpsm" in str(exec_val), (
            f"app_info exec must reference the cpsm binary, got: {exec_val!r}"
        )


class TestVersionSourcesAgree:
    """pyproject.toml and cpsm.__version__ must not drift apart.

    Two independent copies of the version is one too many: the packaged
    artifact reports cpsm.__version__ while the distribution metadata carries
    the pyproject value, so a bump applied to one and not the other produces a
    build that reports a version it is not.  That is precisely the confusion
    the build stamp exists to remove, so it should not be reintroduced here.
    """

    def test_pyproject_matches_dunder_version(self):
        import tomllib
        from pathlib import Path

        import cpsm

        root = Path(__file__).resolve().parent.parent
        with open(root / "pyproject.toml", "rb") as fh:
            data = tomllib.load(fh)
        declared = data["project"]["version"]
        assert declared == cpsm.__version__, (
            f"pyproject.toml says {declared!r} but cpsm.__version__ is "
            f"{cpsm.__version__!r} -- bump both, or the artifact misreports itself"
        )
