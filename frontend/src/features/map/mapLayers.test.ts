import { describe, expect, it } from 'vitest';
import { applyVisibility, installLayers } from './mapLayers';
import { layerRegistry } from '../../stores/layerStore';

describe('map layer lifecycle', () => {
  it('installs every registered layer once, including service tracks', () => {
    const layers = new Map<string, unknown>();
    const map = { getLayer: (id: string) => layers.get(id), addLayer: (layer: {id: string}) => layers.set(layer.id, layer) };
    installLayers(map as Parameters<typeof installLayers>[0]);
    installLayers(map as Parameters<typeof installLayers>[0]);
    expect([...layers.keys()].sort()).toEqual(layerRegistry.map(layer => layer.id).sort());
  });
  it('visibility changes only touch layout properties', () => {
    const changes: unknown[] = [];
    const map = { getLayer: () => ({}), setLayoutProperty: (...args: unknown[]) => changes.push(args) };
    applyVisibility(map as Parameters<typeof applyVisibility>[0], { service: false, railway: true });
    expect(changes).toContainEqual(['service', 'visibility', 'none']);
    expect(changes).toContainEqual(['railway', 'visibility', 'visible']);
  });
});
