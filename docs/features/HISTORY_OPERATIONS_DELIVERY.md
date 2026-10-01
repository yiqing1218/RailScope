# 历史、站内运行、车辆与分析交付记录

分支：`codex/rail-history-operations-workbench`。范围依照 [实施契约](HISTORY_OPERATIONS_WORKBENCH.md) 和用户后文的取舍，不是全部 34 项功能的交付。

## 使用入口与模型对应

| 项目 | 入口与行为 | 领域契约 / 代码 |
|---|---|---|
| 1 历史 | 搜索框左侧“时间回溯”；年份滑块、精确日期、返回实际日期；建设 / 开通 / 停运时间，整线默认或单对象覆盖，设施可继承线路时间 | InfrastructureLifecycle；history.py、history_ui.py、temporal_adapter.py、infrastructure-history.js |
| 4 通道运行图 | 分析 → 通道列车运行图；共用完整物理路径的正反向车次按真实车站里程对齐，停车水平线；SVG / PNG / PDF，线宽与全部字号可调 | Corridor / TrainRun / StopTime；services/analysis.py |
| 8 站内进路 | 选车次和停靠站 → 编辑 → 选择站台与站内进路；真实站台、股道、通道进出边界及实际连续有向路径 | Platform → StationTrack → NetworkEdge；StopTime → StationRoute；station_routing.py |
| 10、11 站表和占用 | 分析或详情“运行”页 → 车站时刻表与站场状态；到发 / 通过、站台 / 股道、股道时间图和实时站场共用全局时间 | 同一 Station / TrainRun；analysis.py，复用真实站场 SVG 布局 |
| 13、14 专业详情 | 统一概览—地图—基础设施—运行—统计—历史，加全部属性；线路档案和已载入真实区间技术属性表 | 原统一元数据模板、line_metadata、领域设施关系 |
| 16 后文替代要求 | 分析 → 导出通道档案；PDF / HTML 的地图页、端点—线路序列、途经车站 / 线路所及完整物理边序列 | RouteIntent / Corridor；真实地图截图，失败反馈 |
| 19 车辆 | 车辆 → 地铁（上海） / 铁路车辆，按线路分组；名称、车型、代码、参数、图片、车次分配和担当表 | 一个 Vehicle 与 TrainRun.vehicle_id；vehicles.py |
| 23 区间统计 | 分析 → 跨通道物理区间统计；全部已加载通道的正反向使用、车种、客货、速度、运行时间、每小时密度及 JSON 输出 | 同一 Repository 的有向 passage；按进入时刻采用半开窗口 |

左侧六模块：图层、运行、车辆、分析、编辑、导入导出。原菜单 / QAction 保留并复用。详情属性名称在上、内容在下，名称灰色、内容深色，长编号完整换行。

## 一套业务契约，按职责存储

业务定义统一在 backend/railscope/domain.py。桌面字典及原运行计划 JSON 是兼容 / 交换 DTO，经 domain_adapter 进入同一 Repository。没有另建历史车站、统计车站或运行车站；名称不是连接键。站表、担当、占用、统计和图表是派生读视图，不另存互相覆盖的权威时刻表。

| 存储 | 权威职责 |
|---|---|
| 原有内部 GIS SQLite / RTree、线路和拓扑库 | 导入后的来源几何与基础设施，日常功能不解析原始 OSM PBF |
| 原有 rail_catalog.workspace.sqlite | 名称、人工分类、目录归属、站场股道等覆盖层 |
| data/user_settings/workspace.sqlite | 本次历史、车辆和人工站台关联的 typed override，每次只写当前对象 |
| 原运行计划 DTO / 身份库 | 完整 Corridor、TrainRun、StopTime、StationRoute 的原编辑 / 交换入口和稳定身份映射，不是第二套业务定义 |
| data/user_settings/assets/vehicles/ | 图片资产，数据库保存相对引用 |

备份 / 搬迁须包含相关 SQLite、运行计划、身份库及图片目录，不能仅复制界面工作区 JSON。原始 OSM 不改写。已有目录和计划 Undo/Redo 保留，车辆分配复用原验证 / 撤销路径；新历史和车辆档案事务保存。

## 精确边界

- 建设前隐藏，建设期虚线，开通后运营，停运后灰色，无停运日期延伸至未来。实际日期模式每分钟检查本机日期，预设开通日到达后计算有效状态，不改原 construction tag；手动回溯维持所选日期。
- 未知日期保持未知，多线共有设施不随意选父线路。当前使用已知几何呈现历史状态，尚无迁建前几何版本和历史时刻表数据集。
- StationRoute 仅替换当前车次本站的有界路径，不改完整 Corridor；直通车沿通道正线。缺失真实连接时 unresolved，不合成渡线；automatic_reference 不代表经核验的联锁进路。
- 站台与股道由用户明确关联。默认停车点是选定真实轨道中点，已有明确米标仍可保留；是否正线邻接站台由实际数据决定。
- 股道时间图包含进站、停车、出站的车头占用参考，未计列车尾部、联锁释放和安全间隔；不能用作调度许可。
- 站场只能显示内部库已有的真实轨道 / 站台。目录中未进入计划的车站可经同一 adapter 载入空站表，不按同名站替换。公共时刻表复用已有 supplied-12306 / GTFS / CSV 导入，没有宣称取得官方批量 API。
- 统计仅覆盖导入 / 自建车次；未知客货保留 unknown，空样本平均值为未知。匹配完整实际 edge 序列，绕行另一股道不会算作使用未经过的物理股道。
- 车辆担当从车次反查，以绝对日期校验重叠；未定义的空驶、回库和检修不补造。线路技术视图目前为专业属性及区间表，不是具有完整实测坡度 / 限速的纵断面图。

## 预留范围

services/extension_contracts.py 预留历史时刻表、两期网络比较、运行关系、高级查询、能力分析、晚点传播、调度沙盘及统一成果导出 provider；history.py 预留历史几何快照 provider。接口使用同一 Repository 与稳定 ID，不显示假完成入口。

2、3、5—7、9 的独立全功能模块、12、15、17、18、20—22、24—30、33、34 未完整交付。31 的统一时间底座和 32 的新增引用已接入，不能据此声称全部未来视图已实现。21、24 按用户后文暂不新增。

## 验证

Windows / Python 3.12 / PySide6 6.11。完整 backend/tests + desktop/tests **316 项通过，48.12 秒**；本次功能针对性测试 **19 项通过，2.30 秒**。JS **10 项通过**，包括生产历史图层的 MapLibre style-spec 校验、重复应用样式不增长及单个铁路历史修改不刷新地铁 / 公路 source。

覆盖时间 / 继承 / 自动开通、SQLite 回读、跨日车辆重叠、跨通道统计、站内绕行且 Corridor 不变、站表 / 动画 / 股道一致、六模块 Qt、单一播放推进者、车辆分配撤销重做、照片替换保留原资产、多股道站台引用的局部依赖加载、已打开站表跟随计划编辑、CSV 引用回读与真实 SVG / PNG / PDF 文件导出。

完整 Qt WebEngine offscreen 启动报 GLES context 创建失败，**真实 WebGL 地图和通道地图截图端到端验收尚未完成**。样式校验及 Qt 控件测试不替代显卡渲染，也不代表全国活动计划负载或调度安全通过。[控件截图](artifacts/workbench-shell.png) 明确标注地图渲染另测。

未删除既有功能、改写原始 OSM、降低几何精度或取消原联动 / Undo。当前不足为历史 / 站场数据完整性、预留功能及上述 WebGL 验收限制。
