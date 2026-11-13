/* Cinematic presentation of the shipped USGS DEM, not a replacement scientific dataset. */
(async () => {
  const canvas = document.getElementById('cinema');
  const status = document.getElementById('scene-state');
  const reduceQuery = matchMedia('(prefers-reduced-motion: reduce)');
  let paused = reduceQuery.matches, raf = 0, disposed = false;
  const toggle = document.getElementById('motion-toggle');
  const syncToggle = () => { document.body.classList.toggle('cinema-paused',paused); toggle.textContent = paused ? 'Resume motion ▷' : 'Pause motion Ⅱ'; toggle.setAttribute('aria-pressed', String(paused)); };
  syncToggle();
  const marker = document.getElementById('terrain-marker');
  marker.addEventListener('click', () => { const open = marker.getAttribute('aria-expanded') !== 'true'; marker.setAttribute('aria-expanded', String(open)); document.getElementById('marker-detail').hidden = !open; });
  document.addEventListener('keydown', e => { if(e.key==='Escape'){ marker.setAttribute('aria-expanded','false'); document.getElementById('marker-detail').hidden=true; } });
  let renderer, scene, camera;
  try {
    await new Promise((resolve, reject) => { const script = document.createElement('script'); script.src='app/vendor/three.r128.min.js'; script.onload=resolve; script.onerror=reject; document.head.append(script); });
    const checked = async url => { const r = await fetch(url); if(!r.ok) throw new Error(`Missing terrain asset: ${url}`); return r; };
    const context = await (await checked('app/world/context.json')).json();
    const D = context.gale_dem;
    const [encoded, texture] = await Promise.all([
      checked('app/' + D.file).then(r => r.text()),
      new Promise((resolve,reject) => new THREE.TextureLoader().load('app/' + D.shade, resolve, undefined, reject))
    ]);
    const bytes = Uint8Array.from(atob(encoded.trim()), c => c.charCodeAt(0));
    const heights = new Uint16Array(bytes.buffer);
    let minimum = Infinity;
    for(let i=0;i<heights.length;i++) if(heights[i]!==65535) minimum=Math.min(minimum,heights[i]/10);
    const exaggeration = 2.5, width = (D.cols-1)*D.step_m/1000, depth = (D.rows-1)*D.step_m/1000;
    const sample = (x,z) => { const col=Math.max(0,Math.min(D.cols-1,Math.round((x/width+.5)*(D.cols-1)))); const row=Math.max(0,Math.min(D.rows-1,Math.round((z/depth+.5)*(D.rows-1)))); const v=heights[row*D.cols+col]; return v===65535?0:(v/10-minimum)/1000*exaggeration; };
    renderer = new THREE.WebGLRenderer({canvas,antialias:innerWidth>700,alpha:false,powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, innerWidth<700?1.25:1.6));
    renderer.outputEncoding=THREE.sRGBEncoding; renderer.toneMapping=THREE.ACESFilmicToneMapping; renderer.toneMappingExposure=1.15;
    scene = new THREE.Scene(); scene.background=new THREE.Color('#4f3428'); scene.fog=new THREE.FogExp2('#72503d',.033);
    const skyGeometry = new THREE.SphereGeometry(110,32,16);
    const skyMaterial = new THREE.ShaderMaterial({side:THREE.BackSide,depthWrite:false,uniforms:{top:{value:new THREE.Color('#171414')},bottom:{value:new THREE.Color('#976f50')}},vertexShader:'varying vec3 vPosition;void main(){vPosition=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',fragmentShader:'varying vec3 vPosition;uniform vec3 top;uniform vec3 bottom;void main(){float h=clamp(normalize(vPosition).y,0.,1.);vec3 c=mix(bottom,top,pow(h,.45));gl_FragColor=vec4(c,1.);}'});
    scene.add(new THREE.Mesh(skyGeometry,skyMaterial));
    camera = new THREE.PerspectiveCamera(49,1,.05,180);
    const geo = new THREE.PlaneGeometry(width,depth,Math.min(220,D.cols-1),Math.min(380,D.rows-1)); geo.rotateX(-Math.PI/2);
    const pos=geo.attributes.position;
    for(let i=0;i<pos.count;i++) pos.setY(i,sample(pos.getX(i),pos.getZ(i)));
    geo.computeVertexNormals(); texture.encoding=THREE.sRGBEncoding; texture.anisotropy=Math.min(4,renderer.capabilities.getMaxAnisotropy());
    // A fine, procedural bump provides illustrative surface texture without altering the measured vertices.
    const bumpCanvas=document.createElement('canvas'); bumpCanvas.width=bumpCanvas.height=256;
    const bx=bumpCanvas.getContext('2d'), pixels=bx.createImageData(256,256); let seed=1100;
    const random=()=>{ seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296; };
    for(let i=0;i<pixels.data.length;i+=4){const v=90+random()*100;pixels.data[i]=pixels.data[i+1]=pixels.data[i+2]=v;pixels.data[i+3]=255;}
    bx.putImageData(pixels,0,0); const bump=new THREE.CanvasTexture(bumpCanvas);bump.wrapS=bump.wrapT=THREE.RepeatWrapping;bump.repeat.set(60,90);
    const terrain=new THREE.Mesh(geo,new THREE.MeshStandardMaterial({map:texture,color:0xc18a64,roughness:1,metalness:0,bumpMap:bump,bumpScale:.065}));scene.add(terrain);
    // Georeferenced archive beacon for the shipped sol 1100 planning region.
    const archiveX=((137.369321-D.west)/D.dlon)*D.step_m/1000-width/2;
    const archiveZ=((D.north-(-4.6728315))/D.dlat)*D.step_m/1000-depth/2;
    const archivePoint=new THREE.Vector3(archiveX,sample(archiveX,archiveZ)+.11,archiveZ);
    const beaconGeometry=new THREE.SphereGeometry(.045,16,12),beaconMaterial=new THREE.MeshBasicMaterial({color:0xf5c192});
    const beacon=new THREE.Mesh(beaconGeometry,beaconMaterial);beacon.position.copy(archivePoint);scene.add(beacon);
    const projectedArchive=new THREE.Vector3();
    scene.add(new THREE.HemisphereLight(0xe8baa1,0x24160f,.85));
    const sunlight=new THREE.DirectionalLight(0xffc58e,2.4); sunlight.position.set(-35,18,18);scene.add(sunlight);
    if(innerWidth>900){renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;sunlight.castShadow=true;sunlight.shadow.mapSize.set(1024,1024);Object.assign(sunlight.shadow.camera,{left:-36,right:36,top:36,bottom:-36,near:1,far:120});sunlight.shadow.camera.updateProjectionMatrix();sunlight.shadow.bias=-.0005;terrain.castShadow=true;terrain.receiveShadow=true;}
    // Transparent atmospheric dust is illustrative, deliberately separate from measured geometry.
    const dustGeo=new THREE.BufferGeometry(), dustPositions=new Float32Array(240*3);
    for(let i=0;i<240;i++){dustPositions[i*3]=(random()-.5)*38;dustPositions[i*3+1]=random()*9+1;dustPositions[i*3+2]=(random()-.5)*52;}
    dustGeo.setAttribute('position',new THREE.BufferAttribute(dustPositions,3));
    const dust=new THREE.Points(dustGeo,new THREE.PointsMaterial({color:0xf5c29a,size:.025,transparent:true,opacity:.3,depthWrite:false}));scene.add(dust);
    // A thin contour scan reveals true DEM topology; it never changes the surface itself.
    const scanMaterial=new THREE.ShaderMaterial({uniforms:{uScan:{value:-25},uColor:{value:new THREE.Color('#f1bf91')}},vertexShader:'varying vec3 vWorld; void main(){vec4 w=modelMatrix*vec4(position,1.);vWorld=w.xyz;gl_Position=projectionMatrix*viewMatrix*w;}',fragmentShader:'uniform float uScan; uniform vec3 uColor; varying vec3 vWorld; void main(){float line=1.-smoothstep(0.0,.045,abs(vWorld.z-uScan));float contours=1.-smoothstep(.0,.018,abs(fract(vWorld.y*2.)-.5));gl_FragColor=vec4(uColor,line*.35+contours*.035);}',transparent:true,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-1});
    scene.add(new THREE.Mesh(geo,scanMaterial));
    const pointer={x:0,y:0}, spring={x:0,y:0,vx:0,vy:0}, target=new THREE.Vector3();
    const onPointer=e=>{pointer.x=(e.clientX/innerWidth-.5)*2;pointer.y=(e.clientY/innerHeight-.5)*2;};
    addEventListener('pointermove',onPointer,{passive:true});
    const resize=()=>{const rect=canvas.getBoundingClientRect();renderer.setSize(rect.width,rect.height,false);camera.aspect=rect.width/rect.height;camera.updateProjectionMatrix();requestAnimationFrame(()=>{if(!disposed)renderer.render(scene,camera);});};
    const observer=new ResizeObserver(resize);observer.observe(canvas);resize();
    // A constant-radius orbit runs on active animation time, independent of page scroll.
    const orbitCenter = new THREE.Vector3(0, sample(0,5), 5);
    const orbitRadius = 14, orbitSpeed = .11; // radians/second; one revolution in about 57 seconds
    let orbitHeight = orbitCenter.y + 6;
    for(let i=0;i<96;i++){
      const angle=i/96*Math.PI*2;
      orbitHeight=Math.max(orbitHeight,sample(Math.cos(angle)*orbitRadius,orbitCenter.z+Math.sin(angle)*orbitRadius)+2.2);
    }
    let orbitElapsed=0, previous=0, lastFrame=0, shown=true, hasPose=false;
    const onScroll=()=>{
      document.body.style.setProperty('--descent',Math.max(0,Math.min(1,scrollY/(innerHeight*1.3))));
      if(scrollY>=innerHeight*.85)marker.hidden=true;
    };
    addEventListener('scroll',onScroll,{passive:true});onScroll();
    const exposureObserver=new IntersectionObserver(entries=>{shown=entries[0].isIntersecting; if(shown) wake();});exposureObserver.observe(canvas);
    const draw=now=>{
      raf=0;if(disposed || document.hidden || !shown)return;
      if(now-lastFrame<32){raf=requestAnimationFrame(draw);return;}lastFrame=now;
      const dt=Math.min((now-(previous||now))/1000,.05);previous=now;
      if(paused && hasPose){renderer.render(scene,camera);return;}
      if(!paused)orbitElapsed+=dt;
      const angle=-2.35+orbitElapsed*orbitSpeed;
      const desiredX=paused?0:pointer.x, desiredY=paused?0:pointer.y;
      spring.vx+=(desiredX-spring.x)*38*dt;spring.vy+=(desiredY-spring.y)*38*dt;
      spring.vx*=Math.exp(-8*dt);spring.vy*=Math.exp(-8*dt);spring.x+=spring.vx*dt;spring.y+=spring.vy*dt;
      camera.position.set(Math.cos(angle)*orbitRadius,orbitHeight,orbitCenter.z+Math.sin(angle)*orbitRadius);
      target.copy(orbitCenter);target.x+=spring.x*.35;target.y+=spring.y*.2;camera.lookAt(target);
      if(!paused){dust.rotation.y+=dt*.008;scanMaterial.uniforms.uScan.value=-27+(orbitElapsed/1.4)%55;}
      renderer.render(scene,camera);hasPose=true;
      projectedArchive.copy(archivePoint).project(camera);
      const onScreen=projectedArchive.z<1 && Math.abs(projectedArchive.x)<.85 && Math.abs(projectedArchive.y)<.85 && scrollY<innerHeight*.85;
      marker.hidden=!onScreen;
      if(onScreen){marker.style.left=((projectedArchive.x+1)/2*canvas.clientWidth)+'px';marker.style.right='auto';marker.style.top=((1-projectedArchive.y)/2*canvas.clientHeight)+'px';marker.style.transform='translate(-18px,-50%)';}
      if(!paused || Math.abs(spring.vx)+Math.abs(spring.vy)>.001)raf=requestAnimationFrame(draw);
    };
    function wake(){if(!raf&&!disposed){previous=0;raf=requestAnimationFrame(draw);}}
    const onVisibility=()=>{if(document.hidden){cancelAnimationFrame(raf);raf=0;}else wake();};document.addEventListener('visibilitychange',onVisibility);
    const onMotion=()=>{paused=reduceQuery.matches;syncToggle();wake();};reduceQuery.addEventListener('change',onMotion);
    toggle.addEventListener('click',()=>{paused=!paused;syncToggle();wake();});
    canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();cancelAnimationFrame(raf);raf=0;document.body.classList.remove('scene-ready');status.textContent='GRAPHICS PAUSED / PHOTOGRAPHIC FALLBACK';});
    canvas.addEventListener('webglcontextrestored',()=>{document.body.classList.add('scene-ready');wake();});
    document.body.classList.add('scene-ready');status.textContent='USGS TERRAIN / CINEMATIC VIEW · 2.5× HEIGHT';wake();
    addEventListener('pagehide',e=>{if(e.persisted){cancelAnimationFrame(raf);raf=0;return;}disposed=true;cancelAnimationFrame(raf);observer.disconnect();exposureObserver.disconnect();removeEventListener('pointermove',onPointer);removeEventListener('scroll',onScroll);document.removeEventListener('visibilitychange',onVisibility);reduceQuery.removeEventListener('change',onMotion);geo.dispose();beaconGeometry.dispose();beaconMaterial.dispose();skyGeometry.dispose();skyMaterial.dispose();terrain.material.dispose();scanMaterial.dispose();dustGeo.dispose();dust.material.dispose();texture.dispose();bump.dispose();renderer.dispose();});
    addEventListener('pageshow',()=>{if(!disposed)wake();});
  } catch(error){console.warn('Cinematic terrain unavailable:',error);status.textContent='ORBITAL IMAGE / 3D UNAVAILABLE';toggle.hidden=true;if(renderer)renderer.dispose();}
})();
