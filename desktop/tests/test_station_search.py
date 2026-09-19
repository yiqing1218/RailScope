from desktop.map_commands import MapCommands
from desktop.station_search import matches_query, name_keys, normalize_name


def test_station_search_accepts_suffix_chinese_english_and_old_name():
    props = {
        "name": "徐家汇站",
        "station_tags": {
            "name:en": "Xujiahui Metro Station",
            "old_name": "徐家汇地铁站",
        },
    }
    assert normalize_name("徐家汇地铁站") == "徐家汇"
    assert "xujiahui" in name_keys(props)
    record = {"name": "徐家汇站", "aliases": ["Xujiahui Metro Station"]}
    assert matches_query(record, "徐家汇")
    assert matches_query(record, "xujiahui station")


def test_pending_map_commands_keep_latest_state_with_independent_toggles():
    pending = MapCommands()
    pending.put("setLines", ([1],), "lines-1")
    pending.put("setLines", ([2],), "lines-2")
    pending.put("setVisibility", ("metro", True), "metro")
    pending.put("setVisibility", ("stations", True), "stations")
    assert list(pending.pending.values()) == ["lines-2", "metro", "stations"]
