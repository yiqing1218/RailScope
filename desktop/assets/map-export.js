/* Independent print render: same CSS viewport/camera, higher physical pixel ratio. */
window.exportRailScopeMap = async function(source, width, overlays = []) {
  const container = source.getContainer(), w = container.clientWidth, h = container.clientHeight;
  const ratio = Math.max(1, width / w), height = Math.round(h * ratio);
  const host = document.createElement('div');
  host.style.cssText = `position:fixed;left:-20000px;top:0;width:${w}px;height:${h}px;pointer-events:none`;
  document.body.appendChild(host);
  let printMap;
  try {
    const style = structuredClone(source.getStyle());
    // Selection is an editing affordance, not a permanent map annotation.
    if(style.sources.selection)style.sources.selection.data={type:'FeatureCollection',features:[]};
    printMap = new window.maplibregl.Map({container:host,style,
      center:source.getCenter(),zoom:source.getZoom(),bearing:source.getBearing(),pitch:source.getPitch(),
      padding:source.getPadding(),pixelRatio:ratio,maxCanvasSize:[width,height],
      interactive:false,attributionControl:false,preserveDrawingBuffer:true,fadeDuration:0});
    printMap.on('styleimagemissing', event=>{
      const value=source.getImage(event.id);
      if(value&&!printMap.hasImage(event.id))printMap.addImage(event.id,value.data,{pixelRatio:value.pixelRatio,sdf:value.sdf});
    });
    await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>reject(new Error('高质量地图加载超时，请等待当前地图瓦片完整后重试')),45000);
      printMap.once('idle',()=>{clearTimeout(timeout);resolve();});
    });
    const rendered=printMap.getCanvas();
    if(rendered.width<width*.98)throw new Error('显卡最大画布尺寸不足，请降低导出宽度');
    const canvas=document.createElement('canvas');canvas.width=rendered.width;canvas.height=rendered.height;
    const ctx=canvas.getContext('2d');ctx.drawImage(rendered,0,0);
    for(const overlay of overlays)overlay?.draw(ctx,ratio);
    ctx.save();ctx.scale(ratio,ratio);
    const legend=document.getElementById('map-legend');
    if(legend&&!legend.hidden&&getComputedStyle(legend).display!=='none'){
      const rect=legend.getBoundingClientRect(), base=container.getBoundingClientRect();
      const x=rect.left-base.left,y=rect.top-base.top;
      ctx.fillStyle='rgba(255,255,255,.94)';ctx.fillRect(x,y,rect.width,rect.height);
      for(const element of legend.querySelectorAll('strong,span,i')){
        if(element.tagName==='SPAN'&&element.children.length)continue;
        const r=element.getBoundingClientRect(),css=getComputedStyle(element);
        const left=r.left-base.left,top=r.top-base.top;
        if(element.tagName==='I'){
          ctx.fillStyle=css.backgroundColor;ctx.fillRect(left,top,r.width,r.height);
          if(parseFloat(css.borderTopWidth)){ctx.strokeStyle=css.borderTopColor;ctx.lineWidth=parseFloat(css.borderTopWidth);ctx.setLineDash(css.borderTopStyle==='dashed'?[4,3]:[]);ctx.beginPath();ctx.moveTo(left,top);ctx.lineTo(left+r.width,top);ctx.stroke();ctx.setLineDash([]);}
        }else{ctx.font=css.font;ctx.fillStyle=css.color;ctx.textBaseline='middle';ctx.fillText(element.textContent,left,top+r.height/2);}
      }
    }
    ctx.font='11px sans-serif';ctx.fillStyle='rgba(255,255,255,.93)';ctx.fillRect(0,h-23,w,23);
    ctx.fillStyle='#263b47';ctx.textBaseline='alphabetic';
    ctx.fillText(document.querySelector('.maplibregl-ctrl-attrib-inner')?.textContent||'© OpenStreetMap contributors',8,h-7);
    ctx.restore();return canvas.toDataURL('image/png');
  } finally {printMap?.remove();host.remove();}
};
