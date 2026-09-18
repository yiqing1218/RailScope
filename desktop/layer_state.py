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
            "railStationAreas",
            "railVehicles",
            "railPlan",
            "road",
            "imported",
            "vehicles",
        )
    }


def editor_sizes(height, index, expanded=False):
    height = max(540, height)
    editor = (
        height - 200
        if expanded
        else (
            min(height - 200, max(540, round(height * 0.65)))
            if index == 1
            else round(height * 0.55)
        )
    )
    return [height - editor, editor if index == 0 else 0, editor if index == 1 else 0]
