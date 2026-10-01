(function(root){
  function state(record,day){
    if(!record)return 'unknown';
    if(record.closed&&day>=record.closed)return 'disused';
    if(record.opened&&day>=record.opened)return 'operating';
    if(record.construction_started&&day<record.construction_started)return 'absent';
    if(record.construction_started)return 'construction';
    return 'unknown';
  }
  class History {
    constructor(){this.day=new Date().toISOString().slice(0,10);this.aliases=new Map();}
    configure(day,records){
      this.day=day;this.aliases.clear();
      for(const record of records)for(const alias of [record.id,...record.source_aliases])this.aliases.set(record.mode+':'+alias,record);
    }
    apply(data,mode){
      return {...data,features:(data.features||[]).flatMap(feature=>{
        const p=feature.properties||{};
        const keys=root.RailScopeEntityPresentation.ownerKeys(p);
        keys.sort((a,b)=>Number(!a.startsWith('object:')&&!a.startsWith('station:'))-Number(!b.startsWith('object:')&&!b.startsWith('station:')));
        let record=keys.map(key=>this.aliases.get(mode+':'+key)).find(Boolean);
        if(!record){
          const lines=[p.line_id,p.catalog_group_id,...(p.operating_line_ids||[]),...(p.construction_line_ids||[])].filter(Boolean);
          if(mode==='metro')lines.push(...[p.route_relation_id,p.osm_relation_id,...(p.route_relation_ids||[])].filter(v=>v!==undefined).map(v=>'metro:relation/'+v));
          if(mode==='road')lines.push(...(p.route_keys||[]).map(v=>'road:route/'+v));
          const parents=[...new Set(lines.map(key=>this.aliases.get(mode+':'+key)).filter(Boolean))];
          if(parents.length===1)record=parents[0];
        }
        if(!record)return [feature];
        const value=state(record,this.day);
        if(value==='absent')return [];
        const properties={...p,history_id:record.id,history_state:value,history_date:this.day};
        if(value!=='unknown')Object.assign(properties,{construction_status:value,construction:value!=='operating'});
        if(value!=='unknown'&&Array.isArray(p.line_ids))Object.assign(properties,{
          operating_line_ids:value==='operating'?p.line_ids:[],construction_line_ids:value==='operating'?[]:p.line_ids
        });
        return [{...feature,properties}];
      })};
    }
  }
  root.RailScopeHistory={History,state};
})(globalThis);
