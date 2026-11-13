/* Real instrument interactions; all displayed metrics come from the shipped dataset. */
export function connectExperience(view) {
  const $=id=>document.getElementById(id);
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const engineControls=[...document.querySelectorAll('#mission-workspace button, #mission-workspace input, #mission-workspace select')];
  engineControls.forEach(el=>el.disabled=true);
  document.addEventListener('marswalk:ready',()=>engineControls.forEach(el=>{if(!el.matches('[data-camera],#mesh-toggle,#orbit-rotation'))el.disabled=false;}),{once:true});
  let cameraFrame=0, originalPose=null, currentOrbit=null, restoreCamera=null;
  const stopCamera=()=>{cancelAnimationFrame(cameraFrame);cameraFrame=0;if(restoreCamera){restoreCamera();restoreCamera=null;}};
  const pose=()=>{const o=window.marswalk?.GL.active;return o?.cam&&o.ctl.target?{o,position:o.cam.position.clone(),target:o.ctl.target.clone()}:null;};
  function cameraTo(position,target,duration=1350){
    const p=pose();if(!p)return;stopCamera();p.o.ctl.autoRotate=false;
    const started=performance.now(),previous=p.o.ctl.enableDamping;p.o.ctl.enableDamping=false;restoreCamera=()=>{p.o.ctl.enableDamping=previous;};
    const animate=now=>{
      const t=reduced.matches?1:Math.min(1,(now-started)/duration);
      const raw=1-(1+9*t)*Math.exp(-9*t),final=1-10*Math.exp(-9),ease=t===1?1:raw/final;
      p.o.cam.position.lerpVectors(p.position,position,ease);
      p.o.ctl.target.lerpVectors(p.target,target,ease);p.o.cam.lookAt(p.o.ctl.target);window.marswalk.GL.dirty=3;
      if(t<1)cameraFrame=requestAnimationFrame(animate);else{p.o.ctl.enableDamping=previous;restoreCamera=null;cameraFrame=0;}
    };
    cameraFrame=requestAnimationFrame(animate);
  }
  document.addEventListener('marswalk:scene',e=>{
    if(e.detail!==view)return;
    originalPose=pose();
    if(originalPose && currentOrbit!==originalPose.o.ctl){currentOrbit=originalPose.o.ctl;currentOrbit.addEventListener?.('start',stopCamera);}
    if(view==='globe') {
      $('orbit-rotation').disabled=false;
      $('orbit-rotation').setAttribute('aria-pressed',String(!!originalPose?.o.ctl.autoRotate));
      $('orbit-rotation').textContent=originalPose?.o.ctl.autoRotate?'Pause orbit Ⅱ':'Resume orbit ▷';
    }
    document.querySelectorAll('[data-camera],#mesh-toggle').forEach(b=>b.disabled=false);
  });
  document.querySelectorAll('[data-camera],#mesh-toggle,#orbit-rotation').forEach(b=>b.disabled=true);
  document.querySelectorAll('[data-camera]').forEach(button=>button.addEventListener('click',()=>{
    const p=pose();if(!p||!originalPose)return;const distance=originalPose.position.distanceTo(originalPose.target);
    const target=originalPose.target.clone();let destination=originalPose.position.clone();
    if(button.dataset.camera==='gale-overhead'||button.dataset.camera==='terrain-top')destination=target.clone().add(new THREE.Vector3(.01,distance*1.1,.01));
    if(button.dataset.camera==='gale-oblique')destination=target.clone().add(new THREE.Vector3(-distance*.6,distance*.35,distance*.65));
    if(button.dataset.camera==='terrain-low')destination=target.clone().add(new THREE.Vector3(-distance*.65,distance*.15,distance*.65));
    cameraTo(destination,target);
    document.querySelectorAll('[data-camera]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  }));
  if(view==='globe'){
    $('orbit-rotation').addEventListener('click',()=>{
      const orbit=window.marswalk?.S.globe?.o;if(!orbit)return;
      stopCamera();orbit.ctl.autoRotate=!orbit.ctl.autoRotate;window.marswalk.GL.dirty=3;
      $('orbit-rotation').setAttribute('aria-pressed',String(orbit.ctl.autoRotate));$('orbit-rotation').textContent=orbit.ctl.autoRotate?'Pause orbit Ⅱ':'Resume orbit ▷';
    });
    document.addEventListener('marswalk:ready',async()=>{
      try {
        const response=await fetch('world/context.json');if(!response.ok)throw new Error('Mission archive unavailable');
        const {sites}=await response.json();const container=$('mission-sites');container.replaceChildren();
        sites.forEach(site=>{
          const button=document.createElement('button');button.type='button';button.setAttribute('aria-pressed','false');
          const name=document.createElement('strong');name.textContent=site.name.split(' · ')[0];const coords=document.createElement('small');coords.textContent=`${Math.abs(site.lat).toFixed(2)}° ${site.lat<0?'S':'N'} / ${Math.abs(site.lon).toFixed(2)}° ${site.lon<0?'W':'E'}`;button.append(name,coords);
          button.addEventListener('click',()=>{
            const globe=window.marswalk?.S.globe?.o;if(!globe)return;
            const lat=site.lat*Math.PI/180,ph=(site.lon+180)*Math.PI/180;
            const radius=innerWidth<760?3.5:2.6;
            const destination=new THREE.Vector3(-Math.cos(ph)*Math.cos(lat)*radius,Math.sin(lat)*radius,Math.sin(ph)*Math.cos(lat)*radius);
            cameraTo(destination,new THREE.Vector3(0,0,0),1700);
            container.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
            $('orbit-rotation').setAttribute('aria-pressed','false');$('orbit-rotation').textContent='Resume orbit ▷';
          });container.append(button);
        });
      }catch(error){$('mission-sites').textContent='Mission catalogue unavailable. The globe remains interactive.';console.warn(error);}
    },{once:true});
  }
  if(view==='map')document.querySelector('[data-map-fit]').addEventListener('click',()=>{const api=window.marswalk;if(!api)return;api.S.fitted=false;api.fitMap();});
  if(view==='terrain')$('mesh-toggle').addEventListener('click',()=>{
    const api=window.marswalk,layer=api?.S.t3dLayers;if(!layer)return;
    const enabled=$('mesh-toggle').getAttribute('aria-pressed')!=='true';
    layer.mesh.material.wireframe=enabled;
    if(layer.costMesh)layer.costMesh.material.wireframe=enabled;
    $('mesh-toggle').setAttribute('aria-pressed',String(enabled));api.GL.dirty=3;
  });
  if(view==='close'){
    const updateSpecimen=key=>{
      const closeups=window.marswalk?.S.data.closeups;if(!closeups)return;
      const stop=closeups.find(c=>String(c.sol)===String(key)) || closeups.find(c=>c.sol===1100) || closeups[0];
      $('specimen-title').textContent=key==='all'?`ALL RECONSTRUCTED STOPS / REFERENCE SOL ${stop.sol}`:`SPECIMEN / SOL ${stop.sol}`;
      for(const [id,file] of [['comparison-photo','step1_photo.jpg'],['comparison-depth','step2_stereo.png']]){
        $(id).src=`${stop.folder}/img/${file}`;$(id).alt=`${id==='comparison-photo'?'Source Navcam photograph':'Measured stereo depth'} / Curiosity sol ${stop.sol}`;
      }
      $('specimen-measures').innerHTML=`<div><b>${stop.photos}</b><span>NAVCAM FRAMES</span></div><div><b>${Math.round(stop.area_m2).toLocaleString()} m²</b><span>RECONSTRUCTED AREA</span></div><div><b>${(stop.error*100).toFixed(1)}%</b><span>HELD-OUT DEPTH ERROR</span></div>`;
      document.querySelectorAll('[data-specimen]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.specimen===String(key))));
    };
    document.addEventListener('marswalk:ready',()=>{
      const stops=window.marswalk.S.data.closeups;$('specimen-count').textContent=String(stops.length).padStart(2,'0');
      const frames=$('contact-frames');
      stops.forEach(stop=>{
        const button=document.createElement('button');button.type='button';button.dataset.specimen=String(stop.sol);
        const image=document.createElement('img');image.src=`${stop.folder}/img/step1_photo.jpg`;image.alt=`Navcam contact frame, sol ${stop.sol}`;image.loading='lazy';
        const title=document.createElement('strong');title.textContent=`SOL ${stop.sol}`;const caption=document.createElement('small');caption.textContent=`${stop.photos} FRAMES / ${Math.round(stop.area_m2)} m²`;
        button.append(image,title,caption);button.addEventListener('click',()=>{$('close-pick').value=String(stop.sol);$('close-pick').dispatchEvent(new Event('change'));});frames.append(button);
      });
      updateSpecimen(new URLSearchParams(location.search).get('sol')||'1100');
    },{once:true});
    document.addEventListener('marswalk:specimen',e=>updateSpecimen(e.detail));
    $('compare-range').addEventListener('input',e=>document.querySelector('.photo-comparison').style.setProperty('--split',`${e.target.value}%`));
  }
  // A spatial entrance for each page's own composition, rather than identical section fades.
  if(!reduced.matches){
    const selectors={globe:'.mission-index,.orbit-destination',gale:'.atlas-heading,.atlas-caption',map:'.departure-sheet,.output-title',terrain:'.relief-tools,.relief-console',close:'.collection-heading,.specimen-topline',street:'.expedition-identity,.street-telemetry'};
    document.querySelectorAll(selectors[view]).forEach((el,i)=>el.animate([
      {transform:`perspective(900px) translate3d(${i%2?-35:35}px,0,50px) rotateY(${i%2?-7:7}deg)`,clipPath:'inset(0 100% 0 0)'},
      {transform:'perspective(900px) translate3d(0,0,0) rotateY(0deg)',clipPath:'inset(0 0 0 0)'}
    ],{duration:1200,delay:150+i*140,easing:'cubic-bezier(.16,1,.3,1)',fill:'backwards'}));
  }
  document.querySelectorAll('a[href]').forEach(link=>link.addEventListener('click',e=>{
    const destination=new URL(link.href);if(e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||link.target||link.hasAttribute('download')||reduced.matches||destination.origin!==location.origin||destination.hash||destination.href===location.href||'startViewTransition' in document)return;
    e.preventDefault();document.body.classList.add('departing');setTimeout(()=>location.assign(destination.href),400);
  }));
  addEventListener('pageshow',()=>document.body.classList.remove('departing'));
  reduced.addEventListener('change',()=>{if(reduced.matches){stopCamera();if(window.marswalk?.S.globe?.o){window.marswalk.S.globe.o.ctl.autoRotate=false;if($('orbit-rotation')){$('orbit-rotation').textContent='Resume orbit ▷';$('orbit-rotation').setAttribute('aria-pressed','false');}}document.getAnimations().forEach(a=>{try{if(a.effect?.getComputedTiming().endTime===Infinity)a.cancel();else a.finish();}catch(_){a.cancel();}});}});
  document.addEventListener('marswalk:error',()=>{
    const status=document.querySelector(`#view-${view} .status`);if(!status)return;
    status.textContent='Terrain link unavailable. Reload this page to reconnect.';const retry=document.createElement('button');retry.className='action';retry.textContent='Reconnect ↗';retry.onclick=()=>location.reload();status.append(retry);
  });
  addEventListener('pagehide',()=>stopCamera());
}
