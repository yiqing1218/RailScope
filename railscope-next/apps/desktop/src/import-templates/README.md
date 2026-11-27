# RailScope 资料导入与导出手册

在桌面菜单选择 **数据 → 导入与导出**。选好当前快照，再按下面的流程操作。成功导入后，软件直接从工作区 SQLite 读取资料。

## 快速开始

| 想做的事 | 在哪里操作 |
| --- | --- |
| 只补一座车站或一条线 | 先在地图选对象，再点「导出所选对象」。编辑 JSON 后，在「导入」选择对应 JSON 类型。 |
| 一次导出整类对象或当前快照全部资料 | 选择资料类型或「全部资料」，再点「全量导出 ZIP」。可按名称、ID、线路或运营日缩小相应范围。 |
| 回填整个资料包 | 解压 ZIP，修改需要回填的文件。压缩时让清单和 data 文件夹处于 ZIP 顶层。先点「校验资料 ZIP」，再点「导入资料 ZIP」。 |
| 与其他软件交换时刻表 | 选择 CSV 或 GTFS，下载 ZIP 后取用内部文件。GTFS 导出时需要指定运营日。 |

全量导出会在一个 ZIP 中保存所有匹配结果。等待期间会显示已整理的记录数。回填 ZIP 时，进度按已处理的子文件显示。核验失败或取消导入会回滚整包。

## 导出现有资料

- **车站、线路及分期、国铁或城轨轨道**。可以按名称或 RailScope 实体 ID 搜索，也可以直接导出地图所选对象。导出文件的 `reference` 提供对象名、原始分类和状态。轨道还有端点和允许方向。选中线路后，可以只导出该线路的轨道，或另导出轨道索引用于编排通道。
- **修改对象资料**。在 `records` 中找到对应记录，只编辑 `changes`。已有值会填入，空字段为 `null`。保持原样的字段不会产生覆盖。已填字段改为 `null` 会清除对应的工作区覆盖。保留 `entity_id`、`kind`、`snapshot_id` 与 `original`。如果同一个待修改字段已在工作区更改，程序会报冲突。可以在文件的 `source` 补充资料来源。
- **通道与运行计划**。通道 JSON 可回填当前有效通道。运行计划可按运营日期筛选，JSON 可回填现有车次服务和运行实例。稳定 ID、快照和导出时的版本须保留。修改通道或运行实例会追加版本，已有车次继续引用原先固定的通道版本。要更新车次的通道引用，可在重新导出的运行计划中填写已保存的新版本。编辑 `services` 的 `train_code` 会更新该服务以及本批次对应运行实例的车次号。到发时间在 `runs` 的 `stops` 中编辑。保留 `original_services` 作为冲突校验基准。
- **CSV 和 GTFS**。用于与其他软件交换时刻。导入这些时刻数据会新增运行实例。回填现有版本使用 RailScope JSON。
- 通道和运行计划的 JSON 用于同快照、同工作区回填。ZIP 中保留根目录的 `railscope-exchange.json`、文件名和目录。编辑对象资料时，修改 `changes`，保留 `original`。首次建通道或运行计划可下载下面的模板。

在地图选中对象后，从右侧 **来源** 页也可复制实体 ID。下载模板时会带上当前快照，以及匹配的所选对象 ID。

## 模板

| 文件 | 用途 |
| --- | --- |
| `station.json` | 国铁或城轨车站资料、名称与别名 |
| `national-line.json` | 国铁线路名称、编号、类型、等级 |
| `urban-line.json` | 城轨城市、制式、分段开通。国铁也可使用同样的 `sections` |
| `track.json` | 物理轨道编号、用途、速度、轨距、供电 |
| `corridor.json` | 按端点和线路编排一条或多条连续单向通道 |
| `plan.json` | 车次、运行日、时区、到发时间、通过、通道版本 |
| `plan.csv` | 批量输入运行实例和到发时刻 |
| `gtfs-stop-map.json` | 导入 GTFS ZIP 时配套使用的站点 ID 对照表 |

示例中的名称、分类、日期、速度和置信度都需要按资料替换。ID 来自同一份 RailScope 快照。请填写快照显示的稳定 ID，OSM 编号只适合记录为出处。示例文件中的占位文字和无依据的可选字段应删除或替换。

## 对象资料 JSON

顶层字段为 `schema_version: 1`、`snapshot_id`、`source`、`records`。每条记录包含 `entity_id`、`kind`、`changes`。`kind` 可选 `station`（车站）、`line`（业务线路）、`rail`（国铁物理边）、`metro`（城轨物理边）。一个文件可混合对象类型，同一个对象只出现一次。

