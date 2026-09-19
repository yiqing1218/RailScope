"""Replace the per-run route-path schema with shared complete Corridors."""

from alembic import op
import sqlalchemy as sa

revision = "0003_corridor_canonical"
down_revision = "0002_complete_domain_schema"


def upgrade():
    op.rename_table("route_path", "corridor")
    op.rename_table("route_path_edge", "corridor_edge")
    op.alter_column(
        "corridor_edge", "route_path_id", new_column_name="corridor_id"
    )
    op.alter_column(
        "corridor", "origin_station_id", new_column_name="legacy_origin_station_id"
    )
    op.alter_column(
        "corridor",
        "destination_station_id",
        new_column_name="legacy_destination_station_id",
    )
    op.add_column("corridor", sa.Column("origin_node_id", sa.Uuid))
    op.add_column("corridor", sa.Column("destination_node_id", sa.Uuid))
    op.execute(
        """
        UPDATE corridor AS c SET origin_node_id = (
            SELECT network_node_id FROM station_network_node
            WHERE station_id = c.legacy_origin_station_id
            ORDER BY CASE role WHEN 'anchor' THEN 0 ELSE 1 END, network_node_id
            LIMIT 1
        ), destination_node_id = (
            SELECT network_node_id FROM station_network_node
            WHERE station_id = c.legacy_destination_station_id
            ORDER BY CASE role WHEN 'anchor' THEN 0 ELSE 1 END, network_node_id
            LIMIT 1
        )
        """
    )
    op.alter_column(
        "train_run", "route_path_id", new_column_name="corridor_id"
    )


def downgrade():
    op.alter_column(
        "train_run", "corridor_id", new_column_name="route_path_id"
    )
    op.drop_column("corridor", "destination_node_id")
    op.drop_column("corridor", "origin_node_id")
    op.alter_column("corridor", "legacy_origin_station_id", new_column_name="origin_station_id")
    op.alter_column("corridor", "legacy_destination_station_id", new_column_name="destination_station_id")
    op.alter_column(
        "corridor_edge", "corridor_id", new_column_name="route_path_id"
    )
    op.rename_table("corridor_edge", "route_path_edge")
    op.rename_table("corridor", "route_path")
