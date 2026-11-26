# RailScope Next

[简体中文](../README.md) · **English**

RailScope Next is an offline rail GIS and operations workspace. Explore railway and metro networks, inspect facilities, build train corridors and edit timetables.

**[Download for Windows x64](https://github.com/yiqing1218/RailScope/releases/latest/download/RailScope-Next-windows-x64-setup.exe)** · [Release notes](https://github.com/yiqing1218/RailScope/releases/latest) · [Report an issue](https://github.com/yiqing1218/RailScope/issues)

## Quick start

1. Download and run the Windows x64 installer. It includes an offline Shanghai map and WebView2.
2. Open **RailScope Next** from the Start menu and choose **Explore the map** in Quick start.
3. Click a line, station or facility footprint on the map, or search the catalog. Use the globe menu to switch languages and the question mark to reopen Quick start.

To run from source, follow the [build instructions](../railscope-next/README.md), then open `Start-RailScope.cmd` in the repository root. To bring in a V1 operating plan, use **Data → Import legacy plan**.

## What you can do

| Workspace | Features |
| --- | --- |
| Map | Browse railways, urban rail, motorways and facilities. Open a station from its map point or linked footprint, and inspect its properties, related lines and sources. |
| Operations | Assemble corridors from endpoints and lines, edit timetables and view time–distance graphs and Shanghai Metro service diagrams. |
| Analysis | Inspect physical track connections, run plan simulations and review resource conflicts. |
| Data | Install or switch offline snapshots and import legacy plans. |

## Offline data

The installer includes Shanghai. To add nationwide or other regional coverage, install a compatible snapshot from **Data → Data packs → Install a local snapshot** using the folder containing `manifest.json`. Place and line names retain their source spelling.

The Shanghai pack is derived from OpenStreetMap and distributed under ODbL 1.0. It is also available as a standalone ZIP on the release page. Route and simulation results are intended for exploration and analysis. The inspector labels the evidence behind line associations. [Data attribution and license](licenses/shanghai-offline-data.md)

## More information

- [Build from source and prepare offline data](../railscope-next/README.md)
- [Station footprints and data model (Chinese)](next-v7/railway-sites.md)
- [Historical screenshots, station diagrams and timetable references (Chinese)](reference/README.md)
