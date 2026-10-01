# Rail Domain Model V2 变更说明

本次修改基于 `codex/rail-domain-v2` 分支。旧版只作分支起点，未运行旧版验证。V2 字段和对象定义见 [领域规范](RAIL_DOMAIN_MODEL_V2.md)。

## 修改文件清单

| 范围 | 文件 |
| --- | --- |
| 共享领域与迁移 | `backend/railscope/domain.py`、`repository.py`、`integrity.py`、`workspace.py`、新增 `rail_semantics.py`、`backend/alembic/versions/0005_rail_domain_v2.py` |
| 来源、SQLite 与工作区 | `desktop/import_rail.py`、`provinces.py`、`rail_categories.py`、`rail_line_store.py`、`rail_line_workspace.py`、`rail_lines.py`、`rail_store.py`、`domain_adapter.py`、新增 `rail_semantics.py`、`rail_semantic_index.py`、`station_track_semantics.py`、`route_intent.py` |
| 目录、地图与编辑器 | `desktop/rail_catalog_ui.py`、`display_names.py`、`rail_style_ui.py`、`rail_ui.py`、`corridor_ui.py`、`launcher.py`、`line_metadata_ui.py`、`station_tracks.py`、`station_track_ui.py`、`station_schematic.py`、`yard_track_names.py`、`assets/map.js`、新增 `rail_catalog_model.py`、`rail_style_resolver.py` |
| 测试 | 新增 `backend/tests/test_rail_domain_v2.py`、`desktop/tests/test_rail_catalog_model.py`、`test_rail_domain_v2_adapter.py`、`test_rail_domain_v2_store.py`；更新 `desktop/tests/road_layers.cjs`、`test_corridors.py`、`test_display_names.py`、`test_entity_presentation.py`、`test_layers.py`、`test_line_membership.py`、`test_rail_catalog_context.py`、`test_rail_categories.py`、`test_rail_line_library.py`、`test_station_identity.py`、`test_station_transfers.py`、`test_yard_track_names.py` |
| 文档 | `README.md`、`docs/data-model.md`、`docs/RAIL_LINE_NAMING_AND_CORRIDORS.md`、新增 `docs/RAIL_DOMAIN_MODEL_V2.md`、本文及实施计划 |

## 领域对象与数据库

`InfrastructureLine`、`NetworkEdge` 增加独立的 `railway_class`、`line_role`、`track_role`、设施归属及逐属性证据。新增 `OperationalPoint`、`Yard`、`StationZone`、`StationTrackEdge`、`RouteIntent`。`StationTrack` 可以按顺序引用多个共享物理边；`Corridor` 仍是一条完整、连续、有向的物理路径，`TrainRun` 和 `StopTime` 保持独立，`StationRoute` 不并入长途通道。

Desktop 派生索引从 v16 升至 **v17**，增加 `edge_semantics` 与 `line_semantics`，并保留原 `edges`、`lines`、RTree 和端点索引。国铁目录另建可重生的分页展示索引，不改变业务线路和轨道 ID。后端 Alembic `0005_rail_domain_v2` 为原有线路、轨道、股道、通道增加语义字段，增建控制点、车场、站区、股道成员、业务径路意图及其步骤表；不删除旧路径、车次或时刻。数据库迁移只做了离线/测试检查，尚未在用户 PostGIS 实例运行。

## 旧数据迁移规则

| 旧数据 | V2 处理 |
| --- | --- |
| 单一 `track_type` | 保留兼容读写；有原始标签时重新逐属性分类，没有原始标签时仅作低置信提示，不能凭复合名称认定股道用途 |
| `service=crossover` / `spur` / `yard` / `siding` | 分别得到 `crossover` / `spur_track` / `unknown` / `unknown`；`spur` 不等于铁路支线，`yard/siding` 不等于到发线或调车线 |
| 旧自动股道号或内部 ID | 不作为正式 `track_number`；原值保存在兼容元数据中。可信来源或人工核验的编号保留 |
| 旧通道、车次、JSON/CSV | 保留已选物理边和时刻；新保存显式携带版本化 `route_intent` / `resolved_corridor`，读取旧文件时适配，不在普通读取时重新寻路 |
| 人工线路整理与显示设置 | 继续保存在独立工作区；来源数据不回写。不能安全重放的引用生成冲突；旧颜色、线宽和显隐键迁移到领域样式键 |
| v13–v16 线路索引 | 来源指纹一致且基础表完整时，就地补建语义索引与端点索引，失败保留旧版本标记以便重试；来源变化或结构不完整时从源库原子重建 |

新样式由 `railway_class`、`line_role`、`track_role` 解析，不再把复合中文标签当作领域事实。线路视角与车站设施视角分离；目录分类和颜色调整不会改写物理路径。

## 验证与性能

- Python：`backend/tests` 与 `desktop/tests` 共 **358 项通过**；有 1 条第三方 Starlette 弃用警告。最终代码调整后又单独验证索引升级与 V2 存储 **6 项通过**。
- 桌面 JavaScript：`node --test desktop/tests/*.cjs`，**2 项通过**。
- 前端：`npm test -- --run`，**1 项通过**；`npm run build` 通过。构建器提示现有主 bundle 较大。
- 分页目录用 3000 条合成记录验证每页最多 128 行、收起分支释放 Qt 行对象；700 条记录验证目录操作、地图定位和工作区覆盖。没有对全国真实 PBF 做耗时/内存基准测试。索引升级仍需顺序读取来源轨道属性，时间随源边数增长；地图继续按视窗/RTree 加载。

仍须人工核验正式线路名称与归属、正线和站线的实际用途、车场/咽喉边界、正式股道号、运营控制点类型、实际接发车进路及信号/联锁资料。自动连续路径只标记为参考，不能称为真实调度进路。
