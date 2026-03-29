from pathlib import Path

from zoteropdf2md.single_file_html import inline_images_from_html_file


def test_inline_images_from_html_file(tmp_path: Path) -> None:
    html_path = tmp_path / "doc.html"
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    html_path.write_text('<html><body><img src="img.png"></body></html>', encoding="utf-8")

    result = inline_images_from_html_file(html_path)

    assert result.inlined_images == 1
    assert "data:image/png;base64," in result.html
    assert "img.png" not in result.html

