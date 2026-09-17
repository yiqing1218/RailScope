import { create } from 'zustand';
export type LayerDefinition = { id:string; name:string; group:string; defaultVisible:boolean; minZoom?:number; maxZoom?:number; type:'line'|'symbol'|'dynamic' };
export const layerRegistry: LayerDefinition[] = [
  {id:'railway', name:'Railway', group:'Infrastructure', defaultVisible:true, type:'line'},
  {id:'roads', name:'Roads', group:'Infrastructure', defaultVisible:true, type:'line'},
  {id:'metro', name:'Metro', group:'Infrastructure', defaultVisible:true, type:'line'},
  {id:'service', name:'Service tracks', group:'Infrastructure', defaultVisible:true, type:'line'},
  {id:'stations', name:'Stations', group:'Facilities', defaultVisible:true, type:'symbol'},
  {id:'blocks', name:'Blocks', group:'Operations', defaultVisible:true, type:'line'},
];
type State = { visible: Record<string, boolean>; toggle:(id:string)=>void };
export const useLayerStore = create<State>((set) => ({visible:Object.fromEntries(layerRegistry.map(l=>[l.id,l.defaultVisible])), toggle:(id)=>set(s=>({visible:{...s.visible,[id]:!s.visible[id]}}))}));
