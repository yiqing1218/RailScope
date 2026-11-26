//! Spatially indexed, lossless-source high zoom MVT generation.
use anyhow::{Context, Result, ensure};
use geo::{BooleanOps, Coord, Geometry, LineString, Polygon, Rect};
use geozero::{ToGeo, wkb::Wkb};
use rusqlite::{Connection, params};
use std::collections::{BTreeMap, HashMap};

pub const MAX_ZOOM: u8 = 22;
pub const LIMIT: usize = 8 * 1024 * 1024;
const EXTENT: i32 = 8192;
const BUFFER: i32 = 64;
const EDGE: f64 = (EXTENT + BUFFER) as f64;
const LEAD: f64 = -(BUFFER as f64);
#[derive(Clone, Copy)]
pub struct Address {
    pub z: u8,
    pub x: u32,
    pub y: u32,
}

impl Address {
    pub fn new(z: u8, x: u32, y: u32) -> Result<Self> {
        ensure!(
            z <= MAX_ZOOM && x < (1 << z) && y < (1 << z),
            "无效瓦片地址"
        );
        Ok(Self { z, x, y })
    }

    fn project(self, c: Coord) -> Coord {
        let y =
            c.y.clamp(-85.0511287798, 85.0511287798)
                .to_radians()
                .tan()
                .asinh();
        let span = f64::from(1_u32 << self.z);
        Coord {
            x: ((c.x + 180.0) / 360.0 * span - f64::from(self.x)) * f64::from(EXTENT),
            y: ((1.0 - y / std::f64::consts::PI) * 0.5 * span - f64::from(self.y))
                * f64::from(EXTENT),
        }
    }

    pub fn bounds(self) -> [f64; 4] {
        let span = f64::from(1_u32 << self.z);
        let buffer = f64::from(BUFFER) / f64::from(EXTENT);
        let x = f64::from(self.x);
        let y = f64::from(self.y);
        let lat = |row: f64| {
            (std::f64::consts::PI * (1.0 - 2.0 * row / span))
                .sinh()
                .atan()
                .to_degrees()
        };
        [
            ((x - buffer) / span * 360.0 - 180.0).max(-180.0),
            lat(y + 1.0 + buffer),
            ((x + 1.0 + buffer) / span * 360.0 - 180.0).min(180.0),
            lat(y - buffer),
        ]
    }
}

#[derive(Clone, Eq, Hash, PartialEq)]
enum Value {
    Text(String),
    UInt(u64),
}

#[derive(Default)]
struct Layer {
    keys: Vec<String>,
    key_index: HashMap<String, u64>,
    values: Vec<Value>,
    value_index: HashMap<Value, u64>,
    features: Vec<u8>,
}
impl Layer {
    fn push(&mut self, id: u64, kind: u64, properties: Vec<(&str, Value)>, commands: Vec<u8>) {
        if commands.is_empty() {
            return;
        }
        let mut tags = Vec::new();
        for (key, value) in properties {
            let key = *self.key_index.entry(key.into()).or_insert_with(|| {
                self.keys.push(key.into());
                self.keys.len() as u64 - 1
            });
            let index = *self.value_index.entry(value.clone()).or_insert_with(|| {
                self.values.push(value);
                self.values.len() as u64 - 1
            });
            varint(&mut tags, key);
            varint(&mut tags, index);
        }
        let mut feature = Vec::new();
        scalar(&mut feature, 1, id);
        field(&mut feature, 2, &tags);
        scalar(&mut feature, 3, kind);
        field(&mut feature, 4, &commands);
        field(&mut self.features, 2, &feature);
    }

    fn encode(self, name: &str) -> Vec<u8> {
        let mut layer = Vec::new();
        field(&mut layer, 1, name.as_bytes());
        layer.extend(self.features);
        for key in self.keys {
            field(&mut layer, 3, key.as_bytes());
        }
        for value in self.values {
            let mut encoded = Vec::new();
            match value {
                Value::Text(value) => field(&mut encoded, 1, value.as_bytes()),
                Value::UInt(value) => scalar(&mut encoded, 5, value),
            }
            field(&mut layer, 4, &encoded);
        }
        scalar(&mut layer, 5, EXTENT as u64);
        scalar(&mut layer, 15, 2);
        layer
    }
}

