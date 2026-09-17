from desktop.repair_hefei_s1 import component_count
from desktop import repair_hefei_s1 as repair
from contextlib import contextmanager
import json
from types import SimpleNamespace
import pytest


def test_gap_check_requires_shared_original_endpoints():
    def feature(coords):
        return {"geometry": {"coordinates": coords}}

    first = feature([[117, 32], [117.1, 32]])
    last = feature([[117.2, 32], [117.3, 32]])
    assert component_count([first, last]) == 2
    assert (
        component_count(
            [first, feature([[117.1, 32], [117.15, 31.98], [117.2, 32]]), last]
        )
        == 1
    )
    assert component_count([first, feature([[117.10001, 32], [117.2, 32]]), last]) == 2


@pytest.mark.parametrize("state", ["construction", "proposed"])
def test_repair_merges_real_nodes_or_rejects_planned_gap_without_overwrite(
    tmp_path, monkeypatch, state
):
    def feature(wid, coords):
        return {
            "type": "Feature",
            "properties": {"osm_way_id": wid, "line_name": repair.NAME},
            "geometry": {"type": "LineString", "coordinates": coords},
        }

    layer = {
        "type": "FeatureCollection",
        "features": [
            feature(1, [[117, 32], [117.1, 32]]),
            feature(2, [[117.2, 32], [117.3, 32]]),
        ],
    }
    target = tmp_path / "layer.json"
    target.write_text(json.dumps(layer), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    original = target.read_bytes()
    way = f'<way id="55" version="1" timestamp="2026-01-01T00:00:00Z"><nd ref="9700440915"/><nd ref="42"/><tag k="name" v="{repair.NAME}"/><tag k="railway" v="{state}"/><tag k="proposed" v="subway"/></way>'

    @contextmanager
    def fake_open(request, timeout):
        if "/way/55/full" in request.full_url:
            xml = f'<osm><node id="9700440915" lon="117.1" lat="32"/><node id="42" lon="117.2" lat="32"/>{way}</osm>'
        else:
            xml = f"<osm>{way}</osm>"
        yield SimpleNamespace(read=lambda: xml.encode("utf-8"))

    monkeypatch.setattr(repair, "urlopen", fake_open)
    if state == "proposed":
        with pytest.raises(RuntimeError):
            repair.recover(target, manifest, tmp_path / "report.json")
        assert target.read_bytes() == original
    else:
        repair.recover(target, manifest, tmp_path / "report.json")
        result = json.loads(target.read_text(encoding="utf-8"))
        assert len(result["features"]) == 3
        assert result["features"][-1]["geometry"]["coordinates"] == [
            [117.1, 32],
            [117.2, 32],
        ]
        assert component_count(result["features"]) == 1
        assert (
            list((tmp_path / "hefei-s1-backups").rglob("layer.json"))[0].read_bytes()
            == original
        )
        first_report = (tmp_path / "report.json").read_bytes()
        repair.recover(target, manifest, tmp_path / "report.json")
        assert (tmp_path / "report.json").read_bytes() == first_report
        assert len(json.loads(target.read_text(encoding="utf-8"))["features"]) == 3
