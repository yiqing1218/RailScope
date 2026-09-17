# Importing data

`python -m railscope.cli gis import file.geojson` validates GeoJSON features
and reports read/imported/skipped counts. The importer boundary intentionally
does not silently accept unsupported geometry.

`python -m railscope.cli osm import region.osm.pbf` checks for optional Osmium
support and reports a clear remediation if unavailable. A production importer
must normalize OSM rail tags, retain unknown tags in metadata, create a
`data_source`, then build and validate topology. OSM identifiers remain source
identifiers, never RailScope business primary keys.

For national rendering use the frontend's source abstraction with PMTiles/MVT;
the GeoJSON endpoint is bounded for demo/debug queries only.
