from zoteropdf2md.export_modes import ExportMode, get_export_mode_spec, parse_export_mode


def test_parse_export_mode_defaults_to_classic() -> None:
    assert parse_export_mode(None) == ExportMode.CLASSIC


def test_html_mode_has_html_artifact() -> None:
    spec = get_export_mode_spec(ExportMode.ZOTERO)
    assert spec.marker_output_format == "html"
    assert spec.artifact_extension == ".html"