- `source.reference` 填链接、文件名或可追溯的资料名称，`source.version` 填资料版本或发布日期。`verification_status` 可填 `user_asserted`、`verified`、`automatic_reference`。自动参考需要填写 `confidence`，取值 0–1。记录也可以各自填写 `source`。这里的已核验表示填写人已核验资料。
- `changes` 可填 `name`（工作区显示名）、`designation`（编号）、`operator`（运营管理单位）、`category`（目录分类）、`group`（分组）、`note`（备注）。车站可填 `associated_lines`，值为线路稳定 ID 数组。
- 只写需要更新的字段。省略字段会保留旧值。`null` 清除字段的工作区值，清除 `profile` 可恢复该对象的全部资料扩展。数组和 `extra` 对象按整个字段替换。`former_names` 和 `other_names` 可在目录搜索。
- 导入的资料与手工编辑都存在工作区。车站或线路改名可改 `name`，确认旧名后可同时填入 `former_names`。在对象「编辑」页也可改名。同一批次里的记录会分别进入现有撤销历史。
- 切换至同区域的新快照时，程序沿用已有迁移规则。对象来源、几何或引用无法安全匹配时，在 **数据 → 审校冲突** 中查看。

### `profile` 字段

| 字段 | 填写规则 |
| --- | --- |
| `former_names`、`other_names` | 曾用名和其他用名，字符串数组 |
| `business` | 车站运输业务。`passenger` 客运、`freight` 货运、`mixed` 客货运、`non_business` 非营业 |
| `technical` | 车站技术作业。`marshalling` 编组站、`district` 区段站、`intermediate` 中间站 |
| `station_grade` | 有资料支持时填写。字符串 `special` 特等或 `1` 至 `5`。等级与技术类型分别记录 |
| `rail_type` | 线路。`high_speed` 高速铁路、`intercity` 城际铁路、`mixed_traffic` 客货共线铁路、`heavy_haul` 重载铁路、`suburban` 市域（郊）铁路、`other` 其他 |
| `railway_grade` | 客货共线铁路等级，`I`、`II`、`III`、`IV`。同时设置 `rail_type: "mixed_traffic"` |
| `city`、`urban_system` | 城市名称。制式可填 `metro`、`light_rail`、`suburban_rapid`、`maglev`、`straddle_monorail`、`suspended_monorail`、`automated_guideway`、`tram`、`guided_rubber_tire`、`electronic_guided_rubber_tire`、`other` |
| `opened_on` | 开通日期，采用 `YYYY-MM-DD`。按期建设的线路应把各期日期写入 `sections` |
| `speed_kmh`、`gauge_mm`、`electrification` | 分别填写数值 km/h、整数 mm、`overhead` 接触网、`third_rail` 第三轨、`none` 无电气化或 `other` |
| `track_type` | 物理边。`main` 正线、`arrival_departure` 到发线、`siding` 侧线、`yard` 站场、`depot` 基地、`crossover` 渡线、`other` |
| `sections` | 线路分期或区段，使用下述格式 |
| `extra` | 自定义字符串键值，例如信号制式或电压。数值型自定义内容用带单位的字符串 |

每个 `sections` 条目包含 `code`（该线路内稳定的区段编号）、`name`、`status`、`edge_ids`，可选 `opened_on`。状态可选 `operating`、`construction`、`planned`、`disused`、`unknown`。边 ID 必须来自该线路的轨道索引。区段资料用于记录工程分期和开通日期；地图轨道状态、接轨关系和运行资格仍取自基础设施快照。

## 通道与运行图

一条通道是从起点边界到终点边界的完整、连续、有向物理边序列。停站和通过由各车次的 `stops` 表示。直达和多停站车次可以共用同一通道。跨线车次应引用已经编排完整的跨线通道。

