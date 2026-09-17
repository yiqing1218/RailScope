# 合肥 S1 在建线缺口修复

截图机场侧与城区侧之间缺失的是合肥轨道交通 S1 线。旧规则仅接收 `railway=construction` + `construction=subway/light_rail/metro`，遗漏了明确在建、但城市轨道类型保留在 `proposed=subway` 中的轨道。

修复规则在 **已有 `railway=construction` 状态** 时，依次识别 `construction:railway`、`construction`、`proposed` 中的轨道类型。明确非城市轨道的类型仍排除，`railway=proposed` 也不会按在建线导入。原始标签不改写。

本次从 OSM API 读取原始节点与完整标签，补导四个 Way：

- [1055692403](https://www.openstreetmap.org/way/1055692403)：6 个节点。
- [1463181483](https://www.openstreetmap.org/way/1463181483)：2 个节点。
- [1055692404](https://www.openstreetmap.org/way/1055692404)：5 个节点。
- [859388432](https://www.openstreetmap.org/way/859388432)：57 个节点。

当前本机图层 S1 从 6 段增至 10 段，原本两个断开部分按原始轨道端点连为一个整体；全国在建要素总数 1002 → 1006。这里的「连接」只校验地图几何连续性，不等于实测轨道、实际施工进度或信号拓扑认证。

原始 PBF 不修改。修复前派生图层与清单备份在 `data/logs/hefei-s1-backups/20260917-080640/`，图层仍位于 `data/processed/osm/china_metro_construction.geojson`。新要素保留原节点顺序、所有 Way 标签、ID、版本、时间和几何来源 URL；清单的 `regional_repairs` 记录补导 ID 与备份位置。新轨道进入原有合肥 S1 在建工程目录，继承深灰虚线与目录开关。

```powershell
python desktop/repair_hefei_s1.py
python desktop/launcher.py --screenshot D:/RailScope/data/logs/hefei-s1.png --smoke-report D:/RailScope/data/logs/hefei-s1.json --verify-hefei
```

修复脚本限定线路名称与连接节点，从 OSM 原始端点查找漏导轨道；不会创建直线连接，遇到规划段或剩余缺口不覆盖当前图层，重复执行不重复添加。脚本需要网络；正常软件显示使用本地补导后的数据。未来重新从 PBF 导入会使用修复后的类型规则；如果旧 PBF 本身未含近期新增节点，需刷新 PBF 或再运行此补导脚本。
