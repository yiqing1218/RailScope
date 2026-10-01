# 工程化站场图设计与现状审查

2026-10-01。目标：按用户给出的工程示意图共性完全替换默认 GIS 压缩布局，不改基础设施连接。

## 已核查的数据

主项目基础 3c832f9 已同步到本分支。旧入口为 launcher.export_station_schematic → StationDiagramDialog → station_schematic.station_svg → station_diagram_geometry。旧算法使用公共弯曲变换和分区轴向缩放；connected_systems 用最近正线传播颜色。两者不符合本次要求。

复用 railscope.domain 的 NetworkEdge、NetworkNode、StationTrack、Yard、InfrastructureLine 与 LineMembership；复用 station_tracks 的稳定 ID、站台关联、SQLite 空间和端点索引以及 workspace override。不新增业务模型。新模块的 DTO 只保存一次导出中的图面单位。

本地五站既有缓存抽查：249–606 条物理 edge，17–264 条股道对象；所有样本均缺 yard_id。站台包含 Polygon 和 LineString；站房轮廓不等同于站区边界。缺完整边界时使用已关联股道和真实站台范围作为图面选取参考，明确 warning，不制造真实站区。站区确有边界时先按 polygon 完整提取铁路，不将空间相交当接轨。线路所属未明确的站线保留灰色。

## 模块与不变量

- extractor：真实站区/站台/股道对象选取，区外仅按节点遍历。
- topology：稳定节点邻接、连通分量、真实分歧、degree-2 链；永不按距离或相交造节点。
- yard_classifier：显式 StationTrack/edge yard 与业务线路、有效 LineMembership、人工覆盖，冲突保持 unresolved。颜色不沿网络传播。
- layout：站台核心横直股道、统一间距；按真实节点次序压缩左右咽喉，所有关联 edge 共享同一节点图面坐标。
- connection_layout：degree-2 站外同归属链使用统一 cubic 模板，按曲线子区间保留每个 edge；分出点/汇入点是真实节点。
- direction_layout：沿真实接轨主线计算站外方向，按圆周方向与相对顺序放置外端口；不复制单线为双线。去向缺证据时提示待核对。
- renderer：统一默认线宽，明确归属颜色，标准道岔、站台、站场名、收束线路名、边缘方向文字和图例；不推断拓扑。
- export：同一 SVG 绘图导出 SVG/PDF/PNG，保留来源/快照/状态/节点端点/归属决策/warnings。

缺少归属不阻断参考图导出，但在预览前后的警告区与 SVG metadata 明示。缺真实 endpoint 或股道引用、归属指定无效 ID 则拒绝。自动结果始终为 automatic_reference_not_dispatch_verified。真实来源几何及 Corridor/StationRoute/TrainRun 不改写。

股道编辑增加分场名称、铁路体系与业务线路，保存到现有 station_track override；分场 ID 由车站稳定 ID 和人工名称派生，并保存人工来源。导出设置保留已有调用接口，退休 GIS 去弯、GIS 比例和按类别线宽的控件。

铁路体系使用共享领域的 railway_class；城际场等分场类型使用 Yard.yard_type，避免另建一套分类。人工明确清空归属时保持待核对，不重新覆盖为源参考线路。图面相交但无共享节点时使用断开符号，仅表示图面无连接，不推断实际立交层级。

## 验收

拓扑端点与原图完全一致；不把非接轨交叉变成道岔。A 正线分出、B 股道引出、C 联络另一正线、D 外侧改变顺序均有断言。人工归属胜过源参考，歧义不着猜测色。图面股道间距一致、站台区水平、颜色不因 GIS 横向移动改变。实际生成并查看真实站输出，核对统一线宽、标准曲线、方向端口以及三种格式。

## 2026-10-01 收尾记录

按用户要求，今天停止新增功能，保存阶段实现。完整 pytest：354 passed（40.65 s）；新模块与修改的站场组件 Ruff F 检查及 compileall 通过。回归中恢复了同步主项目时被覆盖的通用对象改名通知接口和 .mjs 访问规则。测试的临时目录使用 data/logs 下的独立路径，避开系统临时目录访问限制。

从当前本地 SQLite 重新读取并输出义乌、南京南、上海虹桥；分别选取 203/462/253 条真实 edge，识别 18/23/33 根核心股道和 9/8/10 组端口。原始仓库对象未修改；原始 SQLite 的大小和修改时间未变；端点稳定 ID 保持不变。图面端点坐标采用 1e-7 绘图单位的浮点容差。

本地示例与重放缓存在 data/logs/station_engineering_actual/，由 .gitignore 排除；没有提交源 OSM、SQLite、GeoJSON 或可再生 GIS 数据。脚本 scripts/export_engineering_station_examples.py 可从指定 dataset 复现三种格式。仅在当前工作树保存，未推送、未替换 D:\RailScope 主检出。

待继续核对：真实站数据普遍缺少完整站区边界、明确 Yard 归属和部分去向，当前明确 warning，不能把参考图当作已核验站场图。复杂站场的密集咽喉、长联络线和标注仍需结合真实资料逐站检查，当前示例不是对所有车站的最终图面验收。

最后一次看图中，南京南左右咽喉仍有节点过密和局部曲线折返；上海虹桥部分股道区仍出现倾斜连接穿过核心区。先按本次停止要求保存，后续需要继续优化这些布局并核对股道编号对应关系，不能仅以自动测试通过认定图面达到示例标准。