fn varint(buf: &mut Vec<u8>, mut value: u64) {
    while value >= 128 {
        buf.push((value as u8) | 128);
        value >>= 7;
    }
    buf.push(value as u8);
}
fn scalar(buf: &mut Vec<u8>, key: u64, value: u64) {
    varint(buf, key << 3);
    varint(buf, value);
}
fn field(buf: &mut Vec<u8>, key: u64, value: &[u8]) {
    varint(buf, (key << 3) | 2);
    varint(buf, value.len() as u64);
    buf.extend_from_slice(value);
}

type Pixel = (i32, i32);
fn quantize(c: Coord) -> Pixel {
    (c.x.round() as i32, c.y.round() as i32)
}
fn inside(c: Coord) -> bool {
    c.x >= LEAD && c.x <= EDGE && c.y >= LEAD && c.y <= EDGE
}
fn delta(buf: &mut Vec<u8>, cursor: &mut Pixel, pixel: Pixel) {
    for d in [pixel.0 - cursor.0, pixel.1 - cursor.1] {
        varint(buf, ((d << 1) ^ (d >> 31)) as u32 as u64);
    }
    *cursor = pixel;
}

fn emit_path(buf: &mut Vec<u8>, cursor: &mut Pixel, mut points: Vec<Pixel>, ring: Option<bool>) {
    points.dedup();
    if let Some(outer) = ring {
        if points.first() == points.last() {
            points.pop();
        }
        if points.len() < 3 {
            return;
        }
        let sum: i64 = points
            .iter()
            .zip(points.iter().cycle().skip(1))
            .map(|(a, b)| i64::from(a.0) * i64::from(b.1) - i64::from(b.0) * i64::from(a.1))
            .sum();
        if sum == 0 {
            return;
        }
        if (sum > 0) != outer {
            points.reverse();
        }
    } else if points.len() < 2 {
        return;
    }
    varint(buf, 9);
    delta(buf, cursor, points[0]);
    varint(buf, ((points.len() as u64 - 1) << 3) | 2);
    for point in points.into_iter().skip(1) {
        delta(buf, cursor, point);
    }
    if ring.is_some() {
        varint(buf, 15);
    }
}

fn segment(a: Coord, b: Coord) -> Option<(Coord, Coord)> {
    if inside(a) && inside(b) {
        return Some((a, b));
    }
    let (dx, dy) = (b.x - a.x, b.y - a.y);
    let (mut entering, mut leaving) = (0.0_f64, 1.0_f64);
    for (s, d) in [
        (a.x - LEAD, dx),
        (EDGE - a.x, -dx),
        (a.y - LEAD, dy),
        (EDGE - a.y, -dy),
    ] {
        if d > 0.0 {
            entering = entering.max(-s / d);
        } else if d < 0.0 {
            leaving = leaving.min(-s / d);
        } else if s < 0.0 {
            return None;
        }
    }
    if entering > leaving {
        return None;
    }
    let position = |t| Coord {
        x: a.x + dx * t,
        y: a.y + dy * t,
    };
    Some((position(entering), position(leaving)))
}

fn emit_line(buf: &mut Vec<u8>, cursor: &mut Pixel, line: LineString, addr: Address) {
    let mut path = Vec::new();
    for pair in line.0.windows(2) {
        if let Some((a, b)) = segment(addr.project(pair[0]), addr.project(pair[1])) {
            let (a, b) = (quantize(a), quantize(b));
            if path.last() != Some(&a) {
                emit_path(buf, cursor, std::mem::take(&mut path), None);
                path.push(a);
            }
            path.push(b);
        } else {
            emit_path(buf, cursor, std::mem::take(&mut path), None);
        }
    }
    emit_path(buf, cursor, path, None);
}

fn emit_polygon(buf: &mut Vec<u8>, cursor: &mut Pixel, part: Polygon, addr: Address) {
    let project = |line: &LineString| LineString(line.0.iter().map(|c| addr.project(*c)).collect());
    let projected = Polygon::new(
        project(part.exterior()),
        part.interiors().iter().map(project).collect(),
    );
    let emit = |part: &Polygon, buf: &mut Vec<u8>, cursor: &mut Pixel| {
        let before = buf.len();
        emit_path(
            buf,
            cursor,
            part.exterior().0.iter().map(|c| quantize(*c)).collect(),
            Some(true),
        );
        if buf.len() > before {
            for ring in part.interiors() {
                emit_path(
                    buf,
                    cursor,
                    ring.0.iter().map(|c| quantize(*c)).collect(),
                    Some(false),
                );
            }
        }
    };
    if projected.exterior().0.iter().all(|c| inside(*c)) {
        emit(&projected, buf, cursor);
    } else {
        let clip = Rect::new((LEAD, LEAD), (EDGE, EDGE)).to_polygon();
        for part in projected.intersection(&clip) {
            emit(&part, buf, cursor);
        }
    }
}

