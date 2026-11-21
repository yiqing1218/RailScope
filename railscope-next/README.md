# RailScope Next

RailScope Next 是独立的 Windows 交通 GIS 工作台，使用 Tauri 2、Rust、React 和 MapLibre。低缩放地图读取本机 PMTiles，高缩放可按视野从只读 SQLite 生成源几何瓦片。目录、原始 OSM、线路编辑、日期车次、运行图和仿真均可离线使用。

## 启动

仓库中可双击 `Start-RailScope-Next.cmd`。启动脚本使用 `data/processed/railscope-next/packs` 中的本机数据包。

从源码构建需要 Node.js 22.12+、Rust stable MSVC、Microsoft C++ Build Tools 和 WebView2。在仓库根目录运行以下命令。[Tauri Windows 构建环境](https://v2.tauri.app/start/prerequisites/)

```powershell
Set-Location railscope-next/apps/desktop
npm.cmd ci
npm.cmd run tauri -- build --no-bundle
```

要构建 NSIS 安装包，将最后一条命令改为 `npm.cmd run tauri -- build`。独立安装时，在数据包菜单中选择「从本机安装」并输入快照目录，校验成功后选择该快照。默认数据包根目录为 `%LOCALAPPDATA%\app.railscope.next\packs`，也可用 `RAILSCOPE_NEXT_PACKS` 指定。

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

0.4.0 可继续读取已有全国与上海快照，也支持重新抽取铁路设施生成独立快照。新工作区默认加载发现的最新全国源包，已有工作区可在「数据」或顶部数据包菜单切换。下例用匹配旧包 SHA-256 的 PBF 生成新的上海快照。

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

- **地图**可按名称、拼音或 `osm:way:…` 搜索。支持基础设施、运营线路和专题图层，右侧可读原始对象、查看精确几何和编辑工作区属性。
- **运行**按端点、线路和方向编排共享通道。底部提供时刻表、运行图与仿真控制。CSV、JSON、GTFS 可从「车辆与交路」导入导出。地铁运行限定上海。
- **分析与诊断**可执行临时路径查询、检查场景资源风险和查看系统性能。
- **数据**可安装和切换快照、查看许可和来源、导入旧版计划及审校失败的引用。

## 校验

```powershell
python -m unittest discover -s railscope-next/tests -p test_pipeline.py -v
cargo test --locked --manifest-path railscope-next/Cargo.toml -p railscope-next-core
```

Windows 桌面回归通过临时工作区和 WebView2 调试端口运行。详见[0.4.0 验收](../docs/next-v4/acceptance.md)及[架构与迁移](../docs/next-v4/architecture.md)。OSM 数据来源和许可证在应用及清单中保留。[OpenStreetMap 署名与许可](https://www.openstreetmap.org/copyright)
