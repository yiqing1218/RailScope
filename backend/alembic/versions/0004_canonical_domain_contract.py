"""Align PostGIS identifiers and tables with the canonical domain contract."""

from alembic import op
import sqlalchemy as sa

revision = "0004_canonical_domain_contract"
down_revision = "0003_corridor_canonical"


ID_COLUMNS = {
    "data_source": ("id",),
    "network_node": ("id", "station_id"),
    "network_edge": ("id", "from_node_id", "to_node_id"),
    "infrastructure_line": ("id", "source_id"),
    "station": ("id", "source_id"),
    "station_network_node": ("station_id", "network_node_id"),
    "service_route": ("id",),
    "service_calendar": ("id",),
    "train_run": (
        "id",
        "service_route_id",
        "calendar_id",
        "origin_station_id",
        "destination_station_id",
        "corridor_id",
    ),
    "stop_time": ("id", "train_run_id", "station_id"),
    "dispatch_scenario": ("id",),
    "dispatch_event": ("id", "scenario_id", "train_run_id"),
    "corridor": (
        "id",
        "legacy_origin_station_id",
        "legacy_destination_station_id",
        "origin_node_id",
        "destination_node_id",
    ),
    "corridor_edge": ("corridor_id", "edge_id"),
    "block_section": ("id", "start_node_id", "end_node_id"),
    "block_edge": ("block_id", "edge_id"),
    "station_track": ("id", "station_id"),
    "headway_rule": ("id", "block_id"),
    "track_occupancy": ("id", "train_run_id", "resource_id", "scenario_id"),
    "conflict": (
        "id",
        "scenario_id",
        "train_run_a",
        "train_run_b",
        "resource_id",
    ),
}


def _as_text():
    for table, columns in ID_COLUMNS.items():
        for column in columns:
            op.alter_column(
                table,
                column,
                type_=sa.Text(),
                postgresql_using=f"{column}::text",
            )


def _as_uuid():
    for table, columns in reversed(tuple(ID_COLUMNS.items())):
        for column in reversed(columns):
            op.alter_column(
                table,
                column,
                type_=sa.Uuid(),
                postgresql_using=f"{column}::uuid",
            )


