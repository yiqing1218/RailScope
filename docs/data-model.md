# Data model

Rail Domain Model V2 的正式字段、对象、迁移规则见 [RAIL_DOMAIN_MODEL_V2.md](RAIL_DOMAIN_MODEL_V2.md)。`railway_class`、`line_role`、`track_role`、设施归属和来源证据是独立事实；旧 `track_type` 仅是兼容字段。

Infrastructure owns `InfrastructureLine`, `Station`, `NetworkNode` and
`NetworkEdge`. `Station.anchor_node_id` is an association, not an assertion
that a station is the only node. Edges store WGS84 coordinates and import-time
meter lengths.

Operations own UUID-like run ids, service date and seconds-from-service-day
midnight. A `Corridor` is one complete, continuous and directed sequence of
`DirectedEdgeRef` values with cumulative distance. Intermediate stations remain
implicit in that physical path. `TrainRun.stops` alone defines calls, pass-through
control points, times, platforms, station tracks and verified station routes.
Several trains with different stopping patterns can therefore share one
Corridor without copying geometry.

Geometric shortest-path results are automatic references only, with saved
selection and provenance; they are not verified dispatch or interlocking
routes. `RouteIntent` stores the business request separately from a resolved
physical `Corridor`. A scenario may switch a run only to another
already registered complete Corridor; it cannot assemble temporary edge lists.

V5 resources map one or more edges to `BlockSection`; virtual blocks are valid
when real signalling data is unavailable. Scheduled data is immutable.
`DispatchScenario` and `DispatchEvent` derive `EffectiveRun`, occupancy and
conflicts, making reset and later scenario comparison safe.
