use anyhow::{Context, Result};
use railscope_next_core::{Engine, catalog, external_data, operations, workspace};
use serde_json::{Value, json};
use std::{env, path::PathBuf};

#[test]
#[ignore = "requires the local Shanghai v4 offline pack"]
fn external_files_import_atomically_into_the_workspace() -> Result<()> {
    let profile = tempfile::tempdir()?;
    let db_path = profile.path().join("workspace.sqlite");
    let packs = PathBuf::from(env::var("RAILSCOPE_TEST_PACKS")?);
    let snapshot = "shanghai-osm-202609-v4";
    {
        let db = rusqlite::Connection::open(&db_path)?;
        workspace::initialize(&db)?;
        db.execute("UPDATE settings SET active=? WHERE id=1", [snapshot])?;
    }
    let engine = Engine::new(packs.clone(), db_path.clone())?;
    let station = "8806f39b-9cd5-5bf3-b216-36690d6f59d5";
    let line = "408e1caa-58f5-5cc5-b0b5-48d0633616df";
    engine.activate(snapshot)?;
    let pack = engine.for_snapshot(snapshot)?;
    let tracks = serde_json::to_value(external_data::line_tracks(&pack, line)?)?;
    let track = tracks
        .as_array()
        .context("missing track index")?
        .iter()
        .find(|item| item["status"] == "operating" && item["forward"] == true)
        .context("no usable track in fixture")?;
    let mut document = json!({
        "schema_version": 1, "snapshot_id": snapshot,
        "source": {"reference":"测试资料", "version":"1", "verification_status":"user_asserted"},
        "records":[
            {"entity_id":station,"kind":"station","changes":{"name":"虹桥导入测试", "profile":{
                "other_names":["虹桥资料检索测试"], "business":"passenger"
            }}},
            {"entity_id":line,"kind":"line","changes":{"profile":{
                "city":"上海", "sections":[{"code":"TEST","name":"一区","status":"operating",
                "opened_on":"2020-01-01","edge_ids":[track["edge_id"]]}]
            }}}
        ]
    });
    let import = |doc: &Value, preview| {
        engine.with_workspace(snapshot, |db, pack| {
            Ok(serde_json::to_value(external_data::import(
                db,
                pack,
                &doc.to_string(),
                preview,
            )?)?)
        })
    };
    let station_state =
        || engine.with_workspace(snapshot, |db, _| workspace::state(db, snapshot, station));
    let initial = station_state()?.revision;
    assert_eq!(import(&document, true)?["changed"], 2);
    assert_eq!(station_state()?.revision, initial);
    let mut invalid = document.clone();
    invalid["records"][1]["changes"]["profile"]["sections"][0]["edge_ids"][0] = json!(station);
    assert!(import(&invalid, false).is_err());
    assert_eq!(station_state()?.revision, initial);
    assert_eq!(import(&document, false)?["changed"], 2);
    assert_eq!(import(&document, false)?["changed"], 0);
    engine.with_workspace(snapshot, |db, pack| {
        let filter = catalog::Filter {
            query: "虹桥资料检索测试".into(),
            ..Default::default()
        };
        let mut page = catalog::list(pack, filter.clone())?;
        workspace::catalog_overrides(db, pack, &filter, &mut page)?;
        assert_eq!(page.items[0].id, station);
        assert_eq!(page.items[0].name, "虹桥导入测试");
        Ok(())
    })?;
    document["records"][0]["changes"] = json!({"profile":{"other_names":null}});
    document["records"].as_array_mut().unwrap().truncate(1);
    assert_eq!(import(&document, false)?["changed"], 1);
    let state = station_state()?;
    assert_eq!(state.value.name, "虹桥导入测试");
    assert!(state.value.profile.other_names.is_empty());
    assert!(
        !state
            .value
            .import_sources
            .contains_key("profile.other_names")
    );
    let export = |kind: &str, query: &str, cursor, parent| {
        engine.with_workspace(snapshot, |db, pack| {
            external_data::export(
                db,
                pack,
                external_data::ExportFilter {
                    kind: kind.into(),
                    query: query.into(),
                    cursor,
                    parent,
                },
            )
        })
    };
    let exported = export("station", station, None, None)?;
    assert_eq!(exported.count, 1);
    let mut editable: Value = serde_json::from_str(&exported.content)?;
    assert_eq!(editable["records"][0]["entity_id"], station);
    assert_eq!(editable["records"][0]["changes"]["name"], "虹桥导入测试");
    assert!(editable["records"][0]["changes"]["profile"]["other_names"].is_null());
    assert_eq!(import(&editable, false)?["changed"], 0);
    let mut stale = editable.clone();
    editable["records"][0]["changes"]["profile"]["other_names"] = json!(["外部回填别名"]);
    assert_eq!(import(&editable, false)?["changed"], 1);
    assert_eq!(import(&editable, false)?["changed"], 0);
    stale["records"][0]["changes"]["profile"]["other_names"] = json!(["过期修改"]);
    assert!(import(&stale, false).is_err());
    assert_eq!(station_state()?.value.profile.other_names, ["外部回填别名"]);
    let first = export("station", "", None, None)?;
    let second = export("station", "", first.next, None)?;
    let first: Value = serde_json::from_str(&first.content)?;
    let second: Value = serde_json::from_str(&second.content)?;
    assert_ne!(
        first["records"][0]["entity_id"],
        second["records"][0]["entity_id"]
    );
    let inventory = export("metro", "", None, Some(line.into()))?;
    let inventory: Value = serde_json::from_str(&inventory.content)?;
    assert!(inventory["records"][0]["reference"]["track"]["from_node_id"].is_string());
    assert_eq!(import(&inventory, false)?["changed"], 0);

    let mut plan = json!({
        "schema_version":1, "snapshot_id":snapshot, "corridor_intents":[{
            "id":"section-test", "name":"测试单向通道", "mode":"metro",
            "steps":[{"node_id":track["from_node_id"], "line_id":line,"via_edge":track["edge_id"]},
                     {"node_id":track["to_node_id"]}],
            "provenance":{"source_kind":"user_defined","source_url_or_reference":"测试资料","confidence":0.5}
        }]
    });
    let import_plan = |plan: &Value, preview| {
        engine.with_workspace(snapshot, |db, pack| {
        operations::execute(db, pack, json!({
            "action":"import_external_plan","format":"json","content":plan.to_string(),"preview":preview
        }))
    })
    };
    let preview = import_plan(&plan, true)?;
    assert_eq!(preview["corridors"], 1);
    assert!(preview["corridor_ids"].is_null());
    let saved = import_plan(&plan, false)?;
    assert!(saved["corridor_ids"]["section-test"]["id"].is_string());
    assert_eq!(import_plan(&plan, false)?["duplicate"], true);
    let export_plan = |scope| {
        engine.with_workspace(snapshot, |db, pack| {
            operations::execute(
                db,
                pack,
                json!({
                    "action":"export_external_plan","scope":scope,"format":"json"
                }),
            )
        })
    };
    let exported = export_plan("corridors")?;
    let mut editable: Value = serde_json::from_str(exported["content"].as_str().unwrap())?;
    assert_eq!(
        editable["corridors"][0]["id"],
        saved["corridor_ids"]["section-test"]["id"]
    );
    assert_eq!(import_plan(&editable, false)?["changed"], 0);
    editable["corridors"][0]["name"] = json!("回填后的通道名");
    assert_eq!(import_plan(&editable, true)?["changed"], 1);
    assert_eq!(import_plan(&editable, false)?["changed"], 1);
    assert_eq!(import_plan(&editable, false)?["changed"], 0);
    plan["snapshot_id"] = json!("other-snapshot");
    assert!(import_plan(&plan, false).is_err());
    drop(engine);
    let reopened = Engine::new(packs, db_path)?;
    reopened.with_workspace(snapshot, |db, pack| {
        let catalog = operations::execute(db, pack, json!({"action":"list"}))?;
        assert_eq!(catalog["corridors"].as_array().unwrap().len(), 1);
        assert_eq!(
            catalog["corridors"][0]["edge_refs"][0]["edge_id"],
            track["edge_id"]
        );
        assert_eq!(
            catalog["corridors"][0]["verification_status"],
            "automatic_reference"
        );
        assert_eq!(catalog["corridors"][0]["name"], "回填后的通道名");
        assert_eq!(catalog["corridors"][0]["version"], 2);
        assert_eq!(
            workspace::state(db, snapshot, station)?.value.name,
            "虹桥导入测试"
        );
        Ok(())
    })?;
    Ok(())
}
