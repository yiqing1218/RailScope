CREATE TABLE IF NOT EXISTS ops_schema (version INTEGER PRIMARY KEY CHECK(version=1));
INSERT OR IGNORE INTO ops_schema VALUES(1);
CREATE TABLE IF NOT EXISTS ops_corridor (
 id TEXT NOT NULL, version INTEGER NOT NULL, snapshot TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(id,version)
) STRICT;
CREATE INDEX IF NOT EXISTS ops_corridor_snapshot ON ops_corridor(snapshot,id,version);
CREATE TABLE IF NOT EXISTS ops_service (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS ops_run (
 id TEXT NOT NULL, version INTEGER NOT NULL, service_id TEXT NOT NULL REFERENCES ops_service(id),
 service_date TEXT NOT NULL, corridor_id TEXT, corridor_version INTEGER, payload TEXT NOT NULL,
 PRIMARY KEY(id,version), FOREIGN KEY(corridor_id,corridor_version) REFERENCES ops_corridor(id,version)
) STRICT;
CREATE INDEX IF NOT EXISTS ops_run_service ON ops_run(service_id,service_date,id,version);
CREATE TABLE IF NOT EXISTS ops_stock (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS ops_trainset (id TEXT PRIMARY KEY, stock_type_id TEXT REFERENCES ops_stock(id), payload TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS ops_assignment (
 run_id TEXT NOT NULL, run_version INTEGER NOT NULL, trainset_id TEXT NOT NULL REFERENCES ops_trainset(id),
 PRIMARY KEY(run_id,run_version), FOREIGN KEY(run_id,run_version) REFERENCES ops_run(id,version)
) STRICT;
CREATE TABLE IF NOT EXISTS ops_scenario (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
