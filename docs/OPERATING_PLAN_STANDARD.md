# RailScope 运行计划交换标准

版本：地铁 `railscope.operating-plan.v2`；国铁 `railscope.rail-plan.v2`（兼容 v1）。

## 共同约定

UTF-8 JSON，不接受重复键、NaN、Infinity。时间为从运营日 00:00 起的整数秒，范围 0–172799（允许跨日到 47:59:59）；界面使用 HH:MM:SS。距离为有限数值、单位米。编号为非空字符串、同类唯一，车站/线路/区间编号来自软件导出的本地参考目录，不能猜测。到达不晚于发车，下一节点到达严格晚于上一节点发车。通过节点可以到达=发车。

严格校验所有标准对象的必填字段、类型、引用、站序和径路。未知字段拒绝；新增信息只能进入 `extensions`，键必须是命名空间，例如 `example.org/overtaking`。扩展 JSON 原样往返保存，不代表软件执行它。`required_capabilities` 声明执行必须具备的能力；当前只接受空数组，越行、真实联锁进路、复杂贯通运行等要求会明确拒绝，不能悄悄忽略后运行。不得用 AI 推测的计划冒充官方运行图。

## 地铁文件

顶层必填：`schema`（固定版本）、`official`（必须 false）、`system`（metro）、`required_capabilities`（数组）、`extensions`（对象）、`trains`（数组）、`cycles`（数组）、`vehicles`（数组）。旧 v1 可导入并迁移为 v2。

`trains` 中每个车次必填：

| 字段 | 规则 |
|---|---|
| id | 单程车次编号，唯一 |
| line_id | 本地参考目录的线路几何编号，不是展示的线路名 |
| direction | forward / reverse，相对线路参考里程 |
| enabled | true / false |
| source | 数据来源与计划性质说明 |
| stops | 两站以上、按方向排列的连续站序，可以是全线或短交路 |

每个 `stops` 必填 `station_id`、`arrival_s`、`departure_s`、`distance_m`；里程须与参考目录一致，不能自行把短交路起点改成 0。可选 `extensions`。

车次可选 `vehicle_id`（实际车辆身份）、`cycle_id`（生成来源循环）、`turnback_s`（该车次结束后最低折返等待）、`extensions`。同一车辆的车次不得重叠；前次终点站必须等于后次起点站，且折返等待足够。车辆身份与车次编号不是一回事。动画折返等待在端站保持，不模拟未知的实际折返轨迹。

每个 `cycles` 必填：`id`、`line_id`、`outbound`、`inbound`、`turnback_s`。往返站序互为反向；两端首站相对到达均为 0 秒，其他时刻均为相对秒数。两个停站数组格式同车次 stops。可选 `extensions`。

每个 `vehicles` 必填：`id`、`cycle_id`、`start_s`、`end_s`；可选 `extensions`。开始严格早于结束。引用循环必须存在。生成策略为往返循环反复铺排，仅生成时间窗内的完整循环；均匀投放初始偏移=循环周期×车辆序号/车辆数，可在车辆表逐车修改。再次生成同一循环替换该循环车次，保留其他大小交路与其他线路。手动编辑时刻不会反向修改循环模板。

## 国铁文件

v2 顶层必填且只允许：`schema`（railscope.rail-plan.v2）、`service_date`（YYYY-MM-DD）、`timezone`（Asia/Shanghai）、`source`、`required_capabilities`、`extensions`、`routes`、`trains`。

`routes` 是**单向运行通道**数组。每项必填 `id`（非空唯一字符串）、`path`（下述有序区间数组）、`extensions`（命名空间对象），可选 `name`（非空名称）、`track_changes`（下述共享变道数组）。同一方向的通道可以组合不同铁路线、供多个车次引用；基础设施和几何不随新增车次复制为 GIS 文件。反向运行另建一个通道，将 path 顺序反转、每一段方向取反；当前拒绝首尾同一节点的循环通道。

