/* Vehicles move in a DOM overlay; infrastructure GeoJSON and label placement
   stay untouched. Timed vertices come from the shared timetable interpolator. */
(function(root) {
  'use strict';
  function coordinateAt(frames, elapsed) {
    if (!frames?.length) return null;
    let lo=0, hi=frames.length;
    while(lo<hi){const mid=(lo+hi)>>1;if(frames[mid][0]<=elapsed)lo=mid+1;else hi=mid;}
    const a=frames[Math.max(0,lo-1)], b=frames[Math.min(frames.length-1,lo)];
    const ratio=b[0]>a[0]?Math.max(0,Math.min(1,(elapsed-a[0])/(b[0]-a[0]))):0;
    return [a[1]+(b[1]-a[1])*ratio,a[2]+(b[2]-a[2])*ratio];
  }
  class VehicleOverlay {
    constructor(map, Marker, select, layer) {
      this.map=map;this.Marker=Marker;this.select=select;this.layer=layer;
      this.items=new Map();this.visible=false;this.paused=false;this.frame=0;this.selected=new Set();
      this.labelSettings={size:12,minZoom:11};
      map.on?.('zoom',()=>this.refreshLabels());
    }
    update(data, playing) {
      const now=performance.now(), keep=new Set();
      for(const feature of data.features||[]) {
        const p=feature.properties, key=String(p.vehicle_id||p.trip_id);keep.add(key);
        let item=this.items.get(key);
        if(!item){
          const element=document.createElement('button');element.type='button';element.className='moving-vehicle';
          const dot=document.createElement('span');dot.className='moving-vehicle-dot';
          const label=document.createElement('span');label.className='moving-vehicle-label';
          element.append(dot,label);
          item={element,dot,label,marker:new this.Marker({element,anchor:'center',subpixelPositioning:true}),feature};
          element.addEventListener('click',event=>{event.stopPropagation();this.select({...item.feature,layer:{id:this.layer}},event.ctrlKey);});
          this.items.set(key,item);
          item.marker.setLngLat(feature.geometry.coordinates).addTo(this.map);
        }
        item.feature=feature;item.started=now;
        item.frames=playing&&p.motion_frames?.length?p.motion_frames:[[0,...feature.geometry.coordinates]];
        item.label.textContent=p.trip_id||p.vehicle_id||'';
        this.updateLabel(item);
        item.element.title=p.name||item.label.textContent;
        item.element.setAttribute('aria-label',item.element.title);
        item.element.dataset.style=p.display_style||'glow';
        item.element.style.setProperty('--vehicle-color',/^#[0-9a-f]{3,8}$/i.test(p.display_color||'')?p.display_color:'#0c776f');
        item.element.style.setProperty('--vehicle-size',Math.max(8,Math.min(40,Number(p.display_size)||14))+'px');
        item.dot.textContent=p.display_style==='train'?'车':'';
        item.element.dataset.selected=String(this.selected.has(key));
        item.element.style.display=this.visible&&!this.paused?'':'none';
        item.marker.setLngLat(feature.geometry.coordinates);
      }
      for(const [key,item] of this.items)if(!keep.has(key)){item.marker.remove();this.items.delete(key);}
      this.start();
    }
    setVisible(value){this.visible=!!value;for(const item of this.items.values())item.element.style.display=this.visible&&!this.paused?'':'none';this.start();}
    setPaused(value){this.paused=!!value;this.setVisible(this.visible);}
    setLabelSettings(value){
      const valid=(key,low,high,fallback)=>Number.isInteger(value?.[key])&&value[key]>=low&&value[key]<=high?value[key]:fallback;
      this.labelSettings={size:valid('size',8,32,12),minZoom:valid('minZoom',0,22,11)};
      this.refreshLabels();
    }
    labelVisible(){return (this.map.getZoom?.()??22)>=this.labelSettings.minZoom;}
    updateLabel(item){item.label.style.fontSize=this.labelSettings.size+'px';item.label.style.display=this.labelVisible()?'':'none';}
    refreshLabels(){for(const item of this.items.values())this.updateLabel(item);}
    setSelected(ids){this.selected=new Set(ids.map(String));for(const [key,item] of this.items)item.element.dataset.selected=String(this.selected.has(key));}
    start(){if(!this.frame&&this.visible&&!this.paused&&!document.hidden)this.frame=requestAnimationFrame(now=>this.tick(now));}
    tick(now){
      this.frame=0;if(!this.visible||this.paused||document.hidden)return;
      let moving=false;
      for(const item of this.items.values()){
        const elapsed=now-item.started, coordinate=coordinateAt(item.frames,elapsed);
        if(coordinate)item.marker.setLngLat(coordinate);
        moving ||= elapsed<item.frames.at(-1)[0];
      }
      if(moving)this.start();
    }
    draw(ctx,scale=1){
      if(!this.visible||this.paused)return;
      for(const item of this.items.values()){
        const point=this.map.project(item.marker.getLngLat()),p=item.feature.properties;
        ctx.fillStyle=p.display_color||'#0c776f';ctx.strokeStyle='#fff';ctx.lineWidth=2*scale;
        const radius=(p.display_size||14)/2*scale;
        ctx.save();
        if(p.display_style==='train'){
          ctx.fillRect(point.x*scale-radius,point.y*scale-radius,radius*2,radius*2);
          ctx.strokeRect(point.x*scale-radius,point.y*scale-radius,radius*2,radius*2);
          ctx.fillStyle='#fff';ctx.font=`bold ${radius*1.4}px sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText('车',point.x*scale,point.y*scale);
        }else{
          ctx.beginPath();ctx.arc(point.x*scale,point.y*scale,radius,0,Math.PI*2);
          if(p.display_style==='ring')ctx.strokeStyle=ctx.fillStyle;else ctx.fill();
          ctx.stroke();
        }
        ctx.restore();
        if(this.labelVisible()){
          ctx.font=`${this.labelSettings.size*scale}px sans-serif`;
          ctx.fillText(p.trip_id||p.vehicle_id||'',point.x*scale,(point.y-(p.display_size||14)/2-4)*scale);
        }
      }
    }
  }
  root.RailScopeMotion={VehicleOverlay,coordinateAt};
  if(typeof module!=='undefined')module.exports={coordinateAt,VehicleOverlay};
})(typeof window!=='undefined'?window:globalThis);
