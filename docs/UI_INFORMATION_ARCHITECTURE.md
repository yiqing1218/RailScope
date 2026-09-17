# RailScope UI information architecture

## Scope

This desktop UI implements the requested V0 + V1 + V5 product boundary.  It
does not expose V2/V3 playback, animation, timeline or time-distance diagrams.

## Application shell

`Top bar → workspace navigation → left context panel → map/content → right
details/actions → status bar`

## Workspaces

1. **Map** — V1 GIS workspace: layers, search, selected-object details and map.
2. **Operations** — V5 workspace: train runs, blocks, occupancies, conflicts,
   scenario recalculation and manual dispatch.
3. **Data sources** — OSM/GeoJSON imports, source attribution, import reports
   and data readiness.  National metro is shown here as an external GIS source,
   not as an operations feature.
4. **Topology** — infrastructure graph validation and topology diagnostics.

## Menus

- **Data**: import, data sources, import reports.
- **Map**: layer visibility, zoom/selection tools.
- **Operations**: scenario selection, recalculate, dispatch actions.
- **Topology**: validate and diagnostics.
- **Help**: about and OSM attribution.

## Selection rules

- Clicking a map object opens its attributes in the right details panel.
- Clicking a conflict selects its resource and related trains, then fits the map.
- Dispatch changes effective scenario data only; scheduled data remains immutable.
