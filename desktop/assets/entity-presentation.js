/* Index only resident viewport features. Python owns naming/style semantics. */
(function(root){
  function ownerKeys(p){
    const keys=[p.catalog_group_id,p.line_id];
    for(const field of ['service_id','network_edge_id','network_node_id','section_id','infrastructure_id','catalog_id','station_id','route_key','osm_node_id','osm_way_id','osm_relation_id'])
      if(p[field]!==undefined&&p[field]!==null){keys.push('object:'+field+':'+p[field]);break;}
    const tags=typeof p.way_tags==='object'?p.way_tags:{};
    if(String(p.catalog_group_id||'').startsWith('ST-')||['yard','siding'].includes(p.service)||['yard','siding'].includes(tags.service))
      for(const field of ['section_id','network_edge_id'])if(p[field]){keys.push('object:'+field+':'+p[field]);break;}
    for(const field of ['station_source_id','infrastructure_id','station_key','station_id'])
      if(p[field])keys.push('station:'+String(p[field]).replace(/^station:/,''));
    if(p.osm_node_id!==undefined)keys.push('switch:node/'+p.osm_node_id,'node:'+p.osm_node_id,'station:node/'+p.osm_node_id);
    let associated=p.associated_station_ids||[];
    if(typeof associated==='string'){try{associated=JSON.parse(associated);}catch{associated=[];}}
    if(associated.length===1)keys.push('station:'+String(associated[0]).replace(/^station:/,''));
    return keys.filter(Boolean);
  }
  class EntityPresentation {
    constructor(request,paint,onError){
      this.request=request;this.paint=paint;this.onError=onError;
      this.sources=new Map();this.owners=new Map();this.queue=new Set();this.changed=new Set();this.running=false;
    }
    register(source,data){
      for(const [key,refs] of this.owners){
        for(const ref of refs)if(ref.source===source)refs.delete(ref);
        if(!refs.size)this.owners.delete(key);
      }
      this.sources.set(source,data);
      for(const feature of data.features||[]){
        const ref={source,feature,base:structuredClone(feature.properties||{})};
        for(const key of ownerKeys(ref.base)){
          if(!this.owners.has(key))this.owners.set(key,new Set());
          this.owners.get(key).add(ref);
          if(this.changed.has(key))this.queue.add(key);
        }
      }
      this.drain();
    }
    patch(changes){
      for(const key of Object.keys(changes)){this.changed.add(key);this.queue.add(key);}
      return this.drain();
    }
    async drain(){
      if(this.running||!this.queue.size)return;
      this.running=true;
      try{
        while(this.queue.size){
          const refs=new Set();
          for(const key of this.queue)for(const ref of this.owners.get(key)||[])refs.add(ref);
          this.queue.clear();
          if(!refs.size)continue;
          const batch=[...refs];
          const features=batch.map(ref=>({properties:ref.base,geometry:{type:ref.feature.geometry.type}}));
          let values;
          try{values=await this.request(features);}
          catch{values=await this.request(features);} // One transient bridge retry.
          if(values.length!==batch.length)throw new Error('对象增量响应数量无效');
          const dirty=new Set();
          batch.forEach((ref,i)=>{
            const current=this.sources.get(ref.source);
            if(!current?.features.includes(ref.feature))return;
            ref.feature.properties=values[i];dirty.add(ref.source);
          });
          for(const source of dirty)this.paint(source,this.sources.get(source));
        }
      }catch(error){this.onError(error);}
      finally{this.running=false;}
    }
  }
  root.RailScopeEntityPresentation={EntityPresentation,ownerKeys};
})(globalThis);
