"""Add orthogonal rail semantics and facilities without rewriting physical paths."""
from alembic import op
import sqlalchemy as sa

revision = "0005_rail_domain_v2"
down_revision = "0004_canonical_domain_contract"


def _text(name, default=None):
    return sa.Column(name, sa.Text, nullable=default is None, server_default=default)


def _provenance():
    return sa.Column("provenance", sa.JSON, nullable=False, server_default="{}")


def _audit():
    return [_text("source_id"), _text("snapshot_id"), _text("verification_status", "unverified"),
            sa.Column("confidence", sa.Float), _provenance()]


def upgrade():
    for table in ("network_edge", "infrastructure_line"):
        for column in (_text("railway_class", "unknown"), _text("line_role", "unknown"), _provenance()):
            op.add_column(table, column)
    for column in (_text("track_role", "unknown"), _text("facility_id"), _text("yard_id"),
                   _text("zone_id"), _text("track_type")):
        op.add_column("network_edge", column)
    op.add_column("infrastructure_line", _text("snapshot_id"))
    op.add_column("infrastructure_line", sa.Column("design_speed_kmh", sa.Integer))
    op.create_table("operational_point", _text("id"), _text("name", ""),
        _text("point_type", "other_control_point"), _text("station_id"),
        sa.Column("node_ids", sa.JSON, nullable=False, server_default="[]"), *_audit(),
        sa.PrimaryKeyConstraint("id"), sa.ForeignKeyConstraint(["station_id"], ["station.id"]))
    op.create_table("yard", _text("id"), sa.Column("station_id", sa.Text, sa.ForeignKey("station.id"), nullable=False),
        _text("name", ""), _text("yard_type", "unknown"), *_audit(), sa.PrimaryKeyConstraint("id"))
    op.create_table("station_zone", _text("id"), sa.Column("station_id", sa.Text, sa.ForeignKey("station.id"), nullable=False),
        _text("name", ""), _text("zone_type", "throat"),
        sa.Column("yard_id", sa.Text, sa.ForeignKey("yard.id")), *_audit(), sa.PrimaryKeyConstraint("id"))
    for column in (_text("track_role", "unknown"), _text("railway_class", "unknown"),
                   _text("infrastructure_line_id"), _text("yard_id"), _text("zone_id"),
                   _text("role", "unknown"), sa.Column("source_member_ids", sa.JSON, nullable=False, server_default="[]"),
                   sa.Column("legacy_metadata", sa.JSON, nullable=False, server_default="{}"), *_audit()):
        op.add_column("station_track", column)
    op.create_table("station_track_edge",
        sa.Column("station_track_id", sa.Text, sa.ForeignKey("station_track.id"), nullable=False),
        sa.Column("edge_id", sa.Text, sa.ForeignKey("network_edge.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False), _text("direction", "forward"),
        sa.PrimaryKeyConstraint("station_track_id", "sequence"),
        sa.CheckConstraint("sequence > 0"), sa.CheckConstraint("direction IN ('forward','reverse')"))
    op.create_table("route_intent", _text("id"), _text("name", ""),
        _text("snapshot_id"), _text("source_id"), _text("verification_status", "unverified"),
        _provenance(), sa.PrimaryKeyConstraint("id"))
    op.create_table("route_intent_step",
        sa.Column("route_intent_id", sa.Text, sa.ForeignKey("route_intent.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False), _text("kind", "node"),
        sa.Column("reference_id", sa.Text, nullable=False), _text("direction", "unknown"),
        sa.PrimaryKeyConstraint("route_intent_id", "sequence"), sa.CheckConstraint("sequence > 0"),
        sa.CheckConstraint("kind IN ('node','station','operational_point','infrastructure_line')"))
    op.add_column("corridor", sa.Column("route_intent_id", sa.Text, sa.ForeignKey("route_intent.id")))
    op.add_column("corridor", _text("resolution_mode", "automatic_reference"))
    op.add_column("corridor", _provenance())
    op.add_column("corridor", _text("color", "#466979"))
    for table in ("network_edge", "station_track"):
        for column,target in (("yard_id","yard"),("zone_id","station_zone")):
            op.create_foreign_key(f"fk_{table}_{column}",table,target,[column],["id"])
    op.create_foreign_key("fk_station_track_line","station_track","infrastructure_line",["infrastructure_line_id"],["id"])
    for table, columns in (("network_edge", ("railway_class", "line_role", "track_role", "facility_id", "yard_id", "zone_id")),
                           ("infrastructure_line", ("railway_class", "line_role")),
                           ("yard", ("station_id",)), ("station_zone", ("station_id", "yard_id")),
                           ("operational_point", ("station_id",)), ("station_track_edge", ("edge_id",)),
                           ("station_track", ("yard_id", "zone_id")), ("corridor", ("route_intent_id",))):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])

    # Raw tag evidence wins over the old display label. Missing precise service
    # function remains unknown; paths, IDs, stops and train counts are untouched.
    op.execute("""UPDATE network_edge SET track_type=railway_type,
      railway_class=CASE WHEN source_tags->>'railway' IN ('subway','light_rail') THEN 'metro'
        WHEN source_tags->>'highspeed'='yes' THEN 'high_speed'
        WHEN source_tags->>'usage'='industrial' THEN 'industrial'
        WHEN source_tags->>'usage'='freight' THEN 'freight'
        WHEN source_tags->>'highspeed'='no' AND COALESCE(source_tags->>'railway','rail')='rail' THEN 'conventional'
        ELSE 'unknown' END,
      line_role=CASE source_tags->>'usage' WHEN 'main' THEN 'main_line' WHEN 'branch' THEN 'branch_line'
        WHEN 'industrial' THEN 'industrial_line' ELSE 'unknown' END,
      track_role=CASE source_tags->>'service' WHEN 'crossover' THEN 'crossover' WHEN 'spur' THEN 'spur_track'
        WHEN 'yard' THEN 'unknown' WHEN 'siding' THEN 'unknown'
        ELSE CASE WHEN source_tags->>'usage'='main' AND COALESCE(source_tags->>'service','')=''
          THEN 'main_track' ELSE 'unknown' END END""")
    for attribute in ("railway_class", "line_role", "track_role"):
        op.execute(f"""UPDATE network_edge SET provenance=(provenance::jsonb || jsonb_build_object('{attribute}',
          jsonb_build_object('value',{attribute},'source',COALESCE(source_id,'OpenStreetMap'),'snapshot_id',snapshot_id,
            'evidence',source_tags,'verification_status',CASE WHEN {attribute}='unknown' THEN 'unverified' ELSE 'osm_explicit' END,
            'confidence',CASE WHEN {attribute}='unknown' THEN NULL ELSE 0.85 END)))::json""")
    # Keep every legacy number, including user text. Only explicitly sourced
    # or manually verified numbers remain official fields; aliases are recoverable.
    op.execute("""UPDATE station_track SET legacy_metadata=json_build_object('track_number',track_number,
      'track_number_status','unverified_legacy_alias'),track_number=NULL
      WHERE track_number IS NOT NULL AND
        (track_number ~ '^(STTR-|NE-|RS-)' OR track_number=id OR
         COALESCE(metadata->>'verification_status','') NOT IN ('official_confirmed','user_verified','user_named','manual_override','osm_explicit'))""")
    op.execute("""UPDATE station_track SET verification_status=metadata->>'verification_status',
      provenance=json_build_object('track_number',json_build_object('value',track_number,
        'source','legacy_workspace','snapshot_id',snapshot_id,'evidence','Preserved explicitly verified legacy number',
        'verification_status',metadata->>'verification_status','confidence',NULL))
      WHERE track_number IS NOT NULL AND
        metadata->>'verification_status' IN ('official_confirmed','user_verified','user_named','manual_override','osm_explicit')""")


def downgrade():
    # Downgrade would discard verified V2 edits. Preserve the database and
    # require an explicit export/restore procedure instead of silently losing them.
    raise RuntimeError("Rail Domain V2 contains user semantics; export and restore a pre-migration backup to downgrade")
