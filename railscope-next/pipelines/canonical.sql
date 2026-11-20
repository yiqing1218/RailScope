PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE source_object (
    ref TEXT PRIMARY KEY,
    source_version INTEGER NOT NULL,
    tags TEXT NOT NULL
) STRICT;

CREATE TABLE entity (
    id TEXT PRIMARY KEY,
    render_id INTEGER NOT NULL UNIQUE CHECK (render_id BETWEEN 1 AND 4503599627370496),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('rail', 'metro', 'road', 'station', 'poi', 'facility', 'line')),
    class TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('operating','construction','planned','disused','unknown')),
    source_ref TEXT NOT NULL REFERENCES source_object(ref),
    verification TEXT NOT NULL CHECK (verification IN ('automatic_reference','verified','unknown')),
    confidence TEXT NOT NULL CHECK (confidence IN ('low','medium','high'))
) STRICT;
CREATE INDEX entity_catalog ON entity(kind, name, id);

CREATE TABLE geometry (
    rowid INTEGER PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE REFERENCES entity(id),
    wgs84_wkb BLOB,
    min_x REAL NOT NULL,
    max_x REAL NOT NULL,
    min_y REAL NOT NULL,
    max_y REAL NOT NULL
) STRICT;
CREATE VIRTUAL TABLE spatial USING rtree(rowid, min_x, max_x, min_y, max_y);

CREATE TABLE node (
    id TEXT PRIMARY KEY,
    source_ref TEXT NOT NULL REFERENCES source_object(ref),
    network TEXT NOT NULL CHECK (network IN ('rail', 'road')),
    longitude REAL NOT NULL,
    latitude REAL NOT NULL
) STRICT;
CREATE TABLE edge (
    id TEXT PRIMARY KEY REFERENCES entity(id),
    from_node TEXT NOT NULL REFERENCES node(id),
    to_node TEXT NOT NULL REFERENCES node(id),
    forward INTEGER NOT NULL CHECK (forward IN (0, 1)),
    reverse INTEGER NOT NULL CHECK (reverse IN (0, 1)),
    meters REAL NOT NULL CHECK (meters > 0)
) STRICT;
CREATE INDEX edge_from ON edge(from_node);
CREATE INDEX edge_to ON edge(to_node);

CREATE TABLE line_member (
    line_id TEXT NOT NULL REFERENCES entity(id),
    sequence INTEGER NOT NULL,
    position INTEGER NOT NULL,
    member_id TEXT NOT NULL REFERENCES entity(id),
    role TEXT NOT NULL,
    PRIMARY KEY (line_id, sequence, position)
) STRICT;
CREATE INDEX member_of ON line_member(member_id, line_id);

CREATE VIRTUAL TABLE entity_search USING fts5(
    entity_id UNINDEXED, terms, tokenize='unicode61', prefix='2 3 4'
);
