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
  params=function(){const p=_params();p.lpol=0;p.lsys=0;p.losN=p.n;return p};

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
        const l1=0.5*(tr+disc);
        a90=Math.sqrt(Math.max(l1,0))*chiScale(pPct);
      }
    }
    return {phi,R1,R2,_a90:a90};
  }
  bestAperture=function(g,samples,acAlt,gElev){
    const p=params();
    return dfFromSamples(g,samples,acAlt,gElev,p.dfSig*Math.PI/180,p.ellP);
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

  function stripPathNumbers(){
    markers.forEach(m=>{try{m.unbindTooltip()}catch(e){}});
  }
  let wpRing=null;
  function highlightWp(){
    if(wpRing){try{pathLayer.removeLayer(wpRing)}catch(e){} wpRing=null}
    const i=Math.max(1,+$('wpIndex').value|0)-1;
    if(!path[i]) return;
    if($('viewMode').value!=='single') return;
    wpRing=L.circleMarker([path[i].lat,path[i].lng],{radius:16,color:'#facc15',weight:4,fillColor:'#facc15',fillOpacity:.25}).addTo(pathLayer);
    try{map.panTo([path[i].lat,path[i].lng],{animate:true})}catch(e){}
  }
  const _redraw=redrawPath;
  redrawPath=function(){_redraw();stripPathNumbers();highlightWp()};
  stripPathNumbers();

  let rerunT=0;
  function autoSingleHeat(){
    $('viewMode').value='single';
    highlightWp();
    if(!path.length||!aoi) return;
    clearTimeout(rerunT);
    rerunT=setTimeout(()=>{runAnalysis()},250);
  }
  if($('wpIndex')){
    $('wpIndex').addEventListener('change',autoSingleHeat);
    $('wpIndex').addEventListener('input',()=>{highlightWp()});
  }
  if($('viewMode')){
    $('viewMode').addEventListener('change',()=>{
      highlightWp();
      if($('viewMode').value==='single'&&path.length&&aoi) autoSingleHeat();
    });
  }
  if($('minSnr')) $('minSnr').addEventListener('change',()=>{if(!hasAnalysis)return;const req=minSnrReq();snrScale={cmin:req-10,cmax:req+10};updateKpisFromCells();paintHeat(heatKind)});
})();
