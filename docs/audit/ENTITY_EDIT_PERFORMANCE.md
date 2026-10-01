# 对象编辑 / 保存性能专项报告

日期：2026-09-30；修复基线 `c61f003`；分支 `codex/entity-edit-performance-ui-audit`。

## 结论

卡顿的主因不是 PBF：每次属性保存重写整个大型人工覆盖 JSON，随后多个 broad signal 触发目录 / 库 / 视口重复刷新。打开线路编辑器还提前查询了没有使用的关系页。

车站“保存不了”复现为分页模式使用旧版 `station_items`，抛出 AttributeError：覆盖文件已经写入，但真实 materialised record 和界面没有更新。再次显示仍使用旧 record，用户看到修改失败。这不是名称作为主键或 Repository 拒绝写入；本次复现没有发现重新解析 PBF。

本次改为按 owner SQLite 事务、字段级依赖事件、常驻地图表示增量更新与按需关系加载。统一设施模板、对象类型、坐标精度、地图联动和 Undo/Redo 保留。

## 数据与方法

全国内部数据：`rail.sqlite` 3,163,963,392 B，`rail_lines.sqlite` 2,703,171,584 B，目录库 before 479,928,320 B / after 485,822,464 B（同一快照的派生缓存），总计约 6.35 GB。原始下载源为 OSM PBF，编辑使用内部库。

脚本 [benchmark_entity_edit.py](../../scripts/benchmark_entity_edit.py)调用生产 Qt 编辑器 / 保存槽，对各阶段用 `perf_counter` 计时，捕获 Qt 异常、检查原 record 是否改变、检查有效持久化值、记录地图命令，并安装 Python 文件打开审计钩子统计 `.pbf`。源码基线经 `git archive` 在独立目录运行，不切换当前分支。

测试沙盒共享只读几何 / 线路数据；可写目录库通过 SQLite backup 获取一致快照，复制设置与目录种子，只修改沙盒。早期裸文件复制在 GUI 写缓存时曾取得不一致副本，现脚本已改用 backup；原目录库经 `PRAGMA quick_check` 返回 `ok`。不删除 / 修改原始 PBF，不覆盖用户人工改名文件。

三个 station source alias：`node/10003809933`、`node/10010678106`、`node/10011281789`；线路：`RL-003763f8c776e2dfa17a`。这些 station alias 是兼容显示 owner，不是新的业务 ID。

完整原始结果：[before](entity-edit-before.json)、[after](entity-edit-after.json)。改名三样本；其他动作一例，不能据此声称全国所有对象的 P95。测试顺序相同，初始准备独立计时。

```powershell
$env:PYTHONUTF8='1'
python scripts/benchmark_entity_edit.py --count 3 --source-ref c61f003 --output docs/audit/entity-edit-before.json
python scripts/benchmark_entity_edit.py --count 3 --output docs/audit/entity-edit-after.json --capture-directory docs/audit/ui
```

地图使用 MapStub 记录真实 Python 侧调用边界，排除 MapLibre / GPU paint 和完整活动 TrainRun。HTTP 命名 / 样式回放另有真实 localhost 集成测试，JavaScript 常驻源 / 排队 / 视口竞态另有 Node 测试。完整 WebEngine 捕获出现图形上下文丢失，不能把保存槽时间标成屏幕最终出图时间。

## 修改前后（毫秒）