fn geometry(value: Geometry, addr: Address) -> (u64, Vec<u8>) {
    let mut buf = Vec::new();
    let mut cursor = (0, 0);
    let kind = match value {
        Geometry::Point(value) => {
            let point = addr.project(value.0);
            if inside(point) {
                varint(&mut buf, 9);
                delta(&mut buf, &mut cursor, quantize(point));
            }
            1
        }
        Geometry::MultiPoint(value) => {
            let points = value
                .0
                .iter()
                .map(|p| addr.project(p.0))
                .filter(|p| inside(*p))
                .collect::<Vec<_>>();
            if !points.is_empty() {
                varint(&mut buf, (points.len() as u64 * 8) | 1);
                for point in points {
                    delta(&mut buf, &mut cursor, quantize(point));
                }
            }
            1
        }
        Geometry::LineString(line) => {
            emit_line(&mut buf, &mut cursor, line, addr);
            2
        }
        Geometry::MultiLineString(value) => {
            for line in value {
                emit_line(&mut buf, &mut cursor, line, addr);
            }
            2
        }
        Geometry::Polygon(value) => {
            emit_polygon(&mut buf, &mut cursor, value, addr);
            3
        }
        Geometry::MultiPolygon(value) => {
            for part in value {
                emit_polygon(&mut buf, &mut cursor, part, addr);
            }
            3
        }
        _ => 0,
    };
    (kind, buf)
}

pub fn normalized_colour(value: &str) -> Option<String> {
    let trimmed = value.trim();
    let hex = trimmed.strip_prefix('#').unwrap_or(trimmed);
    if hex.len() == 6 && hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Some(format!("#{}", hex.to_ascii_lowercase()));
    }
    if trimmed.starts_with('#') && hex.len() == 3 && hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Some(format!(
            "#{}",
            hex.to_ascii_lowercase()
                .chars()
                .flat_map(|c| [c, c])
                .collect::<String>()
        ));
    }
    let named = match trimmed.to_ascii_lowercase().as_str() {
        "red" => "#ff0000",
        "green" => "#008000",
        "blue" => "#0000ff",
        "orange" => "#ffa500",
        "yellow" => "#ffd42a",
        "purple" => "#800080",
        "brown" => "#a52a2a",
        "pink" => "#ff69b4",
        "gray" | "grey" => "#808080",
        "black" => "#000000",
        "white" => "#ffffff",
        "cyan" => "#00ffff",
        _ => return None,
    };
    Some(named.into())
}

const METRO: &str = "SELECT l.id, COALESCE(NULLIF(json_extract(src.tags,'$.ref'),''),l.name),
       COALESCE(json_extract(src.tags,'$.colour'),'')
       FROM line_member m JOIN entity l ON l.id=m.line_id JOIN source_object src ON src.ref=l.source_ref
       WHERE m.member_id=? AND l.kind='line' ORDER BY l.id LIMIT 64";

#[derive(Clone)]
struct BusinessLabel {
    id: u64,
    key: String,
    text: String,
    layer: &'static str,
}

fn label_text(name: String, short: String, mode: &str) -> String {
    if mode == "subway" && !short.is_empty() {
        if short.bytes().all(|b| b.is_ascii_digit()) {
            return format!("{short}号线");
        }
        return short;
    }
    if name.trim().is_empty() { short } else { name }
}

fn line_weight(shape: &Geometry, addr: Address) -> f64 {
    let Geometry::LineString(line) = shape else {
        return 0.;
    };
    line.0
        .windows(2)
        .filter_map(|p| segment(addr.project(p[0]), addr.project(p[1])))
        .map(|(a, b)| (a.x - b.x).hypot(a.y - b.y))
        .sum::<f64>()
}