这是“物理铁路线 / 轨道 → 单向运行通道 → 车次”三层结构。运行通道不是八纵八横规划分类；前者供车次引用，后者仅整理物理图层。选择车次时，地图展示它引用的共享通道与其他共用车次，不生成专属 OSM 图层。

v2 每个车次必填且只允许 `id`、`route_id`、`stops`、`extensions`。`route_id` 必须引用 routes 中已注册的径路。车次编号唯一，一个编号固定对应一列车、一份时刻表和一幅运行图；国铁不接受额外 vehicles 数组。v1 仍可导入，顶层无 routes，每个车次含 path 而非 route_id。软件保存为 v2。

保留自由度的无损交换使用 JSON；任意未来数据须放在命名空间 extensions，不得增加未知标准字段。

`path` 是按行驶顺序排列的区间数组；每项必填 `edge_id` 与 `direction`（forward/reverse）。edge_id 从导出的物理区间目录获取；方向相对区间 from_node→to_node。相邻区间必须共享同一个真实 OSM 节点，禁止用距离接近代替连接；在建区间禁止排运营车次。区间可以属于不同铁路，不要求整趟列车只属于一条线路。

`stops` 是经停和通过的时刻控制点，必填整数 `node_id`、`arrival_s`、`departure_s`；可选整数 `platform_id`（真实平台 OSM way ID）和 `extensions`。控制点必须按顺序位于声明径路中。需要线路所处分段展示时，将它也加入 stops；通过时 arrival_s=departure_s。站名相同不足以建立拓扑连接。站台 ID 的存在校验不等于已验证站台与进路相接，须人工核对。

stops 另可选 `track_change`，提供时必须且只允许四个字符串字段：`from_track`、`to_track`、`via_node`、`time`。未知均用 `""`；via_node 非空必须为数字 OSM 节点 ID，time 非空必须为 HH:MM[:SS]（00–47 时）。前三项兼容旧版车次备注，新计划应将静态位置 / 股道信息放在通道中；time 是本车次执行时刻。时刻表优先显示共享通道股道与道岔信息，这三列只读；在“通道”面板统一编辑。本车次变道时刻可直接编辑，尚不验证真实进路或执行变道。

### 独立国铁运行通道 JSON

导入 / 导出入口：**文件 → 导入国铁运行通道 / 导出国铁运行通道**。左侧“通道”查看引用关系、编辑名称和共享变道信息；“保存通道与国铁车次”保存完整组合计划，Ctrl+S 在通道面板也保存国铁。导出的独立通道文件不含车次和绝对时刻。

顶层必填且只允许 `schema`（固定 `railscope.rail-corridors.v1`）、`source`（来源字符串）、`required_capabilities`（当前必须 []）、`extensions`（命名空间对象）、`corridors`（数组）。corridors 每项与 rail-plan.v2 的 routes 完全相同，编号就是车次引用的 route_id。允许无引用车次的通道，预览不会自动生成车辆。需先有其引用的真实基础设施，内置 G1 范围内无需全国下载。

通道可选 `track_changes`，每项必填且只允许：

| 字段 | 规则 |
| --- | --- |
| node_id | 整数，变道控制点，必须在通道路径上 |
| from_track / to_track | 字符串股道标识，未知为空字符串 |
| via_node | 整数道岔节点且在通道上，未知为 null |
| extensions | 命名空间扩展对象 |

通道是共享静态路径，**不含车次绝对变道时刻**。股道 / 道岔位置供所有引用车次共享，具体时间来自该车次时刻表；尚不驱动真实联锁、越行或道岔动作。node_id 在几何上存在不等于实际股道 / 进路已验证。尚未提供时可以省略 track_changes 或写 []，不得猜测填写 G1 的实际进路。

导入是按通道 ID 合并，保留所有已有车次；允许同一 ID 修改名称、变道信息和扩展，不允许覆盖其物理 path。改变物理径路须使用新 ID，避免悄悄改变已有车次。任一通道不连续、在建、节点无效或编号重复，整批拒绝，原计划保留。JSON 扩展原样保存。

