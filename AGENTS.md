# RailScope Git workflow

在 `D:\RailScope` 完成任何代码、桌面界面、测试或文档修改后，必须在同一任务内：

1. 运行与修改范围相符的验证；
2. 检查 `git diff`，确认不包含 `.env`、原始 OSM 数据或其他大型生成数据；
3. 将本次修改作为一个独立的本地 Git 提交。

提交信息使用简短中文说明。不要提交 `data/raw/` 的下载数据和 `data/processed/` 的可再生 GIS 输出；这些文件通过导入脚本生成。

# 铁路基础设施与运行数据约束

地图几何只是基础设施对象的表现形式，不是运行拓扑或业务对象。运行对象只能引用稳定的 RailScope ID；不得把 OSM Way/Node ID、Way 内下标或地图 LineString 当作长期业务 ID，也不得为每个车次复制基础设施 geometry。

采用三层结构：共享物理铁路线段 → 单向完整 Corridor → 具体 TrainRun。`NetworkEdge` 是原子物理轨道段；仅在道岔、线路所、共用节点、车站咽喉、线路归属变化点和其他真实拓扑决策点拆分，普通曲线几何折点不得无意义拆 edge。

`Corridor` 必须保存从起点边界到终点边界的完整、连续、有向 `NetworkEdge` 序列，中间车站和节点是隐含路径信息，不因经过车站拆成 `CorridorSegment`。`TrainRun.stops` 单独定义停车、通过、到发时刻、站台、到发线和经核验的 StationRoute。同一完整 Corridor 可由直达、大站停和站站停车次共同引用；跨线车次必须引用另一条完整 Corridor，不得在 TrainRun 临时拼接物理路径。

铁路线段必须可索引：稳定区间编号、线路编号、轨道类型、起终端点、方向及原始来源。导入时在已知道岔、线路所、共用节点等端点断开；后续补全任意用户端点的切分，必须保持旧引用可迁移。不能把整条线路当作不可分割的图片。

通道按“端点—线路—端点—线路—端点”定义，隐含未换线的中间端点。车站、站台与轨道共用基础设施编号；具体停靠站台和进出站股道属于车次，不属于通道。主线最短路径只用于几何仿真，不可声称是实际调度/联锁进路。

Desktop SQLite 与 Backend/PostGIS 只能是同一 `railscope.domain` 契约的 Repository/DTO adapter，不得继续新增第二套独立业务模型。地铁和国铁共享领域 ID、路径不变量与模拟插值。

自动推断必须保存 source、snapshot/version、verification_status/confidence。按用户要求，通道编辑默认采用自动参考模式：在所选车站、线路和运行方向内选取连续可运行路径，明确标记为自动参考、保存具体选择，并保留手工端点/区间调整；不得声称是已核验调度进路。严格唯一模式存在多条合法路径时保持 unresolved。正式 Corridor/TrainRun 默认禁止引用 construction、planned、disused 或 unknown edge。

人工改名、分类、线路归属、车站聚合和通道编辑必须写入独立 override/workspace 层，不得覆盖原始 OSM 派生文件。重新导入后重放 override；无法安全迁移时生成 conflict。删除、重建或拆分基础设施前必须检查 Corridor、StationRoute 和 TrainRun 引用，禁止留下悬空引用。

真实站台、站区、建筑轮廓必须区分来源和类型；不能用圆圈、缓冲区或推测多边形冒充真实站台边界。显示去重不能删除原始 OSM 节点及属性。地铁运行目前只开放上海。
