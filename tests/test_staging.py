from pathlib import Path

from zoteropdf2md.staging import get_max_base_len_for_output_dir


def test_max_base_len_is_positive_for_short_output_path(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    assert get_max_base_len_for_output_dir(output_dir) > 0
