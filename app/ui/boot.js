/* Route-specific experiences reconnect to the existing scientific engine. */
(async () => {
  const root=new URL('../',document.baseURI);
  const view=document.body.dataset.initialView;
  window.MARS_INITIAL_VIEW=view;
  try {
    const {renderExperience,routes}=await import(new URL('ui/experiences.js?v=focus-speed-20261115',document.baseURI));
    window.MarsExperience={view,go(next,query=''){location.assign(new URL(routes[next].slug ? routes[next].slug+'/'+query : query || './',root));}};
    document.body.className=`experience experience-${view}`;
    document.body.innerHTML=renderExperience(view,root);
    document.title=view==='globe' ? 'Marswalk — Mars Observatory' : `${routes[view].title} — Marswalk`;
    if(view==='globe'){
      const style=document.createElement('link');style.rel='stylesheet';style.href='ui/orbit-story.css?v=mastcam-color-20261114';document.head.append(style);
      fetch('ui/orbit-story.html?v=mastcam-color-20261114').then(response=>{
        if(!response.ok)throw new Error('Mission story unavailable');return response.text();
      }).then(markup=>{
        document.getElementById('orbit-story-slot').innerHTML=markup;
        for(const src of ['ui/landing-interactions.js','ui/motion.js']){const script=document.createElement('script');script.src=src+'?v=mastcam-color-20261114';document.body.append(script);}
        if(location.hash==='#transmission'||location.hash==='#perspectives')document.querySelector(location.hash)?.scrollIntoView();
      }).catch(error=>{console.warn(error);document.getElementById('orbit-story-slot').innerHTML='<p class="orbit-story-error">The mission story could not load. Reload to reconnect to the archive.</p>';});
    }
    const menu=document.getElementById('expedition-menu');
    document.getElementById('open-expeditions').addEventListener('click',()=>{
      const street=window.marswalk?.S.street?.streetView;
      if(street){street.active=false;street.keys={};}
      menu.showModal();
    });
    menu.addEventListener('close',()=>{const street=window.marswalk?.S.street?.streetView;if(street){street.keys={};street.active=true;}});
    document.getElementById('close-expeditions').addEventListener('click',()=>menu.close());
    menu.addEventListener('click',e=>{if(e.target===menu)menu.close();});
    const {connectExperience}=await import(new URL('ui/interactions.js?v=mastcam-color-20261114',document.baseURI));
    connectExperience(view);
    if(view==='street'){
      const {connectStreetPanels}=await import(new URL('ui/street-panels.js?v=mastcam-color-20261114',document.baseURI));
      connectStreetPanels();
    }
    for(const src of ['ui/focus-mode.js','vendor/three.r128.min.js','vendor/OrbitControls.r128.js','planner.js','streetview.js','ui/explorer.js']) {
      await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=new URL(src,document.baseURI);if(src==='ui/focus-mode.js')s.src+='?v=focus-speed-20261115';if(src==='streetview.js')s.src+='?v=focus-speed-20261115';if(src==='ui/explorer.js')s.src+='?v=focus-speed-20261115';s.onload=resolve;s.onerror=reject;document.body.append(s);});
    }
  } catch(error) {
    const panel=document.createElement('section');panel.className='fatal-signal';panel.setAttribute('role','alert');panel.innerHTML='<span>CONNECTION INTERRUPTED</span><h1>The signal<br>will return.</h1><p>Serve the repository over HTTP, then reload the page to reconnect to the local terrain archive.</p><button onclick="location.reload()">Reconnect ↗</button>';document.body.append(panel);console.error(error);
  }
})();