| 动作 | 修改前 | 修改后 | 解释 |
|---|---:|---:|---|
| 车站改名：保存点击，3例 | 3,124 / 2,946 / 3,027 | 32.2 / 21.9 / 23.9 | 前3例均抛异常，文件已写但 record 未更新；后3例全部一致 |
| 车站：打开 / 改名 / 保存自动流程 | 3,208 / 4,421 / 4,703 | 162 / 77 / 124 | 不包含人工停留时间；沙盒运行库已准备 |
| 精确 station 查询，3例 | 2,405 / 277 / 214 | 43.3 / 37.1 / 39.4 | 原首次全目录加载；另有 aliases 全表扫描 |
| 车站普通备注 | 2,441 | 12.9 | 原返回异常，后保存一致；零地图 / 目录工作 |
| 线路普通备注 | 7,304 | 15.1 | 消除序列化与多个 broad 刷新 |
| 线路改名 | 3,003 | 22.8 | 目录、搜索和 Label 局部更新 |
| 线路颜色字段 | 6,029 | 14.5 | 只更新该 owner 表示；新 Renderer 回放测试通过 |
| 线路线宽字段 | 5,392 | 15.1 | 不处理 geometry / RTree |
| 线路：打开 / 改名 / 保存自动流程 | 6,655 | 55.0 | 关系页延迟后台加载 |
| 车站类型：编组站 | 55,724 | 391 | 原全量设施缓存重建且返回异常；现子树移动 / 计数 / 可见性 |
| 覆盖层单次写盘，全部样本范围 | 2,154–3,277 | 12.5–23.8 | 原全 JSON；后 touched-owner SQLite 事务 |

车站改名保存中位数从 3,027ms 到 23.9ms，约 127 倍；线路备注约 482 倍。单例倍率只用于说明本次样本，不作为普遍保证。文件系统竞争可造成偶发延迟；早期并行采样曾出现单次 356ms，因此不能宣称任意磁盘条件下一定低于 33ms。

冷准备：原构造 58.28s，修复后 56.63s；设施缓存准备分别 56.45s / 54.36s。此部分没有被消除，本次移到 QThread 的响应式准备流程，有进度和错误反馈，仍需等待完成。沙盒缓存签名缺失 / 变化时触发，不能算成每次编辑成本。首次运行库约 0.90s，也独立于热编辑；未改字段不再使它反复失效。

## 实际调用链定位

### 修复前

1. 打开：精确车站也先 `load_directory`；线路打开直接 `line_relationships`，连不打开的关系页也计算。
2. 表单保存：连接 selector 在普通保存中重新生成部分连接，连带锚点与关联变化。
3. Entity / 项目：`CatalogWorkspace.update` 复制 / 合并完整覆盖集合；`write_json_atomic` 2.15–3.28s，形成主要主线程阻塞。
4. 车站缓存：SQL 名称已更新，然后 `_refresh_station_items` 访问分页模式没有的 `station_items`；抛异常，真实 record 未更新。
5. 线路目录：普通备注进入 `_update_paged_directory`，本次样本 0.63–0.94s；并非每例都重建整树，但做了无必要的目录 / 签名 / 可见性工作。
6. 信号：`metadata_changed` 使 `line_library` 失效、signal boxes / 名称刷新。样本反复重建库 1.04–1.67s，并触发 `reloadRailViewport`、`reloadMetroViewport`、`reloadRoadViewport`、`push_corridors`；有活动计划还重建 canonical repository。
7. Label / 索引：改名 SQL 本身约 8–19ms，并不是保存主因；空间 RTree 没有因改名重建。错误是把名称变化升级为整个视口重查。
8. 类型修改：`populate` → `sync_station_catalog` 完整扫描设施归属约 52s，导致一次类型属性修改长时间卡顿。

上述均有生产路径、计时和异常证据；没有假设“PBF 太大”或通过减精度换速度。

### 修复后

`校验字段 → CatalogWorkspace touched-owner 事务 → 真实 record / 有效 override → touched-owner Undo → dirty → entities_changed 字段 delta → 实际依赖 UI`

持久化必须成功，才改变内存与历史；Undo/Redo 用同一事务反向更新。新增 key 的撤销以 tombstone 防止种子复活；被引用的旧 assembly 仍保留 inactive 身份规则。

分页车站先更新真实 record，与是否存在旧 Qt tree 无关。普通备注不重建树 / 模型、不更新搜索、不发送地图命令；改名更新对应 SQLite 标题、搜索字段、已加载行和相关 Label。精确 station 查询用 PK source_id，aliases 查询从 `SCAN a` 改为 `SEARCH a` 索引查询。

