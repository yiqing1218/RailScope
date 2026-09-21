# RailScope

## Windows 桌面版：下载后如何运行

1. 下载 ZIP 后先完整解压到可写文件夹，中文和空格路径均可；不要直接在 ZIP 内运行。
2. 安装 **64 位 Python 3.12**，安装时勾选加入 PATH；双击根目录 **Start-RailScope.cmd**。
3. 首次运行自动创建 `.venv`、安装桌面依赖和校验地图组件，需要联网。无需 Node、Docker、PostGIS 或手动运行后端。
4. 内置 G1 参考示例无需全国数据。全国地铁/国铁在菜单栏「数据源」下载和提取；原始 PBF 和生成数据不在 GitHub 源码中。

不要从另一台电脑复制 `.venv`。启动失败会保留错误窗口，日志位于 `data/logs/startup.log`。可运行 `powershell -NoProfile -ExecutionPolicy Bypass -File desktop/Run-RailScope.ps1 -CheckOnly` 做启动检查。在线底图和首次依赖下载仍取决于本机网络；不代表离线安装包。

桌面端启动默认只显示底图，所有轨道、车站、站台、在建线路与车辆图层关闭；在左侧按需打开。全部数据导入/下载入口在菜单栏。

国铁目录按轨道中点所在省界分类，跨省同名线路分省列出。旧数据自动升级目录，不必重下 PBF；省界附近保留待核对。

G1 已内置：**运行 → 国铁 · 车次 / 跨线运行图 → 打开国铁运行表 / 运行图**。没有“加载 G1”按钮，无需全国数据即可查看、编辑、导入导出并手动开始/暂停仿真。公开时刻和 OSM 连通几何是参考，不代表实际调度进路。详见 [国铁运行说明](docs/NATIONAL_RAIL_OPERATIONS.md)。

地铁与国铁运行菜单、计划和时钟独立。国铁按车次选择独立时刻表 / 运行图，一车次对应一列车；工作台可手动新增车次，运行菜单可批量导入严格 CSV 表。共享径路 JSON v2 避免每个车次重复声明轨道；兼容 v1。变道信息预留，不表示已验证真实股道。铁路目录按“轨道类型 → 物理线路 / 车站”整理；每条地图轨道保留端点线段属性。地图菜单可按类型设置颜色 / 线宽。

国铁采用三层结构：**稳定物理 NetworkEdge → 单向完整 Corridor → 具体 TrainRun**。左侧“通道”以起点—铁路线—终点表格编排，保存结果是从起点边界到终点边界的完整连续 edge 序列；经过中间车站不拆 CorridorSegment。直达、大站停和站站停车次可以引用同一 Corridor，仅 stops 不同；反向或跨线运行另建完整 Corridor。站台、到发线和车站进路在各车次时刻表中填写。通道独立导入 / 导出支持 `railscope.rail-corridors.v2` JSON 和严格 CSV，兼容旧 v1。详见 [交换标准](docs/OPERATING_PLAN_STANDARD.md)。

RailScope is a desktop-oriented GIS and railway operations foundation. This
iteration implements **V0 + V1 + V5**: infrastructure GIS, explicit railway
topology, complete shared Corridors, virtual blocks, occupancy/conflict detection,
manual scenario dispatch and reference train animation. It excludes real railway
control and does not claim verified signalling or interlocking routes. The native desktop includes a local,
editable Shanghai stop-time table and time-distance diagram for simulation,
not an official timetable or live fleet service.

## Web / backend development prerequisites（不是桌面版启动要求）

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
- 国铁目录只按轨道类型、物理线路/车站浏览。每个地图线段仍记录确定的两个端点及相接 RS；行政区域和八纵八横规划通道不再参与物理分类。全国目录按需限制 Qt 树节点数，完整 RS/端点检索由磁盘索引承担。
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
# 端点—线路通道

国铁通道可在左侧“通道”中按起点 → 铁路线 → 终点表格新建和编辑，复用固定基础设施，不为车次生成线路。编辑菜单支持稳定编号下的线路改名；文件菜单支持通道交换与铁路命名/端点分段目录导出。详见 [命名与通道标准](docs/RAIL_LINE_NAMING_AND_CORRIDORS.md)。
