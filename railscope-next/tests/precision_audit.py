"""Compare archived and source-width MVT geometry around a recorded viewport."""
import argparse
import json
import math
import sqlite3
from pathlib import Path

import mapbox_vector_tile
import numpy as np
import shapely
from shapely.geometry import box, shape, mapping
from shapely.ops import unary_union


def world(x, y):
    return (np.asarray(x)+180)/360, (1-np.arcsinh(np.tan(np.deg2rad(y)))/math.pi)/2


def inverse(x, y):
    return x*360-180, np.rad2deg(np.arctan(np.sinh(math.pi*(1-2*y))))


def spans(folder, zoom):
    probe = json.loads((folder/"tiles.json").read_text(encoding="utf-8"))
    tile = next(entry for entry in probe["tiles"] if entry["z"] == zoom)
    layers = mapbox_vector_tile.decode((folder/tile["tile"]).read_bytes(), default_options={"y_coord_down":True})
    geometries = {}
    for name in ("rail","metro"):
        layer = layers.get(name, {})
        factor = 2**zoom
        extent = layer.get("extent")
        if not extent:
            continue
        for feature in layer["features"]:
            rid = feature["properties"].get("rid")
            if rid is None:
                continue
            transform = lambda x,y:(x/(extent*factor)+tile["x"]/factor,y/(extent*factor)+tile["y"]/factor)
            geom=shapely.transform(shape(feature["geometry"]),transform,interleaved=False)
            geometries.setdefault(rid,[]).append(geom)
    return tile, {rid:unary_union(geoms) for rid,geoms in geometries.items()}


def differences(reference, candidate, pixels):
    if candidate.is_empty:
        return {"visible":False}
    maximum=shapely.hausdorff_distance(reference,candidate,densify=0.25)
    one=shapely.distance(shapely.points(shapely.get_coordinates(reference)),candidate)
    two=shapely.distance(shapely.points(shapely.get_coordinates(candidate)),reference)
    return {
        "visible":True,
        "vertices":len(shapely.get_coordinates(candidate)),
        "max_pixels":float(maximum*pixels),
        "p95_vertex_pixels":float(np.percentile(np.concatenate([one,two]),95)*pixels),
    }


def save_geo(path, feature, rid):
    geometry=shapely.transform(feature,inverse,interleaved=False)
    path.write_text(json.dumps({
        "type":"FeatureCollection","features":[{"type":"Feature","properties":{"rid":rid},"geometry":mapping(geometry)}]
    },ensure_ascii=False),encoding="utf-8")


def read_way_from_pbf(pbf, way_id):
    import osmium

    with_ids = osmium.filter.IdFilter([way_id])
    pool = osmium.io.ThreadPool(2)
    ways = osmium.FileProcessor(pbf, entities=osmium.osm.WAY, thread_pool=pool).with_filter(with_ids)
    for way in ways:
        nodes = [node.ref for node in way.nodes]
        version = way.version
        break
    else:
        raise ValueError(f"OSM source way {way_id} is absent from {pbf.name}")
    coordinates = {}
    distinct_nodes = set(nodes)
    with_ids = osmium.filter.IdFilter(distinct_nodes)
    for node in osmium.FileProcessor(pbf, entities=osmium.osm.NODE, thread_pool=pool).with_filter(with_ids):
        if node.location.valid():
            coordinates[node.id] = [node.location.lon, node.location.lat]
            if len(coordinates) == len(distinct_nodes):
                break
    if any(node not in coordinates for node in nodes):
        raise ValueError("OSM extract is missing nodes from the audited way")
    return {"source_ref":f"osm:way:{way_id}", "version":version, "node_ids":nodes,
            "coordinates":[coordinates[node] for node in nodes]}


