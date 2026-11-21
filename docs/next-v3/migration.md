# 安装、快照迁移与回退

## 构建和启动

从 `railscope-next/apps/desktop` 运行 `npm.cmd ci`、`npm.cmd run tauri -- build`。生成 Windows 安装器 `railscope-next/target/release/bundle/nsis/RailScope Next_0.3.0_x64-setup.exe`。构建会获取 Microsoft WebView2 离线运行时安装器并嵌入 NSIS 包。地图数据独立分发。独立程序默认使用 `%LOCALAPPDATA%\app.railscope.next\packs`，可通过 `RAILSCOPE_NEXT_PACKS` 选择数据根目录。仓库启动脚本已指到构建所在的数据根目录。[Tauri Windows 安装器说明](https://v2.tauri.app/distribute/windows-installer/#webview2-installation-options)

## 升级数据包

给现有同源包增加源对象时，PBF SHA-256 必须与原包清单一致。以下以全国为例，创建新的不可变快照目录：

```powershell
python railscope-next/pipelines/enrich_pack.py `
  data/processed/railscope-next/packs/china-osm-202609-overview `
  data/raw/osm/china-latest.osm.pbf `
  data/processed/railscope-next/packs china-osm-202609-v3
```

安装时检查 manifest、SHA-256、SQLite 和 PMTiles，复制到暂存目录后原子发布，可取消进度。保留 `canonical.sqlite` 和 `transport.pmtiles` 一致。全国原始源补全需要额外临时磁盘和内存。

当前 `china-osm-202609-v3` 已在仓库默认数据根目录生成，约 9.84 GB。仓库启动脚本可直接使用，独立安装后的工作区可从数据包菜单导入该目录。

## 回退

升级前退出应用并备份工作区目录。通常位于 `%LOCALAPPDATA%\app.railscope.next`，也可能由 `RAILSCOPE_NEXT_PROFILE` 指定。切回留存的旧包可回退地图快照。实体覆盖要求稳定 ID、来源和几何复核一致。同源 PBF 且源线路的有序成员完全一致时会重放派生线路，否则记录冲突。Corridor 和车次保持原图谱引用，跨版本时可在冲突列表看到提示。旧二进制回退时恢复升级前的工作区备份。用户标注、时刻和车辆数据均由工作区 SQLite 保存，原数据包可独立替换或归档。
