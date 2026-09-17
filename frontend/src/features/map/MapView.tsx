import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Edge, Station } from '../../types/api';
import { useLayerStore } from '../../stores/layerStore';
import { railTheme } from './styles/theme';

type GeoJson = GeoJSON.FeatureCollection;
const empty: GeoJson = { type: 'FeatureCollection', features: [] };

export function MapView() {
  const root = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map>();
  const { data: edges = [] } = useQuery({ queryKey: ['edges'], queryFn: () => api<Edge[]>('/network/edges') });
  const { data: stations = [] } = useQuery({ queryKey: ['stations'], queryFn: () => api<Station[]>('/stations') });
  const { data: blocks = empty } = useQuery({ queryKey: ['block-geojson'], queryFn: () => api<GeoJson>('/blocks-geojson') });
  const visible = useLayerStore((state) => state.visible);

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
      const network: GeoJson = { type: 'FeatureCollection', features: edges.map((edge) => ({ type: 'Feature', properties: edge, geometry: { type: 'LineString', coordinates: edge.coordinates } })) };
      const stationData: GeoJson = { type: 'FeatureCollection', features: stations.map((station) => ({ type: 'Feature', properties: station, geometry: { type: 'Point', coordinates: [station.lon, station.lat] } })) };
      const put = (id: string, data: GeoJson) => instance.getSource(id) ? (instance.getSource(id) as maplibregl.GeoJSONSource).setData(data) : instance.addSource(id, { type: 'geojson', data });
      put('network', network); put('stations', stationData); put('blocks', blocks);
      if (!instance.getLayer('railway')) {
        instance.addLayer({ id: 'roads', type: 'line', source: 'network', filter: ['==', ['get', 'mode'], 'road'], paint: { 'line-color': '#64748b', 'line-width': 2 } });
        instance.addLayer({ id: 'metro', type: 'line', source: 'network', filter: ['==', ['get', 'mode'], 'metro'], paint: { 'line-color': '#f472b6', 'line-width': 3, 'line-dasharray': [2, 1] } });
        instance.addLayer({ id: 'railway', type: 'line', source: 'network', filter: ['==', ['get', 'mode'], 'rail'], paint: { 'line-color': ['case', ['==', ['get', 'service'], 'siding'], railTheme.service, railTheme.main], 'line-width': 4 } });
        instance.addLayer({ id: 'blocks', type: 'line', source: 'blocks', paint: { 'line-color': railTheme.block, 'line-width': 7, 'line-opacity': 0.35 } });
        instance.addLayer({ id: 'stations', type: 'circle', source: 'stations', paint: { 'circle-radius': 6, 'circle-color': '#f8fafc', 'circle-stroke-color': '#0f172a', 'circle-stroke-width': 2 } });
      }
      for (const id of ['railway', 'roads', 'metro', 'blocks', 'stations']) if (instance.getLayer(id)) instance.setLayoutProperty(id, 'visibility', visible[id] ? 'visible' : 'none');
    };
    if (instance.isStyleLoaded()) sync(); else instance.once('load', sync);
  }, [edges, stations, blocks, visible]);
  return <div ref={root} className="map" aria-label="RailScope infrastructure map" />;
}