def compare(before, after, dbpath, output, pbf=None):
    old, coarse=spans(before,8)
    new, precise=spans(after,12)
    meta=json.loads((after/"tiles.json").read_text(encoding="utf-8"))
    focus=shapely.Point(*world(meta["longitude"],meta["latitude"]))
    x,y=new["x"],new["y"]
    target=box((x+0.02)/4096,(y+0.02)/4096,(x+0.98)/4096,(y+0.98)/4096)
    candidates=sorted(set(coarse)&set(precise),key=lambda rid:(precise[rid].distance(focus),rid))
    conn=sqlite3.connect(dbpath.resolve().as_uri()+"?mode=ro",uri=True)
    samples=[]
    for rid in candidates:
        row=conn.execute("""SELECT g.wgs84_wkb,e.id,e.source_ref,e.name FROM entity e
            JOIN geometry g ON g.entity_id=e.id WHERE e.render_id=?""",(rid,)).fetchone()
        if row is None:
            continue
        unclip=shapely.transform(shapely.from_wkb(row[0]),world,interleaved=False)
        reference=unclip.intersection(target)
        a=coarse[rid].intersection(target)
        b=precise[rid].intersection(target)
        if any(geom.is_empty or geom.length<=0 for geom in (reference,a,b)):
            continue
        sample=(rid,row,reference,a,b)
        if len(samples)<30:
            samples.append(sample)
        else:
            break
    if not samples:
        raise ValueError("No matching archived and native railway segments in the historical viewport")
    # Pick the nearest documented segment and retain aggregate errors for the visible sample.
    rid,row,reference,a,b=samples[0]
    raw_source=None
    output.mkdir(parents=True, exist_ok=True)
    has_source=conn.execute("SELECT 1 FROM sqlite_schema WHERE name='source_way'").fetchone()
    if (has_source or pbf) and row[2].startswith("osm:way:"):
        way=int(row[2].split(":")[-1])
        if pbf:
            primitive = read_way_from_pbf(pbf, way)
            vertices = primitive["coordinates"]
            (output/"source-way.json").write_text(json.dumps(primitive,indent=2)+"\n",encoding="utf-8")
        else:
            vertices=conn.execute("""SELECT n.longitude,n.latitude FROM source_way w,json_each(w.node_ids) j
                 JOIN source_node n ON n.osm_id=j.value WHERE w.osm_id=? ORDER BY CAST(j.key AS INTEGER)""",(way,)).fetchall()
        canonical=shapely.get_coordinates(shapely.from_wkb(row[0])).tolist()
        reference_nodes=[list(point) for point in vertices]
        size=len(canonical)
        def contiguous(geometry):
            return any(reference_nodes[i:i+size]==geometry for i in range(len(vertices)-size+1))
        raw_source={"source":"pbf" if pbf else "canonical_source_tables","raw_way_vertices":len(vertices),"canonical_vertices":size,
                    "exact_contiguous_match":contiguous(canonical) or contiguous(canonical[::-1])}
    conn.close()
    pixels=512*2**12.9
    summary={
        "longitude":meta["longitude"],"latitude":meta["latitude"],"viewport_zoom":12.9,
        "prior_effective_tile":f"8/{old['x']}/{old['y']}",
        "native_effective_tile":f"12/{new['x']}/{new['y']}",
        "nearest_entity":row[1],"source_ref":row[2],"name":row[3],
        "reference_vertices_interior":len(shapely.get_coordinates(reference)),
        "source_vertex_check":raw_source,
        "prior":differences(reference,a,pixels),"native":differences(reference,b,pixels),
        "sampled_segments":[{"entity":sample[1][1],"source_ref":sample[1][2],
                             "prior":differences(sample[2],sample[3],pixels),"native":differences(sample[2],sample[4],pixels)} for sample in samples],
    }
    (output/"report.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for filename,geom in [("original.geojson",reference),("rendered-before.geojson",a),("rendered.geojson",b)]:
        save_geo(output/filename,geom,rid)
    render_comparison(output/"comparison.png",reference,a,b,pixels,focus)
    print(json.dumps({k:v for k,v in summary.items() if k!="sampled_segments"},ensure_ascii=False))


def render_comparison(path, reference, before, after, pixels, focus):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes=plt.subplots(ncols=2, figsize=(10,4), layout="constrained", sharex=True,sharey=True)
    for axis,title,geometry in zip(axes,["Z8 archive at Z12.9","Z12 source tile at Z12.9"],[before,after]):
        for item,label,color,dash in [(reference,"Source WKB","#164a60","-"),(geometry,"Decoded MVT","#c95834","--")]:
            for part in shapely.get_parts(item):
                if part.geom_type!="LineString":
                    continue
                x,y=part.xy
                axis.plot((np.asarray(x)-focus.x)*pixels,(focus.y-np.asarray(y))*pixels,dash,label=label,
                          color=color, linewidth=1.4, marker=".",markersize=3)
        axis.set(title=title,xlabel="Horizontal offset (display px)",ylabel="Vertical offset (display px)",aspect="equal")
        axis.grid(alpha=0.27)
        handles,labels=axis.get_legend_handles_labels()
        dedup=dict(zip(labels,handles))
        axis.legend(dedup.values(),dedup.keys())
    fig.savefig(path,dpi=180)
    plt.close(fig)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ("before","after","canonical","output"): parser.add_argument(arg,type=Path)
    parser.add_argument("--pbf",type=Path,help="Verify the selected way directly against its source PBF")
    args=parser.parse_args()
    compare(args.before,args.after,args.canonical,args.output,args.pbf)
