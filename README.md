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

### 同学从 GitHub 下载后的启动步骤

1. 下载最新 `feature/desktop-workbench` 分支 ZIP，**完整解压**，不要在 ZIP 内运行。
2. 安装 Python 3.12 或更新的兼容版本（Windows 64 位）。当前 GitHub 提供的是源码，不是免安装 EXE。
3. 双击根目录 **`Start-RailScope.cmd`**。也可右键 `desktop/Run-RailScope.ps1` → 使用 PowerShell 运行。
4. 首次会自动创建项目内 `.venv`、安装桌面依赖、下载并校验固定版本地图组件。需要联网；不需要 Node.js、Docker、管理员权限或手工启动服务器。
5. 软件打开后，使用 **数据源 → 自动下载 / 更新全国地铁…** 获取数据。

不再依赖开发者电脑的 `D:\Python312`。启动失败会显示原因并保留窗口，日志位于
`data/logs/startup.log`。如脚本被 Windows 阻止，优先使用根目录 CMD 入口；它仅为
本次 PowerShell 进程设置执行策略，不修改系统全局策略。

**GitHub 不包含全国地铁数据**：约 1.5 GB 的原始 OSM PBF、大型派生 GIS 图层、
Python 环境和地图组件缓存均不提交。仓库只有小型合成示例；没有全国数据也能
打开地图和菜单，而不是因运行编辑器空列表退出。每位使用者首次下载一次即可。

桌面端也支持手工安装：`python -m pip install -r desktop/requirements.txt`，随后
`python desktop/launcher.py`。启动器自动准备地图组件；无需 `npm install`。

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
启动时运行展示关闭；在运行面板主动开启、开始、暂停或关闭。暂停保留位置，
关闭移除列车。列车标记可选择光晕圆点、空心圆环、列车图标和 8–40 px 大小。
地图标题卡片默认隐藏；视图菜单可控制标题、导航工具、图例、坐标浮层及比例尺。

目录归属有误时，使用 **编辑 → 目录层级设置…**，选择线路或整个城市，设置
所属省、市（也可自定义目录名），点击「保存并应用」。同一线路的方向关系
一起移动，重启后保留；支持恢复自动归类，不修改原始 OSM 属性与线路颜色。

`desktop/src-tauri` 仍保留为未来 Tauri 打包壳；本机没有 Rust 工具链，当前
使用已可运行的 Qt WebEngine 壳来提供桌面体验。

### 中国城市地铁 / 轻轨导入

推荐直接使用软件内 **数据源 → 自动下载 / 更新全国地铁…**：

- 下载 Geofabrik 全国快照，支持暂停与续传；通过版本标识避免续传混合不同快照，完整文件校验通过才替换 PBF。
- 默认复用完整的本地 PBF；需要更新时开启「重新下载最新快照」。约 1.5 GB 下载，建议预留至少 10 GB 空间。
- 自动导入运营线路、全部原始标签与颜色、站点、在建工程、真实车站多边形及多面关系。OSM 未绘制的范围不会伪造。
- 导入时可以收起工具继续浏览地图。导入阶段不能暂停；请等待完成后退出。长任务日志保存在 `data/logs/metro-install-*.log`。
- 全部阶段成功才原子切换 `data/processed/osm/active_dataset.json`；失败不改变当前路网。旧目录、运行计划和用户目录设置保留。
- 完成后先保存运行计划，再点击「载入已完成的数据」，在同一工作区加载新数据；或重启软件。

没有手工下载过数据的新用户，**不要只安装依赖后等待地铁自动出现**：请主动点击
上述下载菜单。底图仍需联网，下载本地地铁不等于下载全国底图或卫星影像。

高级用户仍可在已有 PBF 的基础上手工导入：

```powershell
Set-Location backend
python -m railscope.cli metro import ..\data\raw\osm\china-latest.osm.pbf --output ..\data\processed\osm
```

该命令只接受 OSM `type=route` 且 `route=subway` 或 `route=light_rail` 的线路关系；它保留每条关系的完整原始标签、每个轨道成员的完整原始标签、成员顺序和角色。输出目录包含：GeoJSON 地图层、线路目录和导入清单。显示颜色仅使用关系的 `colour`（优先）或 `color` 原值；无有效颜色时以中性灰显示，并在清单中报告，不会猜测线路颜色。

软件自动下载使用 [Geofabrik 官方中国数据源](https://download.geofabrik.de/asia/china.html)。
手工 CLI 输出到旧版目录；如已通过软件下载激活版本目录，需导入到当前版本目录，
或继续使用软件的自动流程。当前活动目录记录在 `active_dataset.json` 中。

## 本版新增：交路、国铁与运行计划

- 「运行 → 地铁 · 大小交路 / 循环与车辆投放」集中设置端点、往返逐站相对时刻、折返等待、车辆数量和每辆车的开始/结束时间；生成后可继续编辑运行表/图，并撤销、重做。
- 运行侧栏可切换地铁 / 国铁；两者分别控制开始、暂停、关闭及导入导出。国铁必须明确跨线物理区间径路，不按站名自动猜路。
- 「数据源 → 下载 / 提取全国国铁」复用同一个全国 PBF。提取实际股道、在建铁路、站点/道岔、站台轮廓与区间；数据按视窗加载，复杂区域需要放大查看。OSM 标注不完整时，不能保证所有站台、线路所、道岔或几台几线都齐全。
- 国铁目录可按省份或“规划通道 → 分段”浏览；「编辑 → 国铁通道 / 分段分类整理」可修改归属。未确认的通道与分段保持未分类，不伪造全国官方分段映射。
- 「文件 → 截图」保存当前地图（带版权标注）或完整运行图 PNG。不会截取其他软件或桌面内容。
- 「数据源 → 车站真实轮廓覆盖检查」区分站台、站区/建筑与缺失轮廓，支持导出核查清单。更新时按 OSM ID 保留旧的真实边界并标注可能过时；不画圆形替代站台。
- [运行计划书面标准](docs/OPERATING_PLAN_STANDARD.md)，在帮助菜单内也能打开。支持旧 v1 迁移、严格 v2 校验、命名空间扩展保留；尚不支持的必要能力会拒绝，不假装实现越行或复杂贯通。
- [站台轮廓策略](docs/STATION_BOUNDARY_STRATEGY.md)。真实折返轨、联锁、股道安全占用尚未验证；动画不是调度安全证明。

### GitHub 下载后在导入 1/3 失败

本次已修复 libosmium 在部分 Windows 中文路径下无法打开已有 PBF 的问题。
使用安全的同盘英文路径硬链接或系统短路径，不复制、重下 1.5 GB 文件；英文临时目录不可写时会快速给出错误，不无限等待。
PBF 已下载后，更新代码再点“开始 / 继续”，保持“重新下载最新快照”关闭。
失败会显示具体阶段及读取器错误，日志在 `data/logs/metro-install-*.log`；旧数据不替换。
完整解压 GitHub 项目，双击根目录 `Start-RailScope.cmd`；需要 Python 3.12+ 和首次依赖下载联网。

## Attribution

Demo geometry is synthetic. External OSM imports must retain OpenStreetMap
contributors attribution, license and import date through `data_source`.
