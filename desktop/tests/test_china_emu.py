"""Reference enrichment must not turn website diagrams into operating facts."""

from dataclasses import asdict
import pytest

from desktop.china_emu import (
    ReferenceStore, parse_profile, line_reference, fill_missing, without_previous_reference,
    atomic_json, load_store, SCHEMA, source_url,
)
from desktop.line_metadata import normalize_line_attributes, source_line_attributes
from desktop.catalog_metadata import station_overview
from railscope.domain import ReferenceProfile


def para(label, value, css=""):
    return f'<div class="para {css}"><div class="para-N">{value}</div><div class="para-M">{label}</div></div>'


def line_page():
    # Reduced structural example: source pages put independent parameter cards
    # under a common line title. Two scopes deliberately disagree on speed.
    cards = ""
    for name, span, speed in [("甲铁路东段", "甲~乙", "200 km/h"), ("甲铁路西段", "乙~丙", "160 km/h")]:
        cards += ('<div class="w-100"><div><div class="font-small">甲线</div>'
                  f'<div class="title-label">{name}</div><div class="text-blue">{span}</div></div><div>'
                  + para("轨距", "1435 mm") + para("线下设计速度", speed)
                  + para("最高运行速度", "120 km/h") + para("计价里程", "50 km") + '</div></div>')
    return "<h2>甲铁路</h2>" + cards


def profile(kind="vehicle", name="参考车型", attributes=None, scopes=(), url=None):
    return asdict(ReferenceProfile(
        id="reference/example", kind=kind, name=name,
        source_url=url or "https://china-emu.cn/Trains/Model/Detail-1.html",
        snapshot_id="sha256:example", retrieved_at="2026-10-01T00:00:00Z",
        attributes=attributes or {}, scopes=scopes,
    ))


def test_section_speeds_are_not_promoted_to_whole_line():
    p = parse_profile(line_page(), "https://china-emu.cn/RailRoads/Line/?LineName=甲铁路")
    assert "civil_design_speed_kmh" not in p["attributes"]
    assert "pricing_distance_km" not in p["attributes"]  # Equal section mileages are not whole-line mileage.
    assert p["scopes"][0]["span"] == "甲~乙"
    store = ReferenceStore([p])
    whole = store.match("line", ["甲铁路"])
    section = store.match("line", ["甲铁路东段"])
    assert "civil_design_speed_kmh" not in whole["attributes"]
    assert section["attributes"]["civil_design_speed_kmh"] == "200 km/h"


def test_osm_and_manual_values_win_and_differences_keep_provenance():
    p = profile("line", "甲铁路", {"gauge_mm": "1435 mm", "max_speed_kmh": "160 km/h", "power_supply": "AC25kV 50Hz"})
    props = {"way_tags": {"gauge": "1000"}, "external_reference": p}
    values = source_line_attributes(props, "rail", {"max_speed_kmh": "120"})
    assert values["gauge_mm"] == "1000"
    assert values["max_speed_kmh"] == "120"
    assert values["power_supply"] == "AC25kV 50Hz"
    assert set(props["reference_conflicts"]) == {"gauge_mm", "max_speed_kmh"}
    assert props["reference_provenance"]["power_supply"]["snapshot_id"] == p["snapshot_id"]
    assert p["verification_status"] == "external_reference_unverified"


def test_equivalent_units_are_not_false_conflicts():
    p = profile("line", "甲铁路", {"gauge_mm": "1435 mm", "max_speed_kmh": "160.0 km/h"})
    values, conflicts, provenance = fill_missing({"gauge_mm": "1435", "max_speed_kmh": "160"}, p, set(p["attributes"]))
    assert values == {"gauge_mm": "1435", "max_speed_kmh": "160"}
    assert conflicts == {}
    assert provenance == {}


def test_station_yards_and_platform_numbers_remain_reference_scopes():
    html = ('<div><h2>甲站</h2><div class="text-muted">上海市</div></div>'
            '<div class="scale">' + para("站台数", "8")
            + para("股道数", "16") + para("站台数", "99", "hidden")
            + '<div><div class="section-word">高速场 [1~4站台]</div><div class="font-small">甲铁路</div></div>'
            '<div class="platform-l">1</div><div class="platform-r">2</div></div>')
    p = parse_profile(html, "https://china-emu.cn/RailRoads/Station/?Station=甲")
    assert p["attributes"]["platform_count"] == "8"
    assert p["scopes"][0]["yards"][0]["name"] == "高速场 [1~4站台]"
    assert p["scopes"][0]["platform_numbers"] == ["1", "2"]
    assert p["scopes"][0]["diagram_status"] == "source_generated_reference"
    assert not {"geometry", "station_route_id", "platform_id"}.intersection(p["attributes"])


def test_homonymous_station_requires_locality_or_line_evidence():
    p = profile("station", "甲站", {"locality": "上海市浦东新区", "main_lines": "甲铁路"})
    store = ReferenceStore([p])
    assert store.match("station", ["甲"], {"city": "上海"})
    assert store.match("station", ["甲站"], {"city": "上海市"})
    assert store.match("station", ["甲站"], {"city": "南京", "line_names": ["乙铁路"]}) is None
    assert store.match("station", ["甲站"], {}) is None


def test_facilities_never_inherit_mainline_parameters():
    store = ReferenceStore([profile("line", "甲铁路", {"gauge_mm": "1435"})])
    record = {"name": "甲铁路", "facility_only": True}
    assert line_reference(record, store) is None


