"""Compose UI library adoption analysis for Kotlin/KMP projects."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from fnmatch import fnmatch
from pathlib import Path

from desloppify.app.commands.helpers.lang import resolve_lang_settings
from desloppify.app.commands.helpers.runtime import command_runtime
from desloppify.core._internal.text_utils import get_project_root
from desloppify.core.file_paths import rel, safe_write_text
from desloppify.core.output import colorize, print_table
from desloppify.core.registry import DetectorMeta
from desloppify.file_discovery import resolve_path
from desloppify.languages._framework.base.types import DetectorPhase
from desloppify.languages._framework.runtime import (
    LangRun,
    LangRunOverrides,
    make_lang_run,
)
from desloppify.state import make_finding

DETECTOR_NAME = "compose_ui_adoption"
DEFAULT_UI_LIBRARY_PACKAGE = "com.toutiao.kmp.base.ui"
DEFAULT_SCREEN_NAME_PATTERNS = ["*Screen", "*Page", "*Route"]
DEFAULT_SCREEN_PATH_PATTERNS = ["**/ui/**", "**/screen/**", "**/page/**"]
DEFAULT_COMPONENT_NAMES: list[str] = []
DEFAULT_MODULE_ROOTS: list[str] = []
DEFAULT_ADOPTION_WARN_THRESHOLD = 0.6
ARTIFACT_PATH = Path(".desloppify") / "artifacts" / "compose-ui-adoption.json"

_COMPOSABLE_ANNOTATION_RE = re.compile(r"@Composable\b")
_FUNCTION_RE = re.compile(r"\bfun\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")


@dataclass(frozen=True)
class ComposeScreenUsage:
    file: str
    function: str
    line: int
    module: str
    covered: bool
    matched_by: str


@dataclass(frozen=True)
class ModuleAdoptionSummary:
    name: str
    covered_screens: int
    total_screens: int
    coverage: float


@dataclass(frozen=True)
class ComposeUiAdoptionReport:
    ui_library_package: str
    artifact_path: str
    covered_screens: int
    total_screens: int
    coverage: float | None
    modules: list[ModuleAdoptionSummary]
    screens: list[ComposeScreenUsage]

    def to_payload(self) -> dict[str, object]:
        return {
            "summary": {
                "ui_library_package": self.ui_library_package,
                "covered_screens": self.covered_screens,
                "total_screens": self.total_screens,
                "coverage": self.coverage,
                "artifact_path": self.artifact_path,
            },
            "modules": [asdict(module) for module in self.modules],
            "screens": [asdict(screen) for screen in self.screens],
        }


def detector_meta() -> DetectorMeta:
    return DetectorMeta(
        DETECTOR_NAME,
        "compose ui adoption",
        "Code quality",
        "manual_fix",
        "increase Compose design-system adoption across screen entry points",
    )


def phase_compose_ui_adoption() -> DetectorPhase:
    def run(path: Path, lang: LangRun) -> tuple[list[dict], dict[str, int]]:
        report = build_compose_ui_adoption_report(path, lang)
        _write_artifact(report)
        findings = build_adoption_findings(report, lang)
        return findings, {DETECTOR_NAME: report.total_screens}

    return DetectorPhase("Compose UI adoption", run)


def cmd_compose_ui_adoption(args: argparse.Namespace) -> None:
    from desloppify.languages import get_lang

    lang_cfg = get_lang("kotlin")
    runtime = command_runtime(args)
    config = runtime.config if isinstance(runtime.config, dict) else {}
    lang_settings = resolve_lang_settings(config, lang_cfg)
    lang = make_lang_run(
        lang_cfg,
        overrides=LangRunOverrides(runtime_settings=lang_settings),
    )
    report = build_compose_ui_adoption_report(Path(args.path), lang)
    _write_artifact(report)

    payload = report.to_payload()
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
        return

    total = report.total_screens
    coverage_text = _format_pct(report.coverage)
    print(
        colorize(
            (
                "\nCompose UI adoption: "
                f"{coverage_text} ({report.covered_screens}/{total})\n"
            ),
            "bold",
        )
    )
    print(f"Package: {report.ui_library_package}")
    print(f"Artifact: {report.artifact_path}")

    if report.modules:
        rows = [
            [
                module.name,
                f"{module.covered_screens}/{module.total_screens}",
                _format_pct(module.coverage),
            ]
            for module in report.modules[: getattr(args, "top", 20)]
        ]
        print()
        print_table(["Module", "Covered", "Coverage"], rows, [36, 12, 10])

    if not report.screens:
        print(
            colorize(
                "\nNo Compose screen candidates matched the configured rules.",
                "green",
            )
        )
        return

    uncovered = [screen for screen in report.screens if not screen.covered]
    if uncovered:
        print(colorize("\nUncovered screens\n", "bold"))
        rows = [
            [screen.module, screen.function, screen.file]
            for screen in uncovered[: getattr(args, "top", 20)]
        ]
        print_table(["Module", "Function", "File"], rows, [24, 28, 70])


def build_compose_ui_adoption_report(
    scan_path: Path,
    lang: LangRun,
) -> ComposeUiAdoptionReport:
    root = scan_path.resolve()
    files = sorted(_kotlin_files(scan_path, lang))
    module_roots = _resolve_module_roots(root, files, lang)

    screens: list[ComposeScreenUsage] = []
    for file in files:
        source = _read_source(file)
        if source is None:
            continue
        rel_file = rel(str(file))
        if not _matches_any(rel_file, _screen_path_patterns(lang)):
            continue

        functions = _extract_composable_functions(source)
        if not functions:
            continue

        matched_by_file_import = _file_import_matches(source, _ui_library_package(lang))
        module_name = _module_name_for_file(file, root, module_roots)
        for function_name, line_no, body in functions:
            if not _matches_any(function_name, _screen_name_patterns(lang)):
                continue
            covered, matched_by = _screen_coverage(
                body=body,
                package_name=_ui_library_package(lang),
                component_names=_component_names(lang),
                file_import_match=matched_by_file_import,
            )
            screens.append(
                ComposeScreenUsage(
                    file=rel_file,
                    function=function_name,
                    line=line_no,
                    module=module_name,
                    covered=covered,
                    matched_by=matched_by,
                )
            )

    covered_screens = sum(1 for screen in screens if screen.covered)
    total_screens = len(screens)
    module_summaries = _summarize_modules(screens)
    coverage = _safe_ratio(covered_screens, total_screens)

    artifact_abs = get_project_root() / ARTIFACT_PATH
    return ComposeUiAdoptionReport(
        ui_library_package=_ui_library_package(lang),
        artifact_path=rel(str(artifact_abs)),
        covered_screens=covered_screens,
        total_screens=total_screens,
        coverage=coverage,
        modules=module_summaries,
        screens=screens,
    )


def build_adoption_findings(
    report: ComposeUiAdoptionReport,
    lang: LangRun,
) -> list[dict]:
    threshold = _warn_threshold(lang)
    if report.total_screens <= 0:
        return []

    findings: list[dict] = []
    if (report.coverage or 0.0) < threshold:
        findings.append(
            make_finding(
                DETECTOR_NAME,
                ".",
                "global",
                tier=3,
                confidence="medium",
                summary=(
                    "Compose UI adoption is "
                    f"{_format_pct(report.coverage)} "
                    f"({report.covered_screens}/{report.total_screens}) "
                    f"below target {_format_pct(threshold)}"
                ),
                detail=_finding_detail(
                    report=report,
                    module_name="(global)",
                    covered_screens=report.covered_screens,
                    total_screens=report.total_screens,
                    coverage=report.coverage,
                    related_files=_related_uncovered_files(report.screens),
                ),
            )
        )

    for module in report.modules:
        if module.total_screens <= 0 or module.coverage >= threshold:
            continue
        related_files = _related_uncovered_files(
            [screen for screen in report.screens if screen.module == module.name]
        )
        findings.append(
            make_finding(
                DETECTOR_NAME,
                module.name,
                _normalize_identifier(module.name),
                tier=3,
                confidence="medium",
                summary=(
                    f"{module.name} Compose UI adoption is "
                    f"{_format_pct(module.coverage)} "
                    f"({module.covered_screens}/{module.total_screens}) "
                    f"below target {_format_pct(threshold)}"
                ),
                detail=_finding_detail(
                    report=report,
                    module_name=module.name,
                    covered_screens=module.covered_screens,
                    total_screens=module.total_screens,
                    coverage=module.coverage,
                    related_files=related_files,
                ),
            )
        )
    return findings


def _finding_detail(
    *,
    report: ComposeUiAdoptionReport,
    module_name: str,
    covered_screens: int,
    total_screens: int,
    coverage: float | None,
    related_files: list[str],
) -> dict[str, object]:
    return {
        "module": module_name,
        "covered_screens": covered_screens,
        "total_screens": total_screens,
        "coverage": coverage,
        "artifact_path": report.artifact_path,
        "ui_library_package": report.ui_library_package,
        "related_files": related_files,
    }


def _related_uncovered_files(
    screens: list[ComposeScreenUsage],
    *,
    limit: int = 8,
) -> list[str]:
    related: list[str] = []
    seen: set[str] = set()
    for screen in screens:
        if screen.covered or screen.file in seen:
            continue
        related.append(screen.file)
        seen.add(screen.file)
        if len(related) >= limit:
            break
    return related


def _summarize_modules(
    screens: list[ComposeScreenUsage],
) -> list[ModuleAdoptionSummary]:
    counters: dict[str, tuple[int, int]] = {}
    for screen in screens:
        covered, total = counters.get(screen.module, (0, 0))
        counters[screen.module] = (covered + int(screen.covered), total + 1)
    summaries = [
        ModuleAdoptionSummary(
            name=name,
            covered_screens=covered,
            total_screens=total,
            coverage=_safe_ratio(covered, total) or 0.0,
        )
        for name, (covered, total) in counters.items()
    ]
    return sorted(summaries, key=lambda item: (item.coverage, item.name))


def _write_artifact(report: ComposeUiAdoptionReport) -> None:
    artifact_abs = get_project_root() / ARTIFACT_PATH
    safe_write_text(artifact_abs, json.dumps(report.to_payload(), indent=2) + "\n")


def _kotlin_files(scan_path: Path, lang: LangRun) -> list[Path]:
    file_finder = getattr(lang, "file_finder", None)
    if file_finder is None:
        return []
    return [Path(resolve_path(filepath)) for filepath in file_finder(scan_path)]


def _read_source(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _extract_composable_functions(source: str) -> list[tuple[str, int, str]]:
    lines = source.splitlines()
    out: list[tuple[str, int, str]] = []
    idx = 0
    while idx < len(lines):
        if _COMPOSABLE_ANNOTATION_RE.search(lines[idx]) is None:
            idx += 1
            continue

        function_line = None
        function_name = ""
        for lookahead in range(idx, min(idx + 8, len(lines))):
            match = _FUNCTION_RE.search(lines[lookahead])
            if match is None:
                continue
            function_line = lookahead + 1
            function_name = match.group(1)
            break

        if function_line is None:
            idx += 1
            continue

        body = _extract_function_body(lines, function_line - 1)
        out.append((function_name, function_line, body))
        idx = function_line
    return out


def _extract_function_body(lines: list[str], start_idx: int) -> str:
    body_lines: list[str] = []
    brace_depth = 0
    seen_open = False
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        body_lines.append(line)
        for char in line:
            if char == "{":
                brace_depth += 1
                seen_open = True
            elif char == "}":
                brace_depth -= 1
        if seen_open and brace_depth <= 0:
            break
    return "\n".join(body_lines)


def _file_import_matches(source: str, package_name: str) -> bool:
    prefix = package_name.rstrip(".")
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("import "):
            continue
        imported = stripped.removeprefix("import ").strip()
        if imported == prefix or imported.startswith(prefix + "."):
            return True
    return False


def _screen_coverage(
    *,
    body: str,
    package_name: str,
    component_names: list[str],
    file_import_match: bool,
) -> tuple[bool, str]:
    if file_import_match:
        return True, "import"

    if package_name and re.search(
        rf"\b{re.escape(package_name)}\.[A-Za-z_]\w*\s*\(",
        body,
    ):
        return True, "qualified_call"

    if component_names:
        component_re = re.compile(
            r"\b(?:"
            + "|".join(re.escape(name) for name in component_names)
            + r")\s*\("
        )
        if component_re.search(body):
            return True, "component_name"

    return False, ""


def _resolve_module_roots(
    root: Path,
    files: list[Path],
    lang: LangRun,
) -> list[Path]:
    configured = _configured_module_roots(root, lang)
    if configured:
        return configured

    discovered: set[Path] = set()
    for marker in ("build.gradle.kts", "build.gradle"):
        for match in root.rglob(marker):
            if any(part in {"build", ".gradle"} for part in match.parts):
                continue
            discovered.add(match.parent.resolve())

    # Keep only roots that contain at least one scanned file.
    contained = [
        candidate
        for candidate in discovered
        if any(_is_under(file, candidate) for file in files)
    ]
    return sorted(contained, key=lambda item: len(item.parts), reverse=True)


def _configured_module_roots(root: Path, lang: LangRun) -> list[Path]:
    resolved: list[Path] = []
    for raw in lang.runtime_setting(
        "compose_ui_module_roots",
        DEFAULT_MODULE_ROOTS,
    ):
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = (root / raw).resolve()
        if candidate.exists():
            resolved.append(candidate)
    return sorted(resolved, key=lambda item: len(item.parts), reverse=True)


def _module_name_for_file(file: Path, root: Path, module_roots: list[Path]) -> str:
    for module_root in module_roots:
        if _is_under(file, module_root):
            label = rel(str(module_root))
            if label != ".":
                return label
            break

    relative_parts = Path(rel(str(file))).parts
    if not relative_parts:
        return "(root)"
    return relative_parts[0]


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _ui_library_package(lang: LangRun) -> str:
    return str(
        lang.runtime_setting(
            "compose_ui_library_package",
            DEFAULT_UI_LIBRARY_PACKAGE,
        )
    ).strip()


def _screen_name_patterns(lang: LangRun) -> list[str]:
    return _normalize_string_list(
        lang.runtime_setting(
            "compose_ui_screen_name_patterns",
            DEFAULT_SCREEN_NAME_PATTERNS,
        )
    ) or list(DEFAULT_SCREEN_NAME_PATTERNS)


def _screen_path_patterns(lang: LangRun) -> list[str]:
    return _normalize_string_list(
        lang.runtime_setting(
            "compose_ui_screen_path_patterns",
            DEFAULT_SCREEN_PATH_PATTERNS,
        )
    ) or list(DEFAULT_SCREEN_PATH_PATTERNS)


def _component_names(lang: LangRun) -> list[str]:
    return _normalize_string_list(
        lang.runtime_setting(
            "compose_ui_component_names",
            DEFAULT_COMPONENT_NAMES,
        )
    )


def _warn_threshold(lang: LangRun) -> float:
    raw = lang.runtime_setting(
        "compose_ui_adoption_warn_threshold",
        DEFAULT_ADOPTION_WARN_THRESHOLD,
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_ADOPTION_WARN_THRESHOLD
    return max(0.0, min(1.0, value))


def _normalize_string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text:
            out.append(text)
    return out


def _matches_any(value: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    normalized = value.replace("\\", "/")
    return any(fnmatch(normalized, pattern) for pattern in patterns)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _normalize_identifier(value: str) -> str:
    return (
        value.strip().lower().replace("\\", "/").replace("/", "_").replace("-", "_")
        or "module"
    )
