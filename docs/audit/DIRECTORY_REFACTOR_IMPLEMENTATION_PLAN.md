# 目录与工作区重构实施计划（待用户确认）

基线与根因：[专项审计](DIRECTORY_PERSISTENCE_ARCHITECTURE_AUDIT.md)。本文件是待实施设计，不代表完成状态。

## 实施范围与顺序

### 1. 建立变更契约与 workspaces 单写入口

- 新建 SQLite workspace adapter，复用 `railscope.domain`/identity contract；提供 typed transaction、touched snapshot、revision、ChangeSet 与原子失败保护。
- 审计现有 shared/local/seed/sidecar 优先级，执行可回滚兼容迁移；保留旧 JSON import/export。不先增加新的 mixed JSON blob 权威层。
- 统一目录命令和 Undo/Redo，所有持久化与消息同事务语义。真实 domain 引用检查不得跳过。
- 验证：有效数据完全等价、tombstone/未知字段/evidence 保留、重启/重复迁移无变化、事务失败原状态与历史不变、冲突明确、稳定 ID 不改。

### 2. 目录投影真正局部更新

- `rail_catalog_model` updater 返回 old/new parent、affected ancestors、对象节点、计数和搜索变更。
- `rail_station_catalog_model` 加批量 `update_station_directory(changed_ids)`；同一 SQL transaction 更新 membership/count/prune，不为每个 station 新 commit。父目录稳定 ID，逐步消除路径冗余。
- `rail_catalog_ui` directory-only 删除全量 override projection/hash、station signature 重算、folder path 全取、selection 全同步；不用 `populate()/reset_from_disk()`。
- `lazy_directory.refresh_affected()` 更新仅已加载节点；保留展开/多选/焦点和分页；搜索 match 与 folder totals/checkbox cache 精确失效。
- 验证：1 line、1 station、100 对象、搜索开启、未展开分支、归档视图、同名共享显示节点、目标新目录、旧目录为空、跨 view 移动；Undo/Redo 同样执行局部路径。

### 3. Assembly 与人工归属脱离全量构建

- Assembly 目录/共享名称/样式只属于逻辑对象；成员 resolver 读取继承。取消两层 `expand_assembly_changes()` 的全量扫描与属性复制。
- 基线 station/facility/track ownership 作为 immutable source artifact；明确 assignment overlay 增量更新 old/new station 子节点和计数，目录移动不改变 ownership artifact。
- 不因 assignment 的目录用途 invalidate library/topology；若某操作真正改变 StationTrack/InfrastructureLine 等业务引用，用 explicit domain command、查询依赖并迁移/校验，不隐含修改 path。
- 验证：同样移动一个 assembly，member=1/100/1000/10000 时持久化 owner 数恒定；name/颜色继承一致；拆分与旧引用/evidence 保持；设施和单 edge 指定/取消/待核对只改相关站区投影。

### 4. Typed signal 与 artifact manifest

- `directory_changed(ChangeSet)`：仅目录模型。
- `presentation_changed(ChangeSet)`：changed Renderer/Label/search；不 global reload。
- `semantic_changed(ChangeSet)`：相关语义索引、线型与业务可用性校验。
- `station_assignment_changed(ChangeSet)`：old/new station tree、实际依赖它的 owner filter。
- `topology_changed(ChangeSet)`：受影响物理/线路 index 与 Corridor/StationRoute/TrainRun 引用校验。
- `operation_changed(ChangeSet)`：运行图与模拟投影。
- `launcher` 删除 broad `metadata_changed` 重量级联动；`rail_ui` library 不再订阅 mixed JSON/SQLite mtime，明确 cache revision 与按 ID 局部 metadata。
- `rail_line_store/provinces/rail_catalog_index` 保存 schema/algorithm/input fingerprint/created_at/dependency/output/complete manifest；启动可复用现有 snapshot，升级不得覆盖人工工作区。
- 验证：仅真正源/算法变化重建对应 artifact；目录、Label、普通字段不改变 geometry/topology/semantic revision；中断构建保留旧完整快照。

