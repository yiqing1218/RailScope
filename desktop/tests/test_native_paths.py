from pathlib import Path
import pytest


def test_unicode_input_and_index_are_native_readable(tmp_path):
    osmium = pytest.importorskip("osmium")
    from railscope.services.importers.native_paths import native_path

    folder = tmp_path / "中文目录 空格"
    folder.mkdir()
    source = folder / "站台.osm"
    source.write_text(
        '<osm version="0.6"><node id="1" lon="121" lat="31"/></osm>', encoding="utf-8"
    )
    readable = native_path(source)
    assert Path(readable).read_bytes() == source.read_bytes()
    with osmium.io.Reader(str(readable)) as reader:
        assert reader.header()
    index = native_path(folder / "节点.idx", output=True)
    assert str(index).isascii() or __import__("sys").platform != "win32"
