"""Support package and legacy desktop script entry points."""

try:
    from ..station_diagram_layout import (
        DiagramEdge,
        DiagramLayout,
        PlatformSymbol,
        edge_role,
        principal_axis,
        reliable_platform_axes,
    )
    from ..station_schematic import (
        station_projection,
        platform_parts,
        mainline_label,
        port_destination,
        ensure_export_font,
    )
except ImportError:
    from station_diagram_layout import (
        DiagramEdge,
        DiagramLayout,
        PlatformSymbol,
        edge_role,
        principal_axis,
        reliable_platform_axes,
    )
    from station_schematic import (
        station_projection,
        platform_parts,
        mainline_label,
        port_destination,
        ensure_export_font,
    )

__all__ = [
    "DiagramEdge",
    "DiagramLayout",
    "PlatformSymbol",
    "edge_role",
    "principal_axis",
    "reliable_platform_axes",
    "station_projection",
    "platform_parts",
    "mainline_label",
    "port_destination",
    "ensure_export_font",
]
