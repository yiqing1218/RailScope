import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, post } from '../../api/client';
import type { Conflict, TrainRun } from '../../types/api';
import { useSelectionStore } from '../../stores/selectionStore';

const clock = (n:number) => `${String(Math.floor(n/3600)).padStart(2,'0')}:${String(Math.floor(n%3600/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
export function OperationsPanel() {
  const qc=useQueryClient(); const selected=useSelectionStore(s=>s.selectedTrainRunId); const select=useSelectionStore(s=>s.selectTrain); const selectConflict=useSelectionStore(s=>s.selectConflict);
  const {data:trains=[]}=useQuery({queryKey:['trains'],queryFn:()=>api<TrainRun[]>('/train-runs')});
  const {data:conflicts=[]}=useQuery({queryKey:['conflicts'],queryFn:()=>api<Conflict[]>('/conflicts')});
  const refresh=()=>qc.invalidateQueries();
  const action=(path:string,payload:object)=>post(path,payload).then(refresh);
  return <aside className="operations"><h2>Operations</h2><div className="section"><h3>Train runs</h3>{trains.map(t=><button className={selected===t.id?'selected':''} key={t.id} onClick={()=>select(t.id)}>{t.train_number} <small>{t.service_date}</small></button>)}</div>{selected&&<div className="section dispatch"><h3>Manual dispatch</h3><button onClick={()=>action('/dispatch/delay',{train_run_id:selected,seconds:300})}>Delay +300 s</button><button onClick={()=>action('/dispatch/hold',{train_run_id:selected,station_id:'station-a',seconds:300})}>Hold Station A +300 s</button><button onClick={()=>action('/dispatch/cancel',{train_run_id:selected})}>Cancel</button><button onClick={()=>action('/dispatch/assign-track',{train_run_id:selected,station_id:'station-b',station_track_id:'track-b-1'})}>Assign B-1</button><button onClick={()=>post('/dispatch/reset').then(refresh)}>Reset scenario</button></div>}<div className="section"><h3>Conflicts ({conflicts.length})</h3>{conflicts.map(c=><button className={`conflict ${c.severity}`} key={c.id} onClick={()=>selectConflict(c.id,c.resource_id)}><b>{c.conflict_type}</b><span>{c.train_run_a} / {c.train_run_b}</span><small>{clock(c.start_time_s)}–{clock(c.end_time_s)}</small></button>)}</div></aside>
}
