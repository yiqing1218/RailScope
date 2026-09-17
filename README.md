# RailScope

RailScope is a desktop-oriented GIS and railway operations foundation. This
iteration implements **V0 + V1 + V5**: infrastructure GIS, explicit railway
topology, route paths, virtual blocks, occupancy/conflict detection, and manual
scenario dispatch. It deliberately excludes V2/V3 playback, national train
animation and real railway control. The native desktop now includes a local,
editable Shanghai stop-time table and time-distance diagram for simulation,
not an official timetable or live fleet service.

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

首次安装桌面依赖：`python -m pip install -r desktop/requirements.txt`。
地图使用本地 MapLibre 资源，首次需在 `frontend` 执行 `npm install`；不必启动
Vite 或单独的 API 服务。全国路网属于可再生导入数据，不随 Git 提交，需按下方
步骤从已有 PBF 导入。本机已导入的数据会继续使用。

直接运行 `desktop/Run-RailScope.ps1`，或在 PowerShell 中执行
`python desktop/launcher.py`。它会打开一个原生 Qt 桌面窗口，不会打开
外部浏览器。桌面使用 Qt 原生菜单、侧栏与运行表/运行图，地图使用嵌入式
Qt WebEngine / MapLibre（因此仍需要 WebEngine 支持）。菜单功能集中在一个
主窗口，地图展示本地 OSM 线路、站点、真实站区多边形与在建工程。

点击最左侧「运行」展开可编辑运行表/运行图。选择线路和方向/支线方案，
新增车次，编辑到发时刻或拖动运行图节点；「整车平移」可提前/延后整列车。
地图上的车辆按这些时刻停站和运行。`Ctrl+S` 保存，下次启动恢复，菜单支持
计划导入/导出。初始 42 列车均为演示数据，**不是上海地铁真实全车队或官方
运行图**。详见 [车辆与计划约定](docs/VEHICLE_LAYER_CONTRACT.md)。

目录归属有误时，使用 **编辑 → 目录层级设置…**，选择线路或整个城市，设置
所属省、市（也可自定义目录名），点击「保存并应用」。同一线路的方向关系
一起移动，重启后保留；支持恢复自动归类，不修改原始 OSM 属性与线路颜色。

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
