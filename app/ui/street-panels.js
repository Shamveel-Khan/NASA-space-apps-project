/* Independent dismiss controls live outside content that the science engine updates. */
export function connectStreetPanels(){
  const view=document.getElementById('view-street');
  const panels=[
    ['.street-compass','heading and inclination'],
    ['#street-banner','sol and navigation'],
    ['.street-telemetry','surface telemetry'],
    ['.street-minimap-card','local reconnaissance map'],
    ['#street-key','imagery and depth legend'],
    ['#street-load-note','terrain loading progress'],
    ['#street-toolbar','viewpoint controls'],
    ['.expedition-identity','expedition title'],
    ['.expedition-help','keyboard guide'],
    ['.expedition-brief','expedition brief'],
    ['#street-boundary','coverage boundary'],
    ['#statusstreet','loading information']
  ];
  const dismissed=new Set();
  const restore=document.createElement('button');
  restore.type='button';restore.className='street-overlay-restore';restore.textContent='Restore panels ＋';restore.hidden=true;
  restore.addEventListener('click',event=>{
    event.stopPropagation();dismissed.forEach(window=>window.hidden=false);dismissed.clear();restore.hidden=true;
  });
  view.append(restore);
  panels.forEach(([selector,label])=>{
    const content=document.querySelector('.expedition-page '+selector);if(!content)return;
    const window=document.createElement('div');
    window.className=content.id==='statusstreet'?'street-window street-load-window':content.className+' street-window';
    content.before(window);window.append(content);
    content.classList.add('street-window-content');content.classList.remove('street-optional');
    const close=document.createElement('button');close.type='button';close.className='street-window-close';
    close.textContent='×';close.setAttribute('aria-label',`Close ${label}`);close.title=`Close ${label}`;
    close.addEventListener('click',event=>{
      event.preventDefault();event.stopPropagation();
      if(content.id==='street-boundary'){content.hidden=true;return;}
      window.hidden=true;dismissed.add(window);restore.hidden=false;
      restore.focus({preventScroll:true});
    });
    window.append(close);
  });
}
