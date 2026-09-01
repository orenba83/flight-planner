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
  if($('mRuler')) $('mRuler').onclick=()=>setMode(mode==='ruler'?(hasAnalysis?'path':'path'):'ruler');
  const _params=params;
  params=function(){const p=_params();p.lpol=0;p.lsys=0;p.losN=p.n;return p};
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
  if($('minSnr')) $('minSnr').addEventListener('change',()=>{if(!hasAnalysis)return;const req=minSnrReq();snrScale={cmin:req-10,cmax:req+10};updateKpisFromCells();paintHeat(heatKind)});
})();
