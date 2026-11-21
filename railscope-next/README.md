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

本机已生成全国源包 `china-osm-202609-v3` 和上海源包 `shanghai-osm-202609-v3`。全国源包含 1,881,112 个交通实体，配套 SQLite 和 PMTiles 合计约 9.84 GB。新工作区默认加载发现的最新全国源包，已有工作区可在数据包菜单选取。0.3.0 也可从原 `china-osm-202609-overview` 的 canonical 库读取其高缩放原几何。下例用匹配旧包 SHA-256 的 PBF 生成上海独立源包。

```powershell
python railscope-next/pipelines/enrich_pack.py `
  data/processed/railscope-next/packs/shanghai-osm-202609-r3 `
  data/raw/osm/shanghai-latest.osm.pbf `
  data/processed/railscope-next/packs shanghai-osm-202609-v3
```

全国源补全的命令与回滚方法见[迁移说明](../docs/next-v3/migration.md)。

每个数据包包括：

- `manifest.json` 记录源文件、坐标系、范围、分类计数和 SHA-256。
- `canonical.sqlite` 保留业务线路与设施的关系、具有方向的轨道与道路边、WGS84 WKB、OSM 来源、RTree 与 FTS5。
- `transport.pmtiles` 包含简化、裁切后的矢量瓦片。可视渲染 ID 通过只读快照解析为 RailScope UUID。

## 使用

- **地图与目录**提供四套底图主题，可按名称、拼音或 `osm:way:…` 搜索，支持当前视野筛选和完整几何高亮。
- **对象与工作区**可查看来源和所属线路，原始对象有序浏览。保存颜色、线宽、透明度、名称、备注、分组和点位置，支持撤销、重做和安全跨版本重放。路径分析面板另提供工作区线路拆分与合并。
- **路径和运行**区分物理边、参考通道、日期车次和车辆。提供 CSV、JSON、GTFS，L1 计划、L2 物理路径、L3 假设资源分析和运行图。地铁运行限定上海。经证据保存的核验状态随通道版本记录。
- **诊断**显示 WebView 帧采样、属性读取和 Rust 瓦片缓存。日志位于应用本地数据目录。

## 校验

```powershell
python -m unittest discover -s railscope-next/tests -p test_pipeline.py -v
cargo test --locked --manifest-path railscope-next/Cargo.toml -p railscope-next-core
```

Windows 桌面回归通过临时工作区和 WebView2 调试端口运行。详见[验收报告](../docs/next-v3/acceptance.md)。OSM 数据来源和许可证在应用及清单中保留。[OpenStreetMap 署名与许可](https://www.openstreetmap.org/copyright)
