use crate::pack::Pack;
use anyhow::{Result, bail, ensure};
use rusqlite::{OptionalExtension, params};
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

fn route_in_snapshot(
    db: &rusqlite::Connection,
    start_id: &str,
    end_id: &str,
    cancel: &AtomicBool,
) -> Result<Route> {
    let endpoint = |id: &str| -> Result<_> {
        let query = "SELECT from_node,to_node,edge.forward,edge.reverse,edge.meters,node.network
                     FROM edge JOIN node ON from_node=node.id JOIN entity ON entity.id=edge.id
                     WHERE edge.id=?1 AND entity.status='operating' AND entity.class!='unknown'";
        Ok(db
            .query_row(query, [id], |r| {
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
    let mut queue = BinaryHeap::new();
    let mut best = HashMap::new();
    for (node, available) in [(to, forward), (from, reverse)] {
        if available && best.insert(node.clone(), start_meters).is_none() {
            queue.push(Visit {
                meters: start_meters,
                node,
            });
        }
    }
    let mut parents: HashMap<String, (String, String)> = HashMap::new();
    let mut is_diamond = db.prepare(
        "SELECT EXISTS(SELECT 1 FROM node JOIN source_object s ON s.ref=node.source_ref
         WHERE node.id=?1 AND node.network='rail' AND json_extract(s.tags,'$.railway')='railway_crossing')",
    )?;
    let mut neighbors = db.prepare(
        "SELECT edge.id, to_node, meters FROM edge JOIN entity ON entity.id=edge.id
         WHERE from_node=?1 AND forward=1 AND status='operating' AND class!='unknown'
           AND edge.id NOT IN (?2,?3)
         UNION ALL
         SELECT edge.id, from_node, meters FROM edge JOIN entity ON entity.id=edge.id
         WHERE to_node=?1 AND reverse=1 AND status='operating' AND class!='unknown'
           AND edge.id NOT IN (?2,?3)",
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
        if (end_forward && node == end_from) || (end_reverse && node == end_to) {
            let mut edges = vec![end_id.to_owned()];
            let mut current = node.as_str();
            while let Some((parent, edge)) = parents.get(current) {
                edges.push(edge.clone());
                current = parent;
            }
            edges.push(start_id.to_owned());
            edges.reverse();
            return deliver(edges, meters + end_meters);
        }
        visited += 1;
        ensure!(visited <= MAX_VISITS, "已达到路径搜索范围上限");
        let mut rows = neighbors.query(params![node, start_id, end_id])?;
        while let Some(row) = rows.next()? {
            let edge: String = row.get(0)?;
            let to: String = row.get(1)?;
            let candidate = meters + row.get::<_, f64>(2)?;
            if best.get(&to).is_none_or(|old| candidate < *old) {
                best.insert(to.clone(), candidate);
                parents.insert(to.clone(), (node.clone(), edge));
                queue.push(Visit {
                    meters: candidate,
                    node: to,
                });
            }
        }
    }
    bail!("在当前数据包内未找到显式连通的运营路径")
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
