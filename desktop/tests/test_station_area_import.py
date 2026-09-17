import json
import pytest
from desktop.import_station_areas import extract


def test_subway_platform_outline_and_previous_real_boundary_are_retained(tmp_path):
    pytest.importorskip("osmium")
    source = tmp_path / "platform.osm"
    source.write_text(
        """<osm version="0.6"><node id="1" lon="121" lat="31"/><node id="2" lon="121.001" lat="31"/><node id="3" lon="121.001" lat="31.001"/><way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="1"/><tag k="railway" v="platform"/><tag k="subway" v="yes"/><tag k="ref" v="2"/></way></osm>""",
        encoding="utf-8",
    )
    previous = tmp_path / "previous.geojson"
    previous.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "osm_relation_id": 20,
                            "source": "OpenStreetMap",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[121, 31], [121.01, 31], [121.01, 31.01], [121, 31]]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "areas.geojson"
    extract(source, output, previous=previous)
    features = json.loads(output.read_text(encoding="utf-8"))["features"]
    assert len(features) == 2
    assert features[0]["properties"]["boundary_kind"] == "platform"
    assert features[1]["properties"]["retained_previous_snapshot"] is True


def test_real_multipolygon_holes_and_raw_tags_preserved(tmp_path):
    pytest.importorskip("osmium")
    source = tmp_path / "stations.osm"
    source.write_text(
        """<osm version="0.6">
    <node id="1" lon="121" lat="31"/><node id="2" lon="121.01" lat="31"/>
    <node id="3" lon="121.01" lat="31.01"/><node id="4" lon="121" lat="31.01"/>
    <node id="5" lon="121.003" lat="31.003"/><node id="6" lon="121.007" lat="31.003"/>
    <node id="7" lon="121.007" lat="31.007"/><node id="8" lon="121.003" lat="31.007"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/></way>
    <way id="11"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/></way>
    <relation id="20"><member type="way" ref="10" role="outer"/>
    <member type="way" ref="11" role="inner"/><tag k="type" v="multipolygon"/>
    <tag k="railway" v="station"/><tag k="station" v="subway"/>
    <tag k="name" v="真实站区"/><tag k="operator" v="测试运营方"/></relation></osm>""",
        encoding="utf-8",
    )
    output = tmp_path / "areas.geojson"
    report = extract(source, output)
    assert report["types"] == {"relation": 1}
    feature = json.loads(output.read_text(encoding="utf-8"))["features"][0]
    assert feature["properties"]["station_area_tags"]["operator"] == "测试运营方"
    assert feature["properties"]["osm_relation_id"] == 20
    assert len(feature["geometry"]["coordinates"][0]) == 2
    extract(source, output)
    assert list(tmp_path.glob("*.bak"))
    assert not list(tmp_path.glob("*.tmp"))