def test_duplicate_sources_with_disagreement_stay_unresolved():
    first = profile("line", "甲铁路", {"track_count": "双线"})
    second = {**first, "id": "reference/other", "attributes": {"track_count": "单线"}}
    assert ReferenceStore([first, second]).match("line", ["甲铁路"]) is None


def test_equivalent_source_pages_can_share_reference_but_not_conflicting_scopes():
    first = profile("line", "甲铁路", {"track_count": "双线"})
    same = {**first, "id": "reference/alternate"}
    assert ReferenceStore([first, same]).match("line", ["甲铁路"])
    changed = {**same, "scopes": [{"name": "仅东段", "attributes": {"track_count": "双线"}}]}
    assert ReferenceStore([first, changed]).match("line", ["甲铁路"]) is None


def test_multiple_station_scales_do_not_become_total_platform_counts():
    html = '<div><h2>甲站</h2><div class="text-muted">上海市</div></div>'
    html += ('<div class="scale">' + para("站台数", "2") + para("股道数", "4") + '</div>') * 2
    p = parse_profile(html, "https://china-emu.cn/RailRoads/Station/?Station=甲")
    assert "platform_count" not in p["attributes"]
    assert "track_count" not in p["attributes"]
    assert len(p["scopes"]) == 2


def test_station_manual_overview_wins(monkeypatch):
    p = profile("station", "甲站", {"locality": "上海市", "platform_count": "8", "station_yards": "高速场 [1~4站台]"})
    import desktop.china_emu as module
    monkeypatch.setattr(module, "load_store", lambda: ReferenceStore([p]))
    props = {"name": "甲站"}
    values = station_overview(props, {"name": "甲站", "city": "上海", "province": "上海市"},
                              {"overview_attributes": {"platform_count": "10"}})
    assert values["platform_count"] == "10"
    assert values["station_yards"] == "高速场 [1~4站台]"
    assert props["reference_conflicts"]["platform_count"]["existing"] == "10"


def test_refresh_and_reimport_do_not_need_osm_changes(tmp_path):
    path = tmp_path / "reference.json"
    atomic_json(path, {"schema": SCHEMA, "profiles": [profile()]})
    assert len(load_store(path).profiles) == 1
    atomic_json(path, {"schema": SCHEMA, "profiles": [profile(name="新车型")]})
    assert load_store(path).profiles[0]["name"] == "新车型"
    assert not list(tmp_path.glob("*.tmp"))


def test_refreshed_selection_drops_old_reference_values_but_preserves_new_edits():
    p = profile("line", "甲铁路", {"gauge_mm": "1435", "max_speed_kmh": "160"})
    old = {"name": "改名后的线路", "gauge_mm": "1435", "max_speed_kmh": "120",
           "external_reference": p, "reference_provenance": {"gauge_mm": {}, "max_speed_kmh": {}},
           "way_tags": {"gauge": "1000"}}
    clean = without_previous_reference(old)
    assert "gauge_mm" not in clean
    assert "external_reference" not in clean
    assert clean["max_speed_kmh"] == "120"
    assert clean["way_tags"] == {"gauge": "1000"}
    assert old["gauge_mm"] == "1435"


@pytest.mark.parametrize("url", ["https://example.com/", "http://china-emu.cn/", "https://china-emu.cn:123/", "https://china-emu.cn.evil.test/"])
def test_source_urls_cannot_fetch_other_hosts(url):
    with pytest.raises(ValueError):
        source_url(url)


def test_new_line_fields_round_trip_without_changing_track_classification():
    values = {"civil_design_speed_kmh": "200", "track_spacing_m": "4.4", "track_structure": "有砟"}
    assert normalize_line_attributes(values, "rail") == values
    assert "track_type" not in values


def test_vehicle_title_fallback_and_unknown_parameters_are_not_facts():
    html = '<title>SYD01 - 动车组列车 - 中国动车组</title><h2></h2>'
    html += para("编组", "2M8T") + para("列控系统", "*")
    p = parse_profile(html, "https://china-emu.cn/Trains/Model/Detail-41000-107-S.html")
    assert p["name"] == "SYD01"
    assert p["attributes"]["formation"] == "2M8T"
    assert "train_control_system" not in p["attributes"]


def test_committed_snapshot_loads_in_a_new_workspace(tmp_path, monkeypatch):
    import desktop.china_emu as module
    shared = tmp_path / "shared.json"
    local = tmp_path / "local.json"
    monkeypatch.setattr(module, "SHARED_REFERENCE_PATH", shared)
    monkeypatch.setattr(module, "REFERENCE_PATH", local)
    atomic_json(shared, {"schema": SCHEMA, "profiles": [profile(name="提交快照")]})
    assert module.load_store().profiles[0]["name"] == "提交快照"
    atomic_json(local, {"schema": SCHEMA, "profiles": [profile(name="本地更新")]})
    assert module.load_store().profiles[0]["name"] == "本地更新"


def test_reference_browser_filters_and_shows_model_parameters(qtbot):
    from desktop.china_emu_ui import ReferenceDialog
    store = ReferenceStore([profile(attributes={"formation": "4M4T", "design_speed_kmh": "350 km/h"})])
    dialog = ReferenceDialog(store=store)
    qtbot.addWidget(dialog)
    assert dialog.objects.rowCount() == 1
    assert any(dialog.details.item(row, 1).text() == "4M4T" for row in range(dialog.details.rowCount()))
    dialog.search.setText("不存在")
    assert dialog.objects.rowCount() == 0
