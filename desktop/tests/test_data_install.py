import hashlib
import io
import json
from pathlib import Path
import shutil
from threading import Event
import pytest
from desktop.data_install import (
    download_file,
    Cancelled,
    activate_dataset,
    active_directory,
    REQUIRED_FILES,
    install,
)


class Response(io.BytesIO):
    def __init__(self, content=b"", headers=None, status=200):
        super().__init__(content)
        self.headers, self.status = headers or {}, status


def source(payload, ranges, checksum=None):
    def open_request(request, timeout):
        if request.get_method() == "HEAD":
            return Response(
                headers={"Content-Length": str(len(payload)), "ETag": "snapshot-1"}
            )
        if request.full_url.endswith(".md5"):
            return Response((checksum or hashlib.md5(payload).hexdigest()).encode())
        range_header = request.get_header("Range")
        if range_header:
            offset = int(range_header.split("=")[1].split("-")[0])
            ranges.append(offset)
            return Response(
                payload[offset:],
                headers={
                    "Content-Range": f"bytes {offset}-{len(payload) - 1}/{len(payload)}"
                },
                status=206,
            )
        return Response(payload)

    return open_request


def test_paused_download_resumes_and_verifies_before_replacing_old_file(tmp_path):
    payload = b"x" * (3 * 1024 * 1024)
    path = tmp_path / "china.osm.pbf"
    path.write_bytes(b"old-complete-file")
    cancel = Event()
    ranges = []

    def pause_after_chunk(value):
        if value["phase"] == "download":
            cancel.set()

    with pytest.raises(Cancelled):
        download_file(
            "https://example.test/china.pbf",
            path,
            pause_after_chunk,
            cancel,
            True,
            source(payload, ranges),
        )
    assert path.read_bytes() == b"old-complete-file"
    assert path.with_suffix(".pbf.part").stat().st_size == 1024 * 1024
    download_file(
        "https://example.test/china.pbf",
        path,
        lambda v: None,
        Event(),
        True,
        source(payload, ranges),
    )
    assert ranges == [1024 * 1024]
    assert path.read_bytes() == payload
    assert not path.with_suffix(".pbf.part").exists()


def test_bad_checksum_does_not_replace_existing_download(tmp_path):
    path = tmp_path / "china.pbf"
    path.write_bytes(b"old")
    with pytest.raises(RuntimeError, match="校验失败"):
        download_file(
            "https://example.test/china.pbf",
            path,
            lambda v: None,
            Event(),
            True,
            source(b"new", [], "0" * 32),
        )
    assert path.read_bytes() == b"old"


def test_failed_activation_preserves_previous_dataset_and_blocks_traversal(tmp_path):
    stage = tmp_path / "data/processed/osm/datasets/test"
    stage.mkdir(parents=True)
    pointer = stage.parents[1] / "active_dataset.json"
    assert active_directory(tmp_path) == stage.parents[1]
    for name in REQUIRED_FILES:
        (stage / name).write_text(
            json.dumps(
                {"type": "FeatureCollection", "features": []}
                if name.endswith(".geojson")
                else {"routes": [{"osm_relation_id": 1}]}
            ),
            encoding="utf-8",
        )
    activate_dataset(tmp_path, stage)
    before = pointer.read_bytes()
    (stage / REQUIRED_FILES[0]).write_text("invalid-json")
    with pytest.raises(ValueError):
        activate_dataset(tmp_path, stage)
    assert pointer.read_bytes() == before
    assert active_directory(tmp_path) == stage.resolve()
    pointer.write_text('{"dataset":"../../../"}')
    with pytest.raises(ValueError):
        active_directory(tmp_path)


def test_actual_three_importers_produce_complete_versioned_dataset(
    tmp_path, monkeypatch
):
    osmium = pytest.importorskip("osmium")
    project = Path(__file__).resolve().parents[2]
    shutil.copytree(
        project / "backend/railscope",
        tmp_path / "backend/railscope",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (tmp_path / "desktop").mkdir()
    shutil.copy2(
        project / "desktop/import_station_areas.py",
        tmp_path / "desktop/import_station_areas.py",
    )
    for name in ("metro_data.py", "geometry.py", "station_search.py"):
        shutil.copy2(project / "desktop" / name, tmp_path / "desktop" / name)
    raw = tmp_path / "china-latest.osm.pbf"
    with osmium.SimpleWriter(str(raw)) as writer:
        for node_id, lon, lat, tags in (
            (1, 121, 31, {"railway": "station", "station": "subway", "name": "A"}),
            (2, 121.02, 31, {"railway": "station", "station": "subway", "name": "B"}),
            (3, 120.999, 30.999, {}),
            (4, 121.001, 30.999, {}),
            (5, 121.001, 31.001, {}),
            (6, 120.999, 31.001, {}),
        ):
            writer.add_node(
                osmium.osm.mutable.Node(id=node_id, location=(lon, lat), tags=tags)
            )
        writer.add_way(
            osmium.osm.mutable.Way(id=10, nodes=[1, 2], tags={"railway": "subway"})
        )
        writer.add_way(
            osmium.osm.mutable.Way(
                id=11,
                nodes=[3, 4, 5, 6, 3],
                tags={"railway": "station", "station": "subway", "name": "A"},
            )
        )
        writer.add_way(
            osmium.osm.mutable.Way(
                id=12,
                nodes=[1, 2],
                tags={
                    "railway": "construction",
                    "proposed": "subway",
                    "name": "在建工程",
                },
            )
        )
        writer.add_relation(
            osmium.osm.mutable.Relation(
                id=20,
                members=[("n", 1, "stop"), ("n", 2, "stop"), ("w", 10, "")],
                tags={
                    "type": "route",
                    "route": "subway",
                    "name": "测试线",
                    "colour": "#123456",
                },
            )
        )
    unicode_raw = tmp_path / "中文下载目录" / "china-latest.osm.pbf"
    unicode_raw.parent.mkdir()
    raw.replace(unicode_raw)
    raw = unicode_raw
    old = tmp_path / "data/processed/osm/china_metro_routes.geojson"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old-data-preserved")
    monkeypatch.setattr(
        "desktop.data_install.shutil.disk_usage",
        lambda _: shutil._ntuple_diskusage(20 * 1024**3, 0, 20 * 1024**3),
    )
    phases = []
    stage = install(tmp_path, phases.append, pbf=raw)
    assert active_directory(tmp_path) == stage
    assert old.read_bytes() == b"old-data-preserved"
    assert not list(stage.glob("*.idx"))
    assert [v["step"] for v in phases if v["phase"] == "import"] == [1, 2, 3]
    route = json.loads(
        (stage / "china_metro_routes.geojson").read_text(encoding="utf-8")
    )["features"][0]
    assert route["properties"]["display_color"] == "#123456"
    assert json.loads(
        (stage / "china_metro_construction.geojson").read_text(encoding="utf-8")
    )["features"]
    assert json.loads(
        (stage / "china_metro_station_areas.geojson").read_text(encoding="utf-8")
    )["features"]
