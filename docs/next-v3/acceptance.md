# RailScope Next 0.3.0 交付与验收

本次交付位于 `codex/railscope-next`。Windows 应用与 NSIS 安装器由 `railscope-next/apps/desktop` 构建。各数据包及测试生成物放在本机独立目录，源码提交只含代码、文档和小型样本 ID。基线、架构及数据契约见[仓库审计](audit.md)、[技术决策](architecture-decision.md)和[数据契约](data-contracts.md)。

| 规格 | 已实现及证据 | 验收边界 |
| --- | --- | --- |
| A 全国源精度 | `core/src/precision.rs`、`pack.rs` 和 RTree 支持自动层级。新全国包 `china-osm-202609-v3` 在原生程序中完成 Z12.9、Z21 和 Z22 抽查，揭示历史 Z8 放大折角，并核对相邻瓦片同一稳定实体。 | 区域快照手动选择。自动跨包拼接和按需联网未接通。全国源覆盖受已安装 OSM 提取和分类范围限制。 |
| B 底图 | `desktop/src/map.ts` 分配地理底图、交通专题、动态列车。四套主题可切换。抽查瓦片的底图道路排除高速和匝道。 | 卫星影像缺少可用授权来源。试验记录属于抽样，未对全国地名做语义去重审计。 |
| C OSM 与设施 | `pipelines/enrich_pack.py` 补全有序原始对象。上海 3、4 号线共线瓦片在 Z21 带两种有源配色。虹桥抽查发现 24 个源站台面、2 个站房面、2 个站区面。工作区可调整名称、颜色和业务线路。 | 来源提取覆盖所纳入的交通/地理对象。OSM 中漏标、未收入或缺成员的对象可见覆盖提示。当前共线样式显示前两列颜色，目录保留成员证据。 |
| D 物理图与通道 | `core/src/graph.rs` 和 `operations.rs` 检查真实连接、方向、状态和边序列。北京南附近抽到两条几何相交却无共享拓扑节点的边。原生测试检查固定 Corridor 版本。 | 没有可独立核验的站内进路数据。跨快照保留旧版本并提示冲突。自动参考默认用可运行候选。 |
| E 服务与仿真 | `core/src/operations/{schedule,sim}.rs` 接通 CSV、JSON、GTFS 和 L1–L3，测试包含两列同时运行、资源冲突、G1 历史参考和上海地铁 1 号线 28 站重建案例的原生地图与运行图联动。 | 地铁模拟限定上海。两项实数路径参考都保留推断锚点。无实时或运营方核验数据，L3 为假设资源分析。 |
| F 界面与安装 | `desktop/src/workbench.tsx` 和 `operations-workbench.tsx` 含目录、属性、视图、运行和诊断。可调面板、工作区、本地数据包安装和版本化回退。错误 SHA 包被拒绝且当前快照保持有效，强制结束测试应用后确认保存数据可恢复。 | 干净虚机的断网安装、系统断电恢复、专用快捷键和独立跨 DPI 交互验收未执行。 |

## 启动和操作

双击仓库根目录 `Start-RailScope.cmd`，或者通过 NSIS 安装后在数据包菜单导入本地快照。选全国包，放大自动获得源几何。「视图」可切主题和定位，「图层」可查看站台、道岔和站场。「目录」搜索对象，右侧查原始 OSM 和编辑。在「运行」中从源物理边建通道、录入日期车次、启动仿真并联动运行图。详见[工作台使用说明](gui-spec.md)和[迁移与回退](migration.md)。

## 验证命令

```powershell
python -m unittest discover -s railscope-next/tests -p test_pipeline.py -v
cargo test --locked --manifest-path railscope-next/Cargo.toml -p railscope-next-core
$env:PYTHONPATH='backend'
python -m pytest -q backend/tests
Set-Location railscope-next/apps/desktop
npm.cmd run tauri -- build
```

原生桌面测试须从临时 `RAILSCOPE_NEXT_PROFILE` 启动，并通过 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9229` 开启测试进程端口。数据包根目录由 `RAILSCOPE_NEXT_PACKS` 指定。用临时工作区启用编辑、实数几何的示例仿真及压力采样：

```powershell
$env:RAILSCOPE_SMOKE_WRITABLE='1'
$env:RAILSCOPE_V3_FIXTURE='railscope-next/tests/fixtures/shanghai-next-v3.json'
$env:RAILSCOPE_STRESS='1'
$env:RAILSCOPE_SMOKE_SWITCH='china-osm-202609-overview'
node railscope-next/tests/desktop-smoke.mjs data/logs/next-validation/next-v3/native
```

Rust 8 项、Python 管道 3 项、旧后端 72 项、Vite 生产构建及最终 Windows 原生端到端验证通过。旧全国包通过 30 分钟地图漫游，新全国源包通过 5 分钟抽查。安装器包含 WebView2 离线安装载荷，大小 288.21 MiB，SHA-256 为 `166c63021b62b49008988e109e633b66ae2ab3d65c6e40a875f4180f34b1ab6b`。独立安装环境未验证。原生测试使用 WebView2 拦截外部 HTTP(S) 请求并关闭浏览器缓存。若要复测漫游，在独立测试进程设 `RAILSCOPE_ROAM_MINUTES=30`、`RAILSCOPE_TEST_PID`，并将 `RAILSCOPE_SMOKE_SWITCH` 指向待测全国快照。用例与性能边界见[基准](benchmark.md)。

## 证据文件

`data/logs/next-validation/next-v3/` 保存精度原始/渲染 GeoJSON、解码瓦片、源设施与共线配色核查、桌面截图及测试 JSON。`g1-reference/` 和 `shanghai-line1-reference/` 保存两个历史或重建参考案例。运行 `railscope-next/pipelines/prepare_reference_plan.py <本地包> <输出 JSON>` 可重建 G1。上海重建需加 `--metro-line1 examples/shanghai-metro/line1_public_reconstruction_spec.json`。令 `RAILSCOPE_REFERENCE_FIXTURE` 指向生成文件，再运行同一个原生桌面脚本可复核径路、时刻和 L2 位置，可选设置 `RAILSCOPE_REFERENCE_UI=1` 保存界面。配置来源、出图责任以及仿真层级详见[底图](basemap-style.md)、[精度](precision-report.md)及[运行模型](operations-model.md)。
