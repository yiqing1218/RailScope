# 目录增量编辑与分层持久化完成报告

日期：2026-09-30。分支：`codex/entity-edit-performance-ui-audit`。依据用户批准的 [四阶段方案](DIRECTORY_REFACTOR_IMPLEMENTATION_PLAN.md)实施；原审计与修改前证据保留原样。

## 结果与真正根因

普通目录移动、车站属性保存、线路展示属性和人工设施归属现在按字段 / 对象更新。统一设施详情模板、地图联动、Undo/Redo、铁路样式语义和几何精度保留。概览统一改为名称在上、内容在下；名称灰色 `#526775`、内容深色 `#20313d`，长编号完整换行。见 [真实 Qt 组件截图](property-overview-after.png)。

两个审计阶段定位的是不同的剩余问题：

| 问题 | 实际根因与证据 | 修复 |
|---|---|---|
| 原车站保存后显示旧值 | 覆盖层已写入，但分页模式仍访问不存在的旧 `station_items`，抛出异常，真实 record 没更新 | 写盘成功后更新真实 record 和相关视图；失败不推进历史 |
| 原普通属性保存数秒 | 全量人工覆盖 JSON 序列化；多个 broad signal 连带目录、库、地图刷新 | touched-owner SQLite 事务、字段事件和常驻地图表示更新 |
| 后续目录移动仍约半秒 | 每次重新扫描约 24 万条 override，计算目录 / 车站缓存签名 | typed revision 和工作区身份构成常量长度失效 token |
| 设施目录移动约 59 秒 | 含路径的设施节点集合变化误判为 ownership 变化，进入完整 `sync_station_catalog` | 单独比较实际设施 membership；纯目录移动不推断归属 |
| 股道归属约 39 秒 | `populate`、全国 ownership 重建、宽泛通知导致线路库 / 地图 / 通道刷新 | indexed assignment overlay，只更新相关旧 / 新站区投影 |
| 合并线路成本随成员增长 | 两层 assembly 展开扫描全部 override，相同属性写给全部成员 | 逻辑 owner 保存一次共享属性，成员读取继承 |
| 一个同名目录成员仍慢 | 删除成员后重新解析同组其余 2,088 个对象来拼接搜索文本 | 成员搜索表、索引查询与局部计数，不重新解析无关成员 |

前两项详细复现与原始属性计时见 [对象编辑报告](ENTITY_EDIT_PERFORMANCE.md)。本轮目录根因、原调用次数与嵌套计时见 [架构审计](DIRECTORY_PERSISTENCE_ARCHITECTURE_AUDIT.md)和 [修改前 JSON](directory-edit-audit.json)。嵌套耗时不能相加。

## 原调用链与新调用链

原普通目录：`move_items / save_station_changes → 局部写盘 → 扫描 override / 重算签名 → 全目录 Model reset / selection 同步`。设施路径还会进入 `sync_station_catalog → 全车站与设施归属推断 → 清空重建投影`；人工股道归属经过 `populate / metadata_changed → library 失效 → 无关地图 reload / corridor 刷新`。

新链路：`字段校验 → workspace touched transaction / command delta / revision → 发布有效 override → affected membership 与 old/new ancestors → 已加载 Qt 节点 → 对应 typed 通知`。Undo/Redo 执行同一局部持久化和投影路径。跨父节点移动后，通过稳定节点 / 对象标识恢复多选、焦点和展开，恢复过程不触发地图选择副作用。

完整 reset / 全国构建保留给真实 schema、来源或算法失效，以及明确的拓扑操作；普通目录命令不调用它们。保存按钮不导出整个项目，也不重处理 PBF。

## 六层数据和持久化

| 层 | 数据 / 权威职责 | 本次普通编辑行为 |
|---|---|---|
| Source | 原始 OSM PBF，导入来源 | 不打开、不解析 |
| Geometry | `rail.sqlite`：物理几何、NetworkEdge 表示、RTree | 名称、备注、目录、颜色、线宽不改几何 / 空间索引 |
| Topology | `rail_lines.sqlite`：线路与端点关系、station alias、路径索引 | 纯目录 / 展示 / 目录用途 assignment 不失效 |
| Catalog | `rail_catalog.sqlite`：来源业务投影及可重建目录 / 车站缓存 | 使用本次 owner 的索引查询，局部投影 SQL |
| Workspace | `rail_catalog.workspace.sqlite`：人工修改、身份别名、分层 revision、command history | touched-owner 事务，失败不发布内存 / 历史 |
| Presentation | Qt 分页目录、统一详情、HTTP feature 属性、MapLibre 常驻源、运行显示 | 按 changed fields 和实际依赖更新 |

