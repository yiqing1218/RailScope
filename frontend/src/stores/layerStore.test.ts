import { describe, expect, it } from 'vitest';
import { layerRegistry } from './layerStore';
describe('layer registry',()=>it('uses stable, unique ids',()=>expect(new Set(layerRegistry.map(x=>x.id)).size).toBe(layerRegistry.length)));
