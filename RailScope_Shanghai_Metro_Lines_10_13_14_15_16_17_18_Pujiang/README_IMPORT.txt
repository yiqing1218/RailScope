RailScope 上海地铁最后一批：10 / 13 / 14 / 15 / 16 / 17 / 18 / 浦江线

适配：yiqing1218/RailScope
格式：railscope.operating-plan.v2
真实性：全部 official=false。

使用：
1. RailScope 先完成“数据源 → 自动下载 / 更新全国地铁…”。
2. 将本包的 .py 和 research_spec.json 放到 RailScope 根目录。
3. 切换目录：
   cd "你的 RailScope 根目录"
4. 运行：
   python generate_railscope_metro_10_13_14_15_16_17_18_pujiang.py
5. 输出：
   data/processed/operations/public_reconstruction_final_batch/
6. 在 RailScope 地铁运行计划菜单导入相应 JSON。

本批特点：
- 10号线：分别读取“航中路—基隆路”和“虹桥火车站—基隆路”本地OSM方案，
  通过多交路叠加匹配航中支线、虹桥支线、龙溪路—江湾体育场及东段公开平均间隔。
- 13号线：大交路金运路—张江路 + 高峰小交路金运路—华鹏路。
- 14号线：大交路封浜—桂桥路 + 小交路真新新村—蓝天路，公开间隔可精确配平。
- 15号线：确认小交路双柏路—古浪路；高峰北段额外补充运行线属于数学配平推定，
  文件source会明确注明，不冒充官方交路。
- 16号线：普通车、大站车、工作日直达车同时编码。通过站仍作为RailScope连续拓扑控制点，
  arrival=departure，并在extensions中标记 user.local/pass。
- 17号线：当前西端为西岑；高峰大交路西岑—虹桥火车站 + 小交路淀山湖大道—虹桥火车站。
- 18号线：已经按2025-12-27二期开通后的“航头—康文路”全线处理，并加入高峰三种交路。
- 浦江线：单一沈杜公路—汇臻路交路，早高峰255秒、晚高峰300秒，其余600秒。

重要：
生成器会从你的本机 RailScope active_dataset 读取 station_id 与 distance_m，
并调用项目自己的 Plan.load() 校验。因此不同OSM快照不会因为硬编码里程而直接导入失败。

当前限制：
- 公开平均间隔不能唯一决定每一趟车的秒点；
- 10、15、18号线部分复杂交路相位属于重建；
- 不含真实车底号、车辆段出入库、司机交路、ATS调整和晚点；
- 10号线周日至周四定点夜班、周五周六延时运营等特殊日期服务暂未并入“普通weekday/weekend”文件；
- 16号线大站/直达车的通过站时刻由公开控制点和普通车时间剖面插值得到。
