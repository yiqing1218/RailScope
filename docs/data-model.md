# Data model

Infrastructure owns `InfrastructureLine`, `Station`, `NetworkNode` and
`NetworkEdge`. `Station.anchor_node_id` is an association, not an assertion
that a station is the only node. Edges store WGS84 coordinates and import-time
meter lengths.

Operations own UUID-like run ids, service date and seconds-from-service-day
midnight. A `RoutePath` is a sequenced list of `RoutePathEdge` with cumulative
distance, so it is independent of map feature rendering.

V5 resources map one or more edges to `BlockSection`; virtual blocks are valid
when real signalling data is unavailable. Scheduled data is immutable.
`DispatchScenario` and `DispatchEvent` derive `EffectiveRun`, occupancy and
conflicts, making reset and later scenario comparison safe.
