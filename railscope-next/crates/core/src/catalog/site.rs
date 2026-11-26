use super::{ENTITY, SUMMARY, StationLink, Summary, summary};
use crate::cartography::{classify, site_role};
use anyhow::Result;
use geo::{Geometry, Intersects};
use geozero::{ToGeo, wkb::Wkb};
use rusqlite::{Connection, OptionalExtension, params};
use serde::Serialize;
use serde_json::Value;

#[derive(Serialize)]
pub struct Site {
    pub role: &'static str,
    pub evidence: &'static str,
    pub owner: Option<Summary>,
    pub areas: Vec<SiteArea>,
}

#[derive(Serialize)]
pub struct SiteArea {
    pub object: Summary,
    pub kind: &'static str,
    pub evidence: &'static str,
}

fn area_kind(class: &str, tags: &Value) -> &'static str {
    if class == "station_building" {
        "building"
    } else if class == "railway_land" || tags["landuse"] == "railway" {
        "railway_land"
    } else {
        "boundary"
    }
}

fn normalized(name: &str) -> &str {
    name.trim().trim_end_matches('站')
}

fn polygon(geometry: &Geometry) -> bool {
    matches!(geometry, Geometry::Polygon(_) | Geometry::MultiPolygon(_))
}

pub(super) fn inspect(
    db: &Connection,
    base: &Summary,
    tags: &Value,
    role: Option<(&'static str, &'static str)>,
) -> Result<Option<Site>> {
    let area_class = matches!(
        base.class.as_str(),
        "station_area" | "station_building" | "railway_land"
    );
    if role.is_none() && !area_class {
        return Ok(None);
    }
    let Some(raw) = db
        .query_row(
            "SELECT wgs84_wkb FROM geometry WHERE entity_id=?",
            [&base.id],
            |r| r.get::<_, Option<Vec<u8>>>(0),
        )
        .optional()?
        .flatten()
    else {
        return Ok(role.map(|(role, evidence)| Site {
            role,
            evidence,
            owner: None,
            areas: Vec::new(),
        }));
    };
    let shape = Wkb(raw).to_geo()?;
    let name = normalized(&base.name);
    let names = [base.name.as_str(), name, &format!("{name}站")];
    let mut areas = Vec::new();
    let mut owner = None;
    if polygon(&shape) && (role.is_some() || area_class) {
        areas.push(SiteArea {
            object: base.clone(),
            kind: area_kind(&base.class, tags),
            evidence: "OSM 原始面",
        });
        // A real footprint can share its station POI when the type, name and
        // containment agree, and the match is unique.
        if !name.is_empty() {
            let mut q = db.prepare(&format!(
                "SELECT {SUMMARY},src.tags,g.wgs84_wkb FROM {ENTITY}
                 JOIN source_object src ON src.ref=e.source_ref
                 WHERE e.kind='station' AND e.name IN (?1,?2,?3)
                 AND g.min_x=g.max_x AND g.min_y=g.max_y
                 AND g.min_x BETWEEN ?4 AND ?6 AND g.min_y BETWEEN ?5 AND ?7 LIMIT 16"
            ))?;
            let mut rows = q.query(params![
                names[0],
                names[1],
                names[2],
                base.bounds[0],
                base.bounds[1],
                base.bounds[2],
                base.bounds[3]
            ])?;
            while let Some(row) = rows.next()? {
                let candidate = summary(row)?;
                let source: Value = serde_json::from_str(&row.get::<_, String>(10)?)?;
                let facts = classify(
                    &source,
                    &candidate.kind,
                    &candidate.class,
                    &candidate.status,
                    false,
                );
                let Some((candidate_role, _)) = site_role(
                    &source,
                    &candidate.kind,
                    &candidate.class,
                    &candidate.name,
                    facts.render_class,
                ) else {
                    continue;
                };
                let compatible = role.map_or(
                    candidate_role == "station"
                        || (candidate_role == "metro" && tags["subway"] == "yes"),
                    |(kind, _)| kind == candidate_role,
                );
                if compatible
                    && let Some(wkb) = row.get::<_, Option<Vec<u8>>>(11)?
                    && let Geometry::Point(point) = Wkb(wkb).to_geo()?
                    && shape.intersects(&point)
                {
                    if owner.is_some() {
                        owner = None;
                        break;
                    }
                    owner = Some(candidate);
                }
            }
        }
    } else if let Geometry::Point(point) = &shape
        && let Some((kind, _)) = role
        && !name.is_empty()
    {
        let mut q=db.prepare(&format!(
            "SELECT {SUMMARY},src.tags,g.wgs84_wkb FROM {ENTITY}
             JOIN source_object src ON src.ref=e.source_ref
             WHERE (e.kind='station' OR (e.kind='facility' AND e.class IN ('station_area','station_building','railway_land')))
             AND e.name IN (?1,?2,?3) AND g.min_x<=?4 AND g.max_x>=?4 AND g.min_y<=?5 AND g.max_y>=?5 LIMIT 24"
        ))?;
        let mut rows = q.query(params![names[0], names[1], names[2], point.x(), point.y()])?;
        while let Some(row) = rows.next()? {
            let candidate = summary(row)?;
            if candidate.bounds[0] == candidate.bounds[2]
                && candidate.bounds[1] == candidate.bounds[3]
            {
                continue;
            }
            let source: Value = serde_json::from_str(&row.get::<_, String>(10)?)?;
            let facts = classify(
                &source,
                &candidate.kind,
                &candidate.class,
                &candidate.status,
                false,
            );
            let candidate_role = site_role(
                &source,
                &candidate.kind,
                &candidate.class,
                &candidate.name,
                facts.render_class,
            );
            if candidate.kind == "station" && candidate_role.is_none() {
                continue;
            }
            let compatible = candidate_role.map_or(
                kind == "station" || (kind == "metro" && source["subway"] == "yes"),
                |(role, _)| role == kind,
            );
            if compatible && let Some(bytes) = row.get::<_, Option<Vec<u8>>>(11)? {
                let footprint = Wkb(bytes).to_geo()?;
                if polygon(&footprint) && footprint.intersects(point) {
                    areas.push(SiteArea {
                        kind: area_kind(&candidate.class, &source),
                        object: candidate,
                        evidence: "同名且轮廓覆盖点位",
                    });
                }
            }
        }
    }
    let (role, evidence) = role.unwrap_or(("area", "OSM 场站范围"));
    Ok((!areas.is_empty() || role != "area").then_some(Site {
        role,
        evidence,
        owner,
        areas,
    }))
}

