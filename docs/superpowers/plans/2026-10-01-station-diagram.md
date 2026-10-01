# 工程化站场图 Implementation Plan

**Goal:** 将现有站场导出替换为拓扑优先、归属优先的工程示意图。
**Architecture:** 独立 desktop/station_diagram，旧入口作为兼容 adapter；共享 railscope.domain。
**Tech Stack:** Python、Shapely、PySide6 SVG/PDF。
**Spec:** ../specs/2026-10-01-station-diagram-design.md

## Global Constraints

只引用稳定 RailScope ID；源 geometry 不改写；全线默认线宽一致；不猜归属；全部输出保留 source/snapshot/verification_status/confidence。

## Review Focus

无站区边界；多个同名/同址股道；真实交叉但不接轨；站外分出/回环；端口密集和方向反序。

## Task 1: 提取、拓扑、明确归属

创建 extractor.py、topology.py、yard_classifier.py 与 types.py。extract(repo,context,options) 返回图面提取结果；build_graph(repo,keys) 返回邻接/degree-2 链；classify(repo,keys,options) 返回每条边归属和来源。增加 tests/test_station_engineering.py，先验证边界完整提取、空间交叉无接轨、缺失节点拒绝、歧义归属中性。读取 SQLite 时真实站区范围内所有铁路必须加入输入，不能只有 ST 分类。

## Task 2: 布局与方向

创建 layout.py、connection_layout.py、direction_layout.py、pipeline.py。build_layout(repo,context,options) 返回兼容 DiagramLayout 并保存标准路径。站台核心横直且等距；共享节点坐标；外端口按真实方向；联络链标准 cubic。测试四类结构、端点精确一致、源不变、方向重排和无双线复制。

## Task 3: 渲染、导出与编辑

创建 renderer.py、export.py。旧 station_diagram_render / station_diagram_layout 调用新实现；StationDiagramDialog 显示 warning 和统一线宽，移除过时 GIS 控件。StationTrackDialog 编辑明确站场/业务线路并通过现有 override 持久化。渲染端口箭头、收束线路名、站场色、道岔标记及数据审计 metadata。测试 SVG、PDF、PNG 和 Qt 控件。

## Task 4: 真实数据验收与提交

用本地真实数据生成至少三个不同结构的图，查看 PNG 并核对 metadata。相关测试全通过，检查 diff 排除原始 OSM/GIS 与敏感文件，中文本地提交。记录实际验证边界。

## 阶段状态（2026-10-01）

Task 1–3 已形成可运行实现；Task 4 的完整测试 354 项通过，三站真实数据示例已经生成。用户要求今天先停，保留当前阶段代码和样例，未继续扩大功能范围。真实资料缺项与复杂站场图面验收仍待下一次继续，见设计文档的收尾记录。
