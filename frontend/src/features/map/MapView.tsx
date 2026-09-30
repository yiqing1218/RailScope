import { useEffect, useMemo, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
import type { FeatureCollection } from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Edge, Station } from '../../types/api';
import { useLayerStore } from '../../stores/layerStore';
import { applyVisibility, installLayers } from './mapLayers';

type GeoJson = FeatureCollection;
const empty: GeoJson = { type: 'FeatureCollection', features: [] };
const noEdges: Edge[] = [];
const noStations: Station[] = [];

export function MapView() {
  const root = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map>();
  const { data: edges = noEdges } = useQuery({ queryKey: ['edges'], queryFn: () => api<Edge[]>('/network/edges') });
  const { data: stations = noStations } = useQuery({ queryKey: ['stations'], queryFn: () => api<Station[]>('/stations') });
  const { data: blocks = empty } = useQuery({ queryKey: ['block-geojson'], queryFn: () => api<GeoJson>('/blocks-geojson') });
  const visible = useLayerStore((state) => state.visible);
  const network = useMemo<GeoJson>(() => ({ type: 'FeatureCollection', features: edges.map((edge) => ({ type: 'Feature', properties: edge, geometry: { type: 'LineString', coordinates: edge.coordinates } })) }), [edges]);
  const stationData = useMemo<GeoJson>(() => ({ type: 'FeatureCollection', features: stations.map((station) => ({ type: 'Feature', properties: station, geometry: { type: 'Point', coordinates: [station.lon, station.lat] } })) }), [stations]);

  useEffect(() => {
    if (!root.current || map.current) return;
    const instance = new maplibregl.Map({
      container: root.current,
      style: { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#071525' } }] },
      center: [116.43, 39.92], zoom: 10,
    });
    map.current = instance;
    return () => {
      instance.remove();
      if (map.current === instance) map.current = undefined;
    };
  }, []);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const sync = () => {
      const put = (id: string, data: GeoJson) => instance.getSource(id) ? (instance.getSource(id) as maplibregl.GeoJSONSource).setData(data) : instance.addSource(id, { type: 'geojson', data });
      put('network', network); put('stations', stationData); put('blocks', blocks);
      installLayers(instance);
      applyVisibility(instance, useLayerStore.getState().visible);
    };
    if (instance.isStyleLoaded()) sync(); else instance.once('load', sync);
    return () => { instance.off('load', sync); };
  }, [network, stationData, blocks]);
  useEffect(() => {
    if (map.current?.isStyleLoaded()) applyVisibility(map.current, visible);
  }, [visible]);
  return <div ref={root} className="map" aria-label="RailScope infrastructure map" />;
}
