use anyhow::{Context, Result};
use railscope_next_core::{Engine, catalog, operations};
use serde_json::{Value, json};
use std::{env, fs, path::PathBuf, sync::atomic::AtomicBool};

fn plans(engine: &Engine, snapshot: &str, action: &str, args: Value) -> Result<Value> {
    let mut request = args;
    request["action"] = json!(action);
    engine.with_workspace(snapshot, |db, pack| operations::execute(db, pack, request))
}

#[test]
#[ignore = "requires local Shanghai and nationwide offline packs and generated timetable bundles"]
fn line7_and_g1_run_on_shared_directed_corridors() -> Result<()> {
    let packs = PathBuf::from(env::var("RAILSCOPE_TEST_PACKS")?);
    let inputs = PathBuf::from(env::var("RAILSCOPE_TEST_PLANS")?);
    let profile = tempfile::tempdir()?;
    let engine = Engine::new(packs, profile.path().join("workspace.sqlite"))?;
    let metro = "shanghai-osm-202609-v4";
    let rail = "china-osm-202609-v3";
    let shanghai = fs::read_to_string(inputs.join("shanghai-line7-reference.json"))?;
    let g1 = fs::read_to_string(inputs.join("g1-reference.json"))?;
    let import = |snapshot: &str, content: &str| {
        plans(
            &engine,
            snapshot,
            "import_timetable",
            json!({"format":"json","content":content}),
        )
    };

    engine.activate(metro)?;
    assert!(import(metro, &g1).is_err());
    let list =
        |snapshot: &str, date: &str| plans(&engine, snapshot, "list", json!({"service_date":date}));
    assert_eq!(list(metro, "2026-11-23")?["corridors"], json!([]));
    let result = import(metro, &shanghai)?;
    assert_eq!(result["corridors"], 6);
    assert_eq!(result["runs"], 2870);
    assert_eq!(import(metro, &shanghai)?["duplicate"], true);

    let mut count = 0;
    let weekdays = [434, 434, 434, 434, 402, 371, 361];
    for (offset, expected) in weekdays.into_iter().enumerate() {
        let date = format!("2026-11-{}", 23 + offset);
        let entries = list(metro, &date)?;
        let runs = entries["runs"].as_array().context("run list missing")?;
        assert_eq!(runs.len(), expected);
        count += runs.len();
        let sample = runs
            .iter()
            .find(|run| {
                run["stops"]
                    .as_array()
                    .is_some_and(|stops| stops.len() == 33)
            })
            .context("full Shanghai service missing")?;
        let stop = &sample["stops"][0];
        let start = stop["departure_time_s"]
            .as_i64()
            .context("departure missing")?;
        let picture = plans(
            &engine,
            metro,
            "simulate",
            json!({"run_ids":[sample["id"]],"time_s":start+45,"level":"l2"}),
        )?;
        assert_eq!(picture["states"][0]["status"], "moving");
        assert!(picture["states"][0]["lon"].as_f64().is_some());
        assert!(sample["stops"][1]["timing_evidence"].as_str().is_some());
        let return_run = runs
            .iter()
            .find(|run| {
                run["stops"]
                    .as_array()
                    .is_some_and(|stops| stops.len() == 33)
                    && run["origin_station_id"] == sample["destination_station_id"]
            })
            .context("opposite Shanghai service missing")?;
        let forward = sample["stops"]
            .as_array()
            .unwrap()
            .iter()
            .map(|stop| &stop["station_id"]);
        let reverse = return_run["stops"]
            .as_array()
            .unwrap()
            .iter()
            .rev()
            .map(|stop| &stop["station_id"]);
        assert!(
            forward.eq(reverse),
            "opposite routes must use a shared station axis"
        );
    }
    assert_eq!(count, 2870);
    let saturday = list(metro, "2026-11-28")?;
    assert!(saturday["runs"].as_array().unwrap().iter().any(|run| {
        run["stops"].as_array().unwrap().iter().any(|stop| {
            stop["arrival_time_s"]
                .as_i64()
                .is_some_and(|time| time >= 86400)
        })
    }));

    engine.activate(rail)?;
    assert_eq!(list(rail, "2026-11-23")?["runs"], json!([]));
    assert_eq!(import(rail, &g1)?["runs"], 1);
    let rail_runs = list(rail, "2026-09-17")?;
    let mut run = rail_runs["runs"][0].clone();
    assert_eq!(run["train_number"], "G1");
    let detail = plans(&engine, rail, "get_run", json!({"id":run["id"]}))?;
    assert_eq!(
        detail["corridor"]["edge_refs"].as_array().unwrap().len(),
        670
    );
    let pack = engine.for_snapshot(rail)?;
    let line = catalog::list(
        &pack,
        catalog::Filter {
            kind: Some("line".into()),
            query: "osm:relation:356778".into(),
            ..catalog::Filter::default()
        },
    )?
    .items
    .remove(0);
    let corridor = &detail["corridor"];
    let from = &corridor["origin_node_id"];
    let to = &corridor["destination_node_id"];
    let stop = AtomicBool::new(false);
    let lines = operations::corridor_query(
        &pack,
        json!({"action":"lines","node":from,"mode":"rail"}),
        &stop,
    )?;
    assert!(
        lines
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item["id"] == line.id)
    );
    let resolved = operations::corridor_query(
        &pack,
        json!({
            "action":"resolve","name":"京沪高铁参考","mode":"rail","strict":false,
            "steps":[{"node_id":from,"line_id":line.id},{"node_id":to,"line_id":null}]
        }),
        &stop,
    )?;
    assert_eq!(resolved["corridor"]["origin_node_id"], *from);
    assert_eq!(resolved["corridor"]["destination_node_id"], *to);
    let time = run["stops"][0]["departure_time_s"]
        .as_i64()
        .context("G1 departure missing")?
        + 600;
    let sample = plans(
        &engine,
        rail,
        "simulate",
        json!({"run_ids":[run["id"]],"time_s":time,"level":"l2"}),
    )?;
    assert_eq!(sample["states"][0]["status"], "moving");
    assert!(sample["states"][0]["lon"].as_f64().is_some());

    for stop in run["stops"].as_array_mut().unwrap() {
        for key in [
            "stop_edge_id",
            "stop_edge_sequence",
            "stop_offset_m",
            "anchor_source",
        ] {
            stop.as_object_mut().unwrap().remove(key);
        }
    }
    let suggested = plans(&engine, rail, "suggest_anchors", json!({"run":run}))?;
    assert_eq!(suggested["matches"].as_array().unwrap().len(), 7);
    assert_eq!(
        suggested["run"]["stops"][0]["anchor_source"],
        "station_geometry_reference"
    );
    Ok(())
}
