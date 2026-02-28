"""Kotlin language plugin — ktlint."""

from desloppify.core.registry import register_detector
from desloppify.languages._framework.base.types import LangValueSpec
from desloppify.languages._framework.generic import generic_lang
from desloppify.languages._framework.treesitter._specs import KOTLIN_SPEC
from desloppify.languages.kotlin.adoption import (
    DEFAULT_ADOPTION_WARN_THRESHOLD,
    DEFAULT_COMPONENT_NAMES,
    DEFAULT_MODULE_ROOTS,
    DEFAULT_SCREEN_NAME_PATTERNS,
    DEFAULT_SCREEN_PATH_PATTERNS,
    DEFAULT_UI_LIBRARY_PACKAGE,
    cmd_compose_ui_adoption,
    detector_meta,
    phase_compose_ui_adoption,
)

cfg = generic_lang(
    name="kotlin",
    extensions=[".kt", ".kts"],
    tools=[
        {
            "label": "ktlint",
            "cmd": "ktlint --reporter=json",
            "fmt": "json",
            "id": "ktlint_violation",
            "tier": 2,
            "fix_cmd": "ktlint --format",
        },
    ],
    exclude=["build"],
    depth="shallow",
    detect_markers=["build.gradle.kts", "build.gradle"],
    treesitter_spec=KOTLIN_SPEC,
)

register_detector(detector_meta())
cfg.phases.insert(1, phase_compose_ui_adoption())
cfg.detect_commands["compose_ui_adoption"] = cmd_compose_ui_adoption
cfg.setting_specs.update(
    {
        "compose_ui_library_package": LangValueSpec(
            str,
            DEFAULT_UI_LIBRARY_PACKAGE,
            "Compose UI library package prefix used for adoption tracking",
        ),
        "compose_ui_screen_name_patterns": LangValueSpec(
            list,
            list(DEFAULT_SCREEN_NAME_PATTERNS),
            "Composable function-name glob patterns treated as screen entry points",
        ),
        "compose_ui_screen_path_patterns": LangValueSpec(
            list,
            list(DEFAULT_SCREEN_PATH_PATTERNS),
            "Relative file-path glob patterns scanned for Compose screen entry points",
        ),
        "compose_ui_component_names": LangValueSpec(
            list,
            list(DEFAULT_COMPONENT_NAMES),
            "Optional component names that count as Compose UI-library adoption",
        ),
        "compose_ui_module_roots": LangValueSpec(
            list,
            list(DEFAULT_MODULE_ROOTS),
            "Optional module-root directories used for adoption aggregation",
        ),
        "compose_ui_adoption_warn_threshold": LangValueSpec(
            float,
            DEFAULT_ADOPTION_WARN_THRESHOLD,
            "Coverage threshold (0-1) below which Compose UI adoption findings open",
        ),
    }
)
