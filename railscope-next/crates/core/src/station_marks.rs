//! Display anchors for stations. Every original entity remains in the catalogue and graph.
use crate::cartography::classify;
use anyhow::Result;
use geo::{InteriorPoint, Point};
use geozero::{ToGeo, wkb::Wkb};
use rusqlite::Connection;
use serde::Serialize;
use std::collections::{BTreeSet, HashMap};
use unicode_normalization::UnicodeNormalization;

const NEARBY_METERS: f64 = 450.;

fn label_key(name: &str) -> String {
    name.nfkc()
        .filter(|ch| !ch.is_whitespace())
        .flat_map(char::to_lowercase)
        .collect()
}

fn nearby(a: Point, b: Point) -> bool {
    let north = (a.y() - b.y()) * 111_195.;
    let east = (a.x() - b.x()) * ((a.y() + b.y()) / 2.).to_radians().cos() * 111_195.;
    east.hypot(north) <= NEARBY_METERS
}

#[derive(Clone, Serialize)]
pub(crate) struct Display {
    pub(crate) render_id: u64,
    pub(crate) mode: &'static str,
    pub(crate) name: String,
}

pub(crate) struct Station {
    pub(crate) display: Display,
    pub(crate) point: Point,
    pub(crate) min_zoom: u64,
    pub(crate) interchange: bool,
    pub(crate) line_rids: BTreeSet<u64>,
    pub(crate) rail_peer: Option<(u64, u64)>,
    pub(crate) business: Option<&'static str>,
    pub(crate) technical: Option<&'static str>,
    rail_ref: String,
}

#[derive(Default)]
pub(crate) struct Stations {
    marks: Vec<Station>,
    owners: HashMap<u64, usize>,
    labels: HashMap<String, Vec<usize>>,
    by_longitude: Vec<usize>,
}

impl Stations {
    pub(crate) fn load(db: &Connection) -> Result<Self> {
        let mut query = db.prepare(
            "SELECT e.render_id,e.id,e.name,e.class,e.status,e.verification,s.tags,g.wgs84_wkb
             FROM entity e JOIN geometry g ON g.entity_id=e.id JOIN source_object s ON s.ref=e.source_ref
             WHERE e.kind='station' AND g.wgs84_wkb IS NOT NULL",
        )?;
        let mut links = db.prepare(
            "SELECT DISTINCT l.render_id FROM line_member m JOIN entity l ON l.id=m.line_id
             WHERE m.member_id=? AND l.status NOT IN ('construction','planned','disused')
               AND l.class IN ('railway','subway','light_rail','tram','train') LIMIT 64",
        )?;
        let mut candidates = Vec::new();
        let mut rows = query.query([])?;
        while let Some(row) = rows.next()? {
            let id = row.get::<_, i64>(0)? as u64;
            let entity: String = row.get(1)?;
            let name: String = row.get(2)?;
            let class: String = row.get(3)?;
            let status: String = row.get(4)?;
            let verified = row.get::<_, String>(5)? == "verified";
            let tags: serde_json::Value = serde_json::from_str(&row.get::<_, String>(6)?)?;
            let facts = classify(&tags, "station", &class, &status, verified);
            let mode = match facts.render_class {
                "rail_station_passenger" | "rail_station_unknown" | "rail_halt" => "rail",
                "metro_station" | "metro_interchange" | "tram_stop" => "metro",
                _ => continue,
            };
            let Some(point) = Wkb(row.get::<_, Vec<u8>>(7)?).to_geo()?.interior_point() else {
                continue;
            };
            let tag = |key: &str| tags[key].as_str().unwrap_or_default().trim();
            let named = ["name:zh", "name", "ref"]
                .iter()
                .any(|key| !tag(key).is_empty());
            let line_rids: BTreeSet<u64> = links
                .query_map([entity], |r| Ok(r.get::<_, i64>(0)? as u64))?
                .collect::<rusqlite::Result<_>>()?;
            let interchange = facts.render_class == "metro_interchange";
            let min_zoom = match facts.render_class {
                "metro_interchange" => 10,
                "metro_station" if line_rids.len() >= 3 => 11,
                "metro_station" => 12,
                "rail_halt" | "tram_stop" => 13,
                _ if facts.grade.is_some() || line_rids.len() >= 2 => 9,
                _ => 11,
            };
            let priority = (
                status != "operating",
                !verified,
                min_zoom,
                tag("railway") != "station",
                std::cmp::Reverse(line_rids.len()),
                id,
            );
            candidates.push((
                priority,
                Station {
                    display: Display {
                        render_id: id,
                        mode,
                        name: if named { name } else { String::new() },
                    },
                    point,
                    min_zoom,
                    interchange,
                    line_rids,
                    rail_peer: None,
                    rail_ref: tag("railway:ref").into(),
                    business: facts.business,
                    technical: facts.technical,
                },
            ));
        }
        candidates.sort_by_key(|(priority, _)| *priority);
        Ok(Self::from_candidates(
            candidates.into_iter().map(|(_, station)| station),
        ))
    }