pub(super) fn area_lines(db: &Connection, site: &Site) -> Result<Vec<StationLink>> {
    let mut found = Vec::new();
    for area in site
        .areas
        .iter()
        .filter(|area| area.kind != "building")
        .take(8)
    {
        let raw: Vec<u8> = db.query_row(
            "SELECT wgs84_wkb FROM geometry WHERE entity_id=?",
            [&area.object.id],
            |r| r.get(0),
        )?;
        let footprint = Wkb(raw).to_geo()?;
        let mut candidates=db.prepare(
            "SELECT e.id,g.wgs84_wkb FROM spatial s CROSS JOIN geometry g ON g.rowid=s.rowid
             JOIN entity e ON e.id=g.entity_id WHERE s.max_x>=?1 AND s.max_y>=?2 AND s.min_x<=?3 AND s.min_y<=?4
             AND e.kind IN ('rail','metro') AND e.status='operating'
             AND EXISTS(SELECT 1 FROM line_member m WHERE m.member_id=e.id) LIMIT 3000"
        )?;
        let mut rows = candidates.query(rusqlite::params_from_iter(area.object.bounds))?;
        let mut tracks = Vec::new();
        while let Some(row) = rows.next()? {
            if let Some(raw) = row.get::<_, Option<Vec<u8>>>(1)?
                && footprint.intersects(&Wkb(raw).to_geo()?)
            {
                tracks.push(row.get::<_, String>(0)?);
            }
        }
        let query=format!(
            "SELECT DISTINCT {SUMMARY},e.source_ref FROM json_each(?1) t
             JOIN line_member m ON m.member_id=t.value JOIN entity e ON e.id=m.line_id JOIN geometry g ON g.entity_id=e.id
             WHERE e.class IN ('railway','subway','light_rail','tram') ORDER BY e.name,e.id LIMIT 60"
        );
        let mut q = db.prepare(&query)?;
        for entry in q.query_map([serde_json::to_string(&tracks)?], |r| {
            Ok(StationLink {
                line: summary(r)?,
                relation: "area_track".into(),
                evidence: "运营轨道几何与场站原始范围相交".into(),
                source_ref: r.get(10)?,
            })
        })? {
            let item = entry?;
            if !found
                .iter()
                .any(|prior: &StationLink| prior.line.id == item.line.id)
            {
                found.push(item);
            }
        }
    }
    Ok(found)
}
