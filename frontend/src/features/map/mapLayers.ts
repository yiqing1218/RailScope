import type { ExpressionSpecification, Map } from 'maplibre-gl';
import { layerRegistry } from '../../stores/layerStore';
import { railTheme } from './styles/theme';

type LayerMap = { getLayer: (id: string) => unknown; addLayer: (layer: Parameters<Map['addLayer']>[0]) => unknown };
type VisibilityMap = { getLayer: (id: string) => unknown; setLayoutProperty: (id: string, name: 'visibility', value: 'visible' | 'none') => unknown };
const serviceTracks: ExpressionSpecification = ['in', ['get', 'service'], ['literal', ['siding', 'yard', 'spur', 'crossover']]];

export function installLayers(map: LayerMap) {
  const add: LayerMap['addLayer'] = (layer) => {
    if (!map.getLayer(layer.id)) map.addLayer(layer);
  };
  add({ id: 'roads', type: 'line', source: 'network', filter: ['==', ['get', 'mode'], 'road'], paint: { 'line-color': '#64748b', 'line-width': 2 } });
  add({ id: 'metro', type: 'line', source: 'network', filter: ['==', ['get', 'mode'], 'metro'], paint: { 'line-color': '#f472b6', 'line-width': 3, 'line-dasharray': [2, 1] } });
  add({ id: 'railway', type: 'line', source: 'network', filter: ['all', ['==', ['get', 'mode'], 'rail'], ['!', serviceTracks]], paint: { 'line-color': railTheme.main, 'line-width': 4 } });
  add({ id: 'service', type: 'line', source: 'network', filter: ['all', ['==', ['get', 'mode'], 'rail'], serviceTracks], paint: { 'line-color': railTheme.service, 'line-width': 3 } });
  add({ id: 'blocks', type: 'line', source: 'blocks', paint: { 'line-color': railTheme.block, 'line-width': 7, 'line-opacity': 0.35 } });
  add({ id: 'stations', type: 'circle', source: 'stations', paint: { 'circle-radius': 6, 'circle-color': '#f8fafc', 'circle-stroke-color': '#0f172a', 'circle-stroke-width': 2 } });
}

export function applyVisibility(map: VisibilityMap, visible: Record<string, boolean>) {
  for (const { id } of layerRegistry) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible[id] ? 'visible' : 'none');
  }
}
