/* Camera-only tools. No infrastructure edits or automatic data downloads. */
(function(root){
  function geometryBounds(features){
    let west=Infinity,south=Infinity,east=-Infinity,north=-Infinity;
    function visit(value){
      if(!Array.isArray(value))return;
      if(typeof value[0]==='number'&&typeof value[1]==='number'){
        if(!Number.isFinite(value[0])||!Number.isFinite(value[1]))return;
        west=Math.min(west,value[0]);east=Math.max(east,value[0]);south=Math.min(south,value[1]);north=Math.max(north,value[1]);
      }else for(const part of value)visit(part);
    }
    for(const feature of features||[])visit(feature.geometry?.coordinates);
    return west===Infinity?null:[[west,south],[east,north]];
  }
  class Navigation {
    constructor(map,document,actions={}){
      this.map=map;this.document=document;this.actions=actions;this.history=[];this.index=-1;this.replaying=false;
      const bind=(id,callback)=>{const button=document.getElementById(id);if(button)button.onclick=callback;};
      bind('view-back',()=>this.back());bind('view-forward',()=>this.forward());
      bind('focus-china',()=>actions.home?.());bind('zoom-in',()=>map.zoomIn());bind('zoom-out',()=>map.zoomOut());
      bind('north',()=>map.easeTo({bearing:0,pitch:0,duration:450}));
      bind('tilt',()=>map.easeTo({pitch:map.getPitch()>20?0:45,duration:450}));
      bind('open-location',()=>actions.location?.());bind('close-location',()=>actions.location?.(false));
      bind('fit-selected',()=>actions.selected?.());bind('fit-visible',()=>actions.visible?.());
      const input=document.getElementById('coordinate-view');
      input?.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();document.getElementById('coordinate-go')?.click();}});
      document.addEventListener('keydown',event=>{
        if(event.key==='Escape')actions.location?.(false);
        if(/INPUT|TEXTAREA|SELECT/.test(event.target?.tagName)||event.target?.isContentEditable)return;
        if(event.altKey&&event.key==='ArrowLeft'){event.preventDefault();this.back();}
        if(event.altKey&&event.key==='ArrowRight'){event.preventDefault();this.forward();}
      });
      map.on('moveend',()=>{this.record();this.update();});
      map.on('move',()=>this.update());
      this.record();this.update();
    }
    camera(){const p=this.map.getCenter();return {center:[p.lng,p.lat],zoom:this.map.getZoom(),bearing:this.map.getBearing(),pitch:this.map.getPitch()};}
    record(){
      if(this.replaying){this.replaying=false;return;}
      if(this.actions.shouldRecord&&!this.actions.shouldRecord())return;
      const camera=this.camera(),previous=this.history[this.index];
      if(previous&&JSON.stringify(previous)===JSON.stringify(camera))return;
      this.history.splice(this.index+1);this.history.push(camera);
      if(this.history.length>40)this.history.shift();
      this.index=this.history.length-1;
    }
    go(index){
      if(index<0||index>=this.history.length)return;
      this.index=index;this.replaying=true;this.map.easeTo({...this.history[index],duration:400});this.update();
    }
    back(){this.go(this.index-1);}
    forward(){this.go(this.index+1);}
    update(){
      const back=this.document.getElementById('view-back'),forward=this.document.getElementById('view-forward');
      if(back)back.disabled=this.index<=0;
      if(forward)forward.disabled=this.index>=this.history.length-1;
      const zoom=this.document.getElementById('zoom-level');if(zoom)zoom.textContent=this.map.getZoom().toFixed(1);
      const compass=this.document.getElementById('compass-arrow');if(compass)compass.style.transform=`rotate(${-this.map.getBearing()}deg)`;
      const tilt=this.document.getElementById('tilt');if(tilt)tilt.setAttribute('aria-pressed',String(this.map.getPitch()>20));
    }
  }
  root.RailScopeNavigation={Navigation,geometryBounds};
})(globalThis);
