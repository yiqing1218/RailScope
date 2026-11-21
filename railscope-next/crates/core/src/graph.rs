use crate::pack::Pack;
use anyhow::{Result, bail, ensure};
use rusqlite::{Connection, OptionalExtension, params};
use serde::Serialize;
use serde_json::Value;
use std::{
    cmp::Ordering,
    collections::{BinaryHeap, HashMap},
    sync::atomic::{AtomicBool, Ordering as AtomicOrdering},
};

const MAX_VISITS: usize = 100_000;

#[derive(Serialize)]
pub struct Route {
    pub edges: Vec<String>,
    pub meters: f64,
    pub verification: &'static str,
    pub geometry: Option<Value>,
}

struct Visit {
    meters: f64,
    node: String,
}
impl PartialEq for Visit {
    fn eq(&self, rhs: &Self) -> bool {
        self.cmp(rhs) == Ordering::Equal
    }
}
impl Eq for Visit {}
impl PartialOrd for Visit {
    fn partial_cmp(&self, rhs: &Self) -> Option<Ordering> {
        Some(self.cmp(rhs))
    }
}
impl Ord for Visit {
    fn cmp(&self, rhs: &Self) -> Ordering {
        rhs.meters
            .total_cmp(&self.meters)
            .then_with(|| self.node.cmp(&rhs.node))
    }
}

/// Route between selected track endpoints. Only explicitly connected source nodes are followed.
pub fn route(pack: &Pack, start_id: &str, end_id: &str, cancel: &AtomicBool) -> Result<Route> {
    let db = pack.connection()?;
    route_in_snapshot(&db, start_id, end_id, cancel)
}

pub(crate) fn route_in_snapshot(
    db: &rusqlite::Connection,
    start_id: &str,
    end_id: &str,
    cancel: &AtomicBool,
) -> Result<Route> {
    route_options(db, start_id, end_id, cancel, None, "", "[]")
}

pub(crate) fn route_options(
    db: &rusqlite::Connection,
    start_id: &str,
    end_id: &str,
    cancel: &AtomicBool,
    kind: Option<&str>,
    excluded: &str,
    lines: &str,
) -> Result<Route> {
    let endpoint = |id: &str| -> Result<_> {
        let query = "SELECT from_node,to_node,edge.forward,edge.reverse,edge.meters,node.network
                     FROM edge JOIN node ON from_node=node.id JOIN entity ON entity.id=edge.id
                     WHERE edge.id=?1 AND entity.status='operating' AND entity.class!='unknown'
                       AND (?2 IS NULL OR entity.kind=?2)
                       AND (?3='[]' OR EXISTS(SELECT 1 FROM line_member m
                         WHERE m.member_id=edge.id AND m.line_id IN (SELECT value FROM json_each(?3))))";
        Ok(db
            .query_row(query, params![id, kind, lines], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, bool>(2)?,
                    r.get::<_, bool>(3)?,
                    r.get::<_, f64>(4)?,
                    r.get::<_, String>(5)?,
                ))
            })
            .optional()?)
    };
    let Some((from, to, forward, reverse, start_meters, network)) = endpoint(start_id)? else {
        bail!("起点必须是类别明确的运营轨道或道路边")
    };
    let Some((end_from, end_to, end_forward, end_reverse, end_meters, end_network)) =
        endpoint(end_id)?
    else {
        bail!("终点必须是类别明确的运营轨道或道路边")
    };
    ensure!(network == end_network, "起终点必须属于同一物理网络");
    let deliver = |edges: Vec<String>, meters: f64| -> Result<Route> {
        let mut geometry = None;
        if edges.len() <= 400 {
            let mut parts = Vec::new();
            for id in &edges {
                ensure!(!cancel.load(AtomicOrdering::Relaxed), "路径查询已取消");
                if let Some(part) = crate::catalog::geometry(db, id)? {
                    parts.push(part);
                }
            }
            geometry = Some(serde_json::json!({"type":"GeometryCollection", "geometries":parts}));
        }
        Ok(Route {
            edges,
            meters,
            verification: "automatic_reference",
            geometry,
        })
    };
    if start_id == end_id {
        return deliver(vec![start_id.to_owned()], start_meters);
    }
    let starts = [(to, forward), (from, reverse)]
        .into_iter()
        .filter_map(|(node, available)| available.then_some((node, start_meters)));
    let end = |node: &str| (end_forward && node == end_from) || (end_reverse && node == end_to);
    let visited = walk(
        db,
        starts,
        end,
        cancel,
        [start_id, end_id, excluded],
        kind,
        lines,
    )?;
    let (mut edges, meters) = visited
        .path()
        .ok_or_else(|| anyhow::anyhow!("在当前数据包内未找到显式连通的运营路径"))?;
    edges.insert(0, start_id.to_owned());
    edges.push(end_id.to_owned());
    deliver(edges, meters + end_meters)
}

struct Walk {
    best: HashMap<String, f64>,
    parents: HashMap<String, (String, String, bool)>,
    destination: Option<(String, f64)>,
}

impl Walk {
    fn directed_path(&self) -> Option<(Vec<(String, bool)>, f64)> {
        let (node, meters) = self.destination.as_ref()?;
        let mut current = node.as_str();
        let mut edges = Vec::new();
        while let Some((parent, edge, forward)) = self.parents.get(current) {
            edges.push((edge.clone(), *forward));
            current = parent;
        }
        edges.reverse();
        Some((edges, *meters))
    }
    fn path(&self) -> Option<(Vec<String>, f64)> {
        self.directed_path()
            .map(|(edges, meters)| (edges.into_iter().map(|(id, _)| id).collect(), meters))
    }
}

fn walk(
    db: &Connection,
    starts: impl IntoIterator<Item = (String, f64)>,
    end: impl Fn(&str) -> bool,
    cancel: &AtomicBool,
    excluded: [&str; 3],
    kind: Option<&str>,
    lines: &str,
) -> Result<Walk> {
    let mut queue = BinaryHeap::new();
    let mut best = HashMap::new();
    for (node, meters) in starts {
        best.insert(node.clone(), meters);
        queue.push(Visit { meters, node });
    }
    let mut parents = HashMap::new();
    let mut is_diamond = db.prepare(
        "SELECT EXISTS(SELECT 1 FROM node JOIN source_object s ON s.ref=node.source_ref
         WHERE node.id=?1 AND node.network='rail' AND json_extract(s.tags,'$.railway')='railway_crossing')",
    )?;
    let mut neighbors = db.prepare(
        "SELECT edge.id, to_node, meters, 1 FROM edge JOIN entity ON entity.id=edge.id
         WHERE from_node=?1 AND forward=1 AND status='operating' AND class!='unknown'
           AND edge.id NOT IN (?2,?3,?4) AND (?5 IS NULL OR entity.kind=?5)
           AND (?6='[]' OR EXISTS(SELECT 1 FROM line_member m
             WHERE m.member_id=edge.id AND m.line_id IN (SELECT value FROM json_each(?6))))
         UNION ALL
         SELECT edge.id, from_node, meters, 0 FROM edge JOIN entity ON entity.id=edge.id
         WHERE to_node=?1 AND reverse=1 AND status='operating' AND class!='unknown'
           AND edge.id NOT IN (?2,?3,?4) AND (?5 IS NULL OR entity.kind=?5)
           AND (?6='[]' OR EXISTS(SELECT 1 FROM line_member m
             WHERE m.member_id=edge.id AND m.line_id IN (SELECT value FROM json_each(?6))))
         ORDER BY 1, 4",
    )?;
    let mut visited = 0;
    while let Some(Visit { meters, node }) = queue.pop() {
        ensure!(!cancel.load(AtomicOrdering::Relaxed), "路径查询已取消");
        if best.get(&node).is_some_and(|cost| *cost < meters) {
            continue;
        }
        // Also protects packs made before explicit crossing ports were introduced.
        if is_diamond.query_row([&node], |row| row.get::<_, bool>(0))? {
            continue;
        }
        if end(&node) {
            return Ok(Walk {
                best,
                parents,
                destination: Some((node, meters)),
            });
        }
        visited += 1;
        ensure!(visited <= MAX_VISITS, "已达到路径搜索范围上限");
        let mut rows = neighbors.query(params![
            node,
            excluded[0],
            excluded[1],
            excluded[2],
            kind,
            lines
        ])?;
        while let Some(row) = rows.next()? {
            let edge: String = row.get(0)?;
            let to: String = row.get(1)?;
            let candidate = meters + row.get::<_, f64>(2)?;
            if best.get(&to).is_none_or(|old| candidate < *old) {
                best.insert(to.clone(), candidate);
                parents.insert(to.clone(), (node.clone(), edge, row.get::<_, bool>(3)?));
                queue.push(Visit {
                    meters: candidate,
                    node: to,
                });
            }
        }
    }
    Ok(Walk {
        best,
        parents,
        destination: None,
    })
}

