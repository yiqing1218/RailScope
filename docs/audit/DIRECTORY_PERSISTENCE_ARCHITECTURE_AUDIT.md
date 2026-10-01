# 目录编辑与数据分层持久化专项审计

审计基线：`codex/entity-edit-performance-ui-audit` / `3f300b4`。本轮只增加诊断工具、证据与方案，不修改应用实现。附件要求先确认结论，再实施。

## 简短结论

1. **普通线路、车站目录移动：局部持久化已经存在，全量缓存签名仍拖慢操作。** 每次重新扫描全部 override 并生成车站目录签名；全国样本约 24 万条 override 时，这一步约占单对象移动耗时的大部分。目录 Model 的完整 reset 也保留着，丢失已加载分支与选择状态，但 reset 函数本身不是这次半秒卡顿的主要耗时。
2. **设施目录移动：目录路径被误判为车站归属结构变化。** `_update_directory_rows()` 比较包含路径的设施节点集合，返回 `facility_changed`，继而运行完整 `sync_station_catalog()`。它重新加载全国车站、推断设施轨道归属，并清空重建车站节点与归属表。这是几十秒等待的直接原因。
3. **站内轨道人工归属：仍走 `populate()` 和宽泛 `metadata_changed`。** 同时触发全国车站缓存重建、线路库失效、铁路/地铁/道路地图 reload、通道显示刷新；加载运行计划时还重建领域 Repository。目录显示归属与真实运行业务变化未被充分区分。
4. **assembly：相同目录属性重复存到每个 member。** 两层调用重复展开；每个 assembly 扫描全部 override，写入量随 member 数量增长。应让逻辑线路拥有自己的 membership 和共享属性，成员通过读取继承。
5. **不能再把“大 JSON 重写”当作当前根因。** 当前 `CatalogWorkspace.update()` 只复制、验证、提交 touched owners 到 `.edits.sqlite`；JSON 已成为 seed/exchange。需要继续正规化工作区结构与失效契约，而不是再次修补已解决的保存环节。

## 证据与测量边界

- 诊断工具：[audit_directory_edits.py](../../scripts/audit_directory_edits.py)。原始结果：[directory-edit-audit.json](directory-edit-audit.json)。执行：`python scripts/audit_directory_edits.py --output docs/audit/directory-edit-audit.json --national`。
- 实际 `RailCatalog`、Qt signal/slot、SQLite 路径；每个函数记录调用次数及嵌套 wall time。嵌套耗时不能相加。直接调用拖拽后的生产保存函数，排除拖拽动画、菜单等待。
- writable `rail_catalog.sqlite` 使用 SQLite backup 隔离；工作区 seed 与增量库复制到临时目录；大几何/拓扑文件共享读取并强制只读。源文件大小、mtime 在运行前后校验一致。诊断期间 PBF open 会直接报错。
- MapStub 记录发给地图的真实命令，不测浏览器绘制与网络请求。没有加载活动 TrainRun：`push_corridors` 只记录请求，Repository 重建分支由源码确认，未计时。不得把这些数值称为完整 GUI/运行图测试。
- 全国样本：`rail.sqlite` 3,163,963,392 B；`rail_lines.sqlite` 2,703,171,584 B；87,451 条 catalog、约 13.5 万条 station cache 节点、32,803 条设施轨道归属。不是 1.5 GB PBF 的重新导入 benchmark。
- SQL `total_changes` 是连接关闭时记录的累计变更次数，包含同一行多次更新及临时选择表；不是不同对象数量。`full_delete_*` 单独记录没有 WHERE 的全表 DELETE。工作区写入量由 `_write_entries()` 的实际 owners 数记录。

全国快照测量（每项一次；Undo/Redo 单独列出，不将其当作同一种状态的重复采样）：

