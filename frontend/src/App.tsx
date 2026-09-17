import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MapView } from './features/map/MapView';
import { OperationsPanel } from './features/operations/OperationsPanel';
import { layerRegistry, useLayerStore } from './stores/layerStore';
import './app.css';
const client=new QueryClient();
function Layers(){const visible=useLayerStore(s=>s.visible);const toggle=useLayerStore(s=>s.toggle);return <aside className="layers"><h2>Layers</h2>{layerRegistry.map(l=><label key={l.id}><input type="checkbox" checked={visible[l.id]} onChange={()=>toggle(l.id)}/>{l.name}</label>)}<p className="note">Static nationwide data is represented by the PMTiles-ready source boundary. The bundled demo uses local GeoJSON/API data.</p></aside>}
export default function App(){return <QueryClientProvider client={client}><main><header><strong>RailScope</strong><span>V0 · V1 · V5</span><span className="scenario">Base Scenario — 2026-09-15</span></header><section className="workspace"><Layers/><MapView/><OperationsPanel/></section></main></QueryClientProvider>}
