import json
import pytest
import sqlite3


def test_rail_path_crosses_lines_but_never_invents_junctions():
    from desktop.rail import compile_rail_plan

    edges = [
        {
            "id": "e1",
            "from_node": 1,
            "to_node": 2,
            "coordinates": [[121, 31], [121.01, 31]],
            "construction": False,
        },
        {
            "id": "e2",
            "from_node": 2,
            "to_node": 3,
            "coordinates": [[121.01, 31], [121.02, 31]],
            "construction": False,
        },
    ]
    payload = {
        "schema": "railscope.rail-plan.v1",
        "service_date": "2026-09-17",
        "timezone": "Asia/Shanghai",
        "source": "用户计划，非官方",
        "extensions": {},
        "required_capabilities": [],
        "trains": [
            {
                "id": "G_TEST",
                "path": [
                    {"edge_id": "e1", "direction": "forward"},
                    {"edge_id": "e2", "direction": "forward"},
                ],
                "stops": [
                    {"node_id": 1, "arrival_s": 25200, "departure_s": 25230},
                    {"node_id": 3, "arrival_s": 25380, "departure_s": 25410},
                ],
                "extensions": {},
            }
        ],
    }
    plan, lines = compile_rail_plan(payload, edges, [])
    assert len(lines) == 1 and plan.position("G_TEST", 25300)["state"] == "区间运行"
    payload["trains"][0]["path"][1]["direction"] = "reverse"
    with pytest.raises(ValueError, match="不连续"):
        compile_rail_plan(payload, edges, [])


def test_actual_rail_import_keeps_switches_platforms_tracks_and_raw_tags(tmp_path):
    pytest.importorskip("osmium")
    from desktop.import_rail import extract

    source = tmp_path / "rail.osm"
    source.write_text(
        """<osm version="0.6"><node id="1" lon="121" lat="31"><tag k="railway" v="station"/><tag k="name" v="A"/></node><node id="2" lon="121.01" lat="31"><tag k="railway" v="switch"/></node><node id="3" lon="121.02" lat="31"/><node id="4" lon="121.01" lat="31.01"/><way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="railway" v="rail"/><tag k="name" v="跨线区间"/><tag k="maxspeed" v="300"/></way><way id="11"><nd ref="2"/><nd ref="4"/><tag k="railway" v="rail"/><tag k="service" v="siding"/></way><way id="12"><nd ref="1"/><nd ref="2"/><nd ref="4"/><nd ref="1"/><tag k="railway" v="platform"/><tag k="train" v="yes"/><tag k="ref" v="1"/></way></osm>""",
        encoding="utf-8",
    )
    extract(source, tmp_path / "out")
    tracks = json.loads(
        (tmp_path / "out/rail_tracks.geojson").read_text(encoding="utf-8")
    )["features"]
    assert len(tracks) == 2 and tracks[0]["properties"]["way_tags"]["maxspeed"] == "300"
    graph = json.loads((tmp_path / "out/rail_graph.json").read_text(encoding="utf-8"))
    assert len(graph["edges"]) == 3
    from desktop.rail_store import viewport, load_edges

    visible = viewport(tmp_path / "out", "rail", [120.99, 30.99, 121.03, 31.02], 14)
    assert len(visible["features"]) == 2 and not visible["truncated"]
    assert len(load_edges(tmp_path / "out", [graph["edges"][0]["id"]])) == 1
    assert any(f["properties"].get("kind") == "switch" for f in graph["points"])
    assert (
        json.loads(
            (tmp_path / "out/rail_platforms.geojson").read_text(encoding="utf-8")
        )["features"][0]["geometry"]["type"]
        == "Polygon"
    )


def test_national_viewport_has_hard_feature_budget(tmp_path):
    from desktop.rail_store import VIEWPORT_FEATURES, viewport

    with sqlite3.connect(tmp_path / "rail.sqlite") as db:
        db.executescript(
            "CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,service TEXT,data TEXT);"
            "CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);"
        )
        feature = json.dumps({
            "type": "Feature",
            "properties": {"osm_node_id": 1},
            "geometry": {"type": "Point", "coordinates": [121, 31]},
        })
        db.executemany(
            "INSERT INTO features VALUES(?,?,?,?)",
            ((index, "railPoints", "main", feature) for index in range(1, VIEWPORT_FEATURES + 2)),
        )
        db.executemany(
            "INSERT INTO bounds VALUES(?,?,?,?,?)",
            ((index, 121, 121, 31, 31) for index in range(1, VIEWPORT_FEATURES + 2)),
        )
    hidden = viewport(tmp_path, "railPoints", [120, 30, 122, 32], 9)
    assert hidden["features"] == []
    visible = viewport(tmp_path, "railPoints", [120, 30, 122, 32], 14)
    assert len(visible["features"]) == VIEWPORT_FEATURES
    assert visible["truncated"] is True
