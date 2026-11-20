# RailScope Next

RailScope Next 是独立的 Windows 交通 GIS 工作台，使用 Tauri 2、Rust、React 和 MapLibre。地图读取本机 PMTiles，属性和目录读取只读 SQLite，人工改动保存到单独的工作区。

## 启动

仓库中可双击 `Start-RailScope-Next.cmd`。启动脚本使用 `data/processed/railscope-next/packs` 中的本机数据包。

从源码构建需要 Node.js 22.12+、Rust stable MSVC、Microsoft C++ Build Tools 和 WebView2。在仓库根目录运行以下命令。[Tauri Windows 构建环境](https://v2.tauri.app/start/prerequisites/)

```powershell
Set-Location railscope-next/apps/desktop
npm.cmd ci
npm.cmd run tauri -- build --no-bundle
```

要构建 NSIS 安装包，将最后一条命令改为 `npm.cmd run tauri -- build`。独立安装时，程序从 `%LOCALAPPDATA%\app.railscope.next\packs` 读取数据包。将每个快照文件夹完整复制到该目录后重启。也可使用 `RAILSCOPE_NEXT_PACKS` 指定自定义数据包根目录。

## 生成离线数据包

在仓库根目录运行。下面的命令使用现有上海 OSM PBF。Natural Earth 陆地多边形参数可省略。

```powershell
python -m pip install -r railscope-next/pipelines/requirements.txt
python railscope-next/pipelines/build_pack.py `
  data/raw/osm/shanghai-latest.osm.pbf data/processed/railscope-next/packs `
  --region 上海 --snapshot shanghai-osm-202609 --maxzoom 14 `
  --land data/processed/admin/natural_earth_10m_land.geojson
```

全国概览可用 `china-latest.osm.pbf`，以及 `--region 全国 --snapshot china-osm-202609 --maxzoom 11 --location-index disk`。全量解析和多级切片耗时较长，磁盘索引会占用额外空间。包生成期间保留暂存目录，通过外键和 SQLite 检查后才发布快照目录。快照标识必须唯一，采用小写英文字母、数字、点、短横线或下划线。

本机已生成上海精细包 `shanghai-osm-202609-r3` 和全国概览包 `china-osm-202609-overview`。全国快照有 1,869,372 个实体。国铁和高速在全国概览比例尺下逐层显示，放大到 Z8 可查看城市轨道和车站。上海包提供至 Z14 的交通和站场瓦片。

每个数据包包括：

- `manifest.json` 记录源文件、坐标系、范围、分类计数和 SHA-256。
- `canonical.sqlite` 保留业务线路与设施的关系、具有方向的轨道与道路边、WGS84 WKB、OSM 来源、RTree 与 FTS5。
- `transport.pmtiles` 包含简化、裁切后的矢量瓦片。可视渲染 ID 通过只读快照解析为 RailScope UUID。

## 使用

- **地图和目录**支持国铁、地铁、高速、车站和设施图层；用中文、旧名、别名、线路编号、全拼或简拼检索，支持当前视野筛选。
- **对象属性**显示来源、自动参考状态、完整几何与所属线路。属性查询、地图瓦片和原始几何各走独立接口。
- **工作区**保存改名、备注、分组、颜色与点位置，支持撤销和重做。源快照仍可浏览。切换同地区版本时，仅将来源、原始几何和稳定 ID 均一致的覆盖重放；在数据包菜单查看冲突。选择保留的旧快照即可回退。
- **路径分析**根据显式连接、方向和运营状态计算自动参考路径。真实跨线桥几何相交不产生连接。已知铁路菱形交叉不会推断成可转线道岔。
- **诊断**显示 WebView 帧采样、属性读取和 Rust 瓦片缓存。日志在应用本地数据目录下的 `railscope.log`。

## 校验

```powershell
python -m unittest discover -s railscope-next/tests -p test_pipeline.py -v
cargo test --locked --manifest-path railscope-next/Cargo.toml -p railscope-next-core
```

Windows 桌面冒烟脚本位于 `tests/desktop-smoke.mjs`。用一个临时 `RAILSCOPE_NEXT_PROFILE` 启动原生程序，设置 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9229`。若需要验证保存、撤销和重做，可对该临时工作区设置 `RAILSCOPE_SMOKE_WRITABLE=1`，然后运行 `node railscope-next/tests/desktop-smoke.mjs`。调试端口仅用于执行测试的程序进程。

本机发布版使用上海快照 `shanghai-osm-202609-r3`（29,178 个实体）通过冒烟验证。测试禁用了 WebView 浏览器缓存并阻断外部请求，验证了瓦片读取、中文与拼音检索、来源与精确几何、图层切换、工作区备注和归组、撤销与重做。另验证了上海与全国快照之间的切换，以及放大后全国线网的渲染。上海单次搜索往返耗时 27.5 ms，记录了 62 次 Rust 瓦片缓存未命中、85 次命中。完整的全国与站场长时性能基准仍待测量。

## 当前覆盖

| 方案阶段 | 本分支覆盖 |
| --- | --- |
| Gate 0 | 主线采用 Tauri 与 MapLibre GL JS。Qt Native 同机对照待实测。 |
| M1—M3 | 独立桌面、离线瓦片、区域包、目录与检索、来源与几何、线路关系。 |
| M4 | 事务工作区、备注、改名、分组、颜色、点几何、撤销重做与迁移冲突。线面改绘和增量重切瓦片待接入。 |
| M5 | 基于显式连通和方向的铁路与高速参考路径；支持后台计算和取消。分区图索引和正式运行 Corridor 待接入。 |
| M6—M7 | 本地包校验与切换。全国定期发布、签名差分、公开地图发布、列车运行和云同步待接入。 |

路径输出只标为自动参考。实体的来源标签、置信度和核验状态随快照保存。OSM 数据来源和许可证在应用及清单中保留。[OpenStreetMap 署名与许可](https://www.openstreetmap.org/copyright)
