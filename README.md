# RailScope

RailScope is a desktop-oriented GIS and railway operations foundation. This
iteration implements **V0 + V1 + V5**: infrastructure GIS, explicit railway
topology, route paths, virtual blocks, occupancy/conflict detection, and manual
scenario dispatch. It deliberately excludes V2/V3 playback, national train
animation, timeline and time-distance diagram products.

## Prerequisites

- Python 3.12+
- Node.js 20+
- Docker Desktop with its daemon running (PostGIS is optional for the included
  in-memory demo and required for the database service)
- Rust/Tauri prerequisites only when building the desktop package

## Start

```powershell
Copy-Item .env.example .env
docker compose up -d db
Set-Location backend
python -m pip install -e '.[dev]'
python -m railscope.cli demo load
python -m uvicorn railscope.main:app --reload
```

In another terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open the Vite URL. The demo loads automatically in the API process; `demo load`
validates and reports the same deterministic data set.

## Tests

```powershell
Set-Location backend; python -m pytest
Set-Location frontend; npm test -- --run; npm run build
```

## Data import

```powershell
Set-Location backend
python -m railscope.cli gis import ..\data\demo\demo_network.geojson
python -m railscope.cli osm import region.osm.pbf
python -m railscope.cli topology validate
```

The generic importer accepts GeoJSON now and deliberately reports unsupported
formats rather than silently ignoring them. The OSM command exposes the stable
normalization boundary; install the optional `osmium` dependency to parse PBF.
Every import returns an ImportReport.

## V5 demo

At `2026-09-15`, trains 101 and 102 intentionally overlap on virtual Block AB.
Select 102 in Operations, apply a hold, then recalculate. Scheduled stop times
are never mutated: all results are derived from scenario events. Reset removes
events and restores the scheduled result.

## Desktop

直接运行 `desktop/Run-RailScope.ps1`，或在 PowerShell 中执行
`python desktop/launcher.py`。它会打开一个原生 Qt 桌面窗口，不会打开
浏览器，也不依赖网页渲染。窗口直接加载演示铁路、站点、虚拟区段、运行
任务、冲突列表和人工调度操作。

`desktop/src-tauri` 仍保留为未来 Tauri 打包壳；本机没有 Rust 工具链，当前
使用已可运行的 Qt WebEngine 壳来提供桌面体验。

### 中国城市地铁 / 轻轨导入

下载 Geofabrik 的 `china-latest.osm.pbf` 后，执行：

```powershell
Set-Location backend
python -m railscope.cli metro import ..\data\raw\osm\china-latest.osm.pbf --output ..\data\processed\osm
```

该命令只接受 OSM `type=route` 且 `route=subway` 或 `route=light_rail` 的线路关系；它保留每条关系的完整原始标签、每个轨道成员的完整原始标签、成员顺序和角色。输出目录包含：GeoJSON 地图层、线路目录和导入清单。显示颜色仅使用关系的 `colour`（优先）或 `color` 原值；无有效颜色时以中性灰显示，并在清单中报告，不会猜测线路颜色。

## Attribution

Demo geometry is synthetic. External OSM imports must retain OpenStreetMap
contributors attribution, license and import date through `data_source`.
