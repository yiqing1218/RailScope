"""Source-backed campus outlines and POIs, one shared campus directory ID."""
from contextlib import closing
from datetime import datetime, timezone
import gc
import json
import math
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4


UNIVERSITY = dict(key="university", directory="universities", table="campuses", id_key="campus_id",
                  name_key="university_name", noun="校园", label="大学/学院", namespace="university_campus",
                  prefix="UNI", node_prefix="campus:", tags=(("amenity", "university"), ("amenity", "college")),
                  match_keys=("wikidata",))
AIRPORT = dict(key="airport", directory="airports", table="airports", id_key="airport_id",
               name_key="airport_name", noun="机场", label="机场", namespace="airport", prefix="APT",
               node_prefix="airport:", tags=(("aeroway", "aerodrome"),), match_keys=("wikidata", "icao", "iata"))


PROVINCES = dict(zip(
    "北京 天津 上海 重庆 河北 山西 辽宁 吉林 黑龙江 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 海南 四川 贵州 云南 陕西 甘肃 青海 台湾 内蒙古 广西 西藏 宁夏 新疆 香港 澳门".split(),
    "北京市 天津市 上海市 重庆市 河北省 山西省 辽宁省 吉林省 黑龙江省 江苏省 浙江省 安徽省 福建省 江西省 山东省 河南省 湖北省 湖南省 广东省 海南省 四川省 贵州省 云南省 陕西省 甘肃省 青海省 台湾省 内蒙古自治区 广西壮族自治区 西藏自治区 宁夏回族自治区 新疆维吾尔自治区 香港特别行政区 澳门特别行政区".split()))
PROVINCE_ALIASES = dict(zip(
    "Beijing Tianjin Shanghai Chongqing Hebei Shanxi Liaoning Jilin Heilongjiang Jiangsu Zhejiang Anhui Fujian Jiangxi Shandong Henan Hubei Hunan Guangdong Hainan Sichuan Guizhou Yunnan Shaanxi Gansu Qinghai Taiwan Inner_Mongolia Guangxi Tibet Ningxia Xinjiang Hong_Kong Macau".lower().split(), PROVINCES.values()))
PROVINCE_ALIASES.update({"廣東省": "广东省", "澳門": "澳门特别行政区", "gwongdung": "广东省"})


def normalize_province(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value in PROVINCES.values():
        return value
    if value in PROVINCES:
        return PROVINCES[value]
    if value.lower().replace(" ", "_") in PROVINCE_ALIASES:
        return PROVINCE_ALIASES[value.lower().replace(" ", "_")]
    return value if value.endswith(("省", "自治区")) else None


def database_path(root, *, profile=UNIVERSITY):
    base = Path(root) / "data/processed" / profile["directory"]
    pointer = base / "active.json"
    if pointer.is_file():
        name = json.loads(pointer.read_text(encoding="utf-8"))["dataset"]
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".sqlite"):
            raise ValueError(profile["label"]+"图层数据指针无效")
        return base / "datasets" / name
    return base / (profile["directory"]+".sqlite")