pub(crate) fn from_node(
    db: &Connection,
    start: &str,
    end: Option<&str>,
    kind: &str,
    line: &str,
    excluded: &str,
    cancel: &AtomicBool,
) -> Result<(Vec<(String, bool)>, HashMap<String, f64>)> {
    let lines = serde_json::to_string(&[line])?;
    let result = walk(
        db,
        [(start.to_owned(), 0.)],
        |node| end == Some(node),
        cancel,
        [excluded, "", ""],
        Some(kind),
        &lines,
    )?;
    let path = match end {
        Some(_) => {
            result
                .directed_path()
                .ok_or_else(|| anyhow::anyhow!("在当前数据包内未找到显式连通的运营路径"))?
                .0
        }
        None => Vec::new(),
    };
    Ok((path, result.best))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    #[test]
    fn respects_direction_status_and_non_switch_crossings() -> Result<()> {
        let db = Connection::open_in_memory()?;
        db.execute_batch(include_str!("../../../pipelines/canonical.sql"))?;
        db.execute_batch("
            INSERT INTO source_object VALUES ('plain',1,'{}'), ('diamond',1,'{\"railway\":\"railway_crossing\"}');
            INSERT INTO node VALUES ('a','plain','rail',0,0), ('b','plain','rail',1,0),
                                    ('c','plain','rail',2,0), ('d','diamond','rail',3,0),
                                    ('e','plain','rail',4,0), ('f','plain','rail',3,-1);
            INSERT INTO entity VALUES
                  ('start',1,'','rail','conventional','operating','plain','automatic_reference','medium'),
                  ('mid',2,'','rail','conventional','operating','plain','automatic_reference','medium'),
                  ('end',3,'','rail','conventional','operating','plain','automatic_reference','medium'),
                  ('crossing',4,'','rail','conventional','operating','plain','automatic_reference','medium'),
                  ('other',5,'','rail','conventional','operating','plain','automatic_reference','medium'),
                  ('construction',6,'','rail','conventional','construction','plain','automatic_reference','medium');
            INSERT INTO edge VALUES ('start','a','b',1,0,10), ('mid','b','c',1,1,20),
                                    ('end','c','d',1,0,30), ('crossing','d','e',1,1,10),
                                    ('other','d','f',1,1,10), ('construction','b','d',1,1,1);
        ")?;
        let cancel = AtomicBool::new(false);
        let result = route_in_snapshot(&db, "start", "end", &cancel)?;
        assert_eq!(result.edges, ["start", "mid", "end"]);
        assert_eq!(result.meters, 60.0);
        assert!(route_in_snapshot(&db, "end", "start", &cancel).is_err());
        assert!(route_in_snapshot(&db, "start", "crossing", &cancel).is_err());
        assert!(route_in_snapshot(&db, "other", "crossing", &cancel).is_err());
        assert!(route_in_snapshot(&db, "start", "construction", &cancel).is_err());
        cancel.store(true, AtomicOrdering::Relaxed);
        assert!(route_in_snapshot(&db, "start", "end", &cancel).is_err());
        Ok(())
    }
}
