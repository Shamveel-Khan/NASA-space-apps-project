(() => {
  const names=['01 / LIGHT FROM ANOTHER WORLD','02 / THE MEASURED SURFACE','03 / FILLING THE UNKNOWN'];
  const descriptions=[
    'Curiosity’s Navcam captures the surface. These original NASA photographs anchor every rover reconstruction in the archive.',
    'NASA Navcam stereo XYZ supplies measured geometry. Missing coverage stays missing at this stage; there is no inferred ground in the measurement.',
    'Depth Anything V2 predictions are scaled against stereo measurements to fill gaps. The ground instruments explicitly distinguish these estimates from measured XYZ.'
  ];
  document.querySelectorAll('[data-process]').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('[data-process]').forEach(b=>{b.classList.toggle('is-active',b===button);b.setAttribute('aria-pressed',String(b===button));});
    const index=Number(button.dataset.process);document.getElementById('process-name').textContent=names[index];document.getElementById('process-description').textContent=descriptions[index];
  }));
  const populateArchive=()=>{
    const results=window.marswalk?.S.data?.results;if(!results)return;
    for(const [id,value] of [['archive-photos',results.photos],['archive-stops',results.stops],['archive-ground',Math.round(results.area_m2)]])document.getElementById(id).textContent=value.toLocaleString();
  };
  if(window.marswalk?.S.data)populateArchive();else document.addEventListener('marswalk:ready',populateArchive,{once:true});
  const reduce=matchMedia('(prefers-reduced-motion: reduce)');
  if(!reduce.matches){
    const observer=new IntersectionObserver(entries=>entries.forEach(entry=>{
      if(!entry.isIntersecting)return;const el=entry.target;
      el.animate([{clipPath:'inset(0 100% 0 0)',transform:'perspective(1000px) rotateY(-6deg)'},{clipPath:'inset(0)',transform:'perspective(1000px) rotateY(0deg)'}],{duration:1300,easing:'cubic-bezier(.16,1,.3,1)'});observer.unobserve(el);
    }),{threshold:.15});
    document.querySelectorAll('.manifesto h2,.perspectives h2,.destination,.archive-ledger').forEach(el=>observer.observe(el));
    reduce.addEventListener('change',()=>{if(reduce.matches){observer.disconnect();document.getAnimations().forEach(animation=>{try{if(animation.effect?.getComputedTiming().endTime===Infinity)animation.cancel();else animation.finish();}catch(_){animation.cancel();}});}});
  }
})();
