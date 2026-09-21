class LineLibraryStub:
    def connected_lines(self, endpoint, query="", limit=100):
        return [{"id": "RL-A", "name": "甲线", "source_name": "甲线", "edge_count": 1}]

    def search_lines(self, query="", limit=100, **_kwargs):
        values = [
            {"id": "RL-A", "name": "甲线", "source_name": "甲线", "edge_count": 1},
            {"id": "RL-B", "name": "乙线", "source_name": "乙线", "edge_count": 1},
        ]
        return [value for value in values if query in value["name"] or query in value["id"]]

    def station_connection_override(self, endpoint, line_ids):
        assert endpoint == "station:node/100"
        return [
            {
                "line_id": line_id,
                "anchor_node": 1 if line_id == "RL-A" else 3,
                "distance_m": 0.0 if line_id == "RL-A" else 52.1,
                "source": "manual",
                "verification_status": "user_verified",
            }
            for line_id in line_ids
        ]


def test_station_connection_selector_searches_adds_removes_and_validates(qtbot):
    from desktop.rail_connection_ui import StationConnectionSelector

    selector = StationConnectionSelector(
        LineLibraryStub(), "station:node/100"
    )
    qtbot.addWidget(selector)
    assert selector.line_ids() == ["RL-A"]
    selector.search.setEditText("乙线")
    selector.search.find_results()
    selector.search.setCurrentIndex(0)
    selector.add_selected_line()
    assert selector.line_ids() == ["RL-A", "RL-B"]
    selector.table.selectRow(0)
    selector.remove_selected_lines()
    assert selector.line_ids() == ["RL-B"]
    assert selector.connections() == [
        {
            "line_id": "RL-B",
            "anchor_node": 3,
            "distance_m": 52.1,
            "source": "manual",
            "verification_status": "user_verified",
        }
    ]
