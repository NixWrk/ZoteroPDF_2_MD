from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExportMode(str, Enum):
    CLASSIC = "classic"
    LLM = "llm_bundle"
    ZOTERO = "zotero_single_html"


@dataclass(frozen=True)
class ExportModeSpec:
    mode: ExportMode
    marker_output_format: str
    artifact_extension: str
    label: str


_SPECS: dict[ExportMode, ExportModeSpec] = {
    ExportMode.CLASSIC: ExportModeSpec(
        mode=ExportMode.CLASSIC,
        marker_output_format="markdown",
        artifact_extension=".md",
        label="Classic (MD in separate folders)",
    ),
    ExportMode.LLM: ExportModeSpec(
        mode=ExportMode.LLM,
        marker_output_format="markdown",
        artifact_extension=".md",
        label="LLM bundle (flat folder: md + images)",
    ),
    ExportMode.ZOTERO: ExportModeSpec(
        mode=ExportMode.ZOTERO,
        marker_output_format="html",
        artifact_extension=".html",
        label="Zotero single-file HTML attachment",
    ),
}


def parse_export_mode(value: str | ExportMode | None) -> ExportMode:
    if isinstance(value, ExportMode):
        return value
    if value is None:
        return ExportMode.CLASSIC
    normalized = str(value).strip().lower()
    for mode in ExportMode:
        if mode.value == normalized:
            return mode
    raise ValueError(f"Unknown export mode: {value}")


def get_export_mode_spec(value: str | ExportMode | None) -> ExportModeSpec:
    mode = parse_export_mode(value)
    return _SPECS[mode]


def all_export_mode_specs() -> list[ExportModeSpec]:
    return [_SPECS[ExportMode.CLASSIC], _SPECS[ExportMode.LLM], _SPECS[ExportMode.ZOTERO]]

