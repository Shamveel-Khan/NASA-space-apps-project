/* Shared full-screen model focus mode for every Marswalk visualization. */
(function () {
  'use strict';

  const target = document.querySelector('[data-focus-target], .view, .viewport');
  if (!target) return;

  target.classList.add('model-focus-target');

  const style = document.createElement('style');
  style.textContent = `
    .model-focus-toggle {
      position: absolute; top: 12px; right: 12px; z-index: 60;
      display: grid; place-items: center; min-width: 36px; min-height: 32px;
      border: 1px solid rgba(255,255,255,.3); border-radius: 3px;
      background: rgba(8,12,12,.72); color: #f3ead7; cursor: pointer;
      font: 600 11px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      letter-spacing: .08em; backdrop-filter: blur(8px);
    }
    .model-focus-toggle:hover, .model-focus-toggle:focus-visible {
      border-color: rgba(255,255,255,.75); background: rgba(22,30,25,.9);
      outline: 2px solid #e2b483; outline-offset: 2px;
    }
    body.model-focus-active { overflow: hidden !important; }
    body.model-focus-active .model-focus-target,
    .model-focus-target:fullscreen,
    .model-focus-target:-webkit-full-screen {
      position: fixed !important; inset: 0 !important; z-index: 2147483646 !important;
      width: 100vw !important; height: 100vh !important; min-width: 0 !important;
      min-height: 0 !important; max-width: none !important; max-height: none !important;
      margin: 0 !important; padding: 0 !important; border: 0 !important;
      border-radius: 0 !important; background: #080b0d !important;
    }
    body.model-focus-active .model-focus-target > :not(canvas):not(img),
    .model-focus-target:fullscreen > :not(canvas):not(img),
    .model-focus-target:-webkit-full-screen > :not(canvas):not(img) { display: none !important; }
    body.model-focus-active .model-focus-target > canvas:not([hidden]),
    body.model-focus-active .model-focus-target > img,
    .model-focus-target:fullscreen > canvas:not([hidden]),
    .model-focus-target:fullscreen > img,
    .model-focus-target:-webkit-full-screen > canvas:not([hidden]),
    .model-focus-target:-webkit-full-screen > img {
      display: block !important; width: 100% !important; height: 100% !important;
      max-width: none !important; object-fit: contain;
    }
  `;
  document.head.append(style);

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'model-focus-toggle';
  button.textContent = 'F';
  button.title = 'Focus on the model (F)';
  button.setAttribute('aria-label', 'Show only this visualization in full screen. Keyboard shortcut: F.');
  button.setAttribute('aria-pressed', 'false');
  target.append(button);

  let active = false;
  const fullscreenElement = () => document.fullscreenElement || document.webkitFullscreenElement;
  const dispatch = () => document.dispatchEvent(new CustomEvent('marswalk:focuschange', {
    detail: { active, target }
  }));

  function setActive(next) {
    active = next;
    document.body.classList.toggle('model-focus-active', active);
    target.classList.toggle('model-focus-active', active);
    button.setAttribute('aria-pressed', String(active));
    dispatch();
    window.dispatchEvent(new Event('resize'));
  }

  async function enter() {
    setActive(true);
    const request = target.requestFullscreen || target.webkitRequestFullscreen;
    if (!request) return;
    try {
      await request.call(target, { navigationUI: 'hide' });
    } catch (_) {
      // CSS focus mode remains available when browser fullscreen is unavailable.
    }
  }

  async function exit() {
    const full = fullscreenElement();
    if (full === target) {
      const close = document.exitFullscreen || document.webkitExitFullscreen;
      if (close) {
        try { await close.call(document); } catch (_) { /* fall through to CSS cleanup */ }
      }
    }
    setActive(false);
  }

  const toggle = () => active ? exit() : enter();
  button.addEventListener('click', toggle);
  document.addEventListener('keydown', event => {
    if (event.code !== 'KeyF' || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
    const element = event.target;
    if (element && (element.isContentEditable || element.closest?.('input, textarea, select'))) return;
    event.preventDefault();
    toggle();
  });

  const syncFullscreen = () => {
    if (active && fullscreenElement() !== target) setActive(false);
    window.dispatchEvent(new Event('resize'));
  };
  document.addEventListener('fullscreenchange', syncFullscreen);
  document.addEventListener('webkitfullscreenchange', syncFullscreen);
})();
