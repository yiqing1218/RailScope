# Rail Domain Model V2

RailScope 把来源、物理拓扑、铁路语义和运行计划分开保存。唯一领域契约是 `backend/railscope/domain.py`；Desktop SQLite、工作区及后端数据库只是适配器。OSM 连通性或自动参考路径不等于实际调度、信号或联锁进路。

| 层级 | 对象 | 含义与引用 |
| --- | --- | --- |
| 来源 | `DataSource`, `DatasetSnapshot` | 原始 OSM/PBF 或其他来源和快照；原标签不被人工修改 |
| 物理 | `NetworkNode`, `NetworkEdge` | 真实共享端点、原子轨道、方向、几何；稳定 `NN-/NE-` ID |
| 铁路语义 | `InfrastructureLine`, `LineMembership` | 一条业务铁路及其物理成员；线路身份不由颜色或目录决定 |
| 控制点 | `OperationalPoint` | 车站、线路所、闭塞所、信号所等业务节点；可关联若干物理节点 |
| 站场 | `Station`, `Yard`, `StationZone`, `StationTrack`, `StationTrackEdge` | 车站、车场、咽喉等空间区和轨道；一条股道可由多个 `NetworkEdge` 组成，编号须有证据 |
| 意图与径路 | `RouteIntent`, `Corridor` (`ResolvedCorridor` 兼容名) | 端点—线路业务意图和已选完整连续单向物理边序列；更新快照时显式重解析 |
| 具体车次 | `StationRoute`, `TrainRun`, `StopTime` | 站内进路与长途通道分离；车次另记到发时刻、股道、站台和经核验的站内进路 |
| 后续运行 | `BlockSection`, `TrackOccupancy`, `Conflict`, `DispatchScenario` | 共用物理基础设施上的闭塞、占用和冲突研究对象 |

每个 `NetworkEdge` 的事实拆成 `railway_class`（铁路技术类别）、`infrastructure_line_id`（业务铁路）、`line_role`（该铁路的网络角色）、`track_role`（该轨道的用途）、`facility_id`、`yard_id`、`zone_id`、`direction`、`construction_status` 及逐属性 `provenance`。`provenance` 保存值、source、snapshot、evidence、verification_status、confidence。`track_type` 仅供旧文件与旧样式兼容，不能决定新的业务身份或路径。

`main_track` 进入车站仍是正线；咽喉是 `StationZone`，不是轨道类型。OSM `service=crossover` 可产生 `track_role=crossover`；`service=spur` 只能产生 `spur_track`，不能推出 `line_role=branch_line`；`service=yard/siding` 不能推出到发线或调车线。缺少资料用 `unknown`。正式 `track_number` 只接受来源标注或人工核验；内部 `NE-/STTR-/RS-` 编号和旧自动编号只作内部 ID/显示别名。

新建/编辑通道默认用自动参考模式，在所选线路与方向上查找连续运营轨道；严格唯一模式遇分支保持 unresolved。结果保存明确的 `route_intent` 和 `resolved_corridor` 版本化 DTO，同时保留旧 `extensions` 兼容读写。普通读取不重算路径；用户在当前快照显式执行“按业务径路意图重新解析”时生成候选，并校验引用车次。没有足够信号和调度资料时，不能把候选称为真实联锁进路。

## 迁移与来源

| 旧数据 | V2 规则 |
| --- | --- |
| `track_type` 复合标签 | 保留原值；仅无原始标签时提供低置信类别提示。用途与线路角色无法证明时为 `unknown` |
| `service=spur/crossover/yard/siding` | 分别是 `spur_track` / `crossover` / `unknown` / `unknown`；站场上下文与用途分开 |
| `track_number` 来自内部 ID 或自动编号 | 正式编号设 `NULL`，旧值进 `legacy_metadata`/`display_alias`；人工与可信来源编号保留 |
| 旧 `railscope.rail-plan.v1/v2`、通道 CSV/JSON | 读取并保留物理路径/时刻；新保存带 `domain_schema_version=2`、`route_intent`、`resolved_corridor`；旧表头继续可读 |
| 工作区源与覆盖层 | 源数据只读，人工更改独立持久化；丢失成员或来源变化产生 conflict，不静默替换 |
| `rail_lines.sqlite` v13–v16 | 来源指纹一致时，就地重读来源标签并补建语义索引；事务失败后版本保持旧值，可重试。来源变化或索引结构异常时原子重建；`NE-/NN-/IL-` 身份沿用来源，不按语义重新编号 |
| PostGIS | Alembic `0005_rail_domain_v2` 增加字段和语义对象，旧运行数据不删除；降级若会丢失 V2 数据则拒绝 |

全国铁路仍使用 SQLite/RTree、视窗查询和有界缓存；国铁主目录和设施目录基于 `QTreeView`、`QAbstractItemModel`、SQLite 分页。地图样式由 `railway_class`、`line_role`、`track_role` 解析；旧颜色/线宽键迁移为新键并保留原值。目录移动和样式调整不修改物理拓扑、官方线路身份或已保存通道。

仍需人工核验：正式业务线路名称和归属、正线/站线的具体用途、车场及咽喉边界、正式股道号、控制点类型、实际接发车进路、信号/联锁/闭塞资料。OSM 的名称、相邻几何和标签不能替代这些资料。
