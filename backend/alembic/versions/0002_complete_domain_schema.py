"""Complete the V0/V1/V5 relational schema while retaining the migration-only database contract."""
from alembic import op
import sqlalchemy as sa

revision = "0002_complete_domain_schema"
down_revision = "0001_baseline"

def upgrade():
    op.create_table("infrastructure_line", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("name", sa.Text), sa.Column("name_en", sa.Text), sa.Column("ref", sa.Text), sa.Column("mode", sa.Text, nullable=False), sa.Column("railway_type", sa.Text), sa.Column("source_id", sa.Uuid), sa.Column("metadata", sa.JSON))
    op.create_table("station", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("name", sa.Text, nullable=False), sa.Column("name_en", sa.Text), sa.Column("code", sa.Text), sa.Column("mode", sa.Text, nullable=False), sa.Column("station_type", sa.Text), sa.Column("source_id", sa.Uuid), sa.Column("metadata", sa.JSON))
    op.execute("ALTER TABLE network_node ADD COLUMN geom geometry(Point,4326) NOT NULL")
    op.execute("ALTER TABLE network_edge ADD COLUMN geom geometry(LineString,4326) NOT NULL")
    op.execute("ALTER TABLE station ADD COLUMN geom geometry(Point,4326) NOT NULL")
    op.create_table("station_network_node", sa.Column("station_id", sa.Uuid, nullable=False), sa.Column("network_node_id", sa.Uuid, nullable=False), sa.Column("role", sa.Text), sa.PrimaryKeyConstraint("station_id", "network_node_id"))
    op.create_table("service_route", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("name", sa.Text, nullable=False), sa.Column("short_name", sa.Text), sa.Column("mode", sa.Text, nullable=False), sa.Column("color", sa.Text), sa.Column("metadata", sa.JSON))
    op.create_table("service_calendar", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("name", sa.Text), sa.Column("start_date", sa.Date), sa.Column("end_date", sa.Date), *[sa.Column(day, sa.Boolean) for day in ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")])
    op.add_column("train_run", sa.Column("service_route_id", sa.Uuid))
    op.add_column("train_run", sa.Column("calendar_id", sa.Uuid))
    op.add_column("train_run", sa.Column("origin_station_id", sa.Uuid))
    op.add_column("train_run", sa.Column("destination_station_id", sa.Uuid))
    op.add_column("train_run", sa.Column("route_path_id", sa.Uuid))
    op.add_column("train_run", sa.Column("status", sa.Text, nullable=False, server_default="scheduled"))
    op.add_column("train_run", sa.Column("metadata", sa.JSON))
    op.add_column("stop_time", sa.Column("scheduled_distance_m", sa.Float))
    op.add_column("stop_time", sa.Column("platform_text", sa.Text))
    op.add_column("stop_time", sa.Column("metadata", sa.JSON))
    op.create_table("route_path", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("name", sa.Text), sa.Column("total_length_m", sa.Float, nullable=False), sa.Column("origin_station_id", sa.Uuid), sa.Column("destination_station_id", sa.Uuid), sa.Column("metadata", sa.JSON))
    op.create_table("route_path_edge", sa.Column("route_path_id", sa.Uuid, nullable=False), sa.Column("sequence", sa.Integer, nullable=False), sa.Column("edge_id", sa.Uuid, nullable=False), sa.Column("forward", sa.Boolean, nullable=False), sa.Column("start_distance_m", sa.Float, nullable=False), sa.Column("end_distance_m", sa.Float, nullable=False), sa.CheckConstraint("end_distance_m > start_distance_m"), sa.PrimaryKeyConstraint("route_path_id", "sequence"))
    op.create_table("block_section", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("name", sa.Text, nullable=False), sa.Column("direction", sa.Text, nullable=False), sa.Column("start_node_id", sa.Uuid, nullable=False), sa.Column("end_node_id", sa.Uuid, nullable=False), sa.Column("length_m", sa.Float, nullable=False), sa.Column("block_type", sa.Text, nullable=False), sa.Column("metadata", sa.JSON))
    op.create_table("block_edge", sa.Column("block_id", sa.Uuid, nullable=False), sa.Column("edge_id", sa.Uuid, nullable=False), sa.Column("sequence", sa.Integer, nullable=False), sa.Column("forward", sa.Boolean, nullable=False), sa.PrimaryKeyConstraint("block_id", "sequence"))
    op.create_table("station_track", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("station_id", sa.Uuid, nullable=False), sa.Column("name", sa.Text, nullable=False), sa.Column("track_number", sa.Text), sa.Column("platform_number", sa.Text), sa.Column("direction", sa.Text), sa.Column("length_m", sa.Float), sa.Column("is_virtual", sa.Boolean, nullable=False), sa.Column("metadata", sa.JSON))
    op.create_table("headway_rule", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("block_id", sa.Uuid), sa.Column("same_direction_min_s", sa.Integer, nullable=False), sa.Column("opposite_direction_min_s", sa.Integer, nullable=False))
    op.create_table("track_occupancy", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("train_run_id", sa.Uuid, nullable=False), sa.Column("resource_type", sa.Text, nullable=False), sa.Column("resource_id", sa.Uuid, nullable=False), sa.Column("start_time_s", sa.Integer, nullable=False), sa.Column("end_time_s", sa.Integer, nullable=False), sa.Column("direction", sa.Text), sa.Column("scenario_id", sa.Uuid, nullable=False), sa.Column("metadata", sa.JSON))
    op.create_table("conflict", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("scenario_id", sa.Uuid, nullable=False), sa.Column("conflict_type", sa.Text, nullable=False), sa.Column("train_run_a", sa.Uuid, nullable=False), sa.Column("train_run_b", sa.Uuid, nullable=False), sa.Column("resource_type", sa.Text, nullable=False), sa.Column("resource_id", sa.Uuid, nullable=False), sa.Column("start_time_s", sa.Integer, nullable=False), sa.Column("end_time_s", sa.Integer, nullable=False), sa.Column("severity", sa.Text, nullable=False), sa.Column("metadata", sa.JSON))
    op.create_index("ix_network_node_geom", "network_node", ["geom"], postgresql_using="gist")
    op.create_index("ix_network_edge_geom", "network_edge", ["geom"], postgresql_using="gist")

def downgrade():
    for table in ("conflict", "track_occupancy", "headway_rule", "station_track", "block_edge", "block_section", "route_path_edge", "route_path", "service_calendar", "service_route", "station_network_node", "station", "infrastructure_line"): op.drop_table(table)
    op.drop_column("network_edge", "geom"); op.drop_column("network_node", "geom")
    for column in ("metadata", "status", "route_path_id", "destination_station_id", "origin_station_id", "calendar_id", "service_route_id"): op.drop_column("train_run", column)
    for column in ("metadata", "platform_text", "scheduled_distance_m"): op.drop_column("stop_time", column)