| 操作 | 总 wall time | 主要耗时 | 工作区 owner 写入 | catalog 连接累计变更 | 全量 station rebuild / reset / library invalidate |
|---|---:|---|---:|---:|---|
| 1 条线路移动 | 687.593 ms | station signature 444.134 ms；目录签名 60.007 ms；磁盘 owner 写入 24.385 ms | 1 | 19 | 0 / 两个目录 reset / 0 |
| 线路 Undo / Redo | 647.432 / 720.511 ms | station signature 417.972 / 428.337 ms | 1 / 1 | 26 / 19 | 0 / 各两个 reset / 0 |
| 1 个车站移动 | 543.100 ms | station signature 475.666 ms；局部 station SQL 9.153 ms；owner 写入 16.635 ms | 1 | 15 | 0 / 一个 station reset / 0 |
| 车站 Undo / Redo | 465.059 / 525.985 ms | station signature 403.166 / 461.038 ms | 1 / 1 | 14 / 15 | 0 / 各一个 station reset / 0 |
| 100 条线路移动 | 1,792.249 ms | 局部目录 update 含签名 771.719 ms；station signature 506.285 ms | 117 | 697 | 0 / 两个目录 reset / 0 |
| 100 个车站移动 | 1,469.768 ms | 100 次 station SQL/事务合计 976.873 ms；signature 424.166 ms | 100 | 1,195 | 0 / 一个 station reset / 0 |
| 1 个设施纯目录移动 | **58,688.098 ms** | **sync_station_catalog 56,301.459 ms** | 1 | **509,057** | **1** / 两目录 + station reset / 0 |
| 1 条站内轨道指定车站 | **38,803.128 ms** | **sync_station_catalog 33,982.441 ms**；library 1,452.583 ms | 1 | **508,840** | **1** / station reset / **1** |

- before moves 共 **243,436 条 effective overrides**；初始化 4,117.182 ms 单列。设施/edge 操作均观测到无 WHERE 的 `DELETE FROM rail_station_nodes` 与 `DELETE FROM rail_facility_track_owners`。
- 100 条 catalog keys 实际写入 117 owners，原因是已有 assembly 扩展与 marker 写入；这本身就是 member 展开的证据。不能把 catalog key 数直接当逻辑线路数。
- 轨道归属操作实际发送 `reloadRailViewport`、`reloadMetroViewport`、`reloadRoadViewport`、`setRailSignalBoxes`，并请求 `push_corridors`。普通 line 移动无地图命令；普通 station folder 移动重发四条 selection/filter 命令。
- source 文件大小/mtime 一致、强制只读校验通过，Qt exceptions 为空。现有 workspace/catalog/presentation/batch 范围测试 **52 passed**（专用 workspace basetemp，避开系统 Temp 权限问题）；这是现有行为的验证，不是重构后的验收。

Synthetic scaling 用生产 line move slots 分离 C/O：C=700 → 7,000（O=0），单线 36.246 → 46.012 ms；C=700、无关 note overlays 从 0 → 100,000，单线 36.246 → 45.954 ms。这些 note 被目录/归属字段过滤，因此不能用该小增幅否认全国有效归属/段字段的 hash 耗时，也不能用它宣称全国操作已接近即时。

Assembly 单独测现有 `expand_assembly_changes()` + 真实 `CatalogWorkspace.update()`，全部 overlay 固定为 10,001，members=1 / 100 / 1,000；实际 expanded owners=**2 / 101 / 1,001**，每种状态 3 次写入。原始 JSON 保存每次展开/提交毫秒值；持久化工作量明确为 O(M)，不满足逻辑线路移动恒定写入要求。

**判断：** 全量签名、设施 folder 引发的全国 ownership 重算、目录触发的 broad library/map/运行显示刷新可完全删除。必要 touched SQL commit、索引查找、目标已加载页 reconciliation 与真正依赖对象更新只能降低其本身耗时；冷导入/算法升级的全国构建应缓存复用，不能声称完全消除。此次未改实现，所以没有优化后的时间或性能承诺，也没有功能、精度或业务数据改动。

## 当前真实数据流与失效机制