def install_index(root, pbf, progress=None, *, profile=UNIVERSITY):
    root = Path(root)
    base = root / "data/processed" / profile["directory"]
    name = uuid4().hex+".sqlite"
    output = base / "datasets" / name
    count = build_index(pbf, output, root / "data/user_settings/workspace.sqlite",
                        root / "data/processed/admin/admin.sqlite", progress, profile=profile)
    # Existing readers keep their old immutable snapshot, including on Windows.
    temporary = base / "active.json.tmp"
    temporary.write_text(json.dumps({"dataset": name}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(base / "active.json")
    return count


def _schema(db, profile=UNIVERSITY):
    db.executescript(f"""
        CREATE TABLE {profile["table"]}(id TEXT PRIMARY KEY,name TEXT,province TEXT,city TEXT,data TEXT);
        CREATE TABLE features(id INTEGER PRIMARY KEY,{profile["id_key"]} TEXT,kind TEXT,data TEXT);
        CREATE INDEX campus_features ON features({profile["id_key"]},kind);
        CREATE VIRTUAL TABLE bounds USING rtree(id,minx,maxx,miny,maxy);
        CREATE TABLE raw_features(source_id TEXT PRIMARY KEY,data TEXT);
        CREATE TABLE directory_nodes(id TEXT PRIMARY KEY,parent_id TEXT,label TEXT,kind TEXT,
            object_id TEXT,path TEXT,child_count INTEGER,total INTEGER,archived INTEGER);
        CREATE INDEX directory_parent ON directory_nodes(parent_id,label);
        CREATE INDEX directory_objects ON directory_nodes(object_id);
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    """)


def ensure_index(path, *, profile=UNIVERSITY):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            _schema(db, profile)
            db.commit()
    return path


class AdministrativeLookup:
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True) if path and Path(path).is_file() else None
        self.shapes = {}

    def locate(self, coordinates, tags):
        from shapely.geometry import Point, shape
        province = normalize_province(tags.get("addr:province") or tags.get("addr:state"))
        city = tags.get("addr:city")
        if city and not city.endswith(("市", "州", "地区", "盟")):
            city = None
        if self.db:
            x, y = coordinates
            point = Point(x, y)
            rows = self.db.execute("SELECT f.id,f.level,f.medium FROM features f JOIN bounds b ON b.id=f.id "
                                   "WHERE f.level IN (4,5) AND b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=?",
                                   (x, x, y, y))
            matches = []
            for ident, level, raw in rows:
                if ident not in self.shapes:
                    feature = json.loads(raw)
                    self.shapes[ident] = (shape(feature["geometry"]), feature["properties"].get("name:zh") or feature["properties"]["name"])
                geometry, name = self.shapes[ident]
                if geometry.covers(point):
                    matches.append((geometry.area, level, name))
            provinces = {normalize_province(name) for _, level, name in matches if level == 4}
            provinces.discard(None)
            cities = {name for _, level, name in matches if level == 5}
            if len(provinces) == 1:
                province = provinces.pop()
            if len(cities) == 1:
                city = cities.pop()
        if province in ("北京市", "上海市", "天津市", "重庆市", "香港特别行政区", "澳门特别行政区"):
            city = province
        return province or "未归属省份", city or "未归属城市"

    def close(self):
        if self.db:
            self.db.close()


def group_sources(records, *, profile=UNIVERSITY):
    """Only associate named POIs that fall inside a matching real campus."""
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    polygons, points = [], []
    for record in records:
        geometry = shape(record["geometry"])
        if geometry.is_empty or not geometry.is_valid:
            continue
        if geometry.geom_type in ("Polygon", "MultiPolygon"):
            polygons.append((record, geometry))
        elif geometry.geom_type == "Point":
            points.append((record, geometry))
    groups = [{"outline": record, "geometry": geometry, "pois": []} for record, geometry in polygons]
    tree = STRtree([geometry for _, geometry in polygons]) if polygons else None
    def name(record):
        tags = record["properties"]["source_tags"]
        return (tags.get("name:zh") or tags.get("name") or "").replace(" ", "").strip()
    for record, point in points:
        candidates = []
        for index in tree.query(point, predicate="intersects") if tree else []:
            other = polygons[int(index)][0]
            a, b = record["properties"]["source_tags"], other["properties"]["source_tags"]
            match = bool(name(record) and name(record) == name(other)) or any(a.get(key) and a.get(key) == b.get(key) for key in profile["match_keys"])
            if match and polygons[int(index)][1].covers(point):
                candidates.append(int(index))
        if len(candidates) == 1:
            groups[candidates[0]]["pois"].append(record)
        else:
            groups.append({"outline": None, "geometry": point, "pois": [record]})
    return groups


def write_index(records, output, identity_path, admin_path=None, snapshot="unknown", progress=None, *, profile=UNIVERSITY):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from railscope.identity import IdentityRegistry
    from shapely.geometry import mapping
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name+"."+uuid4().hex+".tmp")
    registry = IdentityRegistry(identity_path)
    lookup = AdministrativeLookup(admin_path)
    db = sqlite3.connect(temporary)
    _schema(db, profile)
    try:
        for record in records:
            db.execute("INSERT OR REPLACE INTO raw_features VALUES(?,?)",
                       (record["properties"]["source_id"], json.dumps(record, ensure_ascii=False)))
        groups = group_sources(records, profile=profile)
        folders = {}
        feature_id = 0
        with closing(sqlite3.connect(registry.path)) as identities:
            for number, group in enumerate(groups, 1):
                outline, pois, geometry = group["outline"], group["pois"], group["geometry"]
                primary = outline or pois[0]
                source = primary["properties"]["source_id"]
                ident = registry.resolve_alias(profile["namespace"], source, profile["prefix"], identities)
                tags = primary["properties"]["source_tags"]
                name = tags.get("name:zh") or tags.get("name") or ("未命名"+profile["label"])
                anchor = geometry.representative_point()
                coordinates = [anchor.x, anchor.y]
                province, city = lookup.locate(coordinates, tags)
                sources = ([outline["properties"]["source_id"]] if outline else []) + [p["properties"]["source_id"] for p in pois]
                properties = {profile["id_key"]: ident, "infrastructure_id": ident, "name": name, profile["name_key"]: name,
                              "kind": ("aerodrome" if profile is AIRPORT else "university" if tags.get("amenity") == "university" else "college"),
                              "province": province, "city": city, "source": "OpenStreetMap", "snapshot": snapshot,
                              "verification_status": "source_unverified", "association_status": "automatic_reference" if outline and pois else "source_object",
                              "source_member_ids": sources, "source_tags": tags, "license": "ODbL 1.0",
                              "attribution": "© OpenStreetMap contributors", "has_outline": bool(outline),
                              "boundary_status": "source_polygon" if outline else "missing_source_outline",
                              "directory_status": "source_address_or_admin_reference"}
                if profile is AIRPORT:
                    for key in ("iata", "icao", "operator", "aerodrome:type"):
                        values = {record["properties"]["source_tags"][key] for record in ([outline] if outline else [])+pois
                                  if record["properties"]["source_tags"].get(key)}
                        if len(values) == 1:
                            properties[key] = values.pop()
                data = {"properties": properties, "bounds": list(geometry.bounds), "center": coordinates}
                db.execute(f"INSERT INTO {profile['table']} VALUES(?,?,?,?,?)", (ident, name, province, city, json.dumps(data, ensure_ascii=False)))
                rendered = []
                if outline:
                    rendered.append(("outline", {"type": "Feature", "properties": {**properties, "geometry_origin": "source_polygon"}, "geometry": outline["geometry"]}))
                # A label anchor is explicitly derived, never an invented boundary.
                poi_geometry = pois[0]["geometry"] if pois else dict(mapping(anchor))
                rendered.append(("poi", {"type": "Feature", "properties": {**properties, "geometry_origin": "source_poi" if pois else "derived_label_anchor"}, "geometry": poi_geometry}))
                for kind, feature in rendered:
                    feature_id += 1
                    db.execute("INSERT INTO features VALUES(?,?,?,?)", (feature_id, ident, kind, json.dumps(feature, ensure_ascii=False)))
                    bounds = geometry.bounds if kind == "outline" else (*feature["geometry"]["coordinates"],)*2
                    west, south, east, north = bounds
                    db.execute("INSERT INTO bounds VALUES(?,?,?,?,?)", (feature_id, west, east, south, north))
                path = [province, city]
                parent = ""
                for depth in (1, 2):
                    parts = path[:depth]
                    key = "folder:"+json.dumps(parts, ensure_ascii=False)
                    if key not in folders:
                        folders[key] = {"parent": parent, "path": parts, "total": 0}
                    folders[key]["total"] += 1
                    parent = key
                db.execute("INSERT INTO directory_nodes VALUES(?,?,?,'object',?,?,0,1,0)",
                           (profile["node_prefix"]+ident, parent, name, ident, json.dumps(path, ensure_ascii=False)))
                if progress and number % 500 == 0:
                    progress(f"已关联 {number:,} 个{profile['noun']}的轮廓与 POI…")
            for key, folder in folders.items():
                child_count = sum(row[0] == key for row in ((f["parent"],) for f in folders.values()))
                child_count += db.execute("SELECT count(*) FROM directory_nodes WHERE parent_id=?", (key,)).fetchone()[0]
                db.execute("INSERT INTO directory_nodes VALUES(?,?,?,'folder',NULL,?,?,?,0)",
                           (key, folder["parent"], folder["path"][-1], json.dumps(folder["path"], ensure_ascii=False), child_count, folder["total"]))
            db.executemany("INSERT INTO metadata VALUES(?,?)", [("schema", "railscope."+profile["key"]+".v1"), ("snapshot", snapshot),
                ("imported_at", datetime.now(timezone.utc).isoformat()), (profile["table"], str(len(groups))),
                ("source_features", str(len(records))), ("outlines", str(sum(bool(g["outline"]) for g in groups)))])
            db.commit()
            identities.commit()
        db.close()
        temporary.replace(output)
        return len(groups)
    except BaseException:
        db.close()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        lookup.close()


