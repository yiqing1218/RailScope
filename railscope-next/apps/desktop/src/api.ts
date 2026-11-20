import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import type { Geometry } from 'geojson'

export type Kind = 'rail' | 'metro' | 'road' | 'station' | 'poi' | 'facility' | 'line'
export type Bounds = [number, number, number, number]

export type Pack = {
  snapshot: string
  region: string
  source: string
  source_file: string
  source_sha256: string
  license: string
  basemap: string | null
  min_zoom: number
  max_zoom: number
  bounds: Bounds
  counts: Partial<Record<Kind, number>>
}
export type Summary = {
  id: string
  render_id: number
  name: string
  kind: Kind
  class: string
  status: string
  bounds: Bounds
}
export type Entity = Summary & {
  source_ref: string
  source_version: number
  verification: string
  confidence: string
  tags: Record<string, string>
  memberships: Summary[]
  has_geometry: boolean
  has_edge: boolean
}
export type Override = { name: string; note: string; color: string; point: [number, number] | null; group: string }
export type Workspace = { revision: number; value: Override; can_undo: boolean; can_redo: boolean }
export type Inspection = { entity: Entity; workspace: Workspace }
export type Page = { items: Summary[]; next: string | null }
export type Filter = { kind?: Kind; parent?: string; query: string; bbox?: Bounds; cursor?: string }
export type Stats = { hits: number; misses: number; evictions: number; bytes: number; read_micros: number; pending: number }
export type Route = { edges: string[]; meters: number; verification: string; geometry: Geometry | null }
export type Visual = Pick<Summary, 'id' | 'render_id' | 'kind' | 'class'> & { value: Override }
export type Conflict = { entity: string; source_snapshot: string; reason: string }
export type Group = { name: string; count: number }

export const api = {
  packs: () => invoke<{ active: Pack | null; packs: Pack[] }>('list_data_packs'),
  activate: (snapshot: string) => invoke<Pack>('activate_data_pack', { snapshot }),
  page: (snapshot: string, filter: Filter) => invoke<Page>('catalog_page', { snapshot, filter }),
  entity: (snapshot: string, id: string) => invoke<Inspection>('get_entity', { snapshot, id }),
  geometry: (snapshot: string, id: string) => invoke<Geometry | null>('get_geometry', { snapshot, id }),
  update: (snapshot: string, id: string, expected: number, value: Override) =>
    invoke<Workspace>('update_workspace', { snapshot, id, expected, value }),
  replay: (snapshot: string, id: string, expected: number, redo: boolean) =>
    invoke<Workspace>('replay_workspace', { snapshot, id, expected, redo }),
  visuals: (snapshot: string, after?: string) => invoke<Visual[]>('workspace_visuals', { snapshot, after }),
  conflicts: (snapshot: string) => invoke<Conflict[]>('workspace_conflicts', { snapshot }),
  groups: (snapshot: string) => invoke<Group[]>('workspace_groups', { snapshot }),
  groupMembers: (snapshot: string, group: string, after?: string) =>
    invoke<Page>('workspace_group_members', { snapshot, group, after }),
  route: (snapshot: string, start: string, end: string, job: string) =>
    invoke<Route>('query_path', { snapshot, start, end, job }),
  cancel: (job: string) => invoke<void>('cancel_path', { job }),
  diagnostics: () => invoke<Stats | null>('diagnostics'),
}

export function tileTemplate(snapshot: string) {
  return `${convertFileSrc('', 'railscope-tile').replace(/\/+$/, '')}/${snapshot}/{z}/{x}/{y}`
}
