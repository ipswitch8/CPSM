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

    # The set of dialog sets WixUIExtension actually provides. A UIRef naming
    # anything else links fine locally (candle does not resolve it) and then
    # fails only at light.exe time, on Windows, inside CI — which is exactly
    # how `WixUI_ProgressOnly` reached the repo and broke the first release
    # run with "LGHT0094: Unresolved reference to symbol".
    _WIXUI_DIALOG_SETS = frozenset(
        {
            "WixUI_Minimal",
            "WixUI_InstallDir",
            "WixUI_FeatureTree",
            "WixUI_Mondo",
            "WixUI_Advanced",
        }
    )

    def test_wxs_uiref_names_a_real_dialog_set(self) -> None:
        """Any UIRef must name a dialog set WixUIExtension actually ships.

        Having no UIRef at all is fine — the MSI falls back to its built-in
        basic UI — so this only constrains the case where one is present.
        """
        root = self._parse_wxs().getroot()
        bad = [
            el.get("Id")
            for el in root.iter()
            if el.tag.endswith("UIRef") and el.get("Id") not in self._WIXUI_DIALOG_SETS
        ]
        assert not bad, (
            f"cpsm.wxs references UI dialog set(s) that WixUIExtension does not "
            f"provide: {bad}. light.exe will fail with LGHT0094 on the Windows "
            f"release job. Valid sets: {sorted(self._WIXUI_DIALOG_SETS)}, or omit "
            "the UIRef entirely for the built-in basic UI."
        )


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

    # ------------------------------------------------------------------
    # The release tag is a THIRD copy of the version, and the one nobody
    # was checking.
    #
    # v0.2.1 shipped an AppImage named CPSM-0.2.1-x86_64.AppImage that
    # reported "cpsm 0.2.0" when run, because the git tag only feeds the
    # output *filename* — the version inside the binary comes from
    # cpsm.__version__, which had not been bumped. Download 0.2.1, run it,
    # see 0.2.0.
    #
    # This runs when CPSM_RELEASE_TAG is set. release.yml sets it from the
    # tag and runs this test BEFORE building, so a mismatched tag fails the
    # release in seconds instead of publishing a mislabelled binary.
    # ------------------------------------------------------------------

    def test_release_tag_matches_package_version(self) -> None:
        """A release tag must name the version the package actually reports."""
        import os

        tag = os.environ.get("CPSM_RELEASE_TAG")
        if not tag:
            pytest.skip("not a release build (CPSM_RELEASE_TAG unset)")

        import cpsm

        tag_version = tag[1:] if tag.startswith("v") else tag
        assert tag_version == cpsm.__version__, (
            f"release tag {tag!r} implies version {tag_version!r}, but "
            f"cpsm.__version__ is {cpsm.__version__!r}. The artifact would be "
            f"named for one version and report another. Bump pyproject.toml "
            f"and cpsm/__init__.py, or retag."
        )

    # The wiring test below parses the workflow instead of grepping it.
    # An earlier version asserted only that the two strings appeared
    # somewhere in release.yml, and a review demonstrated five separate ways
    # to disable the guard while keeping that version green: commenting the
    # step body out, adding `if: false`, appending `|| true`, renaming the
    # env key to CPSM_RELEASE_TAGX (the old assertion string is a prefix of
    # the typo, so it self-satisfied), and moving the check after the build.
    # Each of those is covered by an assertion here.

    @staticmethod
    def _release_workflow() -> dict:
        import yaml

        path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_release_workflow_actually_runs_the_tag_check(self) -> None:
        """The tag check must be wired in so that it can actually fail a release."""
        steps = self._release_workflow()["jobs"]["build-linux"]["steps"]

        tag_idx = [
            n
            for n, st in enumerate(steps)
            if "test_release_tag_matches_package_version" in str(st.get("run", ""))
        ]
        assert tag_idx, (
            "no step in build-linux runs test_release_tag_matches_package_version, "
            "so the tag/version agreement is never checked during a release."
        )
        idx = tag_idx[0]
        step = steps[idx]

        # A conditional or non-fatal step is a guard in name only.
        assert "if" not in step, (
            f"the tag-check step is conditional (if: {step.get('if')!r}); a guard "
            f"that can be switched off by a condition does not guard anything."
        )
        assert step.get("continue-on-error") is not True, (
            "the tag-check step sets continue-on-error, so a mismatched tag "
            "would be reported and then ignored."
        )

        # The env var must be set on the step, under its exact name.
        env = step.get("env") or {}
        assert "CPSM_RELEASE_TAG" in env, (
            f"the tag-check step does not set CPSM_RELEASE_TAG (env keys: "
            f"{sorted(env)}). Without it the test skips and the step passes "
            f"green without checking anything."
        )
        assert "github.ref_name" in str(env["CPSM_RELEASE_TAG"]), (
            f"CPSM_RELEASE_TAG is set to {env['CPSM_RELEASE_TAG']!r}, not the "
            f"pushed tag; the check would compare against the wrong value."
        )

        # A trailing `|| true` turns a failing pytest into a passing step.
        run = str(step["run"])
        for swallow in ("|| true", "||true", "|| :", "; true", "continue-on-error"):
            assert swallow not in run, (
                f"the tag-check step neutralises its own exit status with "
                f"{swallow!r}, so a mismatched tag cannot fail the release."
            )

        # It must run BEFORE the build, or a mismatch is only caught after
        # several minutes of PyInstaller work -- and after the point where a
        # partial artifact may already exist.
        build_idx = [
            n for n, st in enumerate(steps) if "pyinstaller" in str(st.get("run", "")).lower()
        ]
        assert build_idx, "build-linux no longer has a PyInstaller step; this test needs updating."
        assert idx < build_idx[0], (
            f"the tag check runs at step {idx}, after the PyInstaller build at "
            f"step {build_idx[0]}. It must run first so a bad tag fails fast."
        )

    def test_docs_do_not_name_a_stale_release_artifact(self) -> None:
        """Docs must not tell users to run an AppImage that is not published.

        The version is declared in five places: pyproject.toml,
        cpsm/__init__.py, the README Quick Start, the README format table,
        and install.sh's usage text. The first two already have a test
        pinning them together; the other three drifted to 0.2.0 while the
        package moved to 0.2.1, so the Quick Start told people to chmod a
        file that 404s. This test covers the remaining three.
        """
        import re

        import cpsm

        root = Path(__file__).resolve().parent.parent
        pattern = re.compile(r"CPSM-(\d+\.\d+\.\d+)-x86_64\.AppImage")
        stale: list[str] = []

        for rel in ("README.md", "install.sh", "packaging/install.sh"):
            path = root / rel
            if not path.is_file():
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                for found in pattern.findall(line):
                    if found != cpsm.__version__:
                        stale.append(f"{rel}:{lineno} names {found}")

        assert not stale, (
            "documentation names an AppImage version that is not the current "
            f"release ({cpsm.__version__}). Anyone copying these commands gets "
            "'No such file or directory':\n  " + "\n  ".join(stale)
        )

    def test_release_workflow_verifies_the_built_artifact(self) -> None:
        """The packaged-artifact test must run where dist/ actually exists.

        tests/e2e/test_packaged_artifact.py asserts the built binary reports
        cpsm.__version__, but its dist-gated assertions skip when dist/ is
        absent -- which it is in every ordinary CI job. The release job is
        the only place that directory exists, so it is the only place those
        assertions mean anything.
        """
        steps = self._release_workflow()["jobs"]["build-linux"]["steps"]

        idx = [
            n for n, st in enumerate(steps) if "test_packaged_artifact" in str(st.get("run", ""))
        ]
        assert idx, (
            "no step in build-linux runs tests/e2e/test_packaged_artifact.py, so "
            "nothing compares the BUILT binary's reported version against the "
            "version it was named for."
        )
        step = steps[idx[0]]
        assert "if" not in step, "the packaged-artifact check is conditional."
        assert step.get("continue-on-error") is not True, (
            "the packaged-artifact check sets continue-on-error."
        )

        build_idx = [
            n for n, st in enumerate(steps) if "pyinstaller" in str(st.get("run", "")).lower()
        ]
        assert idx[0] > build_idx[0], (
            f"the packaged-artifact check runs at step {idx[0]}, before the build "
            f"at {build_idx[0]}; dist/ would not exist yet and every dist-gated "
            f"assertion would silently skip."
        )