def build_index(pbf, output, identity_path, admin_path=None, progress=None, *, profile=UNIVERSITY):
    import osmium
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from railscope.services.importers.native_paths import native_path
    pbf, output = Path(pbf), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    location_index = native_path(output.with_name(profile["key"]+"-"+uuid4().hex+".idx"), output=True)
    records, processor = [], None
    if progress:
        progress(f"正在扫描{profile['label']} POI 与真实来源边界；坐标索引保存在磁盘…")
    try:
        amenity = osmium.filter.TagFilter(*profile["tags"])
        processor = (osmium.FileProcessor(str(native_path(pbf))).with_locations(f"sparse_file_array,{location_index}")
                     .with_areas(amenity).with_filter(osmium.filter.EntityFilter(osmium.osm.NODE | osmium.osm.AREA)).with_filter(amenity))
        factory = osmium.geom.GeoJSONFactory()
        for entity in processor:
            if entity.is_node():
                if not entity.location.valid():
                    continue
                source_id = f"node/{entity.id}"
                geometry = {"type": "Point", "coordinates": [entity.location.lon, entity.location.lat]}
            else:
                source_id = f"{'way' if entity.from_way() else 'relation'}/{entity.orig_id()}"
                try:
                    geometry = json.loads(factory.create_multipolygon(entity))
                except RuntimeError:
                    continue
            records.append({"type": "Feature", "properties": {"source_id": source_id, "source_tags": dict(entity.tags)}, "geometry": geometry})
            if progress and len(records) % 500 == 0:
                progress(f"已提取 {len(records):,} 个{profile['label']}来源要素…")
        if not records:
            raise ValueError(f"所选 OSM 快照未包含{profile['label']} POI 或可组装的来源边界")
        stat = pbf.stat()
        snapshot = f"{pbf.name}:{stat.st_size}:{stat.st_mtime_ns}"
        return write_index(records, output, identity_path, admin_path, snapshot, progress, profile=profile)
    finally:
        del processor
        gc.collect()
        try:
            Path(location_index).unlink(missing_ok=True)
        except PermissionError:
            pass


