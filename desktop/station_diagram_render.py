"""Compatibility adapter for independent engineering station diagrams."""
try:
    from .station_diagram.renderer import render_svg, edge_color
    from .station_diagram.export import write_diagram
except ImportError:
    from station_diagram.renderer import render_svg, edge_color
    from station_diagram.export import write_diagram

__all__ = ["render_svg", "write_diagram", "edge_color"]
