// Display overrides are reversible: always derive them from the original feature.
export function namedFeature(feature, names = {}) {
  const p = feature.properties || {}, q = {...p};
  if (p.display_name_managed) {
    if (p.original_display_name == null) delete q.display_name; else q.display_name = p.original_display_name;
    if (p.original_line_display_name == null) delete q.line_display_name; else q.line_display_name = p.original_line_display_name;
    delete q.display_name_source;
    delete q.display_name_verification_status;
    delete q.display_name_confidence;
  }
  const source = p.osm_node_id != null ? `node/${p.osm_node_id}` :
    p.osm_way_id != null ? `way/${p.osm_way_id}` :
    p.osm_relation_id != null ? `relation/${p.osm_relation_id}` : p.infrastructure_id || p.source_ref;
  const explicit = names.features?.[source];
  const station = names.stations?.[source] || names.stations?.[p.station_id] ||
    names.stations?.[String(p.station_id || '').replace(/^station:/, '')];
  const line = names.lines?.[p.catalog_group_id] || names.lines?.[p.line_id];
  const unnamed = !p.line_name || String(p.line_name).startsWith('未命名');
  const yard = unnamed && (names.yards?.[p.catalog_group_id] ||
    (p.station_name && p.station_name !== '未关联站场' && /站场/.test(p.track_type || '') ? `${p.station_name} · 站场股道` : null));
  const name = explicit || station || line || yard;
  if (name) {
    if (!p.display_name_managed) {
      q.original_display_name = p.display_name ?? null;
      q.original_line_display_name = p.line_display_name ?? null;
    }
    q.display_name_managed = true;
    q.source_name = p.source_name || p.name;
    q.display_name = name;
    if (feature.geometry?.type.includes('Line')) q.line_display_name = name;
    if (yard && !explicit && !station && !line) {
      q.display_name_source = 'station_assignment';
      q.display_name_verification_status = p.station_assignment_status || 'automatic_reference';
      q.display_name_confidence = p.station_assignment_confidence ?? null;
    }
  }
  return {...feature, properties: q};
}

export function namedCollection(data, names) {
  return {...data, features: (data?.features || []).map(f => namedFeature(f, names))};
}
