RailScope 上海地铁 4 / 5 / 6 / 11 / 12 号线公开资料重建包

目标：yiqing1218/RailScope
格式：railscope.operating-plan.v2
所有结果 official=false。

使用：
1. RailScope 先完成“数据源 → 自动下载 / 更新全国地铁…”。
2. 把本包三个文件放到 RailScope 根目录。
3. 第一次先执行4号线环线补丁：
   cd "你的 RailScope 根目录"
   python patch_railscope_line4_loop.py
4. 生成：
   python generate_railscope_metro_4_5_6_11_12.py
5. 输出：
   data/processed/operations/public_reconstruction_4_5_6_11_12/
6. 在 RailScope 地铁运行计划菜单导入生成的 JSON。

线路内容：
- 4号线：完整内圈/外圈，一圈分别约63/64分钟；工作日早高峰内圈4分钟、外圈6分40秒，
  平峰约8分钟、晚高峰约5分钟；周末高峰约6分30秒。
- 5号线：莘庄—奉贤新城 + 莘庄—闵行开发区两支线。公开分段间隔可直接通过两支线叠加配平：
  早高峰225s + 450s => 共线150s；晚高峰270s + 540s => 共线180s。
- 6号线：全程港城路—东方体育中心 + 巨峰路—高青路小交路。
- 11号线：花桥/嘉定北两支线，工作日高峰加入罗山路、南翔短交路的保守重建；
  平峰两支端各12分钟叠加为共线6分钟，周末高峰各10分钟叠加为共线5分钟。
- 12号线：全程七莘路—金海路 + 工作日高峰虹梅路—巨峰路小交路。

4号线补丁：
RailScope 当前地铁站序是线性的，完整环线需要“起点在末尾再次出现”。
补丁只在确认4号线OSM path首尾闭合时，把第一站复制到station list末尾，
distance_m设为整圈总里程；不修改OSM原始数据。
自动备份 desktop/metro_data.py。
恢复：
python patch_railscope_line4_loop.py --restore

真实性：
2026官方首末班逐站表是真实公开数据；平均间隔来自公开运行间隔资料。
平均间隔内的具体发车秒点、11号线高峰交路相位、真实车底/出入库/折返股道仍属于重建，
不能称为官方ATS运行图。