pub fn generate(connection: &Connection, addr: Address) -> Result<Vec<u8>> {
    let [west, south, east, north] = addr.bounds();
    let mut query=connection.prepare(
        "SELECT e.id,e.render_id,e.kind,e.class,e.status,e.name,g.wgs84_wkb,src.tags,e.verification FROM spatial s
         CROSS JOIN geometry g ON g.rowid=s.rowid CROSS JOIN entity e ON e.id=g.entity_id
         LEFT JOIN source_object src ON src.ref=e.source_ref AND e.kind IN ('rail','metro','station','facility','road')
         WHERE s.max_x>=?1 AND s.max_y>=?2 AND s.min_x<=?3 AND s.min_y<=?4 AND g.wgs84_wkb IS NOT NULL
         ORDER BY g.rowid",
    )?;
    let new_source: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_schema WHERE name='metro_service_track')",
        [],
        |r| r.get(0),
    )?;
    let mut metro = connection.prepare_cached(if new_source {
        "SELECT DISTINCT l.id, COALESCE(NULLIF(json_extract(src.tags,'$.ref'),''),l.name),
         COALESCE(m.colour,'') FROM metro_service_track m JOIN entity l ON l.id=m.service_id
         JOIN source_object src ON src.ref=l.source_ref WHERE m.edge_id=? ORDER BY l.id LIMIT 64"
    } else {
        METRO
    })?;
    let mut memberships=connection.prepare_cached(
        "SELECT l.render_id, l.name, COALESCE(json_extract(t.tags,'$.ref'),''),
            json_extract(t.tags,'$.route'),COALESCE(json_extract(t.tags,'$.short_name'),'')
         FROM line_member m JOIN entity l ON l.id=m.line_id JOIN source_object t ON t.ref=l.source_ref
         WHERE m.member_id=? AND l.status NOT IN ('construction','planned','disused')
           AND json_extract(t.tags,'$.route') IN ('railway','subway','light_rail','tram','road')
         ORDER BY l.render_id LIMIT 24")?;
    let mut station_links=connection.prepare_cached(
        "SELECT count(DISTINCT m.line_id), group_concat(DISTINCT l.render_id) FROM line_member m JOIN entity l ON l.id=m.line_id
         WHERE m.member_id=? AND l.status NOT IN ('construction','planned','disused') AND l.class IN ('railway','subway','light_rail','tram','train')")?;
    let mut names: BTreeMap<(&'static str, String), (BusinessLabel, u64, f64, Vec<u8>)> =
        BTreeMap::new();
    let mut relation_cache = HashMap::<String, Vec<BusinessLabel>>::new();
    let mut rows = query.query(params![west, south, east, north])?;
    let mut layers: BTreeMap<&str, Layer> = BTreeMap::new();
    let mut used = 0;
    while let Some(row) = rows.next()? {
        let kind: String = row.get(2)?;
        let layer = match kind.as_str() {
            "rail" => "rail",
            "metro" => "metro",
            "road" => "road",
            "station" | "poi" => "points",
            "facility" if addr.z >= 11 => "facilities",
            "base_road" if addr.z >= 10 => "base_road",
            "base_building" if addr.z >= 14 => "base_building",
            "base_water" => "base_water",
            "base_green" => "base_green",
            "base_place" => "base_place",
            "base_admin" => "base_admin",
            _ => continue,
        };
        let id = row.get::<_, i64>(1)? as u64;
        let category: String = row.get(3)?;
        let status: String = row.get(4)?;
        let name: String = row.get(5)?;
        let bytes: Vec<u8> = row.get(6)?;
        let shape = Wkb(bytes)
            .to_geo()
            .with_context(|| format!("无效 WKB · {id}"))?;
        let rail_priority = match category.as_str() {
            "high_speed" => 0,
            "conventional" => 1,
            "freight" => 2,
            "motorway" | "subway" | "light_rail" => 1,
            _ => 4,
        };
        let weight = if status == "operating"
            && addr.z <= 16
            && rail_priority < 4
            && (layer != "road" || addr.z >= 10)
            && matches!(layer, "rail" | "metro" | "road")
        {
            line_weight(&shape, addr)
        } else {
            0.
        };
        let (shape_kind, commands) = geometry(shape, addr);
        if commands.is_empty() {
            continue;
        }
        let mut attrs = vec![
            ("rid", Value::UInt(id)),
            ("class", Value::Text(category.clone())),
            ("status", Value::Text(status.clone())),
        ];
        let source = row.get::<_, Option<String>>(7)?;
        let mut point_label = name.as_str();
        if let Some(source) = &source {
            let tags = serde_json::from_str(source)?;
            let verified = row.get::<_, String>(8)? == "verified";
            let facts = crate::cartography::classify(&tags, &kind, &category, &status, verified);
            attrs.push(("symbol", Value::Text(facts.render_class.into())));
            attrs.push(("life", Value::Text(facts.lifecycle.into())));
            if kind == "station" {
                if !tags["name:zh"]
                    .as_str()
                    .is_some_and(|s| !s.trim().is_empty())
                    && !tags["name"].as_str().is_some_and(|s| !s.trim().is_empty())
                    && !tags["ref"].as_str().is_some_and(|s| !s.trim().is_empty())
                {
                    // Retain an empty label slot so a workspace name can still be rendered.
                    point_label = "";
                }
                let (links, line_ids): (u32, Option<String>) = station_links
                    .query_row([row.get::<_, String>(0)?], |r| Ok((r.get(0)?, r.get(1)?)))?;
                if let Some(ids) = line_ids.filter(|ids| ids.len() <= 480) {
                    attrs.push(("line_rids", Value::Text(format!(",{ids},"))));
                }
                let min_z: u64 = match facts.render_class {
                    "metro_interchange" => 10,
                    "metro_station" if links >= 3 => 11,
                    "metro_station" => 12,
                    "rail_halt" | "tram_stop" => 13,
                    _ if facts.grade.is_some() || links >= 2 => 9,
                    _ => 11,
                };
                attrs.push(("min_z", Value::UInt(min_z)));
                attrs.push(("rank", Value::UInt(min_z.saturating_sub(8))));
            }
            if layer == "facilities" {
                let label = tags["name:zh"]
                    .as_str()
                    .or(tags["name"].as_str())
                    .or(tags["ref"].as_str());
                if let Some(label) = label.filter(|name| !name.is_empty()) {
                    attrs.push(("label", Value::Text(label.to_owned())));
                }
            }
            if matches!(kind.as_str(), "rail" | "metro") {
                if let Some(limit) = facts.speed_kmh {
                    attrs.push((
                        "speed",
                        Value::UInt(
                            [80., 160., 250., 350.]
                                .iter()
                                .filter(|n| limit > **n)
                                .count() as u64
                                + 1,
                        ),
                    ));
                }
                if let Some(gauge) = facts.gauge_mm {
                    attrs.push(("gauge", Value::UInt(u64::from(gauge))));
                }
                if let Some(power) = facts.electrification {
                    attrs.push(("power", Value::Text(power.into())));
                }
            } else {
                if let Some(business) = facts.business {
                    let badge = match business {
                        "passenger" => "客",
                        "freight" => "货",
                        _ => "客货",
                    };
                    attrs.push(("business", Value::Text(badge.into())));
                }
                if let Some(technical) = facts.technical {
                    let badge = match technical {
                        "marshalling" => "编",
                        "district" => "区",
                        _ => "中",
                    };
                    attrs.push(("technical", Value::Text(badge.into())));
                }
                if let Some(scale) = facts.grade {
                    attrs.push(("size_pct", Value::UInt((scale * 100.).round() as u64)));
                }
            }
        }
        if layer == "points" || layer == "base_place" {
            attrs.push(("label", Value::Text(point_label.to_owned())));
        }
        // One representative physical segment per business identity in each visible tile.
        if weight >= 272. {
            let eid: String = row.get(0)?;
            let labels = if let Some(cached) = relation_cache.get(&eid) {
                cached.clone()
            } else {
                let found = memberships
                    .query_map([&eid], |r| {
                        let mode: String = r.get(3)?;
                        let id = r.get::<_, i64>(0)? as u64;
                        let name: String = r.get(1)?;
                        let reference: String = r.get(2)?;
                        let short: String = r.get(4)?;
                        let label = label_text(
                            name,
                            if short.is_empty() { reference } else { short },
                            &mode,
                        );
                        let layer = match mode.as_str() {
                            "road" => "road_names",
                            "railway" => "rail_names",
                            _ => "metro_names",
                        };
                        let key = if layer == "metro_names" {
                            label.clone()
                        } else {
                            id.to_string()
                        };
                        Ok(BusinessLabel {
                            id,
                            key,
                            text: label,
                            layer,
                        })
                    })?
                    .collect::<rusqlite::Result<Vec<_>>>()?;
                relation_cache.insert(eid, found.clone());
                found
            };
            for label in labels.into_iter().filter(|v| !v.text.trim().is_empty()) {
                if (layer == "rail" && label.layer != "rail_names")
                    || (layer == "road" && label.layer != "road_names")
                    || (layer == "metro" && label.layer != "metro_names")
                {
                    continue;
                }
                let key = (label.layer, label.key.clone());
                if names.get(&key).is_none_or(|(_, rank, length, _)| {
                    rail_priority < *rank || (rail_priority == *rank && weight > *length)
                }) {
                    names.insert(key, (label, rail_priority, weight, commands.clone()));
                }
            }
        }
        if layer == "metro" {
            let eid: String = row.get(0)?;
            let mut services = metro.query([eid])?;
            let mut distinct = Vec::new();
            while let Some(s) = services.next()? {
                let label: String = s.get(1)?;
                let colour: String = s.get(2)?;
                if distinct.iter().any(|(existing, _, _)| existing == &label) {
                    continue;
                }
                distinct.push((label, s.get::<_, String>(0)?, normalized_colour(&colour)));
            }
            let count = distinct.len();
            for ((key, color_key), (_, service, colour)) in [
                ("service1", "metro_color"),
                ("service2", "metro_secondary"),
                ("service3", "metro_tertiary"),
            ]
            .into_iter()
            .zip(distinct.iter())
            {
                attrs.push((key, Value::Text(service.clone())));
                if let Some(colour) = colour {
                    attrs.push((color_key, Value::Text(colour.clone())));
                }
            }
            if count > 0 {
                attrs.push(("service_count", Value::UInt(count as u64)));
            }
        }
        used += commands.len()
            + attrs
                .iter()
                .map(|(k, v)| {
                    k.len()
                        + match v {
                            Value::Text(s) => s.len(),
                            _ => 8,
                        }
                        + 8
                })
                .sum::<usize>()
            + 32;
        ensure!(
            used <= LIMIT * 2,
            "高精度瓦片超过资源预算，当前区域需继续放大"
        );
        layers
            .entry(layer)
            .or_default()
            .push(id, shape_kind, attrs, commands);
    }
    if new_source {
        let mut base = connection.prepare(
            "SELECT f.id,f.kind,f.class,f.name,f.wkb FROM base_spatial s
             CROSS JOIN base_feature f ON f.id=s.id
             WHERE s.max_x>=?1 AND s.max_y>=?2 AND s.min_x<=?3 AND s.min_y<=?4 ORDER BY f.id",
        )?;
        let mut features = base.query(params![west, south, east, north])?;
        while let Some(row) = features.next()? {
            let id = row.get::<_, i64>(0)? as u64;
            let key: String = row.get(1)?;
            let layer = match key.as_str() {
                "base_road" if addr.z >= 9 => "base_road",
                "base_building" if addr.z >= 14 => "base_building",
                "base_water" => "base_water",
                "base_green" => "base_green",
                "base_place" => "base_place",
                "base_admin" => "base_admin",
                _ => continue,
            };
            let shape = Wkb(row.get::<_, Vec<u8>>(4)?)
                .to_geo()
                .with_context(|| format!("无效底图 WKB · {id}"))?;
            let (shape_kind, commands) = geometry(shape, addr);
            if commands.is_empty() {
                continue;
            }
            let class: String = row.get(2)?;
            let name: String = row.get(3)?;
            if layer == "base_place"
                && addr.z < 11
                && !matches!(
                    class.as_str(),
                    "city" | "county" | "province" | "state" | "district" | "borough"
                )
            {
                continue;
            }
            if layer == "base_road"
                && addr.z < 11
                && !matches!(
                    class.as_str(),
                    "trunk" | "primary" | "secondary" | "trunk_link" | "primary_link"
                )
            {
                continue;
            }
            used += commands.len() + name.len() + class.len() + 64;
            ensure!(
                used <= LIMIT * 2,
                "高精度瓦片超过资源预算，当前区域需继续放大"
            );
            layers.entry(layer).or_default().push(
                id,
                shape_kind,
                vec![("class", Value::Text(class)), ("label", Value::Text(name))],
                commands,
            );
        }
    }
    for ((layer, _), (label, rank, _, geometry)) in names {
        layers.entry(layer).or_default().push(
            label.id,
            2,
            vec![
                ("rid", Value::UInt(label.id)),
                ("label", Value::Text(label.text)),
                ("rank", Value::UInt(rank)),
            ],
            geometry,
        );
    }
    let mut tile = Vec::new();
    for (name, layer) in layers {
        field(&mut tile, 3, &layer.encode(name));
    }
    ensure!(
        tile.len() <= LIMIT,
        "高精度瓦片超过资源预算，当前区域需继续放大"
    );
    Ok(tile)
}

/// Sparse geographic context for the archived transport overview.
pub fn generate_context(connection: &Connection, addr: Address) -> Result<Vec<u8>> {
    let [west, south, east, north] = addr.bounds();
    let extent = east - west;
    let mut statement = connection.prepare(
        "WITH picked AS MATERIALIZED (
         SELECT f.id FROM base_spatial s
         CROSS JOIN base_feature f ON f.id=s.id
         WHERE s.max_x>=?1 AND s.max_y>=?2 AND s.min_x<=?3 AND s.min_y<=?4
           AND (max(s.max_x-s.min_x,s.max_y-s.min_y)>=?8
                OR max(s.max_x-s.min_x,s.max_y-s.min_y)<0.0001)
           AND (
             (f.kind='base_place' AND f.class IN ('city','province','state','county')
                AND (?5>=7 OR f.class!='county'))
             OR (f.kind='base_admin' AND CAST(f.class AS INTEGER) BETWEEN 2 AND CASE WHEN ?5>=7 THEN 6 ELSE 4 END)
             OR (f.kind='base_water' AND max(s.max_x-s.min_x,s.max_y-s.min_y)>=?6)
             OR (f.kind='base_green' AND max(s.max_x-s.min_x,s.max_y-s.min_y)>=?7)
             OR (f.kind='base_road' AND (f.class IN ('trunk','primary') OR (?5>=8 AND f.class='secondary'))
                AND max(s.max_x-s.min_x,s.max_y-s.min_y)>=?8)
           )
         ORDER BY CASE f.kind WHEN 'base_place' THEN 0 WHEN 'base_water' THEN 1
             WHEN 'base_admin' THEN 2 WHEN 'base_green' THEN 3 ELSE 4 END,
             max(s.max_x-s.min_x,s.max_y-s.min_y) DESC, f.id
         LIMIT 2400)
         SELECT f.id,f.kind,f.class,f.name,f.wkb
         FROM picked CROSS JOIN base_feature f ON f.id=picked.id",
    )?;
    let min_linear_extent = extent / if addr.z >= 7 { 225. } else { 110. };
    let mut rows = statement.query(params![
        west,
        south,
        east,
        north,
        addr.z,
        extent / 38.,
        extent / 28.,
        min_linear_extent
    ])?;
    let mut layers = BTreeMap::<String, Layer>::new();
    let mut used = 0;
    while let Some(row) = rows.next()? {
        let id = row.get::<_, i64>(0)? as u64;
        let layer: String = row.get(1)?;
        let class: String = row.get(2)?;
        let name: String = row.get(3)?;
        let wkb: Vec<u8> = row.get(4)?;
        let (kind, commands) = geometry(Wkb(wkb).to_geo()?, addr);
        used += commands.len() + name.len() + class.len() + 64;
        if used > LIMIT / 2 {
            break;
        }
        layers.entry(layer).or_default().push(
            id,
            kind,
            vec![("class", Value::Text(class)), ("label", Value::Text(name))],
            commands,
        );
    }
    let mut result = Vec::new();
    for (layer, features) in layers {
        field(&mut result, 3, &features.encode(&layer));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_in_tile_turns_and_clips_outside_segments() {
        assert_eq!(
            segment(Coord { x: -100., y: 50. }, Coord { x: 0., y: 50. })
                .unwrap()
                .0
                .x,
            LEAD
        );
        assert!(segment(Coord { x: -100., y: 20. }, Coord { x: -90., y: 40. }).is_none());
        let addr = Address::new(19, 262144, 262144).unwrap();
        let points = LineString::from(vec![
            (0.0001, -0.0001),
            (0.0002, -0.0002),
            (0.00025, -0.0001),
        ]);
        let (_, commands) = geometry(points.into(), addr);
        assert!(commands.contains(&18)); // LineTo with both source vertices
    }
    #[test]
    fn respects_supported_osm_colour_forms() {
        assert_eq!(normalized_colour("#F0a").as_deref(), Some("#ff00aa"));
        assert!(normalized_colour("nonsense").is_none());
    }
}