def upgrade():
    _as_text()
    additions = {
        "data_source": [sa.Column("source_url", sa.Text)],
        "infrastructure_line": [
            sa.Column("construction_status", sa.Text, nullable=False, server_default="unknown"),
            sa.Column("verification_status", sa.Text, nullable=False, server_default="unverified"),
            sa.Column("confidence", sa.Float),
        ],
        "network_node": [
            sa.Column("source_id", sa.Text),
            sa.Column("source_node_ids", sa.JSON),
            sa.Column("snapshot_id", sa.Text),
        ],
        "network_edge": [
            sa.Column("railway_type", sa.Text),
            sa.Column("service", sa.Text),
            sa.Column("infrastructure_line_id", sa.Text),
            sa.Column("source_id", sa.Text),
            sa.Column("construction_status", sa.Text, nullable=False, server_default="operating"),
            sa.Column("snapshot_id", sa.Text),
            sa.Column("osm_way_id", sa.Text),
            sa.Column("osm_node_ids", sa.JSON),
            sa.Column("source_tags", sa.JSON),
            sa.Column("verification_status", sa.Text, nullable=False, server_default="unverified"),
            sa.Column("confidence", sa.Float),
        ],
        "station": [
            sa.Column("anchor_node_id", sa.Text),
            sa.Column("source_member_ids", sa.JSON),
            sa.Column("verification_status", sa.Text, nullable=False, server_default="unverified"),
            sa.Column("confidence", sa.Float),
        ],
        "corridor": [
            sa.Column("snapshot_id", sa.Text),
            sa.Column("source_id", sa.Text),
            sa.Column("verification_status", sa.Text, nullable=False, server_default="unverified"),
            sa.Column("confidence", sa.Float),
        ],
        "train_run": [
            sa.Column("train_length_m", sa.Float),
            sa.Column("service_id", sa.Text),
            sa.Column("internal_train_no", sa.Text),
            sa.Column("source_id", sa.Text),
            sa.Column("source_version", sa.Text),
            sa.Column("verification_status", sa.Text, nullable=False, server_default="unverified"),
        ],
        "stop_time": [
            sa.Column("station_track_id", sa.Text),
            sa.Column("platform_id", sa.Text),
            sa.Column("station_route_id", sa.Text),
        ],
    }
    for table, columns in additions.items():
        for column in columns:
            op.add_column(table, column)

    op.create_table(
        "dataset_snapshot",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("dataset_id", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("retrieved_at", sa.Text, nullable=False),
        sa.Column("effective_date", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
    )
    op.create_table(
        "line_membership",
        sa.Column("edge_id", sa.Text, nullable=False),
        sa.Column("line_id", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text),
        sa.Column("verification_status", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("edge_id", "line_id"),
    )
    for table, owner, start_column, end_column in (
        ("route_section", "route_section_id", "origin_node_id", "destination_node_id"),
        ("station_route", "station_route_id", "entry_node_id", "exit_node_id"),
    ):
        columns = [
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column(start_column, sa.Text, nullable=False),
            sa.Column(end_column, sa.Text, nullable=False),
        ]
        if table == "station_route":
            columns.insert(1, sa.Column("station_id", sa.Text, nullable=False))
            columns.append(sa.Column("verification_status", sa.Text, nullable=False))
        op.create_table(table, *columns)
        op.create_table(
            table + "_edge",
            sa.Column(owner, sa.Text, nullable=False),
            sa.Column("sequence", sa.Integer, nullable=False),
            sa.Column("edge_id", sa.Text, nullable=False),
            sa.Column("forward", sa.Boolean, nullable=False),
            sa.Column("start_distance_m", sa.Float, nullable=False),
            sa.Column("end_distance_m", sa.Float, nullable=False),
            sa.PrimaryKeyConstraint(owner, "sequence"),
        )
    op.create_table(
        "train_service",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("train_code", sa.Text, nullable=False),
        sa.Column("internal_train_no", sa.Text),
        sa.Column("source_id", sa.Text),
        sa.Column("source_version", sa.Text),
    )
    op.create_table(
        "station_area",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("station_id", sa.Text, nullable=False),
        sa.Column("area_type", sa.Text, nullable=False),
        sa.Column("geometry", sa.JSON, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("verification_status", sa.Text, nullable=False),
    )
    op.create_table(
        "platform",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("station_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("area_id", sa.Text),
        sa.Column("source_id", sa.Text),
    )
    op.create_table(
        "stop_position",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("station_id", sa.Text, nullable=False),
        sa.Column("node_id", sa.Text, nullable=False),
    )
    op.create_table(
        "entrance",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("station_id", sa.Text, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("source_id", sa.Text),
    )


def downgrade():
    for table in (
        "entrance",
        "stop_position",
        "platform",
        "station_area",
        "train_service",
        "station_route_edge",
        "station_route",
        "route_section_edge",
        "route_section",
        "line_membership",
        "dataset_snapshot",
    ):
        op.drop_table(table)
    removals = {
        "stop_time": ("station_route_id", "platform_id", "station_track_id"),
        "train_run": (
            "verification_status",
            "source_version",
            "source_id",
            "internal_train_no",
            "service_id",
            "train_length_m",
        ),
        "corridor": ("confidence", "verification_status", "source_id", "snapshot_id"),
        "station": ("confidence", "verification_status", "source_member_ids", "anchor_node_id"),
        "network_edge": (
            "confidence",
            "verification_status",
            "source_tags",
            "osm_node_ids",
            "osm_way_id",
            "snapshot_id",
            "construction_status",
            "source_id",
            "infrastructure_line_id",
            "service",
            "railway_type",
        ),
        "network_node": ("snapshot_id", "source_node_ids", "source_id"),
        "infrastructure_line": ("confidence", "verification_status", "construction_status"),
        "data_source": ("source_url",),
    }
    for table, columns in removals.items():
        for column in columns:
            op.drop_column(table, column)
    _as_uuid()
