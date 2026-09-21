import pytest


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({"highspeed": "yes"}, "高速铁路线"),
        ({"name": "京沪高铁"}, "高速铁路线"),
        ({"usage": "main", "maxspeed": "120"}, "普速铁路线"),
        ({"usage": "industrial"}, "货运铁路线"),
        ({"usage": "main", "passenger": "no"}, "货运铁路线"),
        ({"service": "spur"}, "支线 / 岔道"),
        ({"service": "crossover"}, "渡线 / 道岔连接轨"),
        ({"name": "南夏宋联络线", "highspeed": "yes"}, "联络线 / 匝道"),
        ({"service": "siding", "highspeed": "yes"}, "高速铁路站场股道"),
        ({"service": "yard"}, "站场股道（类型待核对）"),
        ({}, "未确认类型"),
    ],
)
def test_track_type_uses_explicit_evidence(tags, expected):
    from desktop.rail_categories import track_type

    assert track_type(tags)[0] == expected


def test_corridor_is_not_guessed_for_service_tracks():
    from desktop.rail_categories import corridor_for

    assert corridor_for("京沪高速铁路", "高速铁路线") == "纵向 · 京沪通道"
    assert corridor_for("京沪高速铁路", "高速铁路站场股道") == "不适用"
    assert corridor_for("不认识的高铁", "高速铁路线") == "高速通道待核对"


def test_same_named_tracks_split_by_type():
    from desktop.provinces import add_track, ProvinceIndex

    catalog = {}
    for ident, tags in [
        (1, {"highspeed": "yes"}),
        (2, {"highspeed": "yes", "service": "siding"}),
    ]:
        add_track(
            catalog,
            {
                "properties": {
                    "osm_way_id": ident,
                    "way_tags": {"name": "京沪高速铁路", **tags},
                },
                "geometry": {"coordinates": [[121.45, 31.2], [121.46, 31.2]]},
            },
            ProvinceIndex(),
        )
    assert len(catalog) == 2
    assert {r["track_type"] for r in catalog.values()} == {
        "高速铁路线",
        "高速铁路站场股道",
    }


def test_auxiliary_tracks_do_not_become_highspeed_main_lines():
    from desktop.rail_categories import track_type, corridor_for, CORRIDORS

    assert (
        track_type({"highspeed": "yes", "name": "徐兰大西疏解线"})[0] == "联络线 / 匝道"
    )
    assert track_type({"highspeed": "yes", "name": "南昌东立折线"})[0] == "折返线"
    assert (
        track_type({"highspeed": "yes", "name": "丰台动车走行A线"})[0]
        == "车辆段 / 检修线"
    )
    assert len(CORRIDORS) == 16
    assert corridor_for("京广高速线", "高速铁路线") == "纵向 · 京哈—京港澳通道"
    assert corridor_for("沪昆高速线", "高速铁路线") == "横向 · 沪昆通道"


def test_catalog_hierarchy_uses_business_lines_without_province_splitting():
    from desktop.rail_categories import catalog_parents

    meta = {
        "track_type": "货运铁路线",
        "province": "上海市",
        "corridor": "误设通道",
        "section": "分段",
    }
    meta["line_display_name"] = "沪宁线 · IL-TEST"
    assert catalog_parents(meta, 0) == ("其他铁路", "货运铁路")
    meta["track_type"] = "高速铁路线"
    assert catalog_parents(meta, 0) == ("高速铁路", "区域高速铁路")
    meta["line_display_name"] = "广珠城际铁路 · IL-TEST"
    assert catalog_parents(meta, 0) == ("高速铁路", "区域高速铁路")
    meta.update(track_type="普速铁路线", line_display_name="京沪铁路 · IL-TEST")
    assert catalog_parents(meta, 0) == ("普速铁路", "国家铁路干线")
    meta.update(track_type="高速铁路站场股道", station_name="上海虹桥站")
    assert catalog_parents(meta, 0) == ("其他铁路", "不确定铁路")
    meta.update(track_type="未确认类型", line_display_name="金温地方铁路 · IL-TEST")
    assert catalog_parents(meta, 0) == ("其他铁路", "地方铁路")