线路普通属性保持 live library metadata，避免重新读取整份覆盖；目录改名只更新相应 row。线路关系两个页仍存在，首次访问才后台准备。连接未改返回 None，不改变固定锚点；改动连接保留旧合法固定项。

地图浏览器只索引常驻视口 owner；从 localhost 请求相关 feature 的当前权威 presentation，坐标不传回改写。MapLibre `setData` 以 source 为单位，所以提交受影响 source 的当前视口集合是剩余成本，不能说做到 GPU 逐对象绘制。过期响应忽略，队列合并不会丢掉另一对象修改 / 删除。

## PBF 是否参与日常运行

| 场景 | 原始 OSM PBF | 实际使用 |
|---|---|---|
| 编辑属性 / 打开对象 | 不访问、不解析 | 内部 SQLite、分页目录、现有覆盖视图 |
| 保存对象 | 不参与 | `.edits.sqlite` 事务；必要派生目录 row |
| 地图刷新 / 搜索 | 不依赖原始 PBF | SQLite + RTree / 查询索引与内部 presentation |
| 下载 / 首次导入 / 用户重新导入 | 参与 | `desktop/data_install.py` 与导入读取器，生成快照 |

before / after 的 Python 文件审计记录均为 `pbf_accesses: []`，结合调用链确认原始 OSM PBF 的边界。地图可能请求字体 glyph `.pbf` / 矢量瓦片 protobuf，它们不是 1.5 GB OSM 原始数据库；测试审计不覆盖浏览器网络请求。

架构已经是 `OSM PBF → 导入解析 → 内部 SQLite / 拓扑 / 目录 → 日常显示、搜索、编辑`。无需迁移 GeoPackage；现有 SQLite + RTree 合适。本次只为人工编辑持久化增加小型 SQLite sidecar，避免整个大 JSON 重写。

## 可以消除与只能降低

可以消除：普通字段全量 JSON 写盘、无必要全目录刷新、三个视口连带重载、库重复解析、未用关系页预加载、名称引起空间索引工作、未改连接重复推断，以及分页 station_items 异常。

只能降低 / 不应删除：首次导入与设施归属缓存准备、真实结构 / 连接 / 成员变化的领域校验、目录移动的祖先计数 / 模型布局、活动车次显示名称的实际依赖更新、当前视口 source 提交、磁盘事务同步。车站类型当前约 0.39s；复杂结构编辑仍保留原有依赖重建，未宣称所有此类动作即时完成。活动 TrainRun 名称更新仍扫描当前活动计划，其大负载需下一阶段依赖索引与完整 GUI 测试。

## 数据完整性与验证

- 不更改 geometry 精度、实体数量、对象类型或设施模板；Map 增量更新只改 presentation；坐标保持测试通过。
- 原 JSON 种子和 OSM 派生源保留；新编辑在 `data/user_settings/rail_catalog.edits.sqlite`（跟随指定工作区路径）。备份 JSON + SQLite；交换导出有效覆盖，包含颜色 / 宽度。不要删除 sidecar 来回退版本。
- `.edits.sqlite` 是现有覆盖层的 Repository 持久化格式，不是第二套 Corridor / TrainRun 模型。
- 事务失败、损坏 SQLite、种子覆盖、tombstone、重开、Undo/Redo、地图命名 / 颜色回退、视口竞态与队列合并有专门测试；既有目录 / 线路成员 / 计划 / 域约束测试继续保留。
- 最终完整 suite：277 passed（24.46s）；Node：5 passed；compileall 与 JS 语法检查通过；完整 WebEngine / GPU、原始 1.5 GB PBF 再导入、全国所有站点及完整活动列车负载未在本次逐一验证。

没有为了提速删功能、降低精度或损失源数据；新增持久化格式需要在备份 / 回退时携带增量库，是必须明确的兼容注意点。

另有保护：启动只重放增量库触及的目录名称，恢复“事务已成功、缓存尚未刷新”时的旧缓存；canonical Repository 不再静默忽略损坏覆盖库；MapLibre picked feature 的 JSON 字符串 base 可在 Undo 前正确还原。