继续使用现有 SQLite / RTree，无须另换 GeoPackage 或重新读取 1.5 GB PBF。工作库分为 `object_overrides`、`object_aliases`、`directory_folders`、`directory_membership`、`presentation_overrides`、`station_assignments`、`line_assemblies`、`assembly_members`、`revisions`、`command_history`、`migration_conflicts`。

已有运行领域 `workspace.sqlite` 属于 canonical Repository，故目录 adapter 使用 **`rail_catalog.workspace.sqlite`**，避免覆盖原运行库。两者复用 `railscope.domain` / identity 契约，不新建第二套业务对象。领域引用继续用稳定 RailScope ID；遗留 station / object owner 是兼容来源别名，`object_aliases.domain_id` 未解析时可空，不能把列名当作已完成全部身份迁移。名称只是可编辑属性。

迁移按原 shared / local / seed / sidecar 优先级读取旧 JSON 和 `.edits.sqlite`，只读旧库，备份到 `legacy-workspace-backups/<时间戳>`，新库事务成功后原子发布。未知字段、tombstone、来源证据、membership 和冲突记录保留。旧成员重复属性暂留兼容证据，新共享编辑以逻辑 owner 为准，不再复制给 M 个成员。JSON 保留导入 / 导出 / 交换用途；备份须包含工作库和来源 / 身份映射。

人工 assignment 与原始 / 推断 ownership 分开。单股道更具体的人工指定优先于组归属；稳定车站 ID 保存在工作库，来源别名用于定位目录投影。新来源快照重新生成 baseline，避免撤销回到旧来源归属。没有原始证据的旧完整投影接纳明确标记为 `source_verification_required`，不伪称为已核验来源。

## 精确刷新规则与 artifact

| 变化 | 必要工作 | 不发生的工作 |
|---|---|---|
| 站名 / 线名 | Entity / override、对应目录标题、搜索、相关 Label / 名称显示 | 全国目录、几何、空间索引重建 |
| 备注 / 普通字段 | 对应 owner、属性面板、dirty / Undo | 地图工作、搜索 / 空间索引重建 |
| 颜色 / 线宽 | presentation owner、该线路常驻 Renderer | geometry / topology 重算 |
| folder_path | membership、旧 / 新父节点与祖先计数 / 搜索、已加载 Qt 分支 | ownership 推断、library 失效、地图 reload、corridor 重算 |
| 人工站区归属 | assignment overlay、相关站区 / 设施 / 股道投影 | 隐式改写运行路径或基础设施 ID |
| 类别 / 用途 | semantic revision、受影响目录 / Renderer / 路径合法性依赖 | 无关道路 / 地铁地图重载 |
| 真实 geometry / 拓扑 / 成员变化 | 原有显式领域命令、引用检查、必要几何 / 路径刷新 | 不能借普通属性保存绕过引用检查 |

`directory_changed`、`presentation_changed`、`semantic_changed`、`station_assignment_changed`、`topology_changed`、`operation_changed` 分别通知。旧 `metadata_changed` 保留兼容接口，主窗不再订阅它做多项重量刷新。路径库失效 token 仅依赖工作区身份与 source / geometry / topology / semantic revision，不依赖目录、备注或显示颜色的 mixed mtime。

新 / 升级的线路索引、目录 / 车站投影、省域产物记录 schema、algorithm、输入记录 fingerprint、created_at、dependency revisions 和 complete。fingerprint 是输入记录的摘要，普通编辑不会 hash 数 GB 文件。已完整且通过原版本 / 来源检查的旧产物兼容复用；旧省域文件无 manifest 时保留版本检查。这里没有宣称所有历史产物已经补齐全文件内容 hash。

## 全国样本复测

工具：[audit_directory_edits.py](../../scripts/audit_directory_edits.py)。原始结果：[directory-edit-after.json](directory-edit-after.json)。计时代码版本：`347cef1`，应用修复版本：`b3048d0`。

