# Rail Domain Model V2 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for integration and dispatching-parallel-agents for independent file scopes. Steps use checkboxes for tracking.

**Goal:** 按 V2 要求拆分铁路身份、功能、设施和运营意图，保留共享物理拓扑及旧用户数据。

**Architecture:** `railscope.domain` 是唯一领域契约；分类器输出逐属性来源，SQLite 和 Desktop DTO 适配该契约。迁移只增加语义和兼容层，不重编号或重算已保存路径。

**Tech Stack:** Python dataclasses, SQLite/RTree, PySide6, MapLibre, Alembic/PostGIS。

**Spec:** 用户提供的《RailScope Rail Domain Model V2 重构要求》。

## Global Constraints

- 起点 `7792d57`，分支 `codex/rail-domain-v2`；用户明确要求跳过当前版本验证，直接修改，完成后验证。
- 保持 NetworkEdge ID、Corridor 路径、TrainRun 数量、StopTime 时刻和人工覆盖；不改原始 OSM。
- 不猜正式股道号；`service=spur` 不证明 branch_line；yard/siding 不证明到发/调车用途。
- 主线进站仍可为 main_track；throat 是区域；自动路径不是联锁进路。
- 全国数据继续 SQLite/RTree、分页、视窗加载、有界缓存。
- 修改后检查差异并独立中文本地提交，排除数据和敏感文件。

## Review Focus

- 旧人工名称、颜色、目录位置和组合归属保留，旧自动股道编号移入兼容信息。
- 同名异地设施不合并；未知属性不从几何或字符串强猜。
- 多段股道连续性、外键与拆分引用完整性。
- 新索引与旧 DTO/CSV/JSON 共存；失败不得损坏活动索引。
- 保存后的意图、具体物理选择和跨日停站时刻往返一致。

### Task 1: 共享领域、分类与持久化

**Files:** backend/railscope/domain.py, rail_semantics.py (new), repository.py, workspace.py, integrity.py; backend/alembic/versions/0005_rail_domain_v2.py; backend/tests/test_rail_domain_v2.py.

**Interfaces:** `edge_semantics(edge: dict) -> dict` returns railway_class, line_role, track_role, facility_id, yard_id, zone_id, construction_status, verification_status, confidence, provenance. `classify_railway_class`, `classify_line_role`, `classify_track_role`, `classify_operational_status`, `classify_facility_context` return Classification(value,evidence,verification_status,confidence,source,snapshot_id). `legacy_semantics(track_type: str) -> dict` is conservative. Provenance is JSON-compatible per-attribute dictionaries.

- [x] Add orthogonal fields and OperationalPoint/Yard/StationZone/StationTrackEdge, RouteIntent and ResolvedCorridor contracts.
- [x] Add evidence-based classifiers and legacy decode migration; integrity checks and SQL migration.
- [x] Test station mainline, complex yards, multi-edge track, conservative OSM classification, workspace roundtrip and references.

### Task 2: 股道名称与 Desktop adapter

**Files:** desktop/domain_adapter.py, station_tracks.py, yard_track_names.py, station_track_ui.py, display_names.py (name handling only); desktop/tests/test_rail_domain_v2_adapter.py.

- [x] Use Task 1 semantics in canonical adaptation; never set track_number from internal edge ID.
- [x] Preserve manual/source numbers, migrate automatic numbering to display-only aliases with NULL official number.
- [x] Keep multi-edge membership and provenance; verify adapter and naming regressions.

### Task 3: Importer / SQLite / workspace

**Files:** desktop/import_rail.py, rail_store.py, rail_line_store.py, rail_line_workspace.py, rail_lines.py; desktop/tests/test_rail_domain_v2_store.py.

- [x] Populate semantic fields on import and streamed index build; bump index version 16 to 17.
- [x] Upgrade old indexes in place for unchanged sources or rebuild atomically for changed sources; preserve stable identities and separate overrides.
- [x] Query domain attributes using indexed storage; preserve workspace membership and lazy geometry loading.
- [x] Test source immutability, source/override provenance, rebuild failure and bounded queries.

### Task 4: 目录与地图

**Files:** desktop/rail_categories.py, rail_catalog_ui.py, rail_catalog_index.py, rail_style_ui.py, display_names.py (style handling only), assets/map.js; new model/resolver modules as necessary.

- [x] Domain-driven presentation resolver and migration of custom style keys.
- [x] Separate line and facility views; domain attributes shown independently in object detail.
- [x] Use QTreeView/QAbstractItemModel with SQLite paging for national rail browsing.
- [x] Verify style/classification, UI interaction and pagination.

### Task 5: 正式运营意图与验收

**Files:** desktop/rail.py, rail_lines.py, rail_transfer.py, corridor_ui.py as needed; docs/RAIL_DOMAIN_MODEL_V2.md, docs/data-model.md, docs/RAIL_LINE_NAMING_AND_CORRIDORS.md, README.md.

- [x] Promote RouteIntent/resolved selection outside extensions, retaining legacy extension/CSV readers and stored physical path.
- [x] Cover intent re-resolution on a new snapshot with explicit validation, keeping StationRoute separate.
- [x] Run full Python suites, JS checks/tests and frontend tests/build; record synthetic paging evidence and national-data limitations.
- [x] Review diff, document migration matrix and outstanding unverifiable OSM properties; commit independently.