    fn from_candidates(candidates: impl IntoIterator<Item = Station>) -> Self {
        let mut output = Self::default();
        for station in candidates {
            let id = station.display.render_id;
            let label = label_key(&station.display.name);
            let prior = output.labels.get(&label).and_then(|indices| {
                indices.iter().copied().find(|&index| {
                    let other = &output.marks[index];
                    other.display.mode == station.display.mode
                        && nearby(other.point, station.point)
                        && (station.display.mode != "rail"
                            || station.rail_ref.is_empty()
                            || other.rail_ref.is_empty()
                            || station.rail_ref == other.rail_ref)
                })
            });
            if let Some(index) = prior {
                let owner = &mut output.marks[index];
                owner.min_zoom = owner.min_zoom.min(station.min_zoom);
                owner.interchange |= station.interchange;
                owner.business = owner.business.or(station.business);
                owner.technical = owner.technical.or(station.technical);
                owner.line_rids.extend(station.line_rids);
                output.owners.insert(id, index);
            } else {
                let index = output.marks.len();
                if !label.is_empty() {
                    output.labels.entry(label).or_default().push(index);
                }
                output.owners.insert(id, index);
                output.marks.push(station);
            }
        }
        for indices in output.labels.values() {
            for &i in indices {
                let station = &output.marks[i];
                if station.display.mode == "metro" {
                    let peer = indices
                        .iter()
                        .map(|&index| &output.marks[index])
                        .find(|rail| {
                            rail.display.mode == "rail" && nearby(rail.point, station.point)
                        })
                        .map(|rail| (rail.display.render_id, rail.min_zoom));
                    output.marks[i].rail_peer = peer;
                }
            }
        }
        output.by_longitude = (0..output.marks.len()).collect();
        output.by_longitude.sort_by(|&a, &b| {
            output.marks[a]
                .point
                .x()
                .total_cmp(&output.marks[b].point.x())
        });
        output
    }

    pub(crate) fn display(&self, id: u64) -> Option<&Display> {
        self.owners
            .get(&id)
            .map(|&index| &self.marks[index].display)
    }

    pub(crate) fn in_bounds(
        &self,
        [west, south, east, north]: [f64; 4],
    ) -> impl Iterator<Item = &Station> {
        let start = self
            .by_longitude
            .partition_point(|&i| self.marks[i].point.x() < west);
        let end = self
            .by_longitude
            .partition_point(|&i| self.marks[i].point.x() <= east);
        self.by_longitude[start..end]
            .iter()
            .map(|&i| &self.marks[i])
            .filter(move |station| (south..=north).contains(&station.point.y()))
    }

    pub(crate) fn repeats_station_name(&self, name: &str, point: Point) -> bool {
        let key = label_key(name);
        [
            Some(key.as_str()),
            key.strip_suffix("客运站"),
            key.strip_suffix("地铁站"),
            key.strip_suffix("火车站"),
            key.strip_suffix("站"),
        ]
        .into_iter()
        .flatten()
        .any(|key| {
            self.labels
                .get(key)
                .is_some_and(|indices| indices.iter().any(|&i| nearby(self.marks[i].point, point)))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn station(id: u64, mode: &'static str, name: &str, x: f64, rail_ref: &str) -> Station {
        Station {
            display: Display {
                render_id: id,
                mode,
                name: name.into(),
            },
            point: Point::new(x, 31.2),
            min_zoom: 12,
            interchange: false,
            line_rids: BTreeSet::from([id]),
            rail_peer: None,
            rail_ref: rail_ref.into(),
            business: None,
            technical: None,
        }
    }

    #[test]
    fn merges_nearby_records_and_preserves_distinct_homonyms() {
        let stations = Stations::from_candidates([
            station(1, "metro", "人民广场", 121.470, ""),
            station(2, "metro", " 人民 广场 ", 121.468, ""),
            station(3, "metro", "人民广场", 121.610, ""),
            station(4, "rail", "人民广场", 121.471, "A"),
            station(5, "rail", "人民广场", 121.472, "B"),
            station(6, "metro", "", 121.471, ""),
            station(7, "metro", "", 121.472, ""),
        ]);
        let visible: Vec<_> = stations.in_bounds([121.46, 31.19, 121.48, 31.21]).collect();
        assert_eq!(visible.len(), 5);
        assert_eq!(stations.display(2).unwrap().render_id, 1);
        assert_eq!(stations.display(3).unwrap().render_id, 3);
        assert_eq!(stations.display(5).unwrap().render_id, 5);
        let mark = visible
            .iter()
            .find(|mark| mark.display.render_id == 1)
            .unwrap();
        assert_eq!(mark.rail_peer, Some((4, 12)));
        assert_eq!(mark.line_rids, BTreeSet::from([1, 2]));
        assert!(stations.repeats_station_name("人民广场地铁站", Point::new(121.469, 31.2)));
        assert!(!stations.repeats_station_name("人民广场", Point::new(121.550, 31.2)));
    }
}