使用真实 Qt 保存 / 目录槽、实际 launcher 订阅和 SQLite；源 catalog 通过 SQLite backup 获取一致私有副本，几何 / 拓扑强制只读。合成测试把无关 overlay 注入放在计时之外。以下修改前来自原审计单次样本，修改后为最终同类操作的首轮和 5 次强制不同目标的重复测量；小样本不能证明全体对象 P95。

| 操作 | 修改前单次 ms | 修改后首轮 ms | 修改后重复中位数 ms | 重复经验 P95 ms（n=5） |
|---|---:|---:|---:|---:|
| 移动 1 条线路 | 687.6 | 170.3 | 66.4 | 72.1 |
| 移动 1 个车站 | 543.1 | 50.3 | 46.3 | 48.2 |
| 移动 100 条线路 | 1792.2 | 1392.5 | 1063.2 | 1084.8 |
| 移动 100 个车站 | 1469.8 | 185.1 | 139.8 | 270.0 |
| 移动设施目录 | 58688.1 | 149.7 | 67.2 | 68.1 |
| 指定 1 段轨道归属 | 38803.1 | 51.7 | 46.3 | 49.5 |

经验 P95 用 nearest-rank 计算，5 次样本时等于本次最大值，仅描述这些样本。计划的 <50 ms 中位数目标在本轮车站 / 轨道 assignment 达到，线路 / 设施目录约 66–67 ms，未全部达到；首轮线路 / 设施还出现 150–170 ms。不会把早期较快结果替换为最终结果。

目录对象 700 → 7,000（无关 overlay=0），单对象 18.8 → 21.2 ms；固定 700 个对象，无关 overlay=0 / 1,000 / 10,000 / 100,000 时分别为 18.8 / 23.9 / 21.4 / 24.1 ms。这些样本与调用断言共同验证没有全国 / 全 override 扫描，不能解释为任意磁盘下的延迟保证。

Assembly 固定总 overlay=10,001，members=1 / 100 / 1,000 / 10,000：每次展开 / 持久化均只有 **1 个 owner**；每种 3 次。提交中位数依次为 7.93 / 7.58 / 8.05 / 7.51 ms，扩展解析全部 <0.06 ms。

全国首轮单对象 workspace owner 写入均为 1；线路目录改 25 行、车站 16 行、设施 23 行、单轨道 assignment 26 行。100 条线路 / 100 个车站各写 100 个 owner，目录 / 车站投影分别改 968 / 1,068 行。单线和单站 Undo/Redo 也已采样。全部 40 个全国首轮 / 重复操作无全国构建、全表 DELETE、Model reset、library invalidate、无关 map reload 或 corridor 推送；地图命令为空。

本次隔离初始化 **98.30 秒**，包含私有工作区首次迁移 / 缓存准备和首个线路库，不计入保存按钮时间。数据包含 87,451 个 catalog 对象、134,676 个车站投影节点、32,803 个轨道归属、243,473 条有效 override。原始几何 / 拓扑合计约 5.87 GB。全国冷准备成本仍然存在，本轮没有把它声称为零。

必要成本主要是 touched SQL commit、K 个受影响 owner / ancestor 与当前已加载节点 reconciliation。批量 100 个对象仍有实际批量工作和磁盘竞争，不能声称每批都低于一帧。

## 完整性与验证边界

几何 / 拓扑文件大小、mtime 与完整 SHA256 前后一致；私有 catalog 来源表按排序后的 id / data 计算 SHA256，前后一致。原程序在测试期间并发写了自己的 `rail_catalog.sqlite` 派生缓存，因此原目录库的全文件 mtime 并未保持不变（JSON 明确记录 `external_mutable_projection_changes`）。诊断只写隔离副本，不能把可变缓存字节比较混为几何或业务完整性。

单独加载实际已安装运行计划：**83 个 TrainRun、12 个 Corridor、16,798 条 NetworkEdge**。线路 / 车站目录与人工轨道 assignment 的 Undo/Redo 前后，geometry、连接 / 来源别名、完整有向路径、车次 / 停站等领域摘要一致：`3e72fa0a9a1ff44c5d7b42638d211033e81729216eab8f640b52c71cb1b17380`；计划 document 完全一致，Repository 校验通过，live line library 对象保留。此真实计划没有 StationRoute（0 条）；StationRoute 引用不变量由完整测试中的相关 fixtures 覆盖，不冒充真实计划中已测。

全国目录测量中 PBF 打开会直接报错，全部样本无该错误。打开 / 保存 / 地图查询 / 搜索使用内部数据；PBF 仅保留导入或明确重新生成来源阶段。

完整项目 `python -m pytest -q --basetemp=.pytest_final_full`：**297 passed，51.93 秒**；地图增量 `node --test desktop/tests/entity_presentation.test.cjs`：**6 passed**；compileall 和 `git diff --check` 通过。新回归覆盖迁移 / 回滚 / 重启、typed revision、局部 SQL / 搜索 / 计数、分页、assembly 恒定 owner 写入、归属优先级 / 新快照撤销、稳定 ID 来源解析、多选 / 焦点 / 展开，以及统一概览长值。

独立全分支审查的六项问题全部修复，并先观察对应失败回归：HTTP 普通 dict 的 assembly 名称 / 样式继承；组归属覆盖单股道人工归属；稳定 ID + 来源别名定位；新来源 baseline 更新；单逻辑 owner Undo 的 visible count；跨父节点移动的选择 / 焦点。扩展回归还验证共享设计速度和展开车站内的已选股道。

计时使用 MapStub，不包含 MapLibre / GPU 最终出图。实际已安装运行计划的领域完整性单独核查，不把它算作播放 / 浏览器负载测量。真实 Qt 概览组件已截图检查；完整 WebEngine 在本环境出现过图形上下文丢失，仍需要真实桌面绘制验收。没有通过降低数据精度、关闭联动、取消 Undo/Redo、删除设施类型或要求手动刷新来获得数字。

## 已消除、仍需成本与后续范围

完全消除普通命令中的全量 override hash、全项目 JSON 保存、全国设施归属重推断、无关地图 reload、线路库无必要失效、同名组全成员搜索解析，以及 assembly 共享属性的 M 份复制写入。

只能降低必要事务提交、实际 K 个受影响对象和常驻地图 source 的提交成本；首次迁移、导入、真正算法 / schema / 拓扑失效的全国构建仍有成本，以缓存复用和明确失效处理。未引入线程、防抖或延迟刷新来掩盖本轮目录计算。

未实施的全局 CommandRegistry / SelectionController 提取、完整 Dock / Workspace、菜单重组、全套符号 / DPI 视觉回归仍按 [UI 路线](../../REFACTOR_ROADMAP.md)推进。现有兼容别名的全部领域 ID 绑定和历史来源核验也仍有后续工作。

## 核心文件、五层对应与本地提交

- 持久化：`desktop/workspace_sqlite.py`、`catalog_workspace.py`；投影：`rail_catalog_model.py`、`rail_station_catalog_model.py`、`lazy_directory.py`。
- 共享属性 / 归属：`rail_line_workspace.py`、`rail_facility_ownership.py`、`display_names.py`；typed 分派：`rail_catalog_ui.py`、`launcher.py`、`rail_ui.py`。
- 中间产物：`artifact_manifest.py`、`rail_line_store.py`、`rail_catalog_index.py`、`provinces.py`；统一概览：`property_overview.py`。
- 功能、数据、代码、UI 信息位置和视觉规则对应更新到根目录七份文档：`PRODUCT_OVERVIEW.md`、`FEATURE_SPECIFICATION.md`、`FEATURE_ARCHITECTURE_MAP.md`、`UI_UX_AUDIT.md`、`UI_ARCHITECTURE_PROPOSAL.md`、`DESIGN_SYSTEM.md`、`REFACTOR_ROADMAP.md`。

独立本地实现提交：`1a4613d` 分层增量库；`0a4af0d` 局部目录 / 车站投影；`1f2cbe7` assembly 与归属；`7cb74f7` 上下概览；`30d9037` typed 依赖 / 同名搜索；`b3048d0` 审查修复与分色；`347cef1` 采样隔离。未提交 `.env`、原始 OSM 或可再生大型 GIS 数据。保留当前分支，不合并、不推送。

实施判断记录：原方案是四阶段文字，使用等价测试 / 提交账本执行，代价是人工维护记录；兼容来源 owner 保留，领域 ID 由现有 identity adapter 绑定，代价是身份完全统一仍需后续迁移。无本轮审查遗留的 Important 问题。
