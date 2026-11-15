/* Small, dependency-free motion primitives. Navigation and content work without them. */
(() => {
  const preference=matchMedia('(prefers-reduced-motion: reduce)');
  const fine=matchMedia('(pointer:fine)');
  let observer;
  function setup(){
    document.body.classList.toggle('motion-enabled',!preference.matches);
    observer?.disconnect();
    if(preference.matches)return;
    observer=new IntersectionObserver(entries=>entries.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in-view');observer.unobserve(e.target);}}),{threshold:.12});
    document.querySelectorAll('.reveal').forEach(el=>observer.observe(el));
  }
  setup();preference.addEventListener('change',setup);
  document.querySelectorAll('.magnetic').forEach(el=>{
    let x=0,y=0,vx=0,vy=0,tx=0,ty=0,raf=0,last=0;
    function frame(now){const dt=Math.min((now-(last||now))/1000,.032);last=now;
      if(preference.matches){el.style.transform='';raf=0;return;}
      vx+=(tx-x)*130*dt;vy+=(ty-y)*130*dt;vx*=Math.exp(-14*dt);vy*=Math.exp(-14*dt);x+=vx*dt;y+=vy*dt;
      el.style.transform=`translate3d(${x}px,${y}px,0)`;
      if(Math.abs(tx-x)+Math.abs(ty-y)+Math.abs(vx)+Math.abs(vy)>.02)raf=requestAnimationFrame(frame);else{raf=0;last=0;}
    }
    function wake(){if(!raf)raf=requestAnimationFrame(frame);}
    el.addEventListener('pointermove',e=>{if(preference.matches||!fine.matches)return;const r=el.getBoundingClientRect();tx=(e.clientX-r.left-r.width/2)*.15;ty=(e.clientY-r.top-r.height/2)*.2;wake();});
    el.addEventListener('pointerleave',()=>{tx=ty=0;wake();});
    el.addEventListener('blur',()=>{tx=ty=0;wake();});
    preference.addEventListener('change',()=>{tx=ty=0;if(preference.matches){cancelAnimationFrame(raf);raf=0;el.style.transform='';}});
  });
})();
