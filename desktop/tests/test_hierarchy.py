from copy import deepcopy
import json
import pytest
from desktop.hierarchy import Hierarchy, infer_parent

REGIONS = [("南京", "江苏省", 118.80, 32.06), ("昆明", "云南省", 102.83, 24.88)]


def routes():
    return [
        dict(
            osm_relation_id=7957933,
            ref="4",
            name="昆明地铁4号线",
            network="昆明地铁",
            operator="云南京建轨道交通投资建设有限公司",
            relation_tags={"colour": "#DD8800"},
        ),
        dict(
            osm_relation_id=11645555,
            ref="4",
            name="昆明地铁4号线反向",
            network="昆明地铁",
        ),
        dict(osm_relation_id=10, ref="4", name="南京地铁4号线", network="南京地铁"),
    ]


def test_operator_substring_does_not_override_network_city():
    assert infer_parent(routes()[0], [102.8, 24.9], REGIONS) == ("云南省", "昆明")


def test_ambiguous_name_uses_coordinates_and_operator_alone_does_not_match():
    assert infer_parent({"name": "昆明至南京"}, [102.8, 24.9], REGIONS) == (
        "云南省",
        "昆明",
    )
    assert infer_parent({"operator": "云南京建公司"}, None, REGIONS) == (
        "未归类地区",
        "未归类城市",
    )


def test_both_directions_move_together_without_mutating_osm(tmp_path):
    original = routes()
    before = deepcopy(original)
    model = Hierarchy(original, {}, REGIONS, tmp_path / "hierarchy.json")
    assert len(model.grouped()["云南省"]["昆明"]["4号线"]) == 2
    model.set_parent([7957933, 11645555], "自定义省", "自定义市", "我的线路")
    model.save()
    restored = Hierarchy(original, {}, REGIONS, tmp_path / "hierarchy.json")
    restored.load()
    assert restored.parent(original[0]) == ("自定义省", "自定义市", "我的线路")
    assert len(restored.grouped()["自定义省"]["自定义市"]["我的线路"]) == 2
    assert original == before
    restored.reset([7957933, 11645555])
    assert restored.parent(original[0]) == ("云南省", "昆明", "4号线")


def test_province_move_preserves_multiple_cities_and_leaf_names():
    model = Hierarchy(routes(), {}, REGIONS)
    model.set_parent([7957933, 10], province="分组 A")
    assert model.parent(routes()[0]) == ("分组 A", "昆明", "4号线")
    assert model.parent(routes()[2]) == ("分组 A", "南京", "4号线")


def test_invalid_edit_and_invalid_load_are_atomic(tmp_path):
    model = Hierarchy(routes(), {}, REGIONS, tmp_path / "hierarchy.json")
    model.set_parent([7957933], province="我的省")
    before = deepcopy(model.overrides)
    with pytest.raises(ValueError):
        model.set_parent([7957933, 999999], city="其他市")
    assert model.overrides == before
    with pytest.raises(ValueError):
        model.set_parent([7957933], city="  ")
    model.path.write_text(
        json.dumps(
            {
                "schema": "railscope.layer-hierarchy.v1",
                "overrides": {
                    "7957933": {"province": None, "city": "昆明", "label": "4号线"}
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        model.load()
    assert model.overrides == before
