"""Board-aware layout model with migration from the legacy flat list / {"widgets": [...]} forms."""


def migrate_layout(data):
    if isinstance(data, dict) and "boards" in data:
        return data
    if isinstance(data, list):
        widgets = data
    elif isinstance(data, dict):
        widgets = data.get("widgets", [])
    else:
        widgets = []
    return {"boards": [{"id": "main", "name": "Main", "widgets": widgets}], "active": "main"}
