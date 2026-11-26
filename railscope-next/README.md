# RailScope Next

RailScope Next 是独立的 Windows 交通 GIS 工作台，使用 Tauri 2、Rust、React 和 MapLibre。低缩放地图读取本机 PMTiles，高缩放可按视野从只读 SQLite 生成源几何瓦片。目录、原始 OSM、线路编辑、日期车次、运行图和仿真均可离线使用。

## 启动

Windows x64 用户可从 [GitHub Releases](https://github.com/yiqing1218/RailScope/releases/latest) 下载安装包。安装后从开始菜单打开 RailScope Next，首次启动可直接进入随附的上海离线地图。顶栏地球图标切换中文与 English，问号打开使用指南。

For Windows x64, download the installer from [GitHub Releases](https://github.com/yiqing1218/RailScope/releases/latest) and launch RailScope Next from the Start menu. The Shanghai offline map is included. Use the globe menu to switch languages and the question mark to reopen Quick start.

仓库中可双击 `Start-RailScope.cmd`。脚本优先使用与当前源码版本一致的 release 构建，其次检查 debug 构建。缺少匹配版本时，请按下面的命令构建。启动脚本默认读取 `data/processed/railscope-next/packs` 中的本机数据包。

从源码构建需要 Node.js 22.12+、Rust stable MSVC、Microsoft C++ Build Tools 和 WebView2。在仓库根目录运行以下命令。[Tauri Windows 构建环境](https://v2.tauri.app/start/prerequisites/)

```powershell
Set-Location railscope-next/apps/desktop
npm.cmd ci
npm.cmd run tauri -- build --no-bundle
```

要构建普通 NSIS 安装包，将最后一条命令改为 `npm.cmd run tauri -- build`。发布随附上海数据的安装包时，先将发行页的上海数据 ZIP 解压到 `data/processed/railscope-next/packs`，保留里面的 `shanghai-osm-202609-v4` 文件夹，然后运行 `npm.cmd run tauri -- build --config src-tauri/tauri.release.conf.json`。

安装新的本机快照时，在「数据 → 数据包 → 安装本地快照」输入包含 `manifest.json` 的文件夹路径。默认安装到 `%LOCALAPPDATA%\app.railscope.next\packs`，也可通过 `RAILSCOPE_NEXT_PACKS` 指定。随软件安装的上海资源可直接读取。已有工作区会保留上次使用的数据包。

## 生成离线数据包

在仓库根目录运行。下面的命令使用现有上海 OSM PBF。Natural Earth 陆地多边形参数可省略。

```powershell
python -m pip install -r railscope-next/pipelines/requirements.txt
python railscope-next/pipelines/build_pack.py `
  data/raw/osm/shanghai-latest.osm.pbf data/processed/railscope-next/packs `
  --region 上海 --snapshot shanghai-osm-202609 --maxzoom 14 `
  --land data/processed/admin/natural_earth_10m_land.geojson
```

全国概览可用 `china-latest.osm.pbf`，以及 `--region 全国 --snapshot china-osm-202609 --maxzoom 8 --location-index disk`。数据包使用新的快照标识发布，支持小写英文字母、数字、点、短横线和下划线。

0.5.0 可继续读取已有全国与上海快照，也支持重新抽取铁路设施生成独立快照。新工作区默认加载发现的最新全国源包，已有工作区可在「数据」或顶部数据包菜单切换。下例用匹配旧包 SHA-256 的 PBF 生成新的上海快照。

```powershell
python railscope-next/pipelines/enrich_pack.py `
  data/processed/railscope-next/packs/shanghai-osm-202609-r3 `
  data/raw/osm/shanghai-latest.osm.pbf `
  data/processed/railscope-next/packs shanghai-osm-202609-v4
```

全国源补全的命令与回滚方法见[迁移说明](../docs/next-v3/migration.md)。

每个数据包包括：

- `manifest.json` 记录源文件、坐标系、范围、分类计数和 SHA-256。
- `canonical.sqlite` 保留业务线路与设施的关系、具有方向的轨道与道路边、WGS84 WKB、OSM 来源、RTree 与 FTS5。
- `transport.pmtiles` 包含简化、裁切后的矢量瓦片。可视渲染 ID 通过只读快照解析为 RailScope UUID。

## 使用

从 **数据 → 外部资料导入** 导入车站、线路及分期、物理轨道、通道、运行计划与时刻表。也可以从该菜单下载模板。文件格式、来源字段和命令行用法见 [外部资料导入手册](apps/desktop/src/import-templates/README.md)。

在地图左侧打开 **图层**，在 **国家铁路** 和 **城市轨道交通** 分类中分别勾选 **在建线路**。两项默认关闭。地图顶部的 **图层** 提供地图预设和图例。

- **地图**可按名称、拼音或 `osm:way:…` 搜索。分组控制底图文字、国铁、城轨、公路、通道和动态列车。地图的站名与业务线名可独立切换，车站详情可查看相关线路和 OSM 关系证据。
- **运行**在底部按端点、线路和方向编排并预览共享通道。还提供时刻表、单车运行图、上海地铁双向交路图和仿真控制。CSV、JSON、GTFS 可从「车辆与交路」导入导出。
- **分析与诊断**可执行临时路径查询、检查场景资源风险和查看系统性能。
- **数据**可安装和切换快照、查看许可和来源、导入旧版计划及审校失败的引用。

目录、详情页、国铁通道编排及上海 7 号线和 G1 的本地运行验证见[地图与运行工作台](../docs/workbench.md)。

## 校验

```powershell
python -m unittest discover -s railscope-next/tests -p test_pipeline.py -v
cargo test --locked --manifest-path railscope-next/Cargo.toml -p railscope-next-core
```

Windows 桌面回归通过临时工作区和 WebView2 调试端口运行。详见[0.5.0 验收与旧通道迁移](../docs/next-v5/acceptance.md)及[工作台架构](../docs/next-v4/architecture.md)。OSM 数据来源和许可证在应用及清单中保留。[OpenStreetMap 署名与许可](https://www.openstreetmap.org/copyright)