### 国铁批量车次 CSV

菜单“运行 → 国铁 → 导出国铁车次表 / CSV 模板”可得到本地真实 route_id 与 node_id。编码 UTF-8（允许 BOM），逗号分隔，表头顺序必须完全一致：

```text
train_id,route_id,sequence,node_id,arrival,departure,platform_id,from_track,to_track,via_node,change_time
```

每行一个经停 / 通过控制点。同一 train_id 必须使用同一个 route_id；sequence 从 1 连续递增，至少两点，节点必须在通道上依次出现。arrival / departure 使用 HH:MM[:SS]，允许跨日 24–47 时。platform_id 留空或整数；from_track / to_track / via_node 可空，非空时必须与已注册通道该节点的共享信息完全一致，不能靠导入车次覆盖通道；change_time 属于本车次，可空。不得重复导入已有车次编号。引用不存在、时刻错误、站序错误或任意一行错误，整批拒绝，不覆盖原计划。导入是追加，不是覆盖更新。

CSV 是有限列的表格接口，不携带任意 extensions、服务日或来源；使用当前 JSON 计划的服务日、来源和注册径路，不能用于无损迁移完整计划。先用 JSON 注册新径路；AI 只能引用导出的本地编号，不得伪造。需要完整扩展、越行和贯通约定时使用 JSON。

软件按一趟车次的跨线径路累计里程绘制时间—距离图；线路所/区间是时刻控制节点，不是动画跳跃点。官方区间运行图需要所有通过时刻，只有旅客经停表时，中间时刻不得标注为实测。地图动画目前在已知时刻控制点间按距离线性推进，不代表实际加减速曲线。

## 未来接口

建议命名空间：`railscope.org/overtaking`（越行车次、节点、股道及进路）、`railscope.org/through-running`（有序交路腿与物理接续）、`railscope.org/turnback-path`（真实折返区间与方向）、`railscope.org/resources`（站台、股道、道岔占用与安全约束）。这些仅是保留扩展约定，当前不是可执行能力；需要它们执行时，必须在 required_capabilities 声明对应版本，软件拒绝不支持的计划。

## 给 AI 的提示词

请根据本标准和软件导出的参考目录编写 RailScope 运行计划 JSON。禁止猜测线路、车站、区间、站台 ID 或里程；缺少数据先报告。地铁按指定大小交路、固定往返模板、车辆数与逐车投放时间生成车次；区分实际车辆身份与单程车次。国铁必须给出有序、带方向、连续的物理区间径路及经停/通过时刻。所有生成或推定时刻明确标为用户计划，不能冒充官方数据。严格使用本标准字段，扩展放入命名空间 extensions；依赖未实现能力则声明 required_capabilities，不能假装已受支持。只输出 UTF-8 JSON，不附 Markdown 代码围栏。

国铁已有通道时，请优先读取我从软件导出的 rail-corridors.v1 和车次 CSV 模板，保持 route_id 不变，只编写新车次的逐点到发时刻。同一车次唯一、一车次一列车，不创建车辆循环或新的 OSM 数据。新通道另交 rail-corridors.v1，复用本地真实物理区间、精确连接节点与方向；静态变道位置 / 股道放在通道 track_changes，绝对执行时刻放在车次。缺少真实股道 / 道岔信息时留空或 null，不推测。若我要求批量表格，严格输出本文 CSV 表头，避免重复已有车次编号；扩展无损交换使用 JSON。

## 数据真实性与限制

这套格式不是 GTFS，也不是中国铁路调度系统专用格式。OSM 不保证站台、股道、线路所或道岔完整，更不包含可靠联锁进路。公开首末班车表、列车旅客经停表不能还原完整车辆周转和区间通过运行图。

铁路通道默认参考国家发改委《中长期铁路网规划》的“八纵八横”；用户目录分类可以修改，但不能把自行修改的通道名宣称为官方规划。来源：https://www.ndrc.gov.cn/fggz/zcssfz/zcgh/201607/t20160728_1145737.html
