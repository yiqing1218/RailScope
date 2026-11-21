# 数据契约

| 对象 | 主键和契约 |
| --- | --- |
| 原始对象 | `osm:node:*`、`osm:way:*`、`osm:relation:*` 用于追踪来源，保留标签、版本、经纬度、成员顺序和原始角色 |
| 地图实体 | RailScope UUID，快照内 `render_id` 负责瓦片反查。精确几何为 WGS84 WKB |
| 稳定物理边 | `edge.id` 引用 RailScope UUID，带有向端点、所属线、等级和状态。自然折线拐点保留在几何中 |
| 业务线路 | `line_member` 记录有序成员。用户拆分与归并的结果存工作区 `line_edit_batch` |
| Corridor | `id, version` 指向完整连续的 `edge_refs`，保存来源、图快照和核验状态 |
| TrainRun | 日期化服务实例，引用固定的 Corridor 版本。时刻以 Asia/Shanghai 运营日起算秒数表达，可晚于 24:00 |
| TrainState | 采样时间、状态、单位明确的位置或速度、参考/仿真来源。条件不足时返回诊断 |

桌面运行 DTO 沿用 `backend/railscope/domain.py` 的稳定领域 ID、方向、状态和路径成员思想，新增的 SQLite 时刻、车辆和仿真数据由桌面适配器持有。完整的后台与桌面双向同步未接通。源 schema 3 是筛选后的交通及地理对象集。关联成员未收入时保留原始引用，并随详情显示其覆盖状态。
