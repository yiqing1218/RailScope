"""Create RailScope baseline PostGIS schema; domain tables retain separate geometry/topology/operation boundaries."""
from alembic import op
import sqlalchemy as sa
revision = "0001_baseline"
down_revision = None

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table("data_source", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("name", sa.Text, nullable=False), sa.Column("source_type", sa.Text, nullable=False), sa.Column("license", sa.Text), sa.Column("attribution", sa.Text), sa.Column("metadata", sa.JSON))
    op.create_table("network_node", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("mode", sa.Text, nullable=False), sa.Column("node_type", sa.Text, nullable=False), sa.Column("station_id", sa.Uuid), sa.Column("metadata", sa.JSON))
    op.create_table("network_edge", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("from_node_id", sa.Uuid, nullable=False), sa.Column("to_node_id", sa.Uuid, nullable=False), sa.Column("mode", sa.Text, nullable=False), sa.Column("direction", sa.Text, nullable=False), sa.Column("length_m", sa.Float, nullable=False), sa.Column("metadata", sa.JSON))
    op.create_table("train_run", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("service_date", sa.Date, nullable=False), sa.Column("train_number", sa.Text, nullable=False), sa.UniqueConstraint("service_date", "train_number"))
    op.create_table("stop_time", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("train_run_id", sa.Uuid, nullable=False), sa.Column("station_id", sa.Uuid, nullable=False), sa.Column("stop_sequence", sa.Integer, nullable=False), sa.Column("arrival_time_s", sa.Integer), sa.Column("departure_time_s", sa.Integer))
    op.create_table("dispatch_scenario", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("name", sa.Text, nullable=False), sa.Column("service_date", sa.Date, nullable=False))
    op.create_table("dispatch_event", sa.Column("id", sa.Uuid, primary_key=True), sa.Column("scenario_id", sa.Uuid, nullable=False), sa.Column("train_run_id", sa.Uuid, nullable=False), sa.Column("event_type", sa.Text, nullable=False), sa.Column("new_value", sa.JSON))

def downgrade():
    for table in ("dispatch_event", "dispatch_scenario", "stop_time", "train_run", "network_edge", "network_node", "data_source"): op.drop_table(table)
