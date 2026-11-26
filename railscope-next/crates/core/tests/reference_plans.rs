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
    let national_metro = fs::read_to_string(inputs.join("shanghai-line7-national-reference.json"))?;
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
    let shanghai_pack = engine.for_snapshot(metro)?;
    let control = AtomicBool::new(false);
    let station_a = "be352ca4-88d8-5c00-8fa3-f4f6dea2f4a7";
    let station_b = "b1a549cd-ee05-5c3f-aa3b-a1ef48563d27";
    let metro_line = "408e1caa-58f5-5cc5-b0b5-48d0633616df";
    let out = operations::corridor_query(
        &shanghai_pack,
        json!({
            "action":"resolve","name":"上海双向线路关系测试","mode":"metro","strict":true,
            "steps":[{"node_id":station_a,"line_id":metro_line,"via_edge":"1aa9451a-b36f-508e-acbd-2510eb50a0a1"},{"node_id":station_b,"line_id":null}]
        }),
        &control,
    )?;
    let returned = operations::corridor_query(
        &shanghai_pack,
        json!({
            "action":"reverse", "corridor":out["corridor"]
        }),
        &control,
    )?;
    assert_eq!(returned["corridor"]["intent"][0]["node_id"], station_b);
    assert_eq!(returned["corridor"]["intent"][0]["line_id"], metro_line);
    assert_eq!(returned["corridor"]["intent"][1]["node_id"], station_a);
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
    assert_eq!(import(rail, &national_metro)?["runs"], 2870);
    let thursday = list(rail, "2026-11-26")?;
    assert_eq!(
        thursday["dates_by_mode"]["metro"].as_array().unwrap().len(),
        7
    );
    assert_eq!(thursday["dates_by_mode"]["rail"], json!(["2026-09-17"]));
    let g1_entries = list(rail, "2026-09-17")?;
    assert_eq!(g1_entries["runs"][0]["train_number"], "G1");
    let g1_path = g1_entries["corridors"]
        .as_array()
        .unwrap()
        .iter()
        .find(|corridor| corridor["id"] == g1_entries["runs"][0]["corridor_id"])
        .context("G1 corridor missing from directory")?;
    assert_eq!(g1_path["mode"], "rail");
    assert_eq!(g1_path["lines"][0]["id"], line.id);
    assert!(
        thursday["corridors"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|item| item["mode"] == "metro")
            .all(|item| item["lines"][0]["id"].as_str().is_some())
    );
    let mut next_version = g1_path.clone();
    next_version["name"] = json!("G1 通道版本测试");
    let next = plans(
        &engine,
        rail,
        "save_corridor",
        json!({"corridor":next_version}),
    )?;
    assert_eq!(next["version"], g1_path["version"].as_u64().unwrap() + 1);
    plans(
        &engine,
        rail,
        "archive_corridor",
        json!({"id":g1_path["id"]}),
    )?;
    let dated = list(rail, "2026-09-17")?;
    let pinned = dated["corridors"]
        .as_array()
        .unwrap()
        .iter()
        .find(|corridor| corridor["id"] == g1_path["id"])
        .context("archived version used by G1 is missing from directory")?;
    assert_eq!(pinned["version"], dated["runs"][0]["corridor_version"]);
    assert_eq!(pinned["current_version"], false);
    let export = plans(
        &engine,
        rail,
        "export_external_plan",
        json!({"format":"json","scope":"runs","service_date":"2026-09-17"}),
    )?;
    assert_eq!(export["count"], 1);
    let mut workbook: Value = serde_json::from_str(export["content"].as_str().unwrap())?;
    assert!(
        workbook["station_names"]
            .as_object()
            .is_some_and(|names| names.len() == 7)
    );
    let apply = |workbook: &Value, preview| {
        plans(
            &engine,
            rail,
            "import_external_plan",
            json!({"format":"json","content":workbook.to_string(),"preview":preview}),
        )
    };
    assert_eq!(apply(&workbook, false)?["changed"], 0);
    workbook["services"][0]["operator"] = json!("往返测试");
    workbook["runs"][0]["stops"][0]["departure_time_s"] = json!(
        workbook["runs"][0]["stops"][0]["departure_time_s"]
            .as_i64()
            .unwrap()
            + 1
    );
    assert_eq!(apply(&workbook, true)?["changed"], 2);
    assert_eq!(apply(&workbook, false)?["changed"], 2);
    let updated = list(rail, "2026-09-17")?;
    assert_eq!(updated["runs"][0]["id"], dated["runs"][0]["id"]);
    assert_eq!(
        updated["runs"][0]["plan_version"].as_u64(),
        dated["runs"][0]["plan_version"].as_u64().map(|v| v + 1)
    );
    assert_eq!(updated["runs"][0]["corridor_version"], pinned["version"]);
    assert_eq!(apply(&workbook, false)?["changed"], 0);
    for format in ["csv", "gtfs"] {
        let result = plans(
            &engine,
            rail,
            "export_external_plan",
            json!({"format":format,"scope":"runs","service_date":"2026-09-17"}),
        )?;
        assert_eq!(result["count"], 1);
        assert!(
            result["content"]
                .as_str()
                .is_some_and(|content| !content.is_empty())
        );
    }
    let services = thursday["services"].as_array().unwrap();
    assert_eq!(services.len(), 2871);
    let trains = thursday["runs"].as_array().unwrap();
    assert_eq!(trains.len(), 434);
    assert!(trains.iter().all(|run| {
        services
            .iter()
            .any(|service| service["id"] == run["service_id"])
    }));
    let first = &trains[0];
    let start = first["stops"][0]["departure_time_s"]
        .as_i64()
        .context("national metro departure missing")?;
    let playback = plans(
        &engine,
        rail,
        "simulate",
        json!({"run_ids":[first["id"]],"time_s":start+36,"level":"l2"}),
    )?;
    assert_eq!(playback["states"][0]["status"], "moving");
    assert!(playback["states"][0]["lon"].as_f64().is_some());
    engine.activate(metro)?;
    assert_eq!(
        list(metro, "2026-11-26")?["services"]
            .as_array()
            .unwrap()
            .len(),
        2870
    );
    Ok(())
}
