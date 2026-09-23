import pytest

from desktop.rail_point_style_ui import defaults, load, validate


def test_point_style_defaults_and_invalid_file(tmp_path):
    assert validate(defaults())["control"] == {"size": 3, "shape": "solid"}
    path = tmp_path / "style.json"
    path.write_text("{}", encoding="utf-8")
    assert load(path) == defaults()
    with pytest.raises(ValueError):
        validate({"station": {"size": 40, "shape": "ring"}, "control": defaults()["control"]})
