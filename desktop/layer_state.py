"""Each session starts in base-map-only mode, regardless of saved plans."""


def initial_visibility():
    return {
        key: False
        for key in (
            "metro",
            "stations",
            "construction",
            "rail",
            "railConstruction",
            "railPoints",
            "railPlatforms",
            "railVehicles",
            "railPlan",
            "road",
            "imported",
            "vehicles",
        )
    }


def editor_sizes(height, index, expanded=False):
    height = max(540, height)
    editor = height - 200 if expanded else round(height * 0.55)
    return [height - editor, editor if index == 0 else 0, editor if index == 1 else 0]