- **端点编排**。用 `corridor_intents`。每个 `steps` 条目含 `node_id` 和开往下一端点的 `line_id`，可选 `via_edge` 约束途经轨道。最后一个条目只填 `node_id`。按反方向运行时要另建通道。`strict: false` 会保存所选的自动参考路径。`strict: true` 在发现多条合法路径时会拒绝导入，补充端点或指定区段后可重新校验。
- **已知精确边序列**。也可填写 `corridors` 数组。每项填 `id`、`name`、`version: 0`、`snapshot_id`、`verification_status`、`resolution_mode: "manual"`、`provenance`、`edge_refs`。每个 `edge_refs` 条目填 `edge_id`、从 0 开始的 `sequence`、`forward`。数组顺序决定边的先后，`forward` 为真时从轨道索引的 `from_node_id` 走向 `to_node_id`。内部重算距离、端点和序号。公开拓扑推得的径路使用 `automatic_reference`，经发布方资料核验的路径填写 `source_kind: "publisher"`、出处和 `verified_by`。在建、规划、停用和类型不明的轨道不具备正式运行资格。
- **运行计划 JSON**。填 `services`、`runs`，可与上述通道定义放在同一文件中。同包内 `corridor_id` 使用模板里自定的临时键，由导入器换成保存后的 ID 和版本。引用已有通道时必须填稳定 ID 及 `corridor_version`。记录 `mode` 为 `rail` 或 `metro`，`service_date` 为运营日，`time_zone` 通常为 `Asia/Shanghai`，时间是该运营日当地零点起的秒数，跨午夜继续累加。通过时设 `pass_through: true`，到发相同时可只填一项。站台 ID 可选，已核验联锁 StationRoute 须待相应模型安装后使用。有通道且站点能锚定在路径上，才可按该路径仿真。
- **CSV**。一行一个停站。同一实例的 `train_code`、`service_date`、`mode` 和 `run_ref` 应一致，按 `sequence` 排序。可复用同一个已保存通道 ID 和版本。CSV 也可以只登记时刻，日后在运行工作台关联通道。
- **GTFS**。选择运营日，上传 GTFS Schedule ZIP。如果 feed 的 `stop_id` 使用自有编号，须填写 `stop_id → RailScope station_id` 映射。导入后得到时刻，可在运行工作台安排共享通道。RailScope 当前开放上海城轨运行。其他城市可录入基础资料。

`provenance` 填 `source_kind`（`user_defined`、`publisher`、`gtfs`、`osm` 或 `demo`）、`source_url_or_reference`。可选 `source_timestamp`、`confidence`、`verified_by`。端点自动编排须提供 `confidence`。相同文件和参数从这个入口重复提交时，运行计划会复用已导入结果。

## 命令行

在仓库根目录，使用已有 Rust 构建环境。将路径换成你的数据包目录、桌面资料目录下的 `workspace.sqlite` 以及已填好的文件。运行中的桌面会在重新读取数据时取得最新内容，重启也可刷新。

```powershell
cargo run --locked --manifest-path railscope-next/Cargo.toml -p railscope-next-core --bin railscope-import -- --check "<数据包目录>" "<workspace.sqlite>" metadata "<station.json>"
cargo run --locked --manifest-path railscope-next/Cargo.toml -p railscope-next-core --bin railscope-import -- "<数据包目录>" "<workspace.sqlite>" json "<plan.json>"
```

`metadata` 用于对象资料，`json` 用于通道或运行计划，`csv` 用于时刻表。GTFS 命令在 ZIP 路径后加运营日期和可选的映射 JSON 文件路径。命令输出 JSON 报告，失败返回非零退出码。`--check` 会校验整批记录并回滚导入。

## 分类与格式参考

- 车站的运输业务与技术作业是独立分类。编组站、区段站和中间站参考第 184 条的分类。具体车站的事实以填报资料为准。[国家铁路局《铁路技术管理规程》公开文本](https://www.nra.gov.cn/xxgk/gkml/ztjg/gfzd/bmgz/202206/t20220624_327439.shtml)
- 高速、城际、客货共线、重载及客货共线 I–IV 级参照国家铁路局公布的设计规范说明。市域（郊）铁路另有设计规范。[国家铁路局 2024 年第 27 号公告](https://www.nra.gov.cn/xxgk/gkml/ztjg/bzgf/gcjshzjbz/jsbz/202410/P020241029635228146872.pdf)
- 城轨制式参考生态环境部公开文件引用的 T/CAMET 00001-2020 分类。初期运营日期可按发布方公布的区段分别登记。[生态环境部公开文件第 3.1 节](https://www.mee.gov.cn/xxgk2018/xxgk/xxgk06/202401/W020240108546724301267.pdf) · [上海市政府 18 号线二期开通报道](https://www.shanghai.gov.cn/nw4411/20251228/d396ffca53f849b1abd77083547b263b.html)
- 运行图可通过停站时间、间隔、交路等要素描述。[交通运输部《城市轨道交通行车组织管理办法》](https://xxgk.mot.gov.cn/xzgfxwj/202504/t20250414_4166929.html) · [GTFS Schedule 规范](https://gtfs.org/documentation/schedule/reference/)
