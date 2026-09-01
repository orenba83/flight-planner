(function(){
  function hid(id,val){if(document.getElementById(id))return;const i=document.createElement('input');i.id=id;i.type='hidden';i.value=String(val);document.body.appendChild(i)}
  hid('lpol',0);hid('lsys',0);hid('lossamp',24);
  const grid=$('gridn');
  function syncLos(){const el=$('lossamp');if(el&&grid)el.value=grid.value}
  if(grid){grid.addEventListener('change',syncLos);grid.addEventListener('input',syncLos);syncLos()}
  document.querySelectorAll('.tabs button').forEach(btn=>{
    btn.onclick=()=>{
      document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));
      document.querySelectorAll('.tabpane').forEach(p=>p.classList.remove('on'));
      btn.classList.add('on');
      const pane=$('tab-'+btn.dataset.tab);if(pane)pane.classList.add('on');
    };
  });
  if($('mRuler')) $('mRuler').onclick=()=>setMode(mode==='ruler'?(hasAnalysis?'idle':'path'):'ruler');
  const _params=params;
  params=function(){const p=_params();p.lpol=0;p.lsys=0;p.losN=p.n;p.ellP=90;p.useDtm=true;return p};
  function dfFromSamples(g,samples,acAlt,gElev,sigmaRad,pPct){
    if(!samples||!samples.length) return {phi:0,R1:0,R2:0,_a90:Infinity};
    const vs=samples.map(s=>vecFromGround(g,s,acAlt,gElev));
    let phi=0,R1=0,R2=0;
    if(vs.length===1){R1=R2=vlen(vs[0])}
    else{
      let best=-1,ii=0,jj=1;
      for(let i=0;i<vs.length;i++) for(let j=i+1;j<vs.length;j++){
        const a=angBetween(vs[i],vs[j]); if(a>best){best=a;ii=i;jj=j}
      }
      phi=Math.max(0,best); R1=vlen(vs[ii]); R2=vlen(vs[jj]);
    }
    let Ixx=0,Iyy=0,Ixy=0,n=0;
    const sig=Math.max(sigmaRad,0.05*Math.PI/180);
    for(const v of vs){
      const Rh=Math.sqrt(v.x*v.x+v.y*v.y);
      if(Rh<80) continue;
      const nx=-v.y/Rh, ny=v.x/Rh;
      const w=1/(sig*Rh)*(1/(sig*Rh));
      Ixx+=w*nx*nx; Iyy+=w*ny*ny; Ixy+=w*nx*ny; n++;
    }
    let a90=Infinity;
    if(n>=2){
      const det=Ixx*Iyy-Ixy*Ixy;
      if(det>1e-18){
        const Pxx=Iyy/det, Pyy=Ixx/det, Pxy=-Ixy/det;
        const tr=Pxx+Pyy;
        const disc=Math.sqrt(Math.max(0,tr*tr-4*(Pxx*Pyy-Pxy*Pxy)));
        a90=Math.sqrt(Math.max(0.5*(tr+disc),0))*chiScale(pPct);
      }
    }
    return {phi,R1,R2,_a90:a90};
  }
  bestAperture=function(g,samples,acAlt,gElev){
    const p=params();
    return dfFromSamples(g,samples,acAlt,gElev,p.dfSig*Math.PI/180,90);
  };
  ellipseA90=function(ap){return (ap&&isFinite(ap._a90))?ap._a90:Infinity};
  function fillAvgDist(){
    if(!lastCells.length||!path.length) return;
    const samples=resample(path,Math.max(2,+$('psamp').value|0));
    lastCells.forEach(c=>{
      const g={lat:(c.lat0+c.lat1)/2,lng:(c.lng0+c.lng1)/2};
      let s=0;for(const ac of samples)s+=hav(ac,g);
      c.avgDist=samples.length?s/samples.length:0;
    });
  }
  const _paint=paintHeat;
  paintHeat=function(kind){
    if(kind!=='ell'){const req=minSnrReq();snrScale={cmin:req-10,cmax:req+10}}
    fillAvgDist();
    _paint(kind);
    heatLayer.eachLayer(layer=>{
      const b=layer.getBounds&&layer.getBounds();
      if(!b) return;
      const c=lastCells.find(x=>Math.abs(x.lat0-b.getSouth())<1e-8&&Math.abs(x.lng0-b.getWest())<1e-8);
      if(!c) return;
      const v=cellSnr(c);
      const tip=kind==='ell'
        ?(snrOk(v)?('a90 '+fmtKm(c.a90)+' · SNR '+v.toFixed(1)+' dB · avg range '+fmtKm(c.avgDist||0)):('SNR '+v.toFixed(1)+' dB < min · avg range '+fmtKm(c.avgDist||0)))
        :(v.toFixed(1)+' dB · '+freqLabel()+' · a90 '+ellText(c)+' · avg range '+fmtKm(c.avgDist||0));
      layer.bindTooltip(tip,{sticky:true});
    });
    if(kind!=='ell') $('legTitle').textContent='SNR (dB) — min req ±10';
  };
  function stripPathNumbers(){markers.forEach(m=>{try{m.unbindTooltip()}catch(e){}})}
  function pathSampleCount(){return Math.max(2,+$('psamp').value|0)}
  function clampPointIndex(){
    const el=$('wpIndex'); if(!el) return 1;
    const n=pathSampleCount();
    el.min=1; el.max=n;
    let i=+$('wpIndex').value|0;
    if(!Number.isFinite(i)||i<1) i=1;
    if(i>n) i=n;
    if(String(el.value)!==String(i)) el.value=i;
    return i;
  }
  let wpRing=null, wpDot=null;
  function syncSingleUi(){
    const single=$('viewMode')&&$('viewMode').value==='single';
    const box=$('wpIndexField');
    if(box) box.style.display=single?'block':'none';
    if(!single){
      if(wpRing){try{map.removeLayer(wpRing)}catch(e){} wpRing=null}
      if(wpDot){try{map.removeLayer(wpDot)}catch(e){} wpDot=null}
    }
    const dtm=$('useDtm'); if(dtm) dtm.checked=true;
    const ell=$('ellP'); if(ell) ell.value=90;
  }
  function highlightWp(){
    syncSingleUi();
    if(wpRing){try{map.removeLayer(wpRing)}catch(e){} wpRing=null}
    if(wpDot){try{map.removeLayer(wpDot)}catch(e){} wpDot=null}
    if(!path.length) return;
    if(!$('viewMode')||$('viewMode').value!=='single') return;
    const idx=clampPointIndex();
    const samples=resample(path, pathSampleCount());
    const pt=samples[Math.min(samples.length-1, idx-1)];
    if(!pt) return;
    wpRing=L.circleMarker([pt.lat,pt.lng],{radius:18,color:'#facc15',weight:4,fillColor:'#facc15',fillOpacity:.2}).addTo(map);
    wpDot=L.circleMarker([pt.lat,pt.lng],{radius:7,color:'#111827',weight:2,fillColor:'#f59e0b',fillOpacity:1}).addTo(map);
    wpDot.bindTooltip(idx+' / '+samples.length,{permanent:true,direction:'top',offset:[0,-10]});
  }
  const _redraw=redrawPath;
  redrawPath=function(){_redraw();stripPathNumbers();highlightWp()};
  stripPathNumbers();
  clampPointIndex();
  highlightWp();
  let rerunT=0;
  function autoSingleHeat(){
    $('viewMode').value='single';
    highlightWp();
    if(!path.length||!aoi) return;
    clearTimeout(rerunT);
    rerunT=setTimeout(function(){runAnalysis()},250);
  }
  if($('wpIndex')){
    $('wpIndex').addEventListener('change',autoSingleHeat);
    $('wpIndex').addEventListener('input',highlightWp);
  }
  if($('psamp')){
    $('psamp').addEventListener('change',function(){clampPointIndex();highlightWp()});
    $('psamp').addEventListener('input',function(){clampPointIndex();highlightWp()});
  }
  if($('viewMode')){
    $('viewMode').addEventListener('change',function(){
      highlightWp();
      if($('viewMode').value==='single'&&path.length&&aoi) autoSingleHeat();
    });
  }
  syncSingleUi();
  if($('minSnr')) $('minSnr').addEventListener('change',function(){if(!hasAnalysis)return;const req=minSnrReq();snrScale={cmin:req-10,cmax:req+10};updateKpisFromCells();paintHeat(heatKind)});

  function collectConfig(){
    readGainTable();
    const fields={};
    FIELD_IDS.forEach(function(id){const el=$(id); if(!el) return; fields[id]=el.type==='checkbox'?el.checked:el.value});
    fields.ellP='90'; fields.useDtm=true;
    return {
      app:'flight-planner', v:1, savedAt:new Date().toISOString(),
      fields:fields,
      gainTable:gainTable.map(function(r){return [+r[0],+r[1]]}),
      path:path.map(function(p){return {lat:p.lat,lng:p.lng}}),
      aoi:aoi?[[aoi.getSouth(),aoi.getWest()],[aoi.getNorth(),aoi.getEast()]]:null,
      center:map.getCenter(), zoom:map.getZoom()
    };
  }
  function resetDrawing(){
    path=[];markers.forEach(function(m){try{map.removeLayer(m)}catch(e){}});markers=[];
    pathLayer.clearLayers();aoiLayer.clearLayers();heatLayer.clearLayers();
    aoi=null;hasAnalysis=false;lastCells=[];
    if(wpRing){try{map.removeLayer(wpRing)}catch(e){} wpRing=null}
    if(wpDot){try{map.removeLayer(wpDot)}catch(e){} wpDot=null}
    updateModeButtons();updateRun();
  }
  function applyConfig(data){
    if(!data||(data.app&&data.app!=='flight-planner')) throw new Error('Not a flight-planner configuration file');
    resetDrawing();
    if(data.fields){
      FIELD_IDS.forEach(function(id){
        const el=$(id); if(!el||data.fields[id]==null) return;
        if(el.type==='checkbox') el.checked=!!data.fields[id]; else el.value=data.fields[id];
      });
    }
    if($('ellP')) $('ellP').value='90';
    if($('useDtm')) $('useDtm').checked=true;
    if(Array.isArray(data.gainTable)&&data.gainTable.length) gainTable=data.gainTable.map(function(r){return [+r[0],+r[1]]});
    renderGainTable();
    if(Array.isArray(data.path)&&data.path.length) data.path.forEach(function(p){addPathPoint(+p.lat,+p.lng,true)});
    if(data.aoi) setAoi(L.latLngBounds(data.aoi[0],data.aoi[1]),true);
    if(data.center) map.setView(data.center,data.zoom||map.getZoom());
    persistAll(); updateRun(); syncSingleUi(); highlightWp();
    setBanner('<b>Configuration loaded.</b> Draw or Run Analysis.');
    const res=$('result'); if(res) res.textContent='Configuration loaded from file.';
  }
  if($('saveCfgBtn')) $('saveCfgBtn').onclick=function(){
    const blob=new Blob([JSON.stringify(collectConfig(),null,2)],{type:'application/json'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download='flight-planner-config.json';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function(){URL.revokeObjectURL(a.href)},500);
  };
  if($('loadCfgBtn')) $('loadCfgBtn').onclick=function(){if($('loadCfgFile')) $('loadCfgFile').click()};
  if($('loadCfgFile')) $('loadCfgFile').addEventListener('change',function(ev){
    const f=ev.target.files&&ev.target.files[0];
    ev.target.value='';
    if(!f) return;
    const reader=new FileReader();
    reader.onload=function(){
      try{ applyConfig(JSON.parse(String(reader.result||'{}'))); }
      catch(err){ alert('Could not load configuration: '+(err&&err.message?err.message:err)); }
    };
    reader.readAsText(f);
  });
})();
