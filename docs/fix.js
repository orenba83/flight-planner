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
  params=function(){const p=_params();p.lpol=0;p.lsys=0;p.losN=p.n;p.ellP=90;return p};

  const K90=Math.sqrt(-2*Math.log(0.1));

  function dfSamplesAlongPath(nWant){
    if(!path.length) return [];
    const n=Math.max(24, nWant|0, path.length*3);
    return resample(path, n);
  }

  function dfEllipse90(g, acPts, acAlt, gElev, sigmaDeg){
    const sigma=Math.max(0.05, +sigmaDeg||2)*Math.PI/180;
    let Ixx=0,Iyy=0,Ixy=0,used=0,phi=0,R1=0,R2=0;
    const vs=[];
    for(const ac of acPts){
      const v=vecFromGround(g,ac,acAlt,gElev);
      vs.push(v);
      const E=v.x,N=v.y,Rh=Math.hypot(E,N);
      if(Rh<150) continue;
      const r2=Rh*Rh;
      const je=N/r2, jn=-E/r2;
      const w=1/(sigma*sigma);
      Ixx+=w*je*je; Iyy+=w*jn*jn; Ixy+=w*je*jn;
      used++;
    }
    if(vs.length===1){R1=R2=vlen(vs[0])}
    else if(vs.length>1){
      let best=-1,ii=0,jj=1;
      for(let i=0;i<vs.length;i++) for(let j=i+1;j<vs.length;j++){
        const a=angBetween(vs[i],vs[j]); if(a>best){best=a;ii=i;jj=j}
      }
      phi=Math.max(0,best); R1=vlen(vs[ii]); R2=vlen(vs[jj]);
    }
    let a90=Infinity;
    if(used>=2){
      const det=Ixx*Iyy-Ixy*Ixy;
      if(det>1e-24){
        const Pxx=Iyy/det, Pyy=Ixx/det, Pxy=-Ixy/det;
        const tr=Pxx+Pyy;
        const disc=Math.sqrt(Math.max(0,tr*tr-4*(Pxx*Pyy-Pxy*Pxy)));
        a90=K90*Math.sqrt(Math.max(0.5*(tr+disc),0));
      }
    }
    return {phi,R1,R2,_a90:a90,used};
  }

  bestAperture=function(g,samples,acAlt,gElev){
    const p=params();
    const pts=dfSamplesAlongPath(p.samples);
    return dfEllipse90(g, pts.length?pts:samples, acAlt, gElev, p.dfSig);
  };
  ellipseA90=function(ap){return (ap&&Number.isFinite(ap._a90))?ap._a90:Infinity};

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
        ?(snrOk(v)?('a90 '+fmtKm(c.a90)+' \u00b7 SNR '+v.toFixed(1)+' dB \u00b7 avg range '+fmtKm(c.avgDist||0)):('SNR '+v.toFixed(1)+' dB < min \u00b7 avg range '+fmtKm(c.avgDist||0)))
        :(v.toFixed(1)+' dB \u00b7 '+freqLabel()+' \u00b7 a90 '+ellText(c)+' \u00b7 avg range '+fmtKm(c.avgDist||0));
      layer.bindTooltip(tip,{sticky:true});
    });
    if(kind!=='ell') $('legTitle').textContent='SNR (dB) \u2014 min req \u00b110';
  };

  function stripPathNumbers(){markers.forEach(m=>{try{m.unbindTooltip()}catch(e){}})}
  let wpRing=null;
  function highlightWp(){
    if(wpRing){try{pathLayer.removeLayer(wpRing)}catch(e){} wpRing=null}
    const i=Math.max(1,+$('wpIndex').value|0)-1;
    if(!path[i]) return;
    if($('viewMode').value!=='single') return;
    wpRing=L.circleMarker([path[i].lat,path[i].lng],{radius:16,color:'#facc15',weight:4,fillColor:'#facc15',fillOpacity:.25}).addTo(pathLayer);
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
    rerunT=setTimeout(function(){runAnalysis()},250);
  }
  if($('wpIndex')){
    $('wpIndex').addEventListener('change',autoSingleHeat);
    $('wpIndex').addEventListener('input',highlightWp);
  }
  if($('viewMode')){
    $('viewMode').addEventListener('change',function(){
      highlightWp();
      if($('viewMode').value==='single'&&path.length&&aoi) autoSingleHeat();
    });
  }
  if($('minSnr')) $('minSnr').addEventListener('change',function(){if(!hasAnalysis)return;const req=minSnrReq();snrScale={cmin:req-10,cmax:req+10};updateKpisFromCells();paintHeat(heatKind)});
})();
