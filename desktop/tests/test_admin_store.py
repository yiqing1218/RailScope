import sqlite3
import pytest
from desktop.admin_store import build_index, viewport, _simplify_ring


def test_administrative_levels_and_spatial_query(tmp_path):
    osmium = pytest.importorskip("osmium")
    pbf = tmp_path / "admin.osm.pbf"
    with osmium.SimpleWriter(str(pbf)) as writer:
        for n, xy in enumerate([(120,30),(121,30),(121,31),(120,31)],1):
            writer.add_node(osmium.osm.mutable.Node(id=n, location=xy))
        writer.add_way(osmium.osm.mutable.Way(id=10, nodes=[1,2,3,4,1]))
        for ident, level in enumerate((4,5,6),100):
            writer.add_relation(osmium.osm.mutable.Relation(id=ident, members=[('w',10,'outer')],
                tags={"type":"boundary","boundary":"administrative","admin_level":str(level),"name":f"区域{level}"}))
        writer.add_relation(osmium.osm.mutable.Relation(id=200, members=[('w',10,'outer')],
            tags={"type":"multipolygon","landuse":"residential"}))
    database = tmp_path / "admin.sqlite"
    assert build_index(pbf,database) == 3
    for level in (4,5,6):
        result = viewport(database,[119,29,122,32],level,5)
        assert len(result["features"]) == 1
        assert result["features"][0]["properties"]["admin_level"] == level
        assert result["features"][0]["properties"]["verification_status"] == "source_unverified"
        assert viewport(database,[0,0,1,1],level,12)["features"] == []
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM bounds").fetchone()[0] == 3
    assert viewport(tmp_path/"absent.sqlite",[0,0,1,1])["missing"]
    with pytest.raises(ValueError):
        viewport(database,[float('nan'),0,1,1])


def test_closed_rings_survive_display_simplification():
    ring = [[0,0],[.001,0],[1,0],[1,1],[0,1],[0,0]]
    result = _simplify_ring(ring,.02)
    assert result[0] == result[-1]
    assert 4 <= len(result) < len(ring)
