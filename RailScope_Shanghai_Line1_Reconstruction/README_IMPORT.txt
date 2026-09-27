RailScope 上海地铁1号线“公开资料重建运行图”数据包
=================================================

适配项目
--------
GitHub: yiqing1218/RailScope
分支/代码基准: feature/desktop-workbench
导入标准: railscope.operating-plan.v2

为什么不是直接给一份硬编码 JSON
------------------------------
RailScope 对地铁运行计划的 station_id 和 distance_m 做严格校验：
- station_id 必须是你本机 OSM 导入结果里的车站节点 ID；
- distance_m 必须与 RailScope 本机派生的线路里程一致，容差只有 0.01 m；
- 上海1号线在当前代码里使用本地 line_id = sh-1，并优先选择 OSM Relation 199200。

所以最稳妥的方法不是猜 OSM ID，而是让生成器在你的 RailScope 项目里读取
china_metro_route_catalog.json / china_metro_routes.geojson /
china_metro_stations.geojson，再写出完全匹配你本机数据快照的 v2 JSON。

使用方法
--------
1. 先确保 RailScope 已完成“数据源 → 自动下载 / 更新全国地铁…”，地图里能看到上海1号线。
2. 把 shanghai_line1_railscope_generator.py 放到 RailScope 项目根目录。
3. 在 PowerShell / CMD 中切换到 RailScope 根目录后执行：

   python shanghai_line1_railscope_generator.py

4. 生成文件位于：

   data/processed/operations/reconstructed_line1/

   默认生成：
   - shanghai_line1_public_reconstruction_mon-thu.json
   - shanghai_line1_public_reconstruction_friday.json
   - shanghai_line1_public_reconstruction_saturday.json
   - shanghai_line1_public_reconstruction_sunday.json

5. RailScope 中进入“运行 → 地铁”，选择“导入运行计划”，导入相应 JSON。

也可以只生成一种：
   python shanghai_line1_railscope_generator.py --profile friday

数据内容
--------
- 28站完整站序；
- 莘庄→富锦路约65 min的分钟级时刻剖面；
- 富锦路→莘庄约64 min的分钟级时刻剖面；
- 05:30两端全程首班；
- 上海南站04:55→上海火车站05:19特殊首班；
- 上海南站05:18→富锦路06:12特殊首班；
- 上海火车站05:30→莘庄06:04特殊首班；
- 周一至周四：07:00–09:00按150 s，09:00–17:00按300 s，
  17:00–19:00按180 s重建；
- 周五：15:00–19:00按180 s，并给出延时运营近似图；
- 周末：09:00–20:00按240 s；
- “其他时段”公开资料只有区间，因此工作日取6 min、周末取8 min作为代表值；
- 周五/周六22:30以后公开间隔为约10–15 min，重建取12 min代表值。

真实性边界
----------
这不是申通地铁内部列车运行图，也不是ATS导出、司机时刻表或车底周转表。
逐车次发车时刻在公开资料只给平均间隔时，是按照平均间隔重建的。
特殊首班车的中间站时刻在缺少逐秒公开表时按已知端点和分钟级剖面重建。
文件固定 official=false，并在 source / extensions 中写明来源性质。

本版暂不声称恢复：
- 真实列车车底号；
- 每辆车一天的完整交路；
- 出入梅陇车辆段/富锦路停车场的精确时刻；
- 真实站前/站后折返进路；
- 临时加开、清客回库、故障、晚点、当日调度调整；
- 精确到秒的全日官方发车序列。

因此，这个数据包适合：
1. 先在 RailScope 里画出一张结构和密度合理的1号线运行图；
2. 做地图车辆动画；
3. 后续找到司机时刻表/ATS/PIS观测后，再逐条替换推定车次。