def viewport(path, bbox, selected=None, kind="all", zoom=14, overrides=None, *, profile=UNIVERSITY, limits=None):
    from viewport_settings import normalize, enforce_budget, scan_limit
    limits = normalize(limits)
    west, south, east, north = bbox
    if not all(math.isfinite(v) for v in (*bbox, zoom)) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError(profile["label"]+"图层视窗无效")
    if kind not in ("all", "poi", "outline"):
        raise ValueError(profile["label"]+"图层类型无效")
    if selected is not None and (not isinstance(selected, list) or len(selected) > 100000 or any(not isinstance(v, str) for v in selected)):
        raise ValueError(profile["label"]+"目录选择无效")
    result = {"type": "FeatureCollection", "features": [], "truncated": False}
    if not Path(path).is_file() or selected == []:
        return result
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)) as db:
        db.execute("CREATE TEMP TABLE selected(id TEXT PRIMARY KEY)")
        if selected is not None:
            db.executemany("INSERT OR IGNORE INTO selected VALUES(?)", ((key,) for key in selected))
        clauses = ["b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?"]
        args = [west, east, south, north]
        if selected is not None:
            clauses.append(f"f.{profile['id_key']} IN (SELECT id FROM selected)")
        if kind != "all":
            clauses.append("f.kind=?")
            args.append(kind)
        # Two rendered features are one campus. Keep their association together.
        scan = scan_limit(limits)
        ids = [row[0] for row in db.execute(f"SELECT DISTINCT f.{profile['id_key']} FROM features f JOIN bounds b ON f.id=b.id WHERE "+
               " AND ".join(clauses)+" LIMIT ?", [*args, scan+1])]
        result["truncated"] = len(ids) > scan
        for campus_id in ids[:scan]:
            for raw, in db.execute(f"SELECT data FROM features WHERE {profile['id_key']}=?"+(" AND kind=?" if kind != "all" else ""),
                                  (campus_id, kind) if kind != "all" else (campus_id,)):
                feature = json.loads(raw)
                edit = (overrides or {}).get(campus_id, {})
                if edit.get("name"):
                    feature["properties"].update({"name": edit["name"], profile["name_key"]: edit["name"], "display_name": edit["name"]})
                result["features"].append(feature)
    original = result['features']
    enforce_budget(result, limits)
    counts, accepted = {}, {}
    for feature in original:
        ident = feature['properties'][profile['id_key']]
        counts[ident] = counts.get(ident, 0) + 1
    for feature in result['features']:
        ident = feature['properties'][profile['id_key']]
        accepted[ident] = accepted.get(ident, 0) + 1
    # Never leave a campus outline and its label with different check states
    # merely because one representation could not fit the viewport budget.
    result['features'] = [f for f in result['features'] if accepted[f['properties'][profile['id_key']]] == counts[f['properties'][profile['id_key']]]]
    return enforce_budget(result, limits)
