(function () {
'use strict';
const TX_KEY='fp-transmitters-v1', txLayer=L.layerGroup().addTo(map);
let transmitters=[],placing=false,linkLine=null;
const el=id=>document.getElementById(id), clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const km=m=>(m/1000).toFixed(2)+' km', fmt=(v,n=1)=>Number.isFinite(v)?v.toFixed(n):'—';
function save(){try{localStorage.setItem(TX_KEY,JSON.stringify(transmitters))}catch(_){}}
function icon(i){return L.divIcon({className:'',html:'<div class="tx-marker"><span>'+i+'</span></div>',iconSize:[30,30],iconAnchor:[15,28]})}
function render(){
 txLayer.clearLayers();
 transmitters.forEach((t,i)=>{const m=L.marker([t.lat,t.lng],{icon:icon(i+1),draggable:true,title:'Transmitter '+(i+1)}).addTo(txLayer);
 m.bindTooltip('TX '+(i+1)+' · 2 m AGL');m.on('click',e=>{L.DomEvent.stopPropagation(e);const ground=Number(elevAt(t.lat,t.lng));if(typeof window.showSelectedMapPoint==='function')window.showSelectedMapPoint({label:'Transmitter '+(i+1),lat:t.lat,lng:t.lng,elevation:Number.isFinite(ground)?ground:null,heightAgl:2})});m.on('dragend',()=>{const p=m.getLatLng();transmitters[i]={lat:p.lat,lng:p.lng,heightAgl:2};save();renderUi()})});
 renderUi();
}
function renderUi(){
 const list=el('txList'),a=el('linkTxA'),b=el('linkTxB');
 if(list)list.innerHTML=transmitters.length?transmitters.map((t,i)=>'<span class="tx-chip">TX '+(i+1)+' · '+t.lat.toFixed(4)+', '+t.lng.toFixed(4)+'</span>').join(''):'<span class="hint">No transmitters placed.</span>';
 [a,b].forEach((s,k)=>{if(!s)return;const old=s.value;s.innerHTML='';transmitters.forEach((_,i)=>{const o=document.createElement('option');o.value=String(i);o.textContent='Transmitter '+(i+1);s.appendChild(o)});if(transmitters.length)s.value=[...s.options].some(o=>o.value===old)?old:String(Math.min(k,transmitters.length-1));s.disabled=transmitters.length<2});
 if(el('runLinkBtn'))el('runLinkBtn').disabled=transmitters.length<2;
}
function placement(on){
 placing=!!on;
 if(placing){const linkTab=document.querySelector('.tabs button[data-tab="link"]');if(linkTab&&!linkTab.classList.contains('on'))linkTab.click()}
 if(typeof setMode==='function')setMode(placing?'tx':'path');
 if(el('mTx'))el('mTx').classList.toggle('active',placing);
 if(el('startTxPlacement'))el('startTxPlacement').textContent=placing?'Placement active · click map':'Place transmitter on map';
 if(placing){setBanner('<b>Transmitter placement:</b> Click map to add stations (max 10). Click again to exit.');el('sheet').classList.add('open')}
}
const baseSetMode=setMode;
setMode=function(next){if(next!=='tx'&&placing){placing=false;if(el('mTx'))el('mTx').classList.remove('active');if(el('startTxPlacement'))el('startTxPlacement').textContent='Place transmitter on map'}const r=baseSetMode(next);if(next!=='tx'&&el('mTx'))el('mTx').classList.remove('active');return r};
if(el('mTx'))el('mTx').addEventListener('click',()=>placement(!placing));
if(el('startTxPlacement'))el('startTxPlacement').addEventListener('click',()=>placement(!placing));
map.on('click',e=>{if(!placing)return;if(transmitters.length>=10){setBanner('<b>Limit reached:</b> Maximum 10 transmitters.');placement(false);return}
 transmitters.push({lat:e.latlng.lat,lng:e.latlng.lng,heightAgl:2});save();render();setBanner('<b>Transmitter '+transmitters.length+' added.</b> Continue placing or exit placement mode.')});
if(el('clearTransmitters'))el('clearTransmitters').addEventListener('click',()=>{transmitters=[];save();txLayer.clearLayers();if(linkLine){map.removeLayer(linkLine);linkLine=null}renderUi();el('linkResult').textContent='All transmitters cleared.'});
function gridDimensions(){
 const out=el('gridSizeInfo');if(!out)return;if(!aoi){out.textContent='Draw AOI to calculate cell dimensions.';return}
 const n=clamp(parseInt((el('gridn')||{}).value,10)||24,10,40),mid=(aoi.getSouth()+aoi.getNorth())/2;
 const w=hav({lat:mid,lng:aoi.getWest()},{lat:mid,lng:aoi.getEast()}),h=hav({lat:aoi.getSouth(),lng:aoi.getWest()},{lat:aoi.getNorth(),lng:aoi.getWest()}),cw=w/n,ch=h/n;
 out.innerHTML='<b>'+(n*n).toLocaleString()+' cells</b> · each ≈ <b>'+(cw/1000).toFixed(3)+' × '+(ch/1000).toFixed(3)+' km</b><br>AOI ≈ '+(w/1000).toFixed(2)+' × '+(h/1000).toFixed(2)+' km · cell area ≈ '+(cw*ch/1e6).toFixed(4)+' km²';
}
if(el('gridn'))el('gridn').addEventListener('input',gridDimensions);
if(el('clearBtn'))el('clearBtn').addEventListener('click',()=>setTimeout(gridDimensions,0));
if(typeof setAoi==='function'){const oldSetAoi=setAoi;setAoi=function(){const r=oldSetAoi.apply(this,arguments);gridDimensions();return r}}
gridDimensions();
function profileSvg(samples,D){
 const W=820,H=250,p={l:42,r:14,t:18,b:30},vals=samples.flatMap(s=>[s.terrain,s.ray,s.ray+s.fresnel*.6,s.ray-s.fresnel*.6]);
 let lo=Math.min(...vals),hi=Math.max(...vals);if(hi-lo<10){lo-=5;hi+=5}
 const x=d=>p.l+(W-p.l-p.r)*d/D,y=h=>H-p.b-(H-p.t-p.b)*(h-lo)/(hi-lo);
 const pts=k=>samples.map(s=>x(s.d).toFixed(1)+','+y(k(s)).toFixed(1)).join(' ');
 const terr=pts(s=>s.terrain),ray=pts(s=>s.ray),up=pts(s=>s.ray+s.fresnel*.6),dn=pts(s=>s.ray-s.fresnel*.6);
 return '<svg class="link-profile" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Terrain, line of sight and Fresnel profile"><line x1="'+p.l+'" y1="'+(H-p.b)+'" x2="'+(W-p.r)+'" y2="'+(H-p.b)+'" stroke="#64748b"/><polygon points="'+up+' '+dn.split(' ').reverse().join(' ')+'" fill="#38bdf8" opacity=".13"/><polyline points="'+up+'" fill="none" stroke="#38bdf8" stroke-dasharray="4 4"/><polyline points="'+dn+'" fill="none" stroke="#38bdf8" stroke-dasharray="4 4"/><polyline points="'+ray+'" fill="none" stroke="#fbbf24" stroke-width="2.5"/><polyline points="'+terr+'" fill="none" stroke="#86efac" stroke-width="2.5"/><text x="'+p.l+'" y="12" fill="#cbd5e1" font-size="11">Elevation (m AMSL)</text><text x="'+W/2+'" y="'+(H-6)+'" text-anchor="middle" fill="#cbd5e1" font-size="11">Distance (km)</text><text x="'+(p.l+5)+'" y="'+(p.t+12)+'" fill="#86efac" font-size="10">Terrain</text><text x="'+(p.l+65)+'" y="'+(p.t+12)+'" fill="#fbbf24" font-size="10">LOS</text><text x="'+(p.l+110)+'" y="'+(p.t+12)+'" fill="#38bdf8" font-size="10">60% Fresnel zone</text></svg>';
}
async function calculate(){
 if(transmitters.length<2)return;const ia=+el('linkTxA').value,ib=+el('linkTxB').value;
 if(ia===ib){el('linkResult').textContent='Select two different transmitters.';return}
 const A=transmitters[ia],B=transmitters[ib],out=el('linkResult'),btn=el('runLinkBtn');
 btn.disabled=true;btn.textContent='Calculating…';out.textContent='Loading terrain profile…';
 try{
  const p=params(),D=hav(A,B);if(D<1)throw new Error('Transmitters are too close.');
  const count=clamp(Math.ceil(D/250),40,160),points=Array.from({length:count+1},(_,i)=>({lat:A.lat+(B.lat-A.lat)*i/count,lng:A.lng+(B.lng-A.lng)*i/count}));
  const source=await fetchElevations(points),ground=points.map(pt=>Number(elevCache[pt.lat.toFixed(4)+','+pt.lng.toFixed(4)])||0),hA=ground[0]+2,hB=ground[ground.length-1]+2;
  const profiles=p.freqs.map(f=>{
   const lambda=C/(f*1e6);
   const samples=points.map((pt,i)=>{const t=i/count,d=D*t,d2=D-d,bulge=d>0&&d2>0?d*d2/(2*K_EFF*R):0,ray=hA+(hB-hA)*t,fresnel=d>0&&d2>0?Math.sqrt(lambda*d*d2/D):0;return{d,terrain:ground[i]+bulge,ray,fresnel}});
   let vmax=-Infinity,minClear=Infinity,minFresnel=Infinity;
   for(let i=1;i<samples.length-1;i++){const q=samples[i],d1=q.d,d2=D-d1,clear=q.ray-q.terrain;minClear=Math.min(minClear,clear);minFresnel=Math.min(minFresnel,clear-.6*q.fresnel);const v=(q.terrain-q.ray)*Math.sqrt(2/lambda*(1/d1+1/d2));vmax=Math.max(vmax,v)}
   const diff=vmax<=-.78?0:Math.min(knifeEdgeLoss(vmax),60),fspl=20*Math.log10(4*Math.PI*D/lambda),gain=interpGain(f),noise=-174+10*Math.log10(p.bwHz)+p.nf;
   const rx=p.pt+2*gain-fspl-diff-p.lpol-p.lsys,snr=rx-noise,margin=snr-p.minSnr;
   return{f,samples,diff,fspl,gain,noise,rx,snr,margin,minClear,minFresnel};
  });
  const worst=profiles.reduce((a,b)=>a.margin<=b.margin?a:b),{f,samples,diff,fspl,gain,noise,rx,snr,margin,minClear,minFresnel}=worst;
  const status=margin>=6?'PASS':margin>=0?'MARGINAL':'FAIL',cls=status==='PASS'?'ok':status==='MARGINAL'?'warn':'bad';
  if(linkLine)map.removeLayer(linkLine);linkLine=L.polyline([[A.lat,A.lng],[B.lat,B.lng]],{color:status==='PASS'?'#22c55e':status==='MARGINAL'?'#f59e0b':'#ef4444',weight:4,dashArray:'8 5'}).addTo(map);linkLine.bindTooltip('TX '+(ia+1)+' ↔ TX '+(ib+1)+' · '+km(D));map.fitBounds(linkLine.getBounds().pad(.2));
  const frequencyRows=profiles.map(q=>'<div>'+fmt(q.f,3)+' MHz: SNR '+fmt(q.snr,1)+' dB · margin '+fmt(q.margin,1)+' dB · diffraction '+fmt(q.diff,1)+' dB</div>').join('');
  out.innerHTML='<div class="'+cls+'" style="font-size:16px;font-weight:800">'+status+' · Worst-case link margin '+fmt(margin,1)+' dB</div><b>Link:</b> TX '+(ia+1)+' ↔ TX '+(ib+1)+' · '+km(D)+'<br><b>Terrain source:</b> '+source+' · <b>Worst frequency:</b> '+fmt(f,3)+' MHz<br><b>Antennas:</b> 2 m AGL at both ends<hr style="border-color:#334155"><b>Worst-case path budget</b><br>FSPL: '+fmt(fspl,2)+' dB<br>Diffraction loss: '+fmt(diff,2)+' dB<br>Tx power: '+fmt(p.pt,1)+' dBm · Tx/Rx gain: '+fmt(gain,1)+' / '+fmt(gain,1)+' dBi<br><b>Received power:</b> '+fmt(rx,2)+' dBm<br>Noise floor: '+fmt(noise,2)+' dBm<br><b>SNR:</b> '+fmt(snr,2)+' dB · Required: '+fmt(p.minSnr,1)+' dB<br><b>LOS:</b> '+(minClear>=0?'Clear':'Blocked')+' · <b>60% Fresnel:</b> '+(minFresnel>=0?'Clear':'Intrusion')+'<br>Minimum terrain clearance: '+fmt(minClear,1)+' m · Minimum 60% Fresnel clearance: '+fmt(minFresnel,1)+' m<br><b>All configured frequencies</b>'+frequencyRows+'<br>Assumes identical radios and the same antenna gain at both ends.'+profileSvg(samples,D);
 }catch(err){out.textContent='Link calculation failed: '+(err.message||err)}
 finally{btn.disabled=transmitters.length<2;btn.textContent='Calculate radio link'}
}
if(el('runLinkBtn'))el('runLinkBtn').addEventListener('click',calculate);
try{const saved=JSON.parse(localStorage.getItem(TX_KEY)||'[]');if(Array.isArray(saved))transmitters=saved.slice(0,10).filter(t=>Number.isFinite(+t.lat)&&Number.isFinite(+t.lng)).map(t=>({lat:+t.lat,lng:+t.lng,heightAgl:2}))}catch(_){}
render();
window.FlightPlannerTransmitters={get:()=>transmitters.map(t=>({...t})),set:items=>{transmitters=Array.isArray(items)?items.slice(0,10).map(t=>({lat:+t.lat,lng:+t.lng,heightAgl:2})):[];save();render()}};
})();