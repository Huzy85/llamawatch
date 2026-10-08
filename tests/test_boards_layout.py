from llamawatch.layout import migrate_layout


def test_migrate_flat_list_to_single_board():
    flat = [{"id": "network", "x": 0, "y": 0, "w": 4, "h": 2}]
    out = migrate_layout(flat)
    assert out["boards"][0]["id"] == "main"
    assert out["boards"][0]["name"] == "Main"
    assert out["boards"][0]["widgets"] == flat
    assert out["active"] == "main"


def test_migrate_widgets_dict_to_single_board():
    data = {"widgets": [{"id": "gpu", "x": 0, "y": 0, "w": 4, "h": 2}]}
    out = migrate_layout(data)
    assert out["boards"][0]["widgets"] == data["widgets"]
    assert out["active"] == "main"


def test_migrate_empty_dict():
    out = migrate_layout({"widgets": []})
    assert out["boards"][0]["widgets"] == []
    assert out["active"] == "main"


def test_migrate_passthrough_when_already_boards():
    boarded = {"boards": [{"id": "b1", "name": "Ops", "widgets": []}], "active": "b1"}
    assert migrate_layout(boarded) == boarded