实际链路不是严格的单向直线：目录与地图是共享内部数据的两种投影，Qt Model 并不是 `/api/rail` 的几何数据来源。

```mermaid
flowchart TD
  PBF[原始 OSM PBF] --> Import[import_rail / native extraction / boundaries]
  Import --> Identity[identity.sqlite / snapshot / alias migration]
  Import --> Geometry[rail.sqlite: features / edges / RTree]
  Geometry --> Topology[rail_lines.sqlite: line / endpoint / station indexes]
  Geometry --> CatalogJSON[rail_catalog.topology.json]
  Topology --> CatalogJSON
  CatalogJSON --> Catalog[rail_catalog.sqlite: catalog / coverage]
  Seeds[共享 override / directory JSON seeds] --> Workspace[CatalogWorkspace / rail_catalog.edits.sqlite]
  Catalog --> Projection[目录缓存: rail_directory_nodes / rail_station_nodes]
  Topology --> Projection
  Geometry --> Projection
  Workspace --> Projection
  Projection --> Qt[Qt paged Model]
  Geometry --> API[/api/rail: viewport / names / styles]
  Topology --> API
  Workspace --> Config[HTTP config / changed entity patch]
  Config --> API
  API --> Map[MapLibre sources / labels / selection]
  Config --> Map
```

| 层与当前文件 | 保存什么 / 输入 | 当前失效或重建条件 | 审计判断 |
|---|---|---|---|
| Source：OSM PBF | 原始来源；下载/用户选择文件 | 显式导入、重导入、边界导入等操作 | 日常目录编辑、保存、搜索、viewport 不访问 PBF。一次导入目前仍有提取与边界等多遍读取，不能声称现有导入只解析一遍 |
| Identity：`identity.sqlite`、migration/manifest | 稳定 RailScope ID、OSM 来源 alias、快照、迁移冲突 | 导入注册、业务对象 identity 建立 | 稳定 ID 必须保留；OSM station/edge presentation key 只可作为 adapter alias |
| Geometry：`rail.sqlite` | `features` GeoJSON 行、物理 `edges`、`bounds` RTree、feature/group 映射；输入 PBF 与 identity 结果 | 导入构建；`upgrade_render_features()` 等显式派生升级也能修改文件 | 已有内部数据库；不需要因 PBF 大而更换 GeoPackage/PostGIS。文件同时混入显示属性，不能把每个 mtime 变化视为物理拓扑变化 |
| Topology：`rail_lines.sqlite` | 原子 edge/node 邻接、线路与端点索引、station aliases/directory/track positions、semantic 索引 | `INDEX_VERSION=18` + rail.sqlite 大小/mtime + extras 的 fingerprint；旧版可专项升级 | 没有完整 artifact manifest；目录编辑并不直接改此文件。线路库“失效”与磁盘拓扑索引“重建”是两件事 |
| Catalog：`rail_catalog.topology.json` → `rail_catalog.sqlite` | 业务线路/设施投影、源成员与 render coverage；输入几何/线路索引 | topology JSON 主要检查 `VERSION`；catalog SQLite 检查 schema、JSON 大小/mtime、render 大小/mtime、coverage 标记 | JSON 缺少输入快照校验，SQLite 将文件状态当依赖；两者都应改为明确 artifact metadata |
| Workspace：共享 `rail_catalog_overrides.json`、本地 `rail_catalog.json`、line/station directory seeds、`.edits.sqlite` | 人工属性、目录、assembly、归属、tombstone，叠加优先级 | 启动全量载入；编辑 touched owners SQLite；undo/redo touched snapshots | 持久化已增量，但 JSON + 无类型属性 blob + 全量内存 dict 仍被签名与 library metadata 消费 |
| Presentation cache：同一 `rail_catalog.sqlite` 内目录表 | membership、树节点、计数、搜索文本、车站/设施/轨道显示归属 | 全量 override hash；车站签名包含 `paged_directory_signature`、上游文件 mtime、全量归属与段属性 | 派生目录缓存与业务 catalog 共文件；依赖过宽，造成跨层计算 |
| Qt paged Model | 当前展开页、搜索结果、checkbox aggregate | `reset_from_disk()`、`set_search()`；已有 `refresh_affected()` | 分页读取有效，但普通移动没用局部 reconciliation；search 状态也经常 reset |
| HTTP/MapLibre | RTree viewport 按需结果 + 名称、线型、Label 与选择 | map source key、config、reload、patch；station directory 按 size/mtime LRU 缓存 | 地图依赖内部 SQLite，不依赖 PBF；目录变动本可完全不触发地图请求 |

