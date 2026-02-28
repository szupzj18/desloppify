"""Tests for Kotlin Compose UI adoption tracking."""

from __future__ import annotations

import json

import pytest

from desloppify.core.runtime_state import current_runtime_context
from desloppify.languages import get_lang
from desloppify.languages._framework.runtime import LangRunOverrides, make_lang_run
from desloppify.languages.kotlin.adoption import (
    ARTIFACT_PATH,
    DETECTOR_NAME,
    build_compose_ui_adoption_report,
    phase_compose_ui_adoption,
)


@pytest.fixture
def patch_project_root(monkeypatch):
    """Patch project root through RuntimeContext for relative-path helpers."""
    ctx = current_runtime_context()

    def apply(tmp_path):
        monkeypatch.setattr(ctx, "project_root", tmp_path)
        return tmp_path

    return apply


def test_kotlin_plugin_registers_compose_ui_adoption_defaults():
    cfg = get_lang("kotlin")

    assert DETECTOR_NAME in cfg.detect_commands
    assert "compose_ui_library_package" in cfg.setting_specs
    assert (
        cfg.setting_specs["compose_ui_library_package"].default
        == "com.toutiao.kmp.base.ui"
    )
    assert cfg.phases[1].label == "Compose UI adoption"


def test_report_groups_compose_screen_adoption_by_module(tmp_path, patch_project_root):
    root = patch_project_root(tmp_path)
    (root / "build.gradle.kts").write_text("// root\n")
    (root / "feature" / "home").mkdir(parents=True)
    (root / "feature" / "home" / "build.gradle.kts").write_text("// module\n")

    covered = root / "shared" / "src" / "commonMain" / "kotlin" / "profile" / "ui"
    covered.mkdir(parents=True)
    (covered / "ProfileScreen.kt").write_text(
        """
        package profile.ui

        import androidx.compose.runtime.Composable
        import com.toutiao.kmp.base.ui.AppTheme

        @Composable
        fun ProfileScreen() {
            AppTheme { }
        }
        """.strip()
        + "\n"
    )

    uncovered = root / "shared" / "src" / "commonMain" / "kotlin" / "settings" / "ui"
    uncovered.mkdir(parents=True)
    (uncovered / "SettingsScreen.kt").write_text(
        """
        package settings.ui

        import androidx.compose.runtime.Composable

        @Composable
        fun SettingsScreen() {
            Text("settings")
        }
        """.strip()
        + "\n"
    )

    feature_home = (
        root
        / "feature"
        / "home"
        / "src"
        / "commonMain"
        / "kotlin"
        / "home"
        / "ui"
    )
    feature_home.mkdir(parents=True)
    (feature_home / "HomeScreen.kt").write_text(
        """
        package home.ui

        import androidx.compose.runtime.Composable
        import com.toutiao.kmp.base.ui.components.DsCard

        @Composable
        fun HomeScreen() {
            DsCard { }
        }
        """.strip()
        + "\n"
    )

    lang = make_lang_run(get_lang("kotlin"))
    report = build_compose_ui_adoption_report(root, lang)

    assert report.total_screens == 3
    assert report.covered_screens == 2
    assert report.coverage == 0.6667
    assert [module.name for module in report.modules] == ["shared", "feature/home"]
    assert report.modules[0].coverage == 0.5
    assert report.modules[1].coverage == 1.0
    assert report.artifact_path == ".desloppify/artifacts/compose-ui-adoption.json"


def test_report_uses_component_name_fallback_when_configured(
    tmp_path,
    patch_project_root,
):
    root = patch_project_root(tmp_path)
    (root / "build.gradle.kts").write_text("// root\n")
    screen_dir = root / "shared" / "src" / "commonMain" / "kotlin" / "cart" / "ui"
    screen_dir.mkdir(parents=True)
    (screen_dir / "CartScreen.kt").write_text(
        """
        package cart.ui

        import androidx.compose.runtime.Composable

        @Composable
        fun CartScreen() {
            DsButton(onClick = {}) { }
        }
        """.strip()
        + "\n"
    )

    lang = make_lang_run(
        get_lang("kotlin"),
        overrides=LangRunOverrides(
            runtime_settings={"compose_ui_component_names": ["DsButton"]},
        ),
    )
    report = build_compose_ui_adoption_report(root, lang)

    assert report.total_screens == 1
    assert report.covered_screens == 1
    assert report.screens[0].matched_by == "component_name"


def test_phase_writes_artifact_and_opens_low_adoption_findings(
    tmp_path,
    patch_project_root,
):
    root = patch_project_root(tmp_path)
    (root / "build.gradle.kts").write_text("// root\n")

    profile_dir = root / "shared" / "src" / "commonMain" / "kotlin" / "profile" / "ui"
    profile_dir.mkdir(parents=True)
    (profile_dir / "ProfileScreen.kt").write_text(
        """
        package profile.ui

        import androidx.compose.runtime.Composable
        import com.toutiao.kmp.base.ui.AppTheme

        @Composable
        fun ProfileScreen() {
            AppTheme { }
        }
        """.strip()
        + "\n"
    )

    settings_dir = root / "shared" / "src" / "commonMain" / "kotlin" / "settings" / "ui"
    settings_dir.mkdir(parents=True)
    (settings_dir / "SettingsScreen.kt").write_text(
        """
        package settings.ui

        import androidx.compose.runtime.Composable

        @Composable
        fun SettingsScreen() {
            Text("settings")
        }
        """.strip()
        + "\n"
    )

    lang = make_lang_run(
        get_lang("kotlin"),
        overrides=LangRunOverrides(
            runtime_settings={"compose_ui_adoption_warn_threshold": 0.8},
        ),
    )
    phase = phase_compose_ui_adoption()
    findings, signals = phase.run(root, lang)

    assert signals == {DETECTOR_NAME: 2}
    assert len(findings) == 2
    assert findings[0]["detector"] == DETECTOR_NAME
    assert "below target 80.0%" in findings[0]["summary"]

    artifact_file = root / ARTIFACT_PATH
    assert artifact_file.is_file()
    payload = json.loads(artifact_file.read_text())
    assert payload["summary"]["covered_screens"] == 1
    assert payload["summary"]["total_screens"] == 2
