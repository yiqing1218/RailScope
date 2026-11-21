CREATE TABLE IF NOT EXISTS source_snapshot (
  source_sha256 TEXT PRIMARY KEY, file_name TEXT NOT NULL, license TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS source_node (
  osm_id INTEGER PRIMARY KEY, source_version INTEGER NOT NULL,
  longitude REAL NOT NULL, latitude REAL NOT NULL, tags TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS source_way (
  osm_id INTEGER PRIMARY KEY, node_ids TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS source_relation_member (
  relation_ref TEXT NOT NULL REFERENCES source_object(ref),
  ordinal INTEGER NOT NULL, member_ref TEXT NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (relation_ref, ordinal)
) STRICT, WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS source_member_ref ON source_relation_member(member_ref);
CREATE TABLE IF NOT EXISTS metro_service_track (
  edge_id TEXT NOT NULL REFERENCES edge(id),
  service_id TEXT NOT NULL REFERENCES entity(id),
  route_ref TEXT NOT NULL REFERENCES source_object(ref),
  colour TEXT,
  colour_source_ref TEXT REFERENCES source_object(ref),
  PRIMARY KEY (edge_id, service_id, route_ref)
) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS base_feature (
  id INTEGER PRIMARY KEY, source_ref TEXT NOT NULL REFERENCES source_object(ref),
  kind TEXT NOT NULL, class TEXT NOT NULL, name TEXT NOT NULL, wkb BLOB NOT NULL
) STRICT;
CREATE VIRTUAL TABLE IF NOT EXISTS base_spatial USING rtree(id,min_x,max_x,min_y,max_y);
CREATE INDEX IF NOT EXISTS entity_source_ref ON entity(source_ref,kind,id);