源码入口：`desktop/import_rail.py:23,259,319,324`；`rail_store.py:74,185,310`；`rail_line_store.py:33,42,79`；`provinces.py:236`；`rail_catalog_index.py:42`；`catalog_workspace.py:17,56,159`；`rail_station_directory.py:155`；`launcher.py:383`；`assets/map.js:342,824`。

## 三类编辑的实际调用链

### 1. 普通铁路目录移动

`move_items()` → `save_overrides()` → `expand_assembly_changes()` → `_save_local_overrides()`（再次展开）→ `CatalogWorkspace.update()` → touched SQLite commit → `_move_line_items_in_tree()` → `_update_paged_directory()`。

后半段包含：投影全部 override → `_directory_signature()` → `_update_directory_rows(changed_keys)` 局部 SQL → 两个目录 Model reset/refetch → `station_catalog_signature()` 再扫描全量 override，并更新 metadata 签名 → SELECT 全部线路 folder paths → 同步已选对象 visibility aggregate → `directory_changed` → `refresh_directory_overrides()` 复制整个 override dict / 保持 library stamp。

这一普通路径**已不调用**全国 `populate()`、完整 station rebuild、library invalidate 或三类地图 reload。Undo/Redo 同样局部写 owner，但仍重复全量签名和 Model reset。现有 700 对象测试覆盖“没有 populate”，没有覆盖“没有全量签名/Model reset”。

### 2. 车站目录移动

`save_station_changes(folder_path)` → `_save_local_overrides()` → touched SQLite → `_refresh_station_changes()` → `_refresh_station_items()` 只处理缓存里的指定车站 → `update_station_placement()` → 当前车站子树/old-new ancestors 局部 SQL → station Model reset/refetch → 全量 `station_catalog_signature()` → `entities_changed` → live library metadata patch + 更新 stamp → `send_station_visibility()`。

当前**不会因为普通车站 folder_path 直接调用 `sync_station_catalog()` 或 `metadata_changed`**。但移动 signalbox 也触发 `station_presentation_changed` → 全部 signalbox GeoJSON 重发；每次纯目录移动仍重发四条 station selection 命令。JS setters 运行 `sharedRailStationFilter()` 重设地图 filter，未直接调用 `reloadRailViewport()`。

`update_station_placement()` 已经正确避免全国 ownership 推断，但目前将路径冗余存到子树每行，移动有很多子设施的车站需要更新该子树。批量 100 车站还产生 100 个独立 cache SQL 事务，而不是一个批量事务。

### 3. 设施/车辆段/站内轨道

- **设施纯目录移动**：普通 move 链 → `_update_directory_rows()` 的 `(node_id, path)` 旧新集合不同 → `facility_changed=True` → `_prepare_station_catalog()` → `sync_station_catalog()` → 全国 `rail_station_records(limit=100000)` + `facility_track_owners()` + 接入轨道遍历 → 全表 DELETE/reinsert `rail_station_nodes`、`rail_station_matches`、`rail_facility_track_owners` → station Model reset。真实归属没有变，昂贵工作完全多余。
- **目录设施指定车站**：`_assign_station_assets()` → `station_assignment_changes()` 生成 catalog owner/edge overrides → `save_overrides()` 的 reclassifying/placement 分支 → paged 更新或 `populate()` → 完整 station cache 重建 → broad `metadata_changed`。
- **单条站内 edge 指定车站**：`object_changes` 含 `station_id/station_assignment` → `save_overrides()` 明确调用 `populate()` → `_populate_paged_directory()` → `_prepare_station_catalog()` → 完整 station cache 重建 → `metadata_changed` → library invalidate → `refresh_signal_boxes()` → `refresh_map_names()` → 全量 override/config/line presentation + 铁路/地铁/道路 reload + `push_corridors()` + 有运行计划时 `canonical_repository()`。

`push_corridors()` 重生成运行路径的显示 GeoJSON 并刷新名称；源码不能据此断言每次会重新最短路求解。`canonical_repository()` 会重新建立和校验活动领域对象。这个审计没有加载运行计划，因此不报告虚构的 corridor 解算或 Repository 耗时。

## 全量与局部工作清单

令 O 为全部 override 数，C 为全部 catalog 数，S 为车站/设施目录大小，M 为 assembly members，K 为本次对象数，D 为目录深度，T 为当前站子树，V 为当前选择集合，L 为已加载 Qt 节点。

| 位置 | 现有成本 / 原因 | 处理决策 |
|---|---|---|
| `CatalogWorkspace.update():159`、`_commit()` | O(K × touched owner 大小)，局部复制/校验/SQLite；无 JSON 全量重写 | 保留原子写入与失败保护，规范成 typed tables |
| `rail_line_workspace.expand_assembly_changes():19` | 每个 assembly 扫描 O(O)，展开 O(M)；`save_overrides` 与 `_save_local_overrides` 重复调用 | assembly 自持 membership；共享字段读取继承；只在成员增删时更改成员行 |
| `rail_catalog_ui._update_paged_directory():785` | 对全量 override 做字段过滤；后续签名仍包含很多与目录无关的非名称属性 | changed IDs + directory revision，不再构造全集投影 |
| `rail_catalog_model._directory_signature():67` | O(O) 全量 JSON/sort/hash；仅排除 display_name | artifact init 校验与编辑失效分离；普通命令禁止全量 hash |
| `rail_station_catalog_model.station_catalog_signature():31` | 多遍 O(O) comprehension、字段投影、JSON/hash；还依赖整个 paged directory signature | 改为 source/semantic/assignment 等明确 revision；folder 命令仅目录 revision |
| `rail_catalog_model._update_directory_rows():157` | changed key 与 old-new ancestors 局部 SQL；受影响共享节点仍聚合其 members | 返回 affected node/parent/count ChangeSet，不返回“路径变了即归属变了”布尔值 |
| `_update_paged_directory():800`、`_refresh_station_changes():2900` | Model reset O(L) 清缓存 + 页重取；还全取 folder paths | `refresh_affected(old/new parents + ancestors)`；路径列表用 folder table 查询/缓存，不每次全集重建 |
| `update_station_placement():271` | O(T + D)，局部站子树 SQL；批量 K 次建连接/commit | 先同事务批量更新；用 stable folder IDs 消除后代冗余路径，避免移动父站时重写所有后代 |
| `_prepare_station_catalog():935` → `sync_station_catalog():52` | O(S + 全国区域/候选轨道/归属推断)，清表重建 | 冷导入或真正算法/快照升级可保留；目录/明确 assignment 禁止调用 |
| `facility_track_owners():91` | 扫描真实站区、多边形 intersection、索引候选与接入轨道遍历 | immutable baseline ownership artifact；人工 assignment 覆盖其结果，目录移动不重算 |
| `_sync_paged_visibility():950` / Model set_visibility/visible_state | O(V × D + L) 选择集合计数、TEMP rows、loaded state 发射；最坏 V≈全集 | 只维护 touched owners old/new ancestor checkbox count；不关闭联动 |
| `send_station_visibility():2100` | 扫描全部 station overrides、重复发送相同 selection；signalbox folder 也重发 GeoJSON | folder-only 不发地图信号；真实 visibility/geometry/label delta 才发送 |
| `launcher.refresh_directory_overrides():3295` | O(O) 复制全部 overrides；mtime retention workaround | 改 changed owner 或取消地图 config 对 folder 字段的订阅 |
| `launcher.refresh_map_names():3231` | 全量 metadata/config/presentation、三类地图 reload、运行显示与可选 Repository 重建 | 删除 broad subscriber；typed change 精确订阅；相应真实业务改变只校验依赖对象 |
| `rail_ui.line_library():885`、`canonical_repository():184` | mixed override stamp 驱动全量 metadata read/merge、library 重建；物理 index 只有 source fingerprint 失配才重建 | presentation/directory 不失效 topology library；semantic/assignment 的影响需按对象和引用判断 |
| `RailCatalogIndex.sync_directory_paths():280` | 旧接口仍可因全量 hash 扫 C / 清 paths 与 totals | 当前主移动路径未调用；移除编辑入口依赖，保留受控完整缓存构建适配 |

