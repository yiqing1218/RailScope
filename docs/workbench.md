# 地图与运行工作台

## 地图、目录和详情

| 入口 | 用途 |
| --- | --- |
| 地图 → 目录 | 按线路与车站、物理基础设施、道路与其他设施浏览。线路和车站分类可再筛铁路或城市轨道。 |
| 地图 → 图层 | 独立打开线路名、站名、轨道和设施符号。通用的「线路」「轨道」占位名不再作为线名显示。 |
| 右侧 → 概览 | 查看编号、运营管理、分类及速度、轨距或车站业务属性。空值显示「待补充」。 |
| 右侧 → 关联 | 查看来源关系、物理接轨和线路成员。选车站搜索线路，选择后保存「工作区关联」，也可从对应线路查看已关联车站。 |
| 右侧 → 来源 | 查看数据快照、核验状态以及可展开的原始 OSM 标签与成员。 |
| 右侧 → 编辑 | 保存工作区名称、编号、运营管理、分类、分组和备注。给多条线路填写同一个分组名后，目录的「工作区分组」可一起浏览它们。 |

合并和拆分入口位于线路详情下方。工作区分组用于整理对象，拆分和合并记录源线路成员。关联或编辑写入工作区，地图仍可显示原始 OSM 来源。

## 国铁与城市轨道

左侧切换「国铁」或「城市轨道」，按日期、线路和车次号查找计划，也可以浏览通道。右侧显示当日计划摘要、当前车次或通道的来源，以及播放和编辑操作。

1. **通道编排弹窗**沿用 V1 的「端点—线路—端点」。先选起点的物理锚点，再查询沿行车方向接轨的业务线路和可达终点。仅在换线时插入中间控制点。自动参考模式保留实际选中的连续有向轨道。严格唯一模式遇到多条候选会要求进一步指定。若车站没有可用的显式锚点，界面会列出与站点的距离，供用户确认邻近轨道候选。
2. **车次和计划弹窗**用于新建车次、编辑到发时刻、导入计划和管理车组。可按名称搜站，然后用「按所选通道定位停站」补齐几何参考锚点。通道和时刻编辑弹窗打开时可以继续在地图选取站点或轨道。
3. **底部工作区**显示播放时钟、时刻表和运行图。右侧可播放当日计划或从选中车次的发车时刻开始。播放或定位列车时会开启地图的动态列车图层。仿真场景参数在弹窗设置，计算出的占用与冲突在底部查看。来源和几何匹配标为自动参考。

## 本地参考计划

本机工作区的全国和上海快照均已导入上海 7 号线参考计划，可在对应快照的「运行 → 城市轨道」选择车次并播放。参考周为 2026-11-23 至 2026-11-29，7 号线计划混合了公示、观测和推演。全国快照另外有 2026-09-17 的 G1 参考车次，可从「运行 → 国铁」打开。

### 重建参考计划

从仓库根目录运行，Python 环境需要安装 `railscope-next/pipelines/requirements.txt`。输出保存在 Git 忽略的 `data/processed/`。

```powershell
python railscope-next/pipelines/prepare_reference_plan.py `
  data/processed/railscope-next/packs/shanghai-osm-202609-v4 `
  data/processed/railscope-next/operations/shanghai-line7-reference.json `
  --metro-line7 'D:/DeskTop/上海地铁运行图/上海地铁7号线' --week-of 2026-11-23

python railscope-next/pipelines/prepare_reference_plan.py `
  data/processed/railscope-next/packs/china-osm-202609-v3 `
  data/processed/railscope-next/operations/shanghai-line7-national-reference.json `
  --metro-line7 'D:/DeskTop/上海地铁运行图/上海地铁7号线' --week-of 2026-11-23

python railscope-next/pipelines/prepare_reference_plan.py `
  data/processed/railscope-next/packs/china-osm-202609-v3 `
  data/processed/railscope-next/operations/g1-reference.json --bundle
```

换用其他工作区时，从右侧「计划与车辆」导入与所选快照匹配的 RailScope JSON。可替换示例周一生成新的参考周。计划保留逐站证据和跨零点时刻。

导入会一起校验并保存通道和车次，原文件重复导入会复用结果。每种交路的多趟车次共享通道，折返站可位于首末轨道段内部。7 号线双向使用同一套车站编号，选短交路也能在全线站序上查看。
