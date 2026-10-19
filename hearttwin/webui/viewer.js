/* HeartTwin's original geometry inspector. No external rendering or patient assets. */
(function (root) {
  'use strict';
  const MAX_NODES = 30000, MAX_FACES = 60000;
  const faceKey = face => [...face].sort((a,b) => a-b).join(',');
  function parseGeometry(raw) {
    if (!raw || typeof raw !== 'object') throw Error('Import a geometry JSON object.');
    const geometry = raw.geometry || raw.request?.payload?.geometry || raw;
    const nodes = geometry.node_xyz;
    if (!Array.isArray(nodes) || nodes.length < 3 || nodes.length > MAX_NODES)
      throw Error('The viewer requires 3–30,000 node_xyz vertices. Retrieve larger meshes on the worker.');
    if (!nodes.every(p => Array.isArray(p) && p.length === 3 && p.every(v => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 1e9)))
      throw Error('node_xyz must contain finite numeric [x,y,z] coordinates with magnitude ≤ 1e9.');
    const valid = (cells,n) => Array.isArray(cells) && cells.every(c => Array.isArray(c) && c.length === n && new Set(c).size === n && c.every(i => Number.isInteger(i) && i >= 0 && i < nodes.length));
    let faces;
    if (geometry.triangles !== undefined) {
      if (!Array.isArray(geometry.triangles) || geometry.triangles.length > MAX_FACES || !valid(geometry.triangles,3)) throw Error('triangles must contain at most 60,000 valid three-node index lists.');
      faces = geometry.triangles;
    } else if (geometry.tetrahedra !== undefined) {
      if (!Array.isArray(geometry.tetrahedra) || geometry.tetrahedra.length > 80000 || !valid(geometry.tetrahedra,4)) throw Error('tetrahedra must contain at most 80,000 valid four-node index lists.');
      const boundary = new Map();
      for (const [a,b,c,d] of geometry.tetrahedra) {
        for (const face of [[a,b,c],[a,d,b],[a,c,d],[b,d,c]]) {
          const key = faceKey(face), record = boundary.get(key);
          if (record) record.count++; else boundary.set(key,{face,count:1});
        }
      }
      if ([...boundary.values()].some(r => r.count > 2)) throw Error('Non-manifold tetrahedral faces cannot be displayed.');
      faces = [...boundary.values()].filter(r => r.count === 1).map(r => r.face);
    } else throw Error('Geometry requires triangles or tetrahedra, using zero-based indices.');
    if (!faces.length || faces.length > MAX_FACES) throw Error('The visible surface must contain 1–60,000 faces.');
    const low = [0,1,2].map(a => nodes.reduce((m,p) => Math.min(m,p[a]),Infinity));
    const high = [0,1,2].map(a => nodes.reduce((m,p) => Math.max(m,p[a]),-Infinity));
    const extent = Math.max(...high.map((v,i) => v-low[i]));
    if (!(extent > 0)) throw Error('Geometry must have a nonzero spatial extent.');
    const center = low.map((v,i) => (v+high[i])/2);
    const activation = raw.result?.activation_ms ?? raw.activation_ms ?? geometry.activation_ms ?? null;
    if (activation !== null && (!Array.isArray(activation) || activation.length !== nodes.length || !activation.every(v => typeof v === 'number' && Number.isFinite(v))))
      throw Error('activation_ms must have one finite value per node, in the same node order.');
    if (activation && !Number.isFinite(activation.reduce((a,b)=>Math.max(a,b),-Infinity)-activation.reduce((a,b)=>Math.min(a,b),Infinity))) throw Error('Activation range exceeds numerical display limits.');
    const unit = geometry.units?.node_xyz ?? raw.units?.node_xyz;
    return {nodes:nodes.map(p => p.map((v,i) => (v-center[i])*4/extent)), faces:faces.map(f => [...f]), activation,
      units:typeof unit === 'string' ? unit.slice(0,50) : 'unspecified', name:'Imported surface', schematic:false};
  }
  function schematic() {
    const nodes=[],faces=[],faceLayer=[],layers=[];
    function ellipsoid(name,color,center,radii,tilt) {
      const id=layers.length,start=nodes.length,rows=20,cols=32;
      layers.push({name,color,visible:true,opacity:1});
      for(let r=0;r<=rows;r++) for(let c=0;c<=cols;c++) {
        const phi=Math.PI*r/rows,theta=2*Math.PI*c/cols;
        let x=radii[0]*Math.sin(phi)*Math.cos(theta),y=radii[1]*Math.cos(phi),z=radii[2]*Math.sin(phi)*Math.sin(theta);
        const xx=x*Math.cos(tilt)-y*Math.sin(tilt),yy=x*Math.sin(tilt)+y*Math.cos(tilt);
        nodes.push([xx+center[0],yy+center[1],z+center[2]]);
      }
      for(let r=0;r<rows;r++) for(let c=0;c<cols;c++) {
        const a=start+r*(cols+1)+c,b=a+cols+1;
        faces.push([a,b,a+1],[a+1,b,b+1]);faceLayer.push(id,id);
      }
    }
    function vessel(name,color,path,radius) {
      const id=layers.length,start=nodes.length,cols=20;
      layers.push({name,color,visible:true,opacity:1});
      for(const p of path) for(let c=0;c<cols;c++) {
        const theta=c/cols*Math.PI*2;
        nodes.push([p[0]+radius*Math.cos(theta),p[1],p[2]+radius*Math.sin(theta)]);
      }
      for(let r=0;r<path.length-1;r++) for(let c=0;c<cols;c++) {
        const a=start+r*cols+c,b=start+r*cols+(c+1)%cols,d=a+cols,e=b+cols;
        faces.push([a,d,b],[b,d,e]);faceLayer.push(id,id);
      }
    }
    ellipsoid('Left ventricle',[186,62,79],[.42,-.48,0],[.79,1.33,.72],-.3);
    ellipsoid('Right ventricle',[216,111,122],[-.49,-.3,.28],[.69,1.02,.66],.34);
    ellipsoid('Left atrium',[160,70,90],[.48,.94,-.26],[.65,.62,.6],-.16);
    ellipsoid('Right atrium',[190,99,126],[-.63,.93,.1],[.59,.73,.58],.16);
    vessel('Aorta',[197,64,79],[[.12,.8,.18],[.12,1.3,.18],[.2,1.8,.12],[.44,2.17,.02],[.85,2.23,-.1],[1.18,2.03,-.16]],.23);
    vessel('Pulmonary artery',[74,145,172],[[-.13,.65,.65],[-.18,1.13,.68],[-.32,1.5,.67],[-.69,1.72,.5],[-1.03,1.72,.4]],.24);
    return {nodes:nodes.map(p => [p[0],p[1]-.2,p[2]]),faces,faceLayer,layers,activation:null,units:'Schematic units',name:'Schematic heart',schematic:true};
  }
  function init() {
    const $ = id => document.getElementById(id), canvas=$('heart-canvas');
    if (!canvas) return;
    const context=canvas.getContext('2d');
    if(!context){$('mesh-stat').textContent='Canvas rendering is unavailable in this browser.';return;}
    let model, yaw=-.23,pitch=.07,zoom=1,wire=false,clip=1,grid=true,active=0,playing=false,frame=0,last=0;
    function stop(){playing=false;cancelAnimationFrame(frame);$('activation-play').textContent='▶';$('activation-play').setAttribute('aria-label','Play activation sequence');}
    function syncLayer(){const layer=model.layers[active];$('model-opacity').value=Math.round(layer.opacity*100);$('opacity-value').textContent=Math.round(layer.opacity*100)+'%';$('active-layer').value=active;document.querySelectorAll('.layer-select').forEach(b=>b.classList.toggle('selected',Number(b.dataset.layer)===active));}
    function load(next,label) {
      stop();model=next;
      if(!model.layers){model.layers=[{name:'Imported surface',color:[202,94,110],visible:true,opacity:1}];model.faceLayer=model.faces.map(()=>0);}
      yaw=-.23;pitch=.07;zoom=1;clip=1;active=0;
      $('clip-plane').value=100;
      $('layer-tree').replaceChildren();$('active-layer').replaceChildren();
      model.layers.forEach((layer,i)=>{
        const row=document.createElement('div');row.className='layer-row';
        const visible=document.createElement('input');visible.type='checkbox';visible.checked=true;visible.setAttribute('aria-label','Show '+layer.name);visible.addEventListener('change',()=>{layer.visible=visible.checked;draw();});
        const button=document.createElement('button');button.className='layer-select';button.dataset.layer=i;
        const dot=document.createElement('span');dot.className='layer-dot';dot.style.backgroundColor=`rgb(${layer.color.join(',')})`;
        button.append(dot,document.createTextNode(layer.name));button.addEventListener('click',()=>{active=i;syncLayer();});row.append(visible,button);$('layer-tree').append(row);
        const option=document.createElement('option');option.value=i;option.textContent=layer.name;$('active-layer').append(option);
      });
      $('scene-name').textContent=model.name;$('layer-count').textContent=model.layers.length;$('scene-source').textContent=model.schematic?'REFERENCE':'IMPORTED';
      $('model-subtitle').textContent=model.schematic?'Schematic reference · no patient data loaded':label+' · inspect imported geometry';
      $('model-source-note').textContent=model.schematic?'Original procedural schematic for navigating the interface. Import your mesh to inspect research geometry.':label+'. Surface display does not establish mesh quality, anatomy accuracy or simulation readiness.';
      $('viewport-title').textContent=model.schematic?'SCHEMATIC ANATOMY':'IMPORTED GEOMETRY';
      $('view-data-badge').textContent=model.schematic?'Reference illustration · not a simulation':label;
      $('fact-source').textContent=model.schematic?'Schematic':'Imported JSON';
      $('fact-vertices').textContent=model.nodes.length.toLocaleString();$('fact-faces').textContent=model.faces.length.toLocaleString();
      $('fact-field').textContent=model.activation?'Activation (ms)':'None';
      $('mesh-stat').textContent=model.nodes.length.toLocaleString()+' vertices · '+model.faces.length.toLocaleString()+' surface faces';
      $('geometry-units').textContent=model.schematic?'Schematic units':'Coordinate units: '+model.units;
      $('scalar-legend').classList.toggle('hidden',!model.activation);
      $('activation-play').disabled=!model.activation;$('activation-time').disabled=!model.activation;
      $('timeline-source').textContent=model.activation?'RESULT DATA':'NO DATA';
      $('timeline-label').textContent=model.activation?'Activation threshold · '+label:'Activation playback';
      if(model.activation){model.min=model.activation.reduce((a,b)=>Math.min(a,b),Infinity);model.max=model.activation.reduce((a,b)=>Math.max(a,b),-Infinity);$('scalar-min').textContent=model.min.toFixed(1);$('scalar-max').textContent=model.max.toFixed(1);$('activation-time').value=1000;}
      $('time-value').textContent=model.activation?model.max.toFixed(1)+' ms':'No result loaded';
      $('view-orientation').textContent='FRONT';syncLayer();draw();
    }
    function draw() {
      if(!model)return;
      const rect=canvas.getBoundingClientRect();if(!rect.width||!rect.height)return;
      const ratio=Math.min(window.devicePixelRatio||1,2),w=rect.width,h=rect.height;
      if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(h*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
      context.setTransform(ratio,0,0,ratio,0,0);context.clearRect(0,0,w,h);
      const scale=Math.min(w/6.2,h/5.5)*zoom,cy=h*.52;
      const transform=p=>{
        const x=p[0]*Math.cos(yaw)+p[2]*Math.sin(yaw),z=-p[0]*Math.sin(yaw)+p[2]*Math.cos(yaw);
        return [x,p[1]*Math.cos(pitch)-z*Math.sin(pitch),p[1]*Math.sin(pitch)+z*Math.cos(pitch)];
      };
      const project=p=>[w/2+p[0]*scale,cy-p[1]*scale];
      if(grid){context.strokeStyle='#263340';context.lineWidth=.6;for(let i=-4;i<=4;i++){for(const ends of [[[i,-2,-4],[i,-2,4]],[[-4,-2,i],[4,-2,i]]]){const a=project(transform(ends[0])),b=project(transform(ends[1]));context.beginPath();context.moveTo(...a);context.lineTo(...b);context.stroke();}}}
      const points=model.nodes.map(transform),screen=points.map(project);
      const rows=model.faces.map((f,i)=>({f,i,z:(points[f[0]][2]+points[f[1]][2]+points[f[2]][2])/3})).sort((a,b)=>a.z-b.z);
      const threshold=model.activation?model.min+(model.max-model.min)*Number($('activation-time').value)/1000:0;
      for(const {f,i} of rows){
        const layer=model.layers[model.faceLayer[i]];if(!layer.visible)continue;
        if(clip<1&&f.some(n=>model.nodes[n][0]>(clip*4-2)))continue;
        const [a,b,c]=f.map(n=>points[n]);const u=b.map((v,j)=>v-a[j]),v=c.map((x,j)=>x-a[j]);
        const normal=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]],norm=Math.hypot(...normal)||1;
        const light=.48+.52*Math.abs((normal[0]*.25+normal[1]*.4+normal[2]*.88)/norm);
        let color=layer.color;
        if(model.activation){const value=f.reduce((n,j)=>n+model.activation[j],0)/3;if(value>threshold)color=[69,79,95];else{const t=(value-model.min)/(model.max-model.min||1);color=[Math.round(57+197*t),Math.round(178-91*t),Math.round(220-131*t)];}}
        context.fillStyle=`rgba(${color.map(n=>Math.round(n*light)).join(',')},${layer.opacity})`;
        context.beginPath();context.moveTo(...screen[f[0]]);context.lineTo(...screen[f[1]]);context.lineTo(...screen[f[2]]);context.closePath();context.fill();
        context.strokeStyle=wire?'rgba(9,16,25,.65)':`rgba(${color.map(n=>Math.round(n*light)).join(',')},${layer.opacity*.6})`;context.lineWidth=wire ? 0.6 : 0.4;context.stroke();
      }
      // Draw the coordinate triad in the same camera frame as the geometry.
      const origin=[w-40,h-50];
      for(const [axis,color,label] of [[[1,0,0],'#d994a1','X'],[[0,1,0],'#8cc9a7','Y'],[[0,0,1],'#79afe1','Z']]){
        const v=transform(axis),end=[origin[0]+v[0]*23,origin[1]-v[1]*23];
        context.strokeStyle=color;context.lineWidth=1;context.beginPath();context.moveTo(...origin);context.lineTo(...end);context.stroke();context.fillStyle=color;context.font='9px ui-monospace, monospace';context.fillText(label,end[0]+3,end[1]-3);
      }
    }
    function reset(){yaw=-.23;pitch=.07;zoom=1;$('view-orientation').textContent='FRONT';draw();}
    let pointer=null;
    canvas.addEventListener('pointerdown',e=>{pointer=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);canvas.focus();});
    canvas.addEventListener('pointermove',e=>{if(!pointer)return;yaw+=(e.clientX-pointer[0])*.008;pitch=Math.max(-1.5,Math.min(1.5,pitch+(e.clientY-pointer[1])*.008));pointer=[e.clientX,e.clientY];$('view-orientation').textContent='CUSTOM VIEW';draw();});
    canvas.addEventListener('pointerup',()=>pointer=null);canvas.addEventListener('pointercancel',()=>pointer=null);
    canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.min(3,Math.max(.4,zoom*Math.exp(-e.deltaY*.001)));draw();},{passive:false});
    canvas.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','-','=','0'].includes(e.key))return;e.preventDefault();if(e.key==='ArrowLeft')yaw-=.12;if(e.key==='ArrowRight')yaw+=.12;if(e.key==='ArrowUp')pitch=Math.min(1.5,pitch+.12);if(e.key==='ArrowDown')pitch=Math.max(-1.5,pitch-.12);if(['+','='].includes(e.key))zoom=Math.min(3,zoom*1.1);if(e.key==='-')zoom=Math.max(.4,zoom/1.1);if(e.key==='0')reset();else{$('view-orientation').textContent='CUSTOM VIEW';draw();}});
    $('view-fit').addEventListener('click',reset);
    document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{pitch=.07;yaw={front:-.23,back:Math.PI-.23,left:Math.PI/2}[b.dataset.view];$('view-orientation').textContent={front:'FRONT',back:'BACK',left:'SIDE'}[b.dataset.view];draw();}));
    const mode=mesh=>{wire=mesh;['mode-surface','mode-mesh'].forEach((id,i)=>{$(id).classList.toggle('active',Boolean(i)===mesh);$(id).setAttribute('aria-pressed',Boolean(i)===mesh);});draw();};
    $('mode-surface').addEventListener('click',()=>mode(false));$('mode-mesh').addEventListener('click',()=>mode(true));
    $('active-layer').addEventListener('change',()=>{active=Number($('active-layer').value);syncLayer();});
    $('model-opacity').addEventListener('input',()=>{model.layers[active].opacity=Number($('model-opacity').value)/100;$('opacity-value').textContent=$('model-opacity').value+'%';draw();});
    $('show-grid').addEventListener('change',()=>{grid=$('show-grid').checked;draw();});$('clip-plane').addEventListener('input',()=>{clip=Number($('clip-plane').value)/100;draw();});
    $('reset-display').addEventListener('click',()=>{model.layers.forEach(l=>{l.visible=true;l.opacity=1;});$('layer-tree').querySelectorAll('input').forEach(c=>c.checked=true);clip=1;$('clip-plane').value=100;grid=true;$('show-grid').checked=true;mode(false);syncLayer();reset();});
    $('activation-time').addEventListener('input',()=>{if(model.activation){$('time-value').textContent=(model.min+(model.max-model.min)*Number($('activation-time').value)/1000).toFixed(1)+' ms';draw();}});
    function tick(t){if(!playing||document.hidden){stop();return;}const slider=$('activation-time');slider.value=Math.min(1000,Number(slider.value)+(t-last)/5);last=t;slider.dispatchEvent(new Event('input'));if(Number(slider.value)>=1000){stop();return;}frame=requestAnimationFrame(tick);}
    $('activation-play').addEventListener('click',()=>{if(!model.activation)return;if(playing){stop();return;}if(Number($('activation-time').value)>=1000)$('activation-time').value=0;playing=true;last=performance.now();$('activation-play').textContent='Ⅱ';$('activation-play').setAttribute('aria-label','Pause activation sequence');frame=requestAnimationFrame(tick);});
    $('reset-schematic').addEventListener('click',()=>load(schematic(),'Schematic reference'));
    new ResizeObserver(draw).observe(canvas);
    load(schematic(),'Schematic reference');
    return {load:(raw,label)=>load(parseGeometry(raw),label),redraw:draw,stop};
  }
  if(typeof module !== 'undefined' && module.exports) module.exports={parseGeometry,schematic};
  else root.HeartTwinGeometry={parseGeometry,schematic,init};
})(typeof window !== 'undefined' ? window : globalThis);
