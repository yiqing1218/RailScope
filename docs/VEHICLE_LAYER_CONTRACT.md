# 车辆图层数据约定

车辆是独立 GeoJSON Point 图层，不与线路、时刻表和调度事件混合。每个要素应包含 `vehicle_id`、`line_key`、`service_date`、`bearing_deg`、`state`、`sampled_at`，几何坐标为 WGS84 `[longitude, latitude]`。

桌面端的 `MapPanel.set_vehicle_positions(feature_collection)` 接收并显示最新位置。当前版本不实现沿线动画、回放或实时追踪；后续动画必须消费车辆图层和经过拓扑校验的线路几何，不能修改原始线路或运行计划。
