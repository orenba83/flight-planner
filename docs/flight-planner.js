const C=299792458,R=6371000,K_EFF=4/3;
const GAIN=[[30,-35],[60,-24.5],[90,-12.5],[500,-1],[1000,3],[2000,3],[3000,2],[4500,2],[5000,2],[6000,4]];
const $=id=>document.getElementById(id);
const map=L.map('map',{zoomControl:true}).setView([31.8,35.0],7);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'OSM'}).addTo(map);
const heatLayer=L.layerGroup().addTo(map),aoiLayer=L.layerGroup().addTo(map),pathLayer=L.layerGroup().addTo(map);
let mode='path',path=[],markers=[],aoi=null,aoiCorners=[],hasAnalysis=false,dem=null,elevCache={},dtmSource='',demStats={min:0,max:0,mean:0},lastCells=[],heatKind='snr',ellScale={cmin:0,cmax:1},snrScale={cmin:0,cmax:1};
let progT0=0,progLastUi=0;
function showProgress(on){const w=$('progWrap');if(!w)return;w.classList.toggle('show',!!on);if(on){progT0=performance.now();progLastUi=0;setProgress(0,'Starting…')}}
function setProgress(pct,label){
  pct=Math.max(0,Math.min(100,pct));
  const now=performance.now();
  if(pct<100&&now-progLastUi<80) return;
  progLastUi=now;
  if(label)$('progLabel').textContent=label;
  $('progPct').textContent=Math.round(pct)+'%';
  $('progFill').style.width=pct+'%';
  const elapsed=(now-progT0)/1000;
  if(pct>=0.5&&pct<100){
    const eta=elapsed*(100-pct)/Math.max(pct,0.01);
    const m=Math.floor(eta/60),s=Math.max(0,Math.round(eta%60));
    $('progEta').textContent=m>0?('~'+m+' min '+s+' s left · '+elapsed.toFixed(0)+' s elapsed'):('~'+s+' s left · '+elapsed.toFixed(0)+' s elapsed');
  }else if(pct>=100){
    $('progEta').textContent='Done in '+elapsed.toFixed(1)+' s';
  }else{
    $('progEta').textContent='Estimating time…';
  }
}
function yieldUi(){return new Promise(r=>setTimeout(r,0))}
function interpGain(f){if(f<=GAIN[0][0])return GAIN[0][1];if(f>=GAIN[GAIN.length-1][0])return GAIN[GAIN.length-1][1];for(let i=1;i<GAIN.length;i++){const [f0,g0]=GAIN[i-1],[f1,g1]=GAIN[i];if(f<=f1)return g0+(g1-g0)*(f-f0)/(f1-f0)}return GAIN[GAIN.length-1][1]}
function hav(a,b){const p=Math.PI/180,dLat=(b.lat-a.lat)*p,dLon=(b.lng-a.lng)*p,x=Math.sin(dLat/2)**2+Math.cos(a.lat*p)*Math.cos(b.lat*p)*Math.sin(dLon/2)**2;return 2*R*Math.asin(Math.min(1,Math.sqrt(x)))}
function params(){const fmin=+$('fmin').value,fmax=Math.max(+$('fmax').value,fmin),steps=Math.max(1,+$('fsteps').value|0);const freqs=steps===1?[fmin]:Array.from({length:steps},(_,i)=>fmin+(fmax-fmin)*i/(steps-1));return{pt:+$('pt').value,altM:+$('alt').value*304.8,freqs,lpol:+$('lpol').value,lsys:+$('lsys').value,nf:+$('nf').value,bwHz:Math.max(100,+$('bw').value*1000),samples:Math.max(2,+$('psamp').value|0),n:Math.max(10,+$('gridn').value|0),losN:Math.max(6,+$('lossamp').value|0),view:$('viewMode').value,wpIndex:Math.max(1,+$('wpIndex').value|0),useDtm:$('useDtm').checked,dfSig:Math.max(0.05,+$('dfSig').value),ellP:Math.max(50,Math.min(99,+$('ellP').value))}}
function linkBudget(ac,ground,p,freq,gElev,extraLoss){const horiz=hav(ac,ground);const dh=p.altM-(gElev||0);const d=Math.sqrt(horiz*horiz+dh*dh);const fspl=d<=1?0:20*Math.log10(4*Math.PI*d*(freq*1e6)/C);const gr=interpGain(freq);const noise=-174+10*Math.log10(p.bwHz)+p.nf;const Lextra=extraLoss||0;const pr=p.pt+gr-fspl-p.lpol-p.lsys-Lextra;return{d,horiz,fspl,gr,noise,pr,snr:pr-noise,freq,Lextra}}
function resample(pts,n){if(!pts.length)return[];if(pts.length===1||n<=1)return[pts[0]];const cum=[0];for(let i=1;i<pts.length;i++)cum.push(cum[i-1]+hav(pts[i-1],pts[i]));const total=cum[cum.length-1];if(total<=0)return[pts[0]];const out=[];for(let k=0;k<n;k++){const target=total*k/(n-1);let seg=1;while(seg<cum.length-1&&cum[seg]<target)seg++;const a=pts[seg-1],b=pts[seg],len=cum[seg]-cum[seg-1],t=len?(target-cum[seg-1])/len:0;out.push({lat:a.lat+t*(b.lat-a.lat),lng:a.lng+t*(b.lng-a.lng)})}return out}
function color(v,vmin,vmax){const x=Math.max(0,Math.min(1,(v-vmin)/Math.max(vmax-vmin,1e-9)));const s=[[0,[213,43,30]],[.35,[247,148,30]],[.55,[255,221,51]],[.75,[140,200,60]],[1,[46,204,64]]];for(let i=0;i<s.length-1;i++){if(x<=s[i+1][0]){const t=(x-s[i][0])/(s[i+1][0]-s[i][0]||1);const c=s[i][1].map((a,k)=>Math.round(a+t*(s[i+1][1][k]-a)));return 'rgb('+c[0]+','+c[1]+','+c[2]+')'}}return 'rgb(46,204,64)'}
function knifeEdgeLoss(v){if(v<=-0.78) return 0;return 6.9+20*Math.log10(Math.sqrt((v-0.1)*(v-0.1)+1)+v-0.1)}
function diffractionLoss(ac,acAlt,ground,gElev,losN,freqMHz){const total=hav(ac,ground);if(total<30) return 0;const lam=C/(Math.max(freqMHz,30)*1e6);const steps=Math.max(8,Math.min(48,losN|0,Math.ceil(total/600)));let vmax=-1e9;for(let k=1;k<steps;k++){const t=k/steps;const lat=ac.lat+(ground.lat-ac.lat)*t;const lng=ac.lng+(ground.lng-ac.lng)*t;const terr=elevAt(lat,lng);const d1=total*t,d2=total-d1;if(d1<1||d2<1) continue;const bulge=(d1*d2)/(2*K_EFF*R);const ray=acAlt+(gElev-acAlt)*t;const h=(terr+bulge)-ray;const v=h*Math.sqrt(2/lam*(1/d1+1/d2));if(v>vmax) vmax=v}if(vmax<=-0.78) return 0;return Math.min(knifeEdgeLoss(vmax),60)}
function lon2tile(lon,z){return Math.floor((lon+180)/360*Math.pow(2,z))}
function lat2tile(lat,z){const r=lat*Math.PI/180;return Math.floor((1-Math.log(Math.tan(r)+1/Math.cos(r))/Math.PI)/2*Math.pow(2,z))}
function tile2lon(x,z){return x/Math.pow(2,z)*360-180}
function tile2lat(y,z){const n=Math.PI-2*Math.PI*y/Math.pow(2,z);return 180/Math.PI*Math.atan(0.5*(Math.exp(n)-Math.exp(-n)))}
async function elevFromOpenElevation(points){const total=points.length||1;for(let i=0;i<points.length;i+=80){const chunk=points.slice(i,i+80);const body={locations:chunk.map(p=>({latitude:p.lat,longitude:p.lng}))};const res=await fetch('https://api.open-elevation.com/api/v1/lookup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!res.ok) throw new Error('open-elevation '+res.status);const js=await res.json();(js.results||[]).forEach((r,idx)=>{const p=chunk[idx];elevCache[p.lat.toFixed(4)+','+p.lng.toFixed(4)]=Number.isFinite(r.elevation)?r.elevation:0});setProgress(5+25*((i+chunk.length)/total),'Loading DTM…');await yieldUi()}return 'Open-Elevation'}
async function elevFromOpenTopo(points){for(let i=0;i<points.length;i+=50){const chunk=points.slice(i,i+50);const loc=chunk.map(p=>p.lat.toFixed(5)+','+p.lng.toFixed(5)).join('|');const res=await fetch('https://api.opentopodata.org/v1/srtm30m?locations='+loc);if(!res.ok) throw new Error('opentopo '+res.status);const js=await res.json();(js.results||[]).forEach((r,idx)=>{const p=chunk[idx];elevCache[p.lat.toFixed(4)+','+p.lng.toFixed(4)]=Number.isFinite(r.elevation)?r.elevation:0});setProgress(5+25*((i+chunk.length)/Math.max(points.length,1)),'Loading DTM (OpenTopo)…');if(i+50<points.length) await new Promise(r=>setTimeout(r,1100))}return 'OpenTopoData SRTM'}
async function elevFromTerrarium(points){const z=11;const tiles={};for(const p of points){const x=lon2tile(p.lng,z),y=lat2tile(p.lat,z);const key=x+','+y;if(!tiles[key])tiles[key]={x,y,pts:[]};tiles[key].pts.push(p)}const keys=Object.keys(tiles);for(let ti=0;ti<keys.length;ti++){const t=tiles[keys[ti]];const res=await fetch('https://s3.amazonaws.com/elevation-tiles-prod/terrarium/'+z+'/'+t.x+'/'+t.y+'.png');if(!res.ok) throw new Error('terrarium '+res.status);const bmp=await createImageBitmap(await res.blob());const canvas=document.createElement('canvas');canvas.width=bmp.width;canvas.height=bmp.height;const ctx=canvas.getContext('2d');ctx.drawImage(bmp,0,0);const img=ctx.getImageData(0,0,canvas.width,canvas.height).data;const west=tile2lon(t.x,z),east=tile2lon(t.x+1,z),north=tile2lat(t.y,z),south=tile2lat(t.y+1,z);for(const p of t.pts){const px=Math.max(0,Math.min(canvas.width-1,Math.floor((p.lng-west)/(east-west)*canvas.width)));const py=Math.max(0,Math.min(canvas.height-1,Math.floor((north-p.lat)/(north-south)*canvas.height)));const i=(py*canvas.width+px)*4;elevCache[p.lat.toFixed(4)+','+p.lng.toFixed(4)]=img[i]*256+img[i+1]+img[i+2]/256-32768}setProgress(5+25*((ti+1)/keys.length),'Loading DTM (Terrarium)…');await yieldUi()}return 'Terrarium DEM'}
async function fetchElevations(points){const need=[];for(const p of points){const k=p.lat.toFixed(4)+','+p.lng.toFixed(4);if(!(k in elevCache))need.push({lat:p.lat,lng:p.lng})}if(!need.length) return dtmSource||'cache';const errs=[];try{return await elevFromOpenElevation(need)}catch(e){errs.push(e.message)}try{return await elevFromOpenTopo(need)}catch(e){errs.push(e.message)}try{return await elevFromTerrarium(need)}catch(e){errs.push(e.message)}throw new Error('All DTM sources failed: '+errs.join(' | '))}
function elevAt(lat,lng){if(dem){const x=(lng-dem.minLng)/Math.max(dem.dLng,1e-12);const y=(lat-dem.minLat)/Math.max(dem.dLat,1e-12);const x0=Math.max(0,Math.min(dem.nx-2,Math.floor(x)));const y0=Math.max(0,Math.min(dem.ny-2,Math.floor(y)));const tx=Math.max(0,Math.min(1,x-x0)),ty=Math.max(0,Math.min(1,y-y0));const i=y0*dem.nx+x0;return dem.data[i]*(1-tx)*(1-ty)+dem.data[i+1]*tx*(1-ty)+dem.data[i+dem.nx]*(1-tx)*ty+dem.data[i+dem.nx+1]*tx*ty}return elevCache[lat.toFixed(4)+','+lng.toFixed(4)]||0}
async function buildDem(bounds,nx,ny){const minLat=bounds.getSouth(),maxLat=bounds.getNorth(),minLng=bounds.getWest(),maxLng=bounds.getEast();const dLat=(maxLat-minLat)/Math.max(1,ny-1),dLng=(maxLng-minLng)/Math.max(1,nx-1);const pts=[];for(let iy=0;iy<ny;iy++) for(let ix=0;ix<nx;ix++) pts.push({lat:minLat+iy*dLat,lng:minLng+ix*dLng});dtmSource=await fetchElevations(pts);const data=new Float32Array(nx*ny);let mn=Infinity,mx=-Infinity,sum=0;pts.forEach((p,i)=>{const e=Number.isFinite(elevCache[p.lat.toFixed(4)+','+p.lng.toFixed(4)])?elevCache[p.lat.toFixed(4)+','+p.lng.toFixed(4)]:0;data[i]=e;sum+=e;mn=Math.min(mn,e);mx=Math.max(mx,e)});dem={minLat,minLng,dLat,dLng,nx,ny,data};demStats={min:mn,max:mx,mean:sum/data.length};if(mx-mn<5) throw new Error('DTM too flat — possible load failure')}
function statsForCell(acList,ground,p,gElev){let max=-Infinity,sum=0,n=0,diffSum=0,diffMax=0;const perFreq=p.freqs.map(f=>({freq:f,max:-Infinity,sum:0,n:0,diffSum:0}));for(const ac of acList){for(let fi=0;fi<p.freqs.length;fi++){const f=p.freqs[fi];const Ld=p.useDtm?diffractionLoss(ac,p.altM,ground,gElev,p.losN,f):0;const r=linkBudget(ac,ground,p,f,gElev,Ld);sum+=r.snr;n++;diffSum+=Ld;diffMax=Math.max(diffMax,Ld);if(r.snr>max) max=r.snr;const pf=perFreq[fi];pf.sum+=r.snr;pf.n++;pf.diffSum+=Ld;if(r.snr>pf.max) pf.max=r.snr}}for(const pf of perFreq){pf.mean=pf.n?pf.sum/pf.n:NaN;pf.diffMean=pf.n?pf.diffSum/pf.n:0;pf.v=p.view==='max'?pf.max:pf.mean}return{max,mean:n?sum/n:NaN,diffMean:n?diffSum/n:0,diffMax,n,perFreq}}
function chiScale(p){const q=Math.max(0.5,Math.min(0.999,p/100));return Math.sqrt(-2*Math.log(1-q))}
function vecFromGround(g,ac,acAlt,gElev){const east=hav({lat:g.lat,lng:g.lng},{lat:g.lat,lng:ac.lng})*Math.sign(ac.lng-g.lng||1);const north=hav({lat:g.lat,lng:g.lng},{lat:ac.lat,lng:g.lng})*Math.sign(ac.lat-g.lat||1);const up=(acAlt-(gElev||0));return {x:east,y:north,z:up}}
function vlen(v){return Math.sqrt(v.x*v.x+v.y*v.y+v.z*v.z)}
function angBetween(a,b){const la=vlen(a),lb=vlen(b);if(la<1||lb<1) return 0;const c=Math.max(-1,Math.min(1,(a.x*b.x+a.y*b.y+a.z*b.z)/(la*lb)));return Math.acos(c)}
function bestAperture(g,samples,acAlt,gElev){if(!samples.length) return {phi:0,R1:0,R2:0};if(samples.length===1){const v=vecFromGround(g,samples[0],acAlt,gElev);const R=vlen(v);return {phi:0,R1:R,R2:R}}const vs=samples.map(s=>vecFromGround(g,s,acAlt,gElev));let best=-1,ii=0,jj=1;for(let i=0;i<vs.length;i++){for(let j=i+1;j<vs.length;j++){const a=angBetween(vs[i],vs[j]);if(a>best){best=a;ii=i;jj=j}}}return {phi:Math.max(0,best),R1:vlen(vs[ii]),R2:vlen(vs[jj])}}
function ellipseA90(ap,sigmaRad,pPct){const phi=ap.phi,s=Math.sin(phi);if(!(phi>2*Math.PI/180)||s<1e-3) return Infinity;return sigmaRad*Math.sqrt(ap.R1*ap.R1+ap.R2*ap.R2)/s*chiScale(pPct)}
function fmtKm(m){if(!isFinite(m)) return '∞';if(m>=1000) return (m/1000).toFixed(2)+' km';return m.toFixed(0)+' m'}
function setBanner(html){$('banner').innerHTML=html}
function ready(){return path.length>=1&&!!aoi}
function updateRun(){$('runBtn').disabled=!ready();$('runBtn').textContent=ready()?'Run Analysis':'Run Analysis — missing path or AOI'}
function redrawPath(){pathLayer.clearLayers();markers.forEach((m,i)=>{m.setTooltipContent(String(i+1));pathLayer.addLayer(m)});if(path.length>1)L.polyline(path.map(p=>[p.lat,p.lng]),{color:'#ef4444',weight:4}).addTo(pathLayer);const len=path.reduce((s,_,i)=>i?s+hav(path[i-1],path[i]):0,0);$('kPts').textContent=path.length;$('kLen').textContent=path.length?(len/1000).toFixed(1)+' km':'-';updateRun();if(!aoi)setBanner('<b>Step 2:</b> Draw AOI with two clicks.');else if(!hasAnalysis)setBanner('<b>Step 3:</b> Click Run Analysis.')}
function setAoi(bounds){aoi=bounds;aoiLayer.clearLayers();L.rectangle(bounds,{color:'#60a5fa',weight:2,fillOpacity:.08}).addTo(aoiLayer);updateRun();if(ready())setBanner('<b>Ready:</b> Click Run Analysis.')}
function setMode(next){mode=next;['mPath','mAoi'].forEach(id=>$(id).classList.remove('active'));$(next==='path'?'mPath':'mAoi').classList.add('active');aoiCorners=[];if(next==='path')setBanner('<b>Path mode:</b> Click on the map.');if(next==='aoi')setBanner('<b>AOI mode:</b> Two corners of rectangle.')}
$('mPath').onclick=()=>setMode('path');$('mAoi').onclick=()=>setMode('aoi');
$('toggleSheet').onclick=()=>$('sheet').classList.toggle('open');
map.on('click',e=>{if(mode==='path'){const p={lat:e.latlng.lat,lng:e.latlng.lng};const m=L.marker(e.latlng,{draggable:true}).bindTooltip(String(path.length+1),{permanent:true,direction:'top'});m.on('dragend',()=>{const i=markers.indexOf(m),ll=m.getLatLng();path[i]={lat:ll.lat,lng:ll.lng};redrawPath()});markers.push(m);path.push(p);redrawPath()}else{aoiCorners.push(e.latlng);if(aoiCorners.length===1)setBanner('<b>AOI:</b> Click opposite corner.');else{setAoi(L.latLngBounds(aoiCorners[0],aoiCorners[1]));aoiCorners=[];}}});
async function runAnalysis(){
  if(!path.length){$('sheet').classList.add('open');$('result').textContent='No path.';return}
  if(!aoi){$('sheet').classList.add('open');$('result').textContent='No AOI.';return}
  const p=params();
  $('runBtn').disabled=true;$('runBtn').textContent='Running…';
  showProgress(true);setProgress(1,'Starting analysis…');
  try{
    const samples=resample(path,p.samples);
    const ac=p.view==='single'?[samples[Math.min(samples.length-1,p.wpIndex-1)]]:samples;
    const pad=aoi.pad(0.2);
    const pathB=L.latLngBounds(path.map(x=>[x.lat,x.lng]));
    const union=pad.extend(pathB.getSouthWest()).extend(pathB.getNorthEast());
    if(p.useDtm){setBanner('<b>DTM:</b> Loading elevations…');setProgress(5,'Loading DTM…');await buildDem(union,36,36);setProgress(30,'DTM ready · computing…');setBanner('<b>DTM:</b> '+dtmSource+' · '+demStats.min.toFixed(0)+'…'+demStats.max.toFixed(0)+' m')} else {dem=null;demStats={min:0,max:0,mean:0};setProgress(30,'Computing SNR + ellipse…')}
    const sw=aoi.getSouthWest(),ne=aoi.getNorthEast(),n=p.n;
    const totalCells=n*n;const cells=[];let done=0;
    for(let iy=0;iy<n;iy++){
      for(let ix=0;ix<n;ix++){
        const lat0=sw.lat+(ne.lat-sw.lat)*iy/n,lat1=sw.lat+(ne.lat-sw.lat)*(iy+1)/n;
        const lng0=sw.lng+(ne.lng-sw.lng)*ix/n,lng1=sw.lng+(ne.lng-sw.lng)*(ix+1)/n;
        const c={lat:(lat0+lat1)/2,lng:(lng0+lng1)/2};
        const gElev=p.useDtm?elevAt(c.lat,c.lng):0;
        const s=statsForCell(ac,c,p,gElev);
        const v=p.view==='max'?s.max:s.mean;
        const ap=bestAperture(c,samples,p.altM,gElev);
        const a90=ellipseA90(ap,p.dfSig*Math.PI/180,p.ellP);
        done++;
        if(isFinite(v)) cells.push({lat0,lng0,lat1,lng1,v,gElev,diff:s.diffMean,a90,phi:ap.phi,perFreq:s.perFreq});
        if(done%Math.max(1,Math.floor(totalCells/40))===0||done===totalCells){setProgress(30+65*(done/totalCells),'SNR + ellipse · cell '+done+'/'+totalCells);await yieldUi()}
      }
    }
    setProgress(96,'Drawing maps…');await yieldUi();
    lastCells=cells;
    const finiteEll=cells.map(c=>c.a90).filter(isFinite).sort((a,b)=>a-b);
    const qe=(arr,t)=>arr.length?arr[Math.max(0,Math.min(arr.length-1,Math.floor(t*(arr.length-1))))]:1;
    let emin=qe(finiteEll,0.05),emax=qe(finiteEll,0.95);
    if(!(emax>emin)){emin=200;emax=5000}
    const padE=(emax-emin)*0.08;emin=Math.max(1,emin-padE);emax=emax+padE;
    ellScale={cmin:emin,cmax:emax};
    const vals=cells.map(c=>c.v).sort((a,b)=>a-b);
    const q=(t)=>vals.length?vals[Math.max(0,Math.min(vals.length-1,Math.floor(t*(vals.length-1))))]:0;
    let cmin=q(0.05),cmax=q(0.95);
    if(cmax-cmin<3){const mid=(cmin+cmax)/2;cmin=mid-3;cmax=mid+3}
    const padC=(cmax-cmin)*0.05;cmin-=padC;cmax+=padC;
    snrScale={cmin,cmax};
    let sum=0,mx=-Infinity,mn=Infinity,diffSum=0,eSum=0,eN=0,phiSum=0,eMx=0;
    for(const cell of cells){sum+=cell.v;mx=Math.max(mx,cell.v);mn=Math.min(mn,cell.v);diffSum+=cell.diff;if(isFinite(cell.a90)){eSum+=cell.a90;eN++;eMx=Math.max(eMx,cell.a90)}phiSum+=cell.phi||0}
    const avg=cells.length?sum/cells.length:NaN,avgDiff=cells.length?diffSum/cells.length:0,avgE=eN?eSum/eN:NaN,avgPhi=cells.length?(phiSum/cells.length)*180/Math.PI:0;
    $('kAvg').textContent=isFinite(avg)?avg.toFixed(1)+' dB':'-';$('kMax').textContent=isFinite(mx)?mx.toFixed(1)+' dB':'-';$('kDiff').textContent=avgDiff.toFixed(1)+' dB';$('kEll').textContent=isFinite(avgE)?fmtKm(avgE):'∞';$('kPhi').textContent=avgPhi.toFixed(0)+'°';
    fillFreqView(p.freqs);hasAnalysis=true;$('sheet').classList.add('open');paintHeat('snr');
    const altKft=(p.altM/304.8).toFixed(1),elapsed=((performance.now()-progT0)/1000).toFixed(1);
    $('result').innerHTML='<b>Both analyses complete</b> · '+elapsed+' s<br>Altitude: <b>'+altKft+' KFT</b> · DTM '+(p.useDtm?(dtmSource+' · '+demStats.min.toFixed(0)+'…'+demStats.max.toFixed(0)+' m'):'OFF')+'<br><b>1 · SNR</b> avg/max/min: <b>'+(isFinite(avg)?avg.toFixed(2):'-')+'</b> / '+(isFinite(mx)?mx.toFixed(2):'-')+' / '+(isFinite(mn)?mn.toFixed(2):'-')+' dB · diffraction '+avgDiff.toFixed(2)+' dB<br><b>2 · DF ellipse '+p.ellP+'%</b> · σ='+p.dfSig+'° · path samples '+samples.length+'<br>Mean major semi-axis: <b>'+(isFinite(avgE)?fmtKm(avgE):'∞')+'</b> · max '+fmtKm(eMx)+'<br>Mean angular aperture: <b>'+avgPhi.toFixed(1)+'°</b><br>Click «Ellipse map» for a90 heat map (green = small)';
    setBanner('<b>SNR + ellipse ready.</b> Switch map with the buttons.');
    setProgress(100,'Done');map.fitBounds(aoi.pad(0.08));setTimeout(()=>showProgress(false),900);
  }catch(err){$('sheet').classList.add('open');$('result').textContent='Error: '+(err.message||err);setBanner('<b>Error.</b> Try again.');showProgress(false)}finally{updateRun()}
}
function updateScaleInputs(){
  const sc=heatKind==='ell'?ellScale:snrScale;
  if(heatKind==='ell'){
    $('scaleMin').value=(sc.cmin/1000).toFixed(2);
    $('scaleMax').value=(sc.cmax/1000).toFixed(2);
  }else{
    $('scaleMin').value=sc.cmin.toFixed(1);
    $('scaleMax').value=sc.cmax.toFixed(1);
  }
  $('scaleControls').style.display=hasAnalysis?'flex':'none';
}
function applyScale(){
  if(!hasAnalysis) return;
  let vmin=+$('scaleMin').value, vmax=+$('scaleMax').value;
  if(!(vmax>vmin)) return;
  if(heatKind==='ell'){
    ellScale={cmin:vmin*1000,cmax:vmax*1000};
  }else{
    snrScale={cmin:vmin,cmax:vmax};
  }
  paintHeat(heatKind);
}
function fillFreqView(freqs){
  const sel=$('freqView'); if(!sel) return;
  const prev=sel.value;
  sel.innerHTML='';
  const o0=document.createElement('option'); o0.value='all'; o0.textContent=freqs.length>1?('All frequencies · aggregate ('+freqs.length+')'):(freqs[0].toFixed(3)+' MHz');
  sel.appendChild(o0);
  if(freqs.length>1){
    freqs.forEach((f,i)=>{const o=document.createElement('option'); o.value=String(i); o.textContent=f.toFixed(3)+' MHz'; sel.appendChild(o);});
  }
  sel.value=(prev!=='all' && [...sel.options].some(o=>o.value===prev))?prev:'all';
}
function selectedFreqIdx(){const sel=$('freqView'); if(!sel||sel.value==='all') return 'all'; return +sel.value}
function cellSnr(cell){const idx=selectedFreqIdx(); if(idx==='all'||!cell.perFreq||!cell.perFreq[idx]) return cell.v; return cell.perFreq[idx].v}
function cellDiff(cell){const idx=selectedFreqIdx(); if(idx==='all'||!cell.perFreq||!cell.perFreq[idx]) return cell.diff; return cell.perFreq[idx].diffMean}
function freqLabel(){const idx=selectedFreqIdx(); if(idx==='all') return 'all freqs'; const sel=$('freqView'); return sel?sel.options[sel.selectedIndex].textContent:'freq'}
function paintHeat(kind){
  heatKind=kind;
  ['mSnr','mEll'].forEach(id=>{const el=$(id);if(el)el.classList.remove('layerOn')});
  if(kind==='ell') $('mEll').classList.add('layerOn'); else $('mSnr').classList.add('layerOn');
  heatLayer.clearLayers();if(!lastCells.length) return;
  if(kind==='ell'){
    const {cmin,cmax}=ellScale;
    $('legMin').textContent=fmtKm(cmax);$('legMax').textContent=fmtKm(cmin);$('legMid').textContent=fmtKm((cmin+cmax)/2);
    $('legTitle').textContent='Semi-axis a90 — green = small';
    $('legHint').textContent='Red = large · Green = small';
    for(const cell of lastCells){const val=isFinite(cell.a90)?cell.a90:cmax;const col=color(-val,-cmax,-cmin);const tip='a90 '+fmtKm(cell.a90)+' · aperture '+(cell.phi*180/Math.PI).toFixed(1)+'° · SNR '+cellSnr(cell).toFixed(1)+' dB ('+freqLabel()+')';L.rectangle([[cell.lat0,cell.lng0],[cell.lat1,cell.lng1]],{stroke:false,fill:true,fillOpacity:.75,fillColor:col}).bindTooltip(tip,{sticky:true}).addTo(heatLayer)}
  }else{
    const {cmin,cmax}=snrScale;
    $('legMin').textContent=cmin.toFixed(1);$('legMax').textContent=cmax.toFixed(1);$('legMid').textContent=((cmin+cmax)/2).toFixed(1);
    $('legTitle').textContent='SNR (dB) — custom scale';
    $('legHint').textContent='Red = weak · Green = strong';
    for(const cell of lastCells){const v=cellSnr(cell);const tip=v.toFixed(1)+' dB · '+freqLabel()+' · a90 '+fmtKm(cell.a90)+' · aperture '+(cell.phi*180/Math.PI).toFixed(1)+'°';L.rectangle([[cell.lat0,cell.lng0],[cell.lat1,cell.lng1]],{stroke:false,fill:true,fillOpacity:.75,fillColor:color(v,cmin,cmax)}).bindTooltip(tip,{sticky:true}).addTo(heatLayer)}
  }
  updateScaleInputs();
}
$('freqView').onchange=()=>{if(!hasAnalysis) return; updateKpisFromCells(); paintHeat(heatKind)};
function updateKpisFromCells(){
  if(!lastCells.length) return;
  let sum=0,mx=-Infinity,n=0,dSum=0;
  for(const cell of lastCells){const v=cellSnr(cell); if(!isFinite(v)) continue; sum+=v; mx=Math.max(mx,v); n++; dSum+=cellDiff(cell)||0}
  $('kAvg').textContent=n? (sum/n).toFixed(1)+' dB':'-';
  $('kMax').textContent=isFinite(mx)?mx.toFixed(1)+' dB':'-';
  $('kDiff').textContent=n?(dSum/n).toFixed(1)+' dB':'-';
}
$('mSnr').onclick=()=>{if(hasAnalysis)paintHeat('snr')};
$('mEll').onclick=()=>{if(hasAnalysis)paintHeat('ell')};
$('runBtn').onclick=runAnalysis;
$('scaleApply').onclick=applyScale;
$('scaleMin').addEventListener('keydown',e=>{if(e.key==='Enter')applyScale()});
$('scaleMax').addEventListener('keydown',e=>{if(e.key==='Enter')applyScale()});
$('clearBtn').onclick=()=>{path=[];markers.forEach(m=>map.removeLayer(m));markers=[];pathLayer.clearLayers();aoiLayer.clearLayers();heatLayer.clearLayers();aoi=null;aoiCorners=[];hasAnalysis=false;dem=null;elevCache={};lastCells=[];showProgress(false);$('scaleControls').style.display='none';$('kPts').textContent='0';$('kLen').textContent='-';$('kAvg').textContent='-';$('kMax').textContent='-';$('kDiff').textContent='-';$('kEll').textContent='-';$('kPhi').textContent='-';$('result').textContent='Cleared.';const fv=$('freqView'); if(fv){fv.innerHTML='<option value="all">All frequencies (aggregate)</option>'; fv.value='all';}setMode('path');updateRun()}
$('saveBtn').onclick=()=>{localStorage.setItem('fp-mission',JSON.stringify({path,aoi:aoi?[[aoi.getSouth(),aoi.getWest()],[aoi.getNorth(),aoi.getEast()]]:null}));$('result').textContent='Saved.'}
$('loadBtn').onclick=()=>{const raw=localStorage.getItem('fp-mission');if(!raw){$('result').textContent='Nothing saved.';return}const data=JSON.parse(raw);$('clearBtn').onclick();(data.path||[]).forEach(p=>{const m=L.marker([p.lat,p.lng],{draggable:true}).bindTooltip(String(path.length+1),{permanent:true,direction:'top'});m.on('dragend',()=>{const i=markers.indexOf(m),ll=m.getLatLng();path[i]={lat:ll.lat,lng:ll.lng};redrawPath()});markers.push(m);path.push(p)});redrawPath();if(data.aoi)setAoi(L.latLngBounds(data.aoi[0],data.aoi[1]));$('result').textContent='Loaded.';}
updateRun();