`SqliteDirectoryModel.refresh_affected():262` 已实现局部 loaded-branch reconciliation；不是直接替换 reset 就完成任务：需要先从 SQL updater 返回准确 affected parents/ancestors，更新 station `_folder_totals`、visibility counts、搜索 match 表，并验证分页边界、展开与多选。

## 拟确立的数据分层与失效契约

原始数据不可变；昂贵中间结果可复用；用户修改进工作区；显示投影按需。

| Revision | 何时变化 | 真正订阅者 / 不应失效内容 |
|---|---|---|
| source | 原始导入快照改变 | import pipeline / source identity migration |
| geometry | 物理坐标/边界变化 | RTree、viewport geometry、相关归属推断；目录移动不变 |
| topology | edge 连接、真实线路成员关系、决策节点改变 | 路径索引、引用迁移/校验；目录/显示改名不变 |
| semantic | 轨道用途、线路类别、通行条件等实际业务语义改变 | 线型分类、相关 traversal eligibility、引用该 edge 的运行对象校验；不重建物理 geometry |
| directory | folder / membership / 排序 / 目录隐藏状态改变 | Qt directory/search projection/counts；不改 geometry/topology/semantic |
| presentation | 名称、颜色、线宽、地图显示属性改变 | Label、命中对象 Renderer、显示搜索文本；不重算路径 |
| assignment（新增） | 设施/站内轨道的人工显示归属改变 | old/new station 下的设施树与归属筛选；必要的真正 StationTrack 业务变化走对应 Domain command，不能隐含改 NetworkEdge |
| operation | Corridor/StationRoute/TrainRun 业务内容变化 | 运行图、模拟、活动计划 projection；不复制全国 geometry |

不能简单规定“path 只看 topology”：正式路径可用性还依赖 semantic、source verification 和 construction 状态。**目录命令**必须证明这些 revision 均不变；语义/真实归属变动必须查 Corridor、StationRoute、TrainRun 引用并局部重新校验。

每次工作区命令产生 `ChangeSet(command_id, changed_ids, changed_fields, change_types, affected_parents, affected_ancestors, revisions_before/after)`。Undo/Redo 使用同一事务和同一 ChangeSet 构建器，禁止另走 `populate()`。UI cache 是可恢复投影，先提交权威状态，失败不得被静默吞掉；投影失败须标记需要修复并可重放该 command，避免先落盘再无提示丢显示。

## Intermediate artifact 约定

每个 immutable artifact 保存：`artifact_id / dataset_snapshot_id / schema_version / algorithm_version / input_artifact_ids + input_hashes / dependency_revisions / created_at / output_digest / complete_status`。

内容 hash 在导入或构建时计算并保存；普通编辑读取固定 manifest，不重新 hash 1.5 GB PBF 或 3 GB SQLite。算法版本和 schema 版本独立。临时文件完整构建、校验后原子发布；多文件通过快照 manifest/active pointer 统一切换。旧版 version-only / mtime fingerprint 只在兼容识别阶段使用，不能继续作为编辑时的拓扑依赖。

