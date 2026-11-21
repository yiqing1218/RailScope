import gzip
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import mapbox_vector_tile
from pmtiles.reader import Reader
from pmtiles.tile import TileType
from shapely import from_wkb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))
from build_pack import build, checksum
from enrich_pack import enrich

OSM = """<osm version="0.6" generator="fixture">
  <node id="1" lat="31.20" lon="121.40" version="1"/>
  <node id="2" lat="31.21" lon="121.45" version="1"/>
  <node id="3" lat="31.22" lon="121.50" version="1">
    <tag k="railway" v="switch"/>
  </node>
  <node id="4" lat="31.23" lon="121.55" version="1"/>
  <node id="5" lat="31.25" lon="121.57" version="1"/>
  <node id="6" lat="31.15" lon="121.53" version="1"/>
  <node id="7" lat="31.26" lon="121.43" version="1"/>
  <node id="8" lat="31.21" lon="121.47" version="1">
    <tag k="railway" v="station"/><tag k="name" v="测试车站"/>
    <tag k="old_name" v="旧地名"/>
  </node>
  <node id="21" lat="31.22" lon="121.47" version="2">
    <tag k="railway" v="junction"/><tag k="ref" v="J-21"/>
  </node>
  <node id="22" lat="31.23" lon="121.47" version="3">
    <tag k="railway" v="signal"/><tag k="railway:signal:direction" v="forward"/>
  </node>
  <node id="23" lat="31.24" lon="121.47" version="1">
    <tag k="railway" v="subway_entrance"/>
  </node>
  <way id="10" version="1">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/>
    <tag k="railway" v="rail"/><tag k="highspeed" v="yes"/>
  </way>
  <way id="11" version="1">
    <nd ref="3"/><nd ref="5"/><tag k="railway" v="rail"/><tag k="highspeed" v="no"/>
  </way>
  <way id="12" version="1">
    <nd ref="6"/><nd ref="7"/><tag k="railway" v="rail"/>
    <tag k="highspeed" v="yes"/><tag k="bridge" v="yes"/><tag k="layer" v="1"/>
  </way>
  <way id="13" version="1">
    <nd ref="6"/><nd ref="7"/><tag k="highway" v="motorway"/><tag k="oneway" v="-1"/>
  </way>
  <way id="14" version="1">
    <nd ref="3"/><nd ref="4"/><tag k="railway" v="platform"/>
  </way>
  <way id="15" version="1">
    <nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="5"/>
    <tag k="landuse" v="railway"/>
  </way>
  <way id="16" version="1">
    <nd ref="1"/><nd ref="2"/><nd ref="7"/><nd ref="1"/>
    <tag k="building" v="train_station"/>
  </way>
  <relation id="20" version="1">
    <member type="way" ref="10" role=""/>
    <member type="node" ref="8" role="stop"/>
    <tag k="type" v="route"/><tag k="route" v="railway"/><tag k="name" v="测试线路"/>
  </relation>
</osm>"""


class PackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        original = root / "fixture.osm"
        original.write_text(OSM, encoding="utf-8")
        cls.original = original
        cls.pack = build(original, root / "packs", "Test", "fixture-v1", 9, "memory")
        cls.manifest = json.loads((cls.pack / "manifest.json").read_text(encoding="utf-8"))
        cls.db = sqlite3.connect(cls.pack / "canonical.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_topology_keeps_curve_and_separates_crossings(self):
        ways = self.db.execute(
            "SELECT source_ref,count(*) FROM entity JOIN edge USING(id) GROUP BY source_ref"
        ).fetchall()
        self.assertEqual(ways, [("osm:way:10", 2), ("osm:way:11", 1), ("osm:way:12", 1), ("osm:way:13", 1)])
        endpoint = self.db.execute(
            "SELECT node.network,count(*) FROM node WHERE source_ref='osm:node:6' GROUP BY node.network"
        ).fetchall()
        self.assertEqual(endpoint, [("rail", 1), ("road", 1)])
        self.assertFalse(self.db.execute("SELECT 1 FROM node WHERE source_ref='osm:node:2'").fetchone())
        self.assertEqual(self.db.execute(
            "SELECT forward,reverse FROM edge JOIN entity USING(id) WHERE source_ref='osm:way:13'"
        ).fetchone(), (0, 1))

    def test_search_and_business_membership(self):
        for query in ('"测试车站"*', '"旧地"*', '"ceshichezhan"*', '"cscz"*'):
            self.assertEqual(self.db.execute(
                "SELECT name FROM entity_search JOIN entity ON entity.id=entity_id WHERE entity_search MATCH ?",
                [query],
            ).fetchall(), [("测试车站",)])
        self.assertEqual(self.db.execute("SELECT count(*) FROM line_member").fetchone()[0], 3)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_render_ids_link_to_read_only_snapshot(self):
        for file, expected in self.manifest["files"].items():
            self.assertEqual(checksum(self.pack / file), expected)
        data = (self.pack / "transport.pmtiles").read_bytes()
        reader = Reader(lambda offset, length: data[offset:offset + length])
        self.assertEqual(reader.header()["tile_type"], TileType.MVT)
        lon, lat, z = 121.47, 31.21, 9
        x = int((lon + 180) / 360 * (1 << z))
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * (1 << z))
        tile = mapbox_vector_tile.decode(gzip.decompress(reader.get(z, x, y)))
        ids = {rid for (rid,) in self.db.execute("SELECT render_id FROM entity")}
        self.assertTrue(tile["rail"]["features"])
        for layer in tile.values():
            for feature in layer["features"]:
                self.assertIn(feature["properties"]["rid"], ids)
                self.assertNotIn("source_ref", feature["properties"])

    def test_enrichment_keeps_facility_source_types_and_geometry(self):
        enrich(self.pack, self.original, self.pack.parent, "fixture-enriched")
        with closing(sqlite3.connect(self.pack.parent / "fixture-enriched" / "canonical.sqlite")) as enriched:
            rows = enriched.execute("""
                SELECT e.source_ref,e.class,g.wgs84_wkb,s.tags
                FROM entity e JOIN source_object s ON s.ref=e.source_ref JOIN geometry g ON g.entity_id=e.id
                WHERE e.kind='facility'
            """).fetchall()
            classes = {ref: (kind, from_wkb(geometry).geom_type, json.loads(tags))
                       for ref, kind, geometry, tags in rows}
            for ref, kind, shape in [
                ("osm:node:21", "junction", "Point"),
                ("osm:node:22", "signal", "Point"),
                ("osm:node:23", "subway_entrance", "Point"),
                ("osm:way:14", "platform", "LineString"),
                ("osm:way:15", "railway_land", "Polygon"),
                ("osm:way:16", "station_building", "Polygon"),
            ]:
                self.assertEqual(classes[ref][:2], (kind, shape))
                self.assertEqual(sum(row[0] == ref for row in rows), 1)
            self.assertEqual(classes["osm:node:21"][2]["ref"], "J-21")
            self.assertEqual(classes["osm:node:22"][2]["railway:signal:direction"], "forward")


if __name__ == "__main__":
    unittest.main()
