# RailScope 统一领域与完整通道实施计划

**Goal:** 保留现有桌面工作台、上海地铁、全国导入和通道功能，按修订第 11 条逐步建立稳定领域契约。

**Architecture:** `backend/railscope/domain.py` 是唯一领域模型。旧 JSON 和桌面字典保留为兼容 DTO，通过 adapter 转换；地图几何不充当业务身份。Corridor 永远保存完整、有向、连续 edge 序列，RS 只作可选编辑辅助，不要求 CorridorSegment。

**Tech Stack:** Python dataclasses、SQLite/RTree、PySide6、MapLibre、pytest。

**Spec:** 用户本任务提供的 32 项需求及替换后的第 11 条；第 10、12、17、31 条分段表述按第 11 条调整。

## 约束

- 不覆盖用户已有未跟踪上海运行数据；先严格验证并报告。
- 不提交原始数据、生成 GIS 输出、环境密钥；完成验证和 diff 检查后中文本地提交。
- 自动匹配保留来源和核验状态，歧义进入冲突，不静默最短路。
- 中间车站隐含于完整路径，停站、时刻、股道及站台属于车次。
- 不扩展真实联锁调度；保持上海为唯一地铁运行实例。

## 顺序及验证

1. **Topology V1** — 扩展 `domain.py` 和 `repository.py`；新增身份注册与 snapshot 对比服务，接入 `desktop/import_rail.py`，旧 ID 仅作 source alias。验证几何折点变化保留 ID、分拆合并冲突、原始标签保留、引用完整性。
2. **Station V1** — 新增共享 Station resolver/registry，接入已有站点显示和真实区域导入。验证同站聚合、不同网络不误合并、原始节点不丢失、站区来源与类型。
3. **Editing V1** — 新增事务式 SQLite workspace 和编辑 session；线路/成员、车站、完整通道使用 candidate state、Undo/Redo、显式保存与引用检查。验证失败回滚、重新导入后覆盖仍存、保存重新加载。
4. **Corridor V1** — 增量修改既有国铁工作台；完整路径复用、不同停站模式、明确迁移旧站内覆盖、不在 TrainRun 临时拼接跨线路径。验证 G1 参考路径与旧数据兼容。
5. **Timetable V1** — 标准化 GTFS/12306 adapter、日期/跨午夜/来源、车站解析、通道顺序匹配与核验门槛；严格检查已提供上海数据。验证歧义不运行、真实 edge 插值。
6. **集成** — 保留 SQLite/RTree 视窗接口、增量地图样式；更新架构约束与可用边界。运行完整 backend/desktop 测试和 JS 语法检查，检查 diff，独立本地提交。

基线：`PYTHONPATH=backend;.`、`QT_QPA_PLATFORM=offscreen`，`python -m pytest backend/tests desktop/tests -q --basetemp=data/processed/test-baseline`：114 passed。默认系统 pytest 临时目录不可写，验证均使用独立项目内生成目录。