需明确缓存：geometry/RTree；line/endpoint/station alias index；catalog/source membership；真实站区产生的 baseline facility ownership；目录搜索/计数 projection。后三者不能因为目录变动重新读取上游 geometry。纯人工设施归属写 explicit overlay；若未来允许修改真实多边形或归属推断规则，才对受影响区域重算推断 artifact。

## Workspace 目标与兼容迁移

采用统一 `workspace.sqlite`，是现有 `railscope.domain` 的 Repository/DTO adapter；不新建第二套独立业务模型，也不重写原始派生数据。

| 表 | 关键字段 / 职责 |
|---|---|
| workspace_meta / revisions | schema、workspace ID、源快照、typed revision、迁移状态 |
| object_overrides | 稳定 domain object_id、字段/属性 blob、provenance；仅人工业务字段 |
| object_aliases | 旧 `RL-/ST-/station:node/…/object:…` projection key 到稳定 RailScope ID；歧义进 conflicts |
| directory_folders | folder_id、view/workspace、parent_id、name、排序；与业务 Entity ID 分离 |
| directory_membership | view + logical_object_id 主键、folder_id、排序/目录状态；assembly 只一条 membership |
| line_assemblies | assembly_id、display_name、shared_metadata、active、provenance；逻辑线路身份 |
| assembly_members | assembly_id + 稳定 member_id、source membership/version；不重复 folder/name/color |
| presentation_overrides | object/assembly ID、Label/颜色/线宽等显示字段 |
| station_assignments | object_id、station_id、source/snapshot/version、verification/confidence；explicit/pending |
| command_history / migration_conflicts | touched before/after、change types、命令状态；冲突不能覆盖或静默忽略 |

单对象移动：一条 membership UPDATE + old/new parents/ancestor count 的局部投影事务。目录路径由 folder parent chain 生成；不把路径冗余复制给全国对象。logical assembly shared fields 通过 metadata resolver 继承，继续保证目录、属性面板和地图名称一致。对成员的显式局部几何/来源信息单独保留，逻辑合并不得改 physical edges 或使旧 Corridor 引用悬空。

迁移顺序与回滚：

1. 只读备份 seed JSON、directory JSON、`.edits.sqlite`，记录 fingerprint；明确现有优先级：shared base → directory seeds → 本地 seed → SQLite incremental/tombstone。读取工作区实际 `load()` 的合并结果，不通过名称猜测对象身份。
2. 临时 `workspace.sqlite` 一个事务建 schema、解析稳定 IDs/aliases、拆字段类别、建 folders/memberships/assignments；组共享值不一致要保留 per-member 差异或生成 conflict，不能选第一个覆盖全部。
3. 验证 effective metadata、membership、assembly active/members、归属 evidence、tombstone、旧业务引用；验证所有几何/edge IDs 与完整 Corridor/TrainRun path 不变；提交 migration marker 后原子启用。
4. 单 writer：迁移后所有编辑只写新工作区。旧 JSON/sidecar 保留只读备份；通过兼容 adapter 提供旧 `meta()/read_overrides()` 语义，按需查 touched objects，禁止读回全集作为普通编辑流程。
5. JSON 只用于 import/export/backup/config exchange，输出完整 effective state；导入在 transaction 中验证并标记 conflicts。不能让旧 JSON mtime 影响 topology revision。
6. 未完成启用前可丢弃临时迁移文件并继续旧 workspace。启用后回滚旧版本前应导出兼容 exchange 或恢复明确检查点，不能直接退回旧 seed 丢失新编辑。原始备份不删除。

完整实现分期与验收见 [DIRECTORY_REFACTOR_IMPLEMENTATION_PLAN.md](DIRECTORY_REFACTOR_IMPLEMENTATION_PLAN.md)。本轮尚未实施以上方案，也没有“优化后”时间。