每阶段测试通过后再进入下一阶段。不以线程、防抖或延迟刷新代替上述依赖与持久化重构。既有导入进度机制可保留，用于真正冷构建。

## 必须新增的回归断言

1. 移动 1 条线路：`populate/_populate_paged_directory/build_catalog_index/sync_catalog_directory/sync_station_catalog` 均不进入全量构建；无全量 `_directory_signature/station_catalog_signature`。
2. library invalidate、`reloadRailViewport/reloadMetroViewport/reloadRoadViewport`、`push_corridors/canonical_repository/resolve` 调用次数为零。
3. 移动 1 个 station：无无条件 `DELETE FROM rail_station_nodes`；only touched membership + old/new ancestors/count/search；不修改全国未受影响 SQL 行。
4. 纯设施 folder 移动：无 ownership 推断、全表 DELETE、library invalidate；assignment 只涉及选中对象和 old/new stations。
5. Undo/Redo：同样 ChangeSet、同样局部 DML、同样禁止调用断言；重启后实际保存数据一致。
6. Assembly：共享目录只写一条 logical membership，不因 M 增大而展开 M 份 folder；不扫描全体 overrides 发现 members。
7. Qt：无需 reset；展开、多选、选择焦点、search matches、folder total、checkbox aggregate、分页位置正确；地图联动仍正常。
8. 固定快照下，对比 geometry/edge connectivity/source aliases/完整 directed Corridor sequence/StationRoute refs/TrainRun paths 的摘要与引用，移动前后完全一致。不能仅比文件 mtime 当作业务等价。
9. 迁移：旧 seed + tombstone sidecar + shared layers，冲突、未知扩展字段、provenance、inactive assembly 与现有运行引用全部保留；失败不半迁移。

## Benchmark 与完成条件

- 保留本轮原始诊断记录作为 before；优化后用同一生产 slots、样本、源快照和相同 instrument 记录 after，不用 synthetic 小样本替代全国数据验证。
- 1 line、1 station、100 lines、100 stations、facility folder、explicit track assignment；Undo/Redo；M=1/100/1000/10000 assembly；多个 O/C/S 规模独立改变。
- 报告初始化/冷 artifact 构建与 warm edit 分开；多次重复报中位数/P95和采样数、磁盘事务、affected owners/SQL rows、full-work 调用次数。额外验证真实地图与加载运行计划，报告绘制边界。
- 首要通过结构断言：耗时不含 O(全国 source/全部 override)；移动成本约 O(K × 目录深度 + 实际依赖对象 + 已加载受影响节点)，SQLite 查找允许索引 logN。UI 定位/焦点涉及目标页加载应单独报告。
- 本机目标：单对象正常 warm directory 编辑接近即时（目标中位数 <50 ms；目标是待测验收指标，不是承诺或已有结果），batch 100 随 K 增长；无需人工刷新。不关闭功能、不改变精度、不限制设施类型、不取消 Undo/Redo。
- 最终提交报告应列：原/新调用链、删除的全量工作、artifact manifest、迁移表、核心文件、测试与 before/after 数字、尚未完成事项及来源/业务数据完整性边界。

## 预计核心文件

`desktop/catalog_workspace.py` / 新 workspace adapter；`rail_line_workspace.py`；`rail_catalog_ui.py`；`rail_catalog_model.py`；`rail_station_catalog_model.py`；`lazy_directory.py`；`launcher.py`；`rail_ui.py`；`rail_line_store.py`；`rail_catalog_index.py`；`provinces.py`；现有 domain/identity adapters 的最小兼容入口；对应 regression tests 与 benchmark。

本计划不重设计桌面视觉，不重复上轮 UI/UX 审计。变更保持统一设施详情模板、地图/目录多选、稳定 IDs、共享领域模型和三层完整运行路径约束。
