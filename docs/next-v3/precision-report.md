# 全国源精度与济南镜头

指定中心为 `116.9003E, 36.0676N`，显示缩放为 Z12.9。检查原全国库这个中心对应的 Z12、Z13、Z15、Z17、Z19、Z21，精确交通瓦片为空。在检查范围内发现的邻近铁路位于约 `116.9058643E, 36.2033374N`，与指定中心相距约 15.1 公里。数值对照使用该邻近铁路，来源为 `osm:way:840021768`，稳定实体 ID 为 `9b430853-3225-55bf-97cb-c944865a1e20`。

| 指标 | 归档 Z8 放大至 Z12.9 | 动态 Z12 显示于 Z12.9 |
| --- | ---: | ---: |
| 对照片段内部顶点 | 4 | 42 |
| 最大屏幕几何偏差 | 7.397 px | 0.069 px |
| 顶点 P95 屏幕偏差 | 7.095 px | 0.066 px |

另从全国原始 PBF 独立读取该 Way 的全部 42 个节点及顺序，逐点核对 canonical WKB，坐标完全一致。原始节点与 OSM 版本记录在 `data/logs/next-validation/next-v3/jinan/source-way.json`。最终全国包 `china-osm-202609-v3` 的原始节点表也给出完全一致的顺序和坐标，记录在 `data/logs/next-validation/next-v3/jinan-final/report.json`。桌面端截图包含指定镜头、邻近轨道和 Z21、Z22。

同一铁路抽查 Z4、Z8、Z11、Z12、Z13、Z15、Z17、Z19、Z21。精细瓦片裁切线与面，保留裁切范围内的实际转折。向量坐标按 MVT extent 8192 量化，视口屏幕偏差同时包含这一量化误差。低 Zoom 概览仍使用为易读性简化的归档。

全国包的源审计记录了 36 个无效 Way 面、766 个无法组成完整面环的关系，以及 764 个面拓扑校验失败的关系。原始成员仍可追踪，问题面未发布为真实面要素。

图、源和解码结果见仓库本地 `data/logs/next-validation/next-v3/jinan/`，包含 `comparison.png`、`report.json`、`original.geojson`、`rendered-before.geojson` 和 `rendered.geojson`。`railscope-next/tests/precision_audit.py` 可重算同一对照。传入 `--pbf` 从原文件核对节点，使用源 schema 3 快照时也可直接从原始对象表核对。
