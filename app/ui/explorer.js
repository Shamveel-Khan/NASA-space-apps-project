
(function () {
  const $ = (id) => document.getElementById(id);
  const css = (n) => getComputedStyle(document.body).getPropertyValue(n).trim();
  const S = { data: null, img: null, H: null, slope: null, cols: 0, rows: 0, g: 2, view: { s: 0.35, x: 0, y: 0 },
              picks: [], route: null, slopeCanvas: null };
  const SLOPE_COL = [[15, [47, 158, 98]], [25, [217, 150, 28]], [91, [214, 69, 65]]];
  // AI4Mars 4-class regolith color palette (0=Soil 1=Bedrock 2=Sand 3=BigRock 255=Unknown→Soil)
  const REGOLITH_COL = { 0: [194, 178, 128], 1: [128, 128, 128], 2: [237, 201, 175], 3: [180, 50, 50], 255: [194, 178, 128] };
  const REGOLITH_NAMES = { 0: 'Soil', 1: 'Bedrock', 2: 'Sand', 3: 'Big Rock', 255: 'Unknown' };
  const closeKey = c => c.id || String(c.sol);
  const closeLabel = c => c.label || (c.sol === 1100 ? 'Sol 1100 · Navcam grayscale' : `Sol ${c.sol}`);
  const closeEntries = () => S.data.closeups.flatMap(c => [c, ...(S.data.closeup_variants || []).filter(v => v.sol === c.sol)]);
  const CLOSE_MODES = ['photo', 'slope', 'regolith', 'depth_src'];
  const CLOSE_MODE_LABELS = { photo: 'Show walk slope', slope: 'Show AI4Mars regolith', regolith: 'Show depth source', depth_src: 'Show photo' };

  function b64ToU8(b64) { const s = atob(b64.trim()), u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u; }
  const fmtMin = (sec) => { const m = Math.round(sec / 60); return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}`; };
  const hhmm = (min) => `${String(Math.floor(min / 60) % 24).padStart(2, '0')}:${String(Math.round(min % 60)).padStart(2, '0')}`;
  const toMin = (t) => { const [h, m] = (t || '0:0').split(':').map(Number); return h * 60 + (m || 0); };

  // ---------------- terrain + slope ----------------
  function prepareTerrain(u8) {
    const n = S.cols * S.rows, u16 = new Uint16Array(u8.buffer, 0, n);
    S.H = new Float32Array(n); S.valid = new Uint8Array(n);
    for (let i = 0; i < n; i++) { if (u16[i] !== 65535) { S.H[i] = u16[i] / 100; S.valid[i] = 1; } else S.H[i] = NaN; }
    S.slope = new Float32Array(n);
    const g = S.g, C = S.cols;
    for (let y = 0; y < S.rows; y++) for (let x = 0; x < C; x++) {
      const i = y * C + x;
      if (!S.valid[i]) { S.slope[i] = 99; continue; }
      const xl = x > 0 ? i - 1 : i, xr = x < C - 1 ? i + 1 : i, yu = y > 0 ? i - C : i, yd = y < S.rows - 1 ? i + C : i;
      const dx = (S.H[xr] - S.H[xl]) / ((xr - xl) * g || 1), dy = (S.H[yd] - S.H[yu]) / (((yd - yu) / C) * g || 1);
      S.slope[i] = Math.atan(Math.hypot(dx || 0, dy || 0)) * 180 / Math.PI;
    }
    const c = document.createElement('canvas'); c.width = C; c.height = S.rows;
    const ctx = c.getContext('2d'), im = ctx.createImageData(C, S.rows);
    for (let i = 0; i < n; i++) {
      const s = S.slope[i], col = s > 90 ? null : SLOPE_COL.find(([lim]) => s < lim)[1];
      if (!col) continue;
      im.data[i * 4] = col[0]; im.data[i * 4 + 1] = col[1]; im.data[i * 4 + 2] = col[2]; im.data[i * 4 + 3] = s < 15 ? 0 : 160;  // easy ground stays clear so the photo reads
    }
    ctx.putImageData(im, 0, 0); S.slopeCanvas = c;
  }

  // ---------------- LightGBM traversability cost overlay ----------------
  /**
   * Build a canvas texture from the uint8 LightGBM traversability cost grid (0=safe, 255=impassable).
   * The canvas is the same resolution as the terrain grid (2 m/cell).
   * Color map (Bekker-Wong hazard scale):
   *   0–63   (<25%): transparent — safe flat ground, shows through to satellite photo
   *   64–127 (25–50%): amber — moderate slope / mild roughness
   *   128–191 (50–75%): orange — eolian sand / high roughness
   *   192–255 (>75%): crimson — cliffs >22°, boulder-field, impassable
   */
  function prepareCostOverlay(u8) {
    const n = S.cols * S.rows, costs = new Uint8Array(u8.buffer, 0, n);
    S.costValues = costs;
    const c = document.createElement('canvas'); c.width = S.cols; c.height = S.rows;
    const solid = document.createElement('canvas'); solid.width = S.cols; solid.height = S.rows;
    const ctx = c.getContext('2d'), solidCtx = solid.getContext('2d');
    const im = ctx.createImageData(S.cols, S.rows), solidIm = solidCtx.createImageData(S.cols, S.rows);
    for (let i = 0; i < n; i++) {
      const v = costs[i];
      let r = 0, g = 0, b = 0, a = 0;
      if (v < 64)        { r = 47;  g = 190; b = 100; a = 0; }          // safe: transparent
      else if (v < 128)  { r = 217; g = 150; b = 28;  a = 140; }        // moderate: amber
      else if (v < 192)  { r = 214; g = 90;  b = 35;  a = 175; }        // high: orange
      else               { r = 200; g = 30;  b = 30;  a = 210; }        // critical: crimson
      im.data[i * 4] = r; im.data[i * 4 + 1] = g; im.data[i * 4 + 2] = b; im.data[i * 4 + 3] = a;
      solidIm.data[i * 4] = r; solidIm.data[i * 4 + 1] = g; solidIm.data[i * 4 + 2] = b; solidIm.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(im, 0, 0); solidCtx.putImageData(solidIm, 0, 0);
    S.costCanvas = c; S.costSolidCanvas = solid;
  }

  // ---------------- route: app/planner.js (tested with node --test app/tests/) ----------------
  const plan = (a, b, maxSlope) => MarswalkPlanner.planRoute(S, a, b, maxSlope, S.hazardCost);

  // ---------------- map drawing ----------------
  const mapC = $('map'), mctx = mapC.getContext('2d');
  function fitMap() {
    const r = mapC.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
    mapC.width = r.width * dpr; mapC.height = r.height * dpr; mctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!S.fitted && S.img && r.width > 0) {
      const size = S.data.window.size_m;
      S.view.s = Math.min(r.width, r.height) / (size * 0.62);
      S.view.x = r.width / 2 - (size / 2) * S.view.s; S.view.y = r.height / 2 - (size / 2) * S.view.s; S.fitted = true;
    }
    drawMap();
  }
  const toScreen = (x, y) => [S.view.x + x * S.view.s, S.view.y + y * S.view.s];
  const toWorld = (sx, sy) => [(sx - S.view.x) / S.view.s, (sy - S.view.y) / S.view.s];
  function drawMap() {
    if (!S.img) return;
    const r = mapC.getBoundingClientRect(), size = S.data.window.size_m, v = S.view;
    mctx.fillStyle = css('--view'); mctx.fillRect(0, 0, r.width, r.height);
    mctx.imageSmoothingEnabled = v.s < 2;
    mctx.drawImage(S.img, v.x, v.y, size * v.s, size * v.s);
    if ($('lay-slope').checked) { mctx.imageSmoothingEnabled = false; mctx.drawImage(S.slopeCanvas, v.x, v.y, size * v.s, size * v.s); }
    if ($('lay-cost') && $('lay-cost').checked && S.costCanvas) {
      mctx.imageSmoothingEnabled = false;
      mctx.drawImage(S.costCanvas, v.x, v.y, size * v.s, size * v.s);
      if ($('cost-key')) $('cost-key').hidden = false;
      if ($('cost-key2')) $('cost-key2').hidden = false;
      if ($('cost-key3')) $('cost-key3').hidden = false;
    } else {
      if ($('cost-key')) $('cost-key').hidden = true;
      if ($('cost-key2')) $('cost-key2').hidden = true;
      if ($('cost-key3')) $('cost-key3').hidden = true;
    }
    if ($('lay-path').checked) {
      mctx.strokeStyle = css('--accent'); mctx.lineWidth = 2.5; mctx.lineJoin = 'round';
      for (const seg of S.data.path) { mctx.beginPath(); seg.forEach(([x, y], k) => { const [sx, sy] = toScreen(x, y); k ? mctx.lineTo(sx, sy) : mctx.moveTo(sx, sy); }); mctx.stroke(); }
      mctx.font = '600 11px "Martian Mono", monospace';
      for (const s of S.data.stops) {
        const [sx, sy] = toScreen(s.x, s.y), big = S.closeSols.has(s.sol);
        mctx.fillStyle = big ? '#ffffff' : css('--accent'); mctx.beginPath(); mctx.arc(sx, sy, big ? 6 : 3.2, 0, Math.PI * 2); mctx.fill();
        if (big) { mctx.strokeStyle = css('--accent'); mctx.lineWidth = 3; mctx.stroke(); }
        if (big || v.s > 1.2) { mctx.fillStyle = '#fff'; mctx.shadowColor = 'rgba(0,0,0,.8)'; mctx.shadowBlur = 4; mctx.fillText(big ? `sol ${s.sol} · 3D` : 'sol ' + s.sol, sx + 9, sy - 8); mctx.shadowBlur = 0; }
      }
    }
    if ($('lay-haz').checked && v.s > 1.2) {
      for (const [hx, hy, hh] of S.hazards) {
        const [sx, sy] = toScreen(hx, hy);
        mctx.fillStyle = hh >= 0.5 ? '#ff5a4f' : '#ffb84d';
        mctx.beginPath(); mctx.arc(sx, sy, Math.max(1.6, Math.min(7, hh * v.s * 1.2)), 0, Math.PI * 2); mctx.fill();
      }
    }
    if (S.route) {
      const C = S.cols, g = S.g;
      mctx.strokeStyle = 'rgba(0,0,0,.55)'; mctx.lineWidth = 6; mctx.beginPath();
      S.route.cells.forEach((i, k) => { const [sx, sy] = toScreen((i % C) * g, ((i / C) | 0) * g); k ? mctx.lineTo(sx, sy) : mctx.moveTo(sx, sy); }); mctx.stroke();
      mctx.strokeStyle = css('--route'); mctx.lineWidth = 3; mctx.stroke();
    }
    S.picks.forEach((p, k) => {
      const [sx, sy] = toScreen(p.x, p.y);
      mctx.fillStyle = css('--route'); mctx.strokeStyle = '#0b0f14'; mctx.lineWidth = 2;
      mctx.beginPath(); mctx.arc(sx, sy, 9, 0, Math.PI * 2); mctx.fill(); mctx.stroke();
      mctx.fillStyle = '#0b0f14'; mctx.font = '700 11px "Martian Mono", monospace'; mctx.textAlign = 'center'; mctx.fillText(k ? 'B' : 'A', sx, sy + 4); mctx.textAlign = 'start';
    });
    const nice = [10, 20, 50, 100, 200, 500, 1000].find((m) => m * v.s > 70) || 1000;
    $('scalebar').style.width = nice * v.s + 'px'; $('scaletext').textContent = nice + ' m';
  }

  function bindMap() {
    const pts = new Map(); let moved = 0, last = null, pinch = null;
    mapC.addEventListener('pointerdown', (e) => { mapC.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.offsetX, e.offsetY]); moved = 0; last = [e.offsetX, e.offsetY]; if (pts.size === 2) { const [a, b] = [...pts.values()]; pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), s: S.view.s }; } });
    mapC.addEventListener('pointermove', (e) => {
      if (!pts.has(e.pointerId)) return; pts.set(e.pointerId, [e.offsetX, e.offsetY]);
      if (pts.size === 2 && pinch) { const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]); zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (pinch.s * d / pinch.d) / S.view.s); moved = 99; return; }
      const dx = e.offsetX - last[0], dy = e.offsetY - last[1]; moved += Math.abs(dx) + Math.abs(dy); last = [e.offsetX, e.offsetY];
      S.view.x += dx; S.view.y += dy; drawMap();
    });
    const up = (e) => {
      if (!pts.has(e.pointerId)) return; pts.delete(e.pointerId); if (pts.size < 2) pinch = null;
      if (moved < 6 && e.type === 'pointerup') tapAt(e.offsetX, e.offsetY);
    };
    mapC.addEventListener('pointerup', up); mapC.addEventListener('pointercancel', up);
    mapC.addEventListener('wheel', (e) => { e.preventDefault(); zoomAt(e.offsetX, e.offsetY, Math.exp(-e.deltaY * 0.0015)); }, { passive: false });
  }
  function zoomAt(sx, sy, f) { const s2 = Math.min(12, Math.max(0.12, S.view.s * f)); f = s2 / S.view.s; S.view.x = sx - (sx - S.view.x) * f; S.view.y = sy - (sy - S.view.y) * f; S.view.s = s2; drawMap(); }

  function tapAt(sx, sy) {
    const [x, y] = toWorld(sx, sy), size = S.data.window.size_m;
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    if (S.picks.length >= 2) { S.picks = []; S.route = null; }
    S.picks.push({ x, y });
    if (S.picks.length === 2) runPlan(); else { $('stepline').innerHTML = '<b>Now tap the end</b> of the walk.'; drawMap(); }
  }

  function runPlan() {
    $('stepline').textContent = 'Planning…';
    setTimeout(() => {
      const res = plan(S.picks[0], S.picks[1], +$('maxslope').value);
      if (res.error) { S.route = null; showResult(null, res.error); }
      else { S.route = res; showResult(res); }
      drawMap(); if (S.t3d) S.t3d.setRoute(S.route); if (S.street && S.street.streetView) S.street.streetView.setPlannedRoute(S.route);
    }, 20);
  }

  // ---------------- results + conditions ----------------
  function showResult(r, err) {
    const flags = [];
    $('routebox').hidden = !r; $('noroute').hidden = !!r; $('profile').innerHTML = '';
    if (!r) { $('stepline').innerHTML = err ? '<b>No route.</b> Tap a new start.' : '<b>Tap the start</b> on the map.'; $('flags').innerHTML = err ? `<div class="flag bad">${err}</div>` : ''; return; }
    $('stepline').innerHTML = 'Walk planned. <b>Tap again</b> to start a new one.';
    $('r-dist').textContent = r.dist < 1000 ? `${Math.round(r.dist)} m` : `${(r.dist / 1000).toFixed(2)} km`;
    $('r-time').textContent = fmtMin(r.time);
    $('r-up').textContent = `+${r.up.toFixed(0)} m`; $('r-down').textContent = `−${r.down.toFixed(0)} m`;
    $('r-max').textContent = `${r.maxs.toFixed(0)}°`;
    // ---- Minetti 0.38g bioenergetics HUD ----
    if (r.bioenergetics && $('r-kcal')) {
      const b = r.bioenergetics;
      $('r-kcal').textContent = `${b.totalKcal.toFixed(0)} kcal`;
      $('r-o2').textContent = `${b.totalO2L.toFixed(1)} L`;
      $('r-o2pct').textContent = `${b.o2PctEvaSuit.toFixed(1)}% of EVA suit`;
      const bioFlag = b.o2PctEvaSuit > 50
        ? ['bad',  `⚠ Walkback margin exceeded: ${b.o2PctEvaSuit.toFixed(1)}% of suit O₂ (720 L limit). Shorten the route.`]
        : b.o2PctEvaSuit > 25
        ? ['warn', `O₂ use ${b.o2PctEvaSuit.toFixed(1)}% of suit capacity — plan a rest stop.`]
        : ['ok',   `O₂ well within walkback margin (${b.o2PctEvaSuit.toFixed(1)}% of 720 L suit capacity).`];
      flags.push(bioFlag);
    }
    const C = S.cols, g = S.g, pts = r.cells.map((i) => [(i % C) * g, ((i / C) | 0) * g]);
    const near = S.hazards.filter(([hx, hy, hh]) => hh >= 0.5 && pts.some(([px, py]) => Math.abs(px - hx) < 6 && Math.abs(py - hy) < 6));
    $('r-haz').textContent = r.hazardCells; $('r-hazb').textContent = near.length;
    const w = S.data.weather[String($('sol').value)], start = toMin($('start').value), end = start + r.time / 60;
    $('r-end').textContent = hhmm(end);
    if (w && w.sunset) { const ss = toMin(w.sunset); flags.push(end > ss - 60 ? ['bad', `Back at ${hhmm(end)}, too close to sunset (${w.sunset}). Leave earlier.`] : ['ok', `Back ${fmtMin((ss - end) * 60)} before sunset (${w.sunset}).`]); }
    if (near.length) flags.push(['warn', `Route passes within 6 m of ${near.length} rock${near.length > 1 ? 's' : ''} taller than 50 cm (seen in the close-ups) and goes round them.`]);
    flags.push(r.maxs > 20 ? ['warn', `Steepest stretch is ${r.maxs.toFixed(0)}°. Plan rest stops.`] : ['ok', 'No stretch steeper than 20°.']);
    const dose = S.data.radiation.msv_per_day * (r.time / 3600) / 24.66;
    flags.push(['ok', `Radiation for this walk: about ${(dose * 1000).toFixed(0)} µSv (Curiosity RAD average), well below a chest CT scan.`]);
    $('flags').innerHTML = flags.map(([c, t]) => `<div class="flag ${c}">${t}</div>`).join('');
    profileChart(r.prof);
  }

  function profileChart(prof) {
    const W = 300, Hh = 120, pl = 34, pr = 6, pt = 8, pb = 20;
    const d = prof[prof.length - 1][0] || 1, hs = prof.map((p) => p[1]), lo = Math.min(...hs), hi = Math.max(...hs), span = Math.max(hi - lo, 2);
    const X = (v) => pl + (v / d) * (W - pl - pr), Y = (v) => pt + (1 - (v - lo) / span) * (Hh - pt - pb);
    const step = Math.max(1, Math.floor(prof.length / 200)), pts = prof.filter((_, i) => i % step === 0 || i === prof.length - 1);
    const line = pts.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ');
    const ticksY = [lo, lo + span / 2, lo + span].map((v) => `<text x="${pl - 4}" y="${Y(v) + 3}" text-anchor="end">${(v + S.data.window.height_min).toFixed(0)}</text><line x1="${pl}" x2="${W - pr}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)" />`).join('');
    $('profile').innerHTML = `<div class="label" style="margin-bottom:4px">Height along the walk (m)</div>
      <svg viewBox="0 0 ${W} ${Hh}" role="img" aria-label="Height profile of the walk">
        <g font-family="Martian Mono, monospace" font-size="8" fill="var(--muted)">${ticksY}
        <text x="${pl}" y="${Hh - 6}">0</text><text x="${W - pr}" y="${Hh - 6}" text-anchor="end">${d < 1000 ? Math.round(d) + ' m' : (d / 1000).toFixed(2) + ' km'}</text></g>
        <polygon points="${pl},${Y(lo)} ${line} ${X(d)},${Y(lo)}" fill="var(--route)" fill-opacity="0.18" />
        <polyline points="${line}" fill="none" stroke="var(--route)" stroke-width="2" />
        <circle cx="${X(d)}" cy="${Y(prof[prof.length - 1][1])}" r="3" fill="var(--route)" /></svg>`;
  }

  function conditions() {
    const sol = String($('sol').value), w = S.data.weather[sol];
    $('cond-title').textContent = `Conditions on sol ${sol}`;
    if (!w) { $('conditions').innerHTML = ''; $('cond-note').textContent = `Curiosity has no weather record for sol ${sol}. Try a nearby sol.`; return; }
    const f = (label, val) => `<div class="fact"><small>${label}</small><b>${val}</b></div>`;
    $('conditions').innerHTML = f('Air, low to high', `${w.min_temp} to ${w.max_temp} °C`) + f('Ground, low to high', `${w.min_gts_temp} to ${w.max_gts_temp} °C`) +
      f('Air pressure', `${w.pressure} Pa`) + f('Sky / UV', `${w.atmo_opacity || '–'} / ${w.local_uv_irradiance_index || '–'}`) +
      f('Sunrise', w.sunrise || '–') + f('Sunset', w.sunset || '–');
    $('cond-note').textContent = `Measured by Curiosity's REMS weather station on ${w.terrestrial_date} (Earth date), Mars season: ${w.season}.`;
    if (S.route) showResult(S.route);
  }

  // ---------------- 3D terrain ----------------
  // One WebGL renderer for every 3D view: it moves into whichever view is open. Browsers (phones especially) cap the
  // number of live 3D contexts, and each one holds GPU memory, so four separate renderers fail on small devices.
  const GL = { r: null, views: [], active: null };
  function glRenderer() {
    if (!GL.r) {
      GL.r = new THREE.WebGLRenderer({ antialias: true });
      GL.r.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
      GL.r.domElement.style.cssText = 'display:block;width:100%;height:100%;touch-action:none';
      // Let wheel scrolling reach the document without OrbitControls consuming it as zoom.
      // Touch pinch and explicit camera controls remain available.
      GL.r.domElement.addEventListener('wheel', e => e.stopImmediatePropagation(), {capture:true, passive:true});
      const fit = () => { const v = GL.active; if (!v) return; const b = v.slot.parentElement.getBoundingClientRect(); if (!b.width) return;
        GL.r.setSize(b.width, b.height, false); v.cam.aspect = b.width / b.height; v.cam.updateProjectionMatrix(); };
      GL.fit = () => { fit(); GL.dirty = 2; }; new ResizeObserver(GL.fit).observe(document.querySelector('.stage'));
      // Draw only when the camera moves or something changed (plus 4 times a second for late-loading pieces):
      // hundreds of thousands of triangles at 60 fps for a still picture wastes laptop GPUs and phone batteries.
      GL.dirty = 2; GL.last = 0;
      (function loop(now) {
        const v = GL.active;
        if (v && !document.hidden && v.slot.parentElement && !v.slot.parentElement.hidden && !GL.lost) {
          try {
            const moved = v.ctl.update();
            if (moved || GL.dirty > 0 || now - GL.last > 250) {
              GL.r.render(v.scene, v.cam); if (v.onFrame) v.onFrame();
              GL.last = now; GL.dirty = Math.max(0, GL.dirty - 1);
            }
          } catch (error) {
            GL.active = null;
            if (S.street && v === S.street.o) showStreetLoadError(error, S.street.streetView.currentSol);
            else { console.error(error); document.dispatchEvent(new Event('marswalk:error')); }
          }
        }
        requestAnimationFrame(loop); })(0);
      // If the browser takes the 3D context back (low graphics memory), say so instead of showing a blank panel.
      GL.r.domElement.addEventListener('webglcontextlost', (e) => { e.preventDefault(); GL.lost = true; GL.r.domElement.style.opacity = '0.15';
        const v = GL.active; const st = v && v.slot.parentElement.querySelector('.status');
        if (st) { st.hidden = false; st.textContent = 'The 3D view was paused because graphics memory ran low. Close other 3D tabs; it will come back on its own.'; } });
      GL.r.domElement.addEventListener('webglcontextrestored', () => { GL.lost = false; GL.r.domElement.style.opacity = ''; GL.dirty = 3;
        const v = GL.active; const st = v && v.slot.parentElement.querySelector('.status'); if (st) st.hidden = true; });
    }
    return GL.r;
  }
  function activateView(v) {
    if (!v) return;
    const r = glRenderer();
    GL.views.forEach((x) => { x.ctl.enabled = x === v; x.slot.hidden = false; });
    v.slot.hidden = true; v.slot.parentElement.insertBefore(r.domElement, v.slot);
    GL.active = v; GL.fit(); GL.dirty = 3;
  }
  function makeOrbit(slot) {
    const r = glRenderer();
    const scene = new THREE.Scene(); scene.background = new THREE.Color(css('--view') || '#1c232c');
    const cam = new THREE.PerspectiveCamera(50, 1, 0.5, 20000), ctl = new THREE.OrbitControls(cam, r.domElement);
    ctl.enableDamping = true; ctl.maxPolarAngle = Math.PI * 0.49;
    scene.add(new THREE.HemisphereLight(0xfff1e0, 0x3a3026, 0.9));
    const sun = new THREE.DirectionalLight(0xffffff, 0.7); sun.position.set(-1, 1.2, -0.6); scene.add(sun);
    const api = { scene, cam, ctl, slot, onFrame: null };
    GL.views.push(api);
    if (!slot.parentElement.hidden) activateView(api); else ctl.enabled = false;
    return api;
  }

  function initTerrain3d() {
    if (S.t3d) return;
    const step = Math.max(2, Math.ceil(Math.max(S.cols, S.rows) / 1000)), C = S.cols, R = S.rows, g = S.g, nx = Math.floor(C / step), ny = Math.floor(R / step);
    const o = makeOrbit($('terrain3d'));
    S.t3d_o = o;
    const geom = new THREE.PlaneGeometry((nx - 1) * step * g, (ny - 1) * step * g, nx - 1, ny - 1);
    geom.rotateX(-Math.PI / 2);
    const pos = geom.attributes.position, mid = S.H[(R >> 1) * C + (C >> 1)];
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const h = S.H[(j * step) * C + i * step]; pos.setY(j * nx + i, isFinite(h) ? h - mid : 0);
    }
    geom.computeVertexNormals();
    const tex = new THREE.Texture(S.img); tex.needsUpdate = true; tex.anisotropy = 4;
    const terrainMat = new THREE.MeshLambertMaterial({ map: tex });
    const mesh = new THREE.Mesh(geom, terrainMat);
    const holder = new THREE.Group(); holder.add(mesh); o.scene.add(holder);
    // ---- LightGBM AI Traversability Hazard overlay mesh ----
    let costMesh = null;
    if (S.costCanvas) {
      const costOverlayTex = new THREE.Texture(S.costCanvas); costOverlayTex.needsUpdate = true;
      const costSolidTex = new THREE.Texture(S.costSolidCanvas); costSolidTex.needsUpdate = true;
      const costGeom = geom.clone();
      // Lift cost mesh slightly above terrain to prevent z-fighting
      const cpos = costGeom.attributes.position;
      for (let pi = 0; pi < cpos.count; pi++) cpos.setY(pi, cpos.getY(pi) + 0.15);
      costGeom.computeVertexNormals();
      costMesh = new THREE.Mesh(costGeom, new THREE.MeshBasicMaterial({ map: costSolidTex, transparent: true, opacity: 1.0, depthWrite: false }));
      costMesh.renderOrder = 1;
      holder.add(costMesh);
      costMesh.visible = false; // off by default; toggled by the Terrain Layer control
      S.t3dCostTextures = { overlay: costOverlayTex, solid: costSolidTex };
    }
    // Terrain layer toggle: satellite | AI hazard | both
    S.t3dLayers = { mesh, costMesh, tex, terrainMat };
    const half = (nx - 1) * step * g / 2;
    const at = (x, y, lift = 0) => { const i = Math.min(C - 1, Math.max(0, Math.round(x / g))), j = Math.min(R - 1, Math.max(0, Math.round(y / g))); return new THREE.Vector3(x - half, (S.H[j * C + i] - mid) + lift, y - half); };
    const pinMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(css('--accent')) });
    S.data.closeups.forEach((c) => { const pin = new THREE.Mesh(new THREE.CylinderGeometry(1.2, 1.2, 14, 12), pinMat); pin.position.copy(at(c.x, c.y, 7)); holder.add(pin); });
    const pathMat = new THREE.LineBasicMaterial({ color: new THREE.Color(css('--accent')) });
    S.data.path.forEach((seg) => holder.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(seg.filter((_, k) => k % 3 === 0).map(([x, y]) => at(x, y, 1.5))), pathMat)));
    let routeLine = null;
    const setRoute = (r) => {
      if (routeLine) { holder.remove(routeLine); routeLine = null; }
      if (!r) return;
      const ptsR = r.cells.filter((_, k) => k % 2 === 0).map((i) => at((i % C) * g, ((i / C) | 0) * g, 2.5));
      routeLine = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(ptsR), Math.min(800, ptsR.length * 2), 2.2, 6), new THREE.MeshBasicMaterial({ color: new THREE.Color(css('--route')) }));
      holder.add(routeLine);
    };
    const exag = () => { holder.scale.y = +$('exag').value; $('exagv').textContent = $('exag').value + '×'; };
    $('exag').addEventListener('input', exag); exag();
    // ---- 3D Terrain legend & layer toggle ----
    function updateTerrainLegend(mode) {
      const k = $('terrain-key'); if (!k) return;
      if (mode === 'hazard' || mode === 'both') {
        k.innerHTML = `
          <div class="key-title">${S.data.window.hazard_source ? 'Terrain Slope & Roughness' : 'AI Traversability Hazard'}</div>
          <span><i class="sw" style="background:#2fbe64"></i>&lt; 25% <small>safe / firm bedrock</small></span>
          <span><i class="sw" style="background:#d9961c"></i>25–50% <small>moderate / cobbles</small></span>
          <span><i class="sw" style="background:#d65a23"></i>50–75% <small>high / eolian sand</small></span>
          <span><i class="sw" style="background:#c81e1e"></i>&gt; 75% <small>cliff &gt;22° / barrier</small></span>
          <div class="key-sub">${S.data.window.hazard_source || 'LightGBM ground-to-orbit transfer'}</div>
        `;
      } else {
        k.innerHTML = `
          <div class="key-title">HiRISE Orbital Orthophoto</div>
          <span><i class="sw" style="background:#d29b6f"></i>25 cm/px <small>USGS Gale mosaic</small></span>
          <span><i class="sw" style="background:var(--accent)"></i>Curiosity Path <small>waypoints</small></span>
          <span><i class="sw" style="background:var(--route)"></i>Planned Walk <small>A* route</small></span>
          <div class="key-sub">MRO HiRISE / CTX elevation merge</div>
        `;
      }
    }
    function applyTerrain3dLayer(mode) {
      const L = S.t3dLayers; if (!L) return;
      if (mode === 'photo')  { L.mesh.visible = true;  if (L.costMesh) L.costMesh.visible = false; }
      else if (mode === 'hazard') { L.mesh.visible = false; if (L.costMesh) { L.costMesh.visible = true; L.costMesh.material.map = S.t3dCostTextures.solid; L.costMesh.material.opacity = 1.0; L.costMesh.material.needsUpdate = true; } }
      else { L.mesh.visible = true; if (L.costMesh) { L.costMesh.visible = true; L.costMesh.material.map = S.t3dCostTextures.overlay; L.costMesh.material.opacity = 0.85; L.costMesh.material.needsUpdate = true; } }
      GL.dirty = 2;
      updateTerrainLegend(mode);
    }
    if ($('terrain3d-layer')) {
      $('terrain3d-layer').addEventListener('change', () => applyTerrain3dLayer($('terrain3d-layer').value));
      updateTerrainLegend($('terrain3d-layer').value);
    }
    o.cam.position.set(-half * 0.55, half * 0.75, half * 0.95); o.ctl.target.set(0, 0, 0); o.ctl.maxDistance = half * 4;
    const groundMat = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide });
    const addCloseups = () => {
      $('load-closeups').hidden = true;
      // one at a time, so the terrain stays responsive while ~3.5 MB per stop streams in
      S.data.closeups.reduce((chain, c) => chain.then(() => loadStop(c).then((sc) => {
        const m = new THREE.Mesh(sc.geom, groundMat); m.position.copy(at(c.x, c.y, 0.2)); holder.add(m); GL.dirty = 2;
      }).catch(() => {})), Promise.resolve());
    };
    const total = S.data.closeups.length;
    const light = window.matchMedia('(max-width: 760px)').matches || (navigator.connection && navigator.connection.saveData);
    if (light && total) { $('load-closeups').hidden = false; $('load-closeups').textContent = `Load ${total} ground close-ups (~${Math.round(total * 3.5)} MB)`;
      $('load-closeups').onclick = addCloseups; } else addCloseups();
    S.t3d = { setRoute }; setRoute(S.route); $('status3d').remove();
    document.dispatchEvent(new CustomEvent('marswalk:scene', {detail:'terrain'}));
  }

  // ---------------- Navcam close-ups (one stop, or all stops placed where they really are) ----------------
  const closeCache = {}, closePending = new Map();
  async function checkedAsset(url, type, optional = false) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), optional ? 5000 : 25000);
    try {
      const response = await fetch(url, { signal: controller.signal, ...(type === 'json' ? { cache: 'no-cache' } : {}) });
      if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
      return await response[type]();
    } catch (error) {
      if (optional) return null;
      if (error.name === 'AbortError') throw new Error(`Timed out loading ${url}. Retry this viewpoint.`);
      throw error;
    } finally { clearTimeout(timer); }
  }
  function loadStop(c) {
    if (closeCache[c.folder]) return Promise.resolve(closeCache[c.folder]);
    if (closePending.has(c.folder)) return closePending.get(c.folder);
    const pending = decodeStop(c).finally(() => closePending.delete(c.folder));
    closePending.set(c.folder, pending);
    return pending;
  }
  async function stopMetadata(c) {
    // A deployed build supplies the public facade URL. Static/offline hosting
    // continues to read the original local NASA-derived metadata directly.
    if (window.MARS_NAVCAM_API && !c.variant) {
      try {
        return await checkedAsset(new URL(`stops/${c.sol}`, window.MARS_NAVCAM_API).href, 'json');
      } catch (error) {
        console.warn('Navcam API unavailable; using the shipped scene metadata.');
      }
    }
    return checkedAsset(c.folder + '/scene.json', 'json');
  }
  async function decodeStop(c) {
    const [meta, b64] = await Promise.all([
      stopMetadata(c),
      checkedAsset(c.folder + '/scene.b64.txt', 'text')
    ]);
    const buf = b64ToU8(b64).buffer, n = meta.counts.vertices, nf = meta.counts.faces;
    let off = 0; const p16 = new Int16Array(buf, off, n * 3); off += n * 6;
    const gray = new Uint8Array(buf, off, n); off += n;
    const src  = new Uint8Array(buf, off, n); off += n;   // 0=stereo 1=Depth Anything V2
    const slope = new Uint8Array(buf, off, n); off += n;
    const idx = new Uint32Array(buf.slice(off, off + nf * 12));
    const pos = new Float32Array(n * 3); for (let i = 0; i < n * 3; i++) pos[i] = p16[i] / 100;
    const geom = new THREE.BufferGeometry(); geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geom.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 3), 3)); geom.setIndex(new THREE.BufferAttribute(idx, 1)); geom.computeVertexNormals();
    let rgb = null;
    if (meta.colorization?.asset) {
      rgb = b64ToU8(await checkedAsset(c.folder + '/' + meta.colorization.asset, 'text'));
      if (rgb.length !== n * 3) throw new Error('Invalid RGB vertex array');
    }
    const regolith = null;
    const sc = (closeCache[c.folder] = { geom, gray, rgb, src, slope, regolith, n, meta });
    paintStop(sc, 'photo');
    // Semantic labels are optional and must not hold the geometry loading screen open.
    checkedAsset(c.folder + '/regolith.b64.txt', 'text', true).then(encoded => {
      if (!encoded) return;
      sc.regolith = b64ToU8(encoded);
      const mode = window.MARS_INITIAL_VIEW === 'street' ? $('street-mode-select').value : S.close?.closeMode;
      if (mode) paintStop(sc, mode);
      GL.dirty = 3;
    }).catch(error => console.warn('Optional terrain classification unavailable:', error));
    return sc;
  }
  function paintStop(sc, mode) {
    const col = sc.geom.attributes.color.array;
    for (let i = 0; i < sc.n; i++) {
      let rgb;
      if (mode === 'slope') {
        const v = sc.slope[i];
        rgb = (v === 255 ? [125, 135, 145] : SLOPE_COL.find(([lim]) => v < lim)[1]).map((x) => x / 255);
      } else if (mode === 'regolith') {
        const cls = sc.regolith ? sc.regolith[i] : 255;
        const c = REGOLITH_COL[cls] || REGOLITH_COL[255];
        rgb = c.map((x) => x / 255);
      } else if (mode === 'depth_src') {
        // src: 0 = stereo ground-truth (green), 1 = Depth Anything V2 fill (orange)
        rgb = sc.src[i] === 0 ? [0.2, 0.75, 0.35] : [0.95, 0.48, 0.15];
      } else {
        const g = sc.gray[i] / 255;
        rgb = sc.rgb ? [sc.rgb[i * 3] / 255, sc.rgb[i * 3 + 1] / 255, sc.rgb[i * 3 + 2] / 255] : [g, g, g];
      }
      col[i * 3] = rgb[0]; col[i * 3 + 1] = rgb[1]; col[i * 3 + 2] = rgb[2];
    }
    sc.geom.attributes.color.needsUpdate = true;
  }
  async function showClose(key) {
    const C = S.close, cs = closeEntries();
    const requestId = C.requestId = (C.requestId || 0) + 1;
    const list = key === 'all' ? S.data.closeups : cs.filter((c) => closeKey(c) === key);
    if (!list.length) return;
    $('statusclose').hidden = false; $('statusclose').textContent = 'Loading Navcam ground model…';
    try {
      const scenes = await Promise.all(list.map(loadStop));
      if (C.requestId !== requestId) return;
      while (C.group.children.length) C.group.remove(C.group.children[0]);
      const ref = list.find((c) => c.sol === 1100) || list[0];
      list.forEach((c, k) => {
        const sc = scenes[k]; paintStop(sc, C.closeMode);
        const dx = c.x - ref.x, dz = c.y - ref.y, dy = c.elev - ref.elev;   // map metres: x east, y south
        const mesh = new THREE.Mesh(sc.geom, C.mat); mesh.position.set(dx, dy, dz); C.group.add(mesh);
        const rover = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(3.0, 2.2, 2.8)), C.lineMat);
        rover.position.set(dx, dy + 1.1, dz); rover.rotation.y = Math.PI / 2 - sc.meta.rover_heading * Math.PI / 180; C.group.add(rover);
      });
      const far = Math.max(14, ...list.map((c) => Math.hypot(c.x - ref.x, c.y - ref.y)));
      const mx = list.reduce((t, c) => t + c.x - ref.x, 0) / list.length, mz = list.reduce((t, c) => t + c.y - ref.y, 0) / list.length;
      const r = key === 'all' ? far * 0.55 : 0;   // aim at the middle of all stops
      C.o.ctl.target.set(mx, -1, mz); C.o.cam.position.set(mx - 0.45 * r - 9, 0.5 * r + 11, mz + 0.6 * r + 13); C.o.ctl.maxDistance = far * 4 + 60;
      $('close-report').hidden = key === 'all'; if (key !== 'all') $('close-report').href = list[0].folder + '/index.html';
      C.activeColor = list.length === 1 ? scenes[0].meta.colorization : null;
      if (C.activeColor?.focus) {
        const [x, y, z] = C.activeColor.focus;
        C.o.ctl.target.set(x, y, z);
        C.o.cam.position.set(x - 9, y + 11, z + 13);
      }
      $('statusclose').hidden = true;
      updateCloseLegend(C.closeMode);
      document.dispatchEvent(new CustomEvent('marswalk:specimen', {detail:key}));
      document.dispatchEvent(new CustomEvent('marswalk:scene', {detail:'close'}));
    } catch (e) { if (C.requestId !== requestId) return; $('statusclose').textContent = 'The close-up could not load. Reload the page to try again.'; }
  }
  function updateCloseLegend(mode, keyId = 'close-key') {
    const k = $(keyId); if (!k) return;
    if (mode === 'photo' && keyId === 'close-key' && S.close?.activeColor) {
      const color = S.close.activeColor;
      k.innerHTML = `<div class="key-title">Mastcam color · Navcam geometry</div><span><i class="sw" style="background:#bf9270"></i>NASA Mastcam RGB</span><span><i class="sw" style="background:#888"></i>Original grayscale where uncovered</span><div class="key-sub">${(color.vertex_coverage * 100).toFixed(1)}% of vertices colored · ${color.images.length} source images</div>`;
      return;
    }
    if (mode === 'regolith') {
      k.innerHTML = `
        <div class="key-title">AI4Mars Regolith Classification</div>
        <span><i class="sw" style="background:#c2b280"></i>Soil <small>slip μ=1.5 (firm)</small></span>
        <span><i class="sw" style="background:#808080"></i>Bedrock <small>slip μ=1.0 (anchor)</small></span>
        <span><i class="sw" style="background:#edc9af"></i>Sand <small>slip μ=5.0 (hazard)</small></span>
        <span><i class="sw" style="background:#b43232"></i>Big Rock <small>obstacle &ge;20cm</small></span>
        <div class="key-sub">AI semantic segmentation (DeepLabV3+)</div>
      `;
    } else if (mode === 'slope') {
      k.innerHTML = `
        <div class="key-title">Walk Slope Gradient</div>
        <span><i class="sw" style="background:#2f9e62"></i>under 15° <small>easy walk</small></span>
        <span><i class="sw" style="background:#d9961c"></i>15–25° <small>careful (slow)</small></span>
        <span><i class="sw" style="background:#d64541"></i>over 25° <small>no go (blocked)</small></span>
        <span><i class="sw" style="background:#7d8791"></i>edge / gap <small>unmeasured</small></span>
        <div class="key-sub">Computed from 15 cm 3D ground mesh</div>
      `;
    } else if (mode === 'depth_src') {
      k.innerHTML = `
        <div class="key-title">3D Depth Reconstruction</div>
        <span><i class="sw" style="background:#33bf59"></i>Stereo Ground Truth <small>NASA Navcam XYZ</small></span>
        <span><i class="sw" style="background:#f27a26"></i>Depth Anything V2 <small>neural gap fill</small></span>
        <span><i class="sw" style="background:transparent;border:1.5px solid #e3a254"></i>Curiosity Rover <small>3.0×2.2m pose</small></span>
        <div class="key-sub">0.5 mm stereo calibration accuracy</div>
      `;
    } else {
      k.innerHTML = `
        <div class="key-title">Navcam Radiance Imagery</div>
        <span><i class="sw" style="background:#e0e0e0"></i>High Albedo <small>bright dust / rock</small></span>
        <span><i class="sw" style="background:#707070"></i>Medium Albedo <small>soil / outcrop</small></span>
        <span><i class="sw" style="background:#282828"></i>Low Albedo <small>basalt sand / shade</small></span>
        <div class="key-sub">Curiosity Left Navcam (PDS EDRs)</div>
      `;
    }
    if (keyId === 'street-key') {
      k.insertAdjacentHTML('beforeend', '<div class="key-sub">Sky: custom star panorama · user-provided images</div>');
    }
  }

  function setCloseMode(mode) {
    if (!S.close) return;
    S.close.closeMode = mode;
    if ($('close-mode-select')) $('close-mode-select').value = mode;
    if ($('close-mode')) $('close-mode').textContent = CLOSE_MODE_LABELS[mode];
    Object.values(closeCache).forEach((sc) => paintStop(sc, mode));
    GL.dirty = 2;
    updateCloseLegend(mode);
  }

  function initClose(key) {
    if (!S.close) {
      const o = makeOrbit($('close3d')), group = new THREE.Group(); o.scene.add(group);
      S.close = { o, group, closeMode: 'photo', mat: new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }),
                  lineMat: new THREE.LineBasicMaterial({ color: new THREE.Color(css('--accent')) }) };
      if ($('close-mode-select')) {
        $('close-mode-select').value = 'photo';
        $('close-mode-select').addEventListener('change', (e) => setCloseMode(e.target.value));
      }
      if ($('close-mode')) {
        $('close-mode').textContent = CLOSE_MODE_LABELS['photo'];
        $('close-mode').addEventListener('click', () => {
          const modeIdx = (CLOSE_MODES.indexOf(S.close.closeMode) + 1) % CLOSE_MODES.length;
          setCloseMode(CLOSE_MODES[modeIdx]);
        });
      }
      $('close-pick').addEventListener('change', () => showClose($('close-pick').value));
    }
    if (key) $('close-pick').value = key;
    showClose($('close-pick').value);
  }
  function openClose(key) {
    if (window.MARS_INITIAL_VIEW !== 'close') { window.MarsExperience.go('close', '?sol=' + encodeURIComponent(key)); return; }
    initClose(String(key));
  }
  function listCloseups() {
    const cs = closeEntries();
    S.closeSols = new Set(cs.map((c) => c.sol));
    $('close-pick').innerHTML = cs.map((c) => `<option value="${closeKey(c)}">${closeLabel(c)}</option>`).join('') + (cs.length > 1 ? '<option value="all">All stops together</option>' : '');
    const def = cs.find((c) => c.sol === 1100) || cs[0]; if (def) $('close-pick').value = String(def.sol);
    $('closeup-list').innerHTML = cs.map((c) => `<button data-sol="${closeKey(c)}">${closeLabel(c)}<span>${c.photos} photos · ${Math.round(c.area_m2)} m² · ${(c.error * 100).toFixed(1)}% error</span></button>`).join('') +
      (cs.length > 1 ? '<button data-sol="all">All stops together<span>placed by real position</span></button>' : '');
    $('closeup-list').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => openClose(b.dataset.sol)));
  }

  // ---------------- labels that follow 3D points ----------------
  function labeler(o, box, items, sphere) {
    box.innerHTML = '';
    items.forEach((it) => { it.el = document.createElement('span'); it.el.textContent = it.text; if (it.main) it.el.className = 'main'; box.appendChild(it.el); });
    const v = new THREE.Vector3(), toCam = new THREE.Vector3();
    o.onFrame = () => {
      const w = box.clientWidth, h = box.clientHeight;
      for (const it of items) {
        v.copy(it.pos);
        if (it.group) it.group.localToWorld(v);
        let show = true;
        if (sphere) { toCam.copy(o.cam.position).sub(v); show = toCam.dot(v) > 0 && (it.main || o.cam.position.length() < 3.2); }
        v.project(o.cam);
        show = show && v.z < 1 && Math.abs(v.x) < 1.1 && Math.abs(v.y) < 1.1;
        it.el.style.display = show ? '' : 'none';
        if (show) { it.el.style.left = ((v.x + 1) / 2 * w) + 'px'; it.el.style.top = ((1 - v.y) / 2 * h) + 'px'; }
      }
    };
  }
  const loadTex = (url) => new Promise((res, rej) => new THREE.TextureLoader().load(url, res, undefined, rej));
  const context = () => S.ctx || (S.ctx = fetch('world/context.json').then((r) => r.json()));

  // ---------------- zoom level 1: Mars ----------------
  const ll = (lat, lon, r = 1) => { const la = lat * Math.PI / 180, ph = (lon + 180) * Math.PI / 180;
    return new THREE.Vector3(-Math.cos(ph) * Math.cos(la) * r, Math.sin(la) * r, Math.sin(ph) * Math.cos(la) * r); };
  async function initGlobe() {
    if (S.globe) return; S.globe = {};
    try {
      const ctx = await context(), tex = await loadTex(ctx.globe.texture);
      tex.anisotropy = 8;
      const o = makeOrbit($('globe3d'));
      o.cam.near = 0.01; o.cam.updateProjectionMatrix();
      tex.encoding = THREE.sRGBEncoding;
      o.scene.add(new THREE.Mesh(new THREE.SphereGeometry(1, 128, 96), new THREE.MeshStandardMaterial({ map: tex, roughness: 1, metalness: 0 })));
      const atmosphere = new THREE.ShaderMaterial({
        uniforms: { tint: { value: new THREE.Color('#c68c6a') } },
        vertexShader: 'varying vec3 vNormal; varying vec3 vEye; void main(){vec4 p=modelViewMatrix*vec4(position,1.);vNormal=normalize(normalMatrix*normal);vEye=-p.xyz;gl_Position=projectionMatrix*p;}',
        fragmentShader: 'uniform vec3 tint;varying vec3 vNormal;varying vec3 vEye;void main(){float rim=pow(1.-max(0.,dot(normalize(vNormal),normalize(vEye))),4.);gl_FragColor=vec4(tint,rim*.38);}',
        transparent: true, depthWrite: false, blending: THREE.AdditiveBlending
      });
      o.scene.add(new THREE.Mesh(new THREE.SphereGeometry(1.018, 64, 48), atmosphere));
      const stars = new THREE.BufferGeometry(), sp = new Float32Array(2400 * 3);
      for (let i = 0; i < sp.length; i += 3) { const v = new THREE.Vector3().randomDirection ? new THREE.Vector3().randomDirection() : new THREE.Vector3(Math.random() - .5, Math.random() - .5, Math.random() - .5).normalize(); v.multiplyScalar(40 + Math.random() * 20); sp.set([v.x, v.y, v.z], i); }
      stars.setAttribute('position', new THREE.BufferAttribute(sp, 3));
      o.scene.add(new THREE.Points(stars, new THREE.PointsMaterial({ color: 0xcfd6de, size: 0.12, sizeAttenuation: true, transparent: true, opacity: 0.7 })));
      const path = ctx.path.map(([lon, lat]) => ll(lat, lon, 1.0012));
      o.scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(path), new THREE.LineBasicMaterial({ color: new THREE.Color(css('--accent')) })));
      const gale = ctx.sites.find((x) => x.main);
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.012, 16, 12), new THREE.MeshBasicMaterial({ color: new THREE.Color(css('--accent')) }));
      dot.position.copy(ll(gale.lat, gale.lon, 1.002)); o.scene.add(dot);
      labeler(o, $('globe-labels'), ctx.sites.map((x) => ({ text: x.name, main: x.main, pos: ll(x.lat, x.lon, 1.003) })), true);
      const narrow = Math.max(1, 1.2 / Math.max(o.cam.aspect, 0.3));   // pull back on tall phone screens so the whole planet fits
      o.cam.position.copy(ll(gale.lat + 18, gale.lon - 35, 2.9 * narrow)); o.ctl.target.set(0, 0, 0);
      o.ctl.maxPolarAngle = Math.PI; o.ctl.minDistance = 1.08; o.ctl.maxDistance = 6; o.ctl.rotateSpeed = 0.5; o.ctl.autoRotate = !window.matchMedia('(prefers-reduced-motion: reduce)').matches; o.ctl.autoRotateSpeed = 1.5;
      o.ctl.addEventListener('start', () => { o.ctl.autoRotate = false; });
      S.globe = { o, gale };
      document.dispatchEvent(new CustomEvent('marswalk:scene', {detail:'globe'}));
      $('statusglobe').hidden = true;
    } catch (e) { $('statusglobe').textContent = 'Mars could not load. Reload the page to try again.'; }
  }
  function flyToGale() {
    const G = S.globe; if (!G || !G.o) { openTab('gale'); return; }
    const o = G.o, from = o.cam.position.clone(), to = ll(G.gale.lat, G.gale.lon, 1.12), t0 = performance.now();
    o.ctl.autoRotate = false;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    (function step(now) {
      const k = reduce ? 1 : Math.min(1, (now - t0) / 1600), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      const dir = from.clone().normalize().lerp(to.clone().normalize(), e).normalize();
      o.cam.position.copy(dir.multiplyScalar(from.length() + (to.length() - from.length()) * e)); o.cam.lookAt(0, 0, 0);
      if (k < 1) requestAnimationFrame(step); else setTimeout(() => openTab('gale'), 250);
    })(t0);
  }

  // ---------------- zoom level 2: Gale Crater ----------------
  async function initGale() {
    if (S.gale) return; S.gale = {};
    try {
      const ctx = await context(), D = ctx.gale_dem;
      const [b64, tex] = await Promise.all([fetch(D.file).then((r) => r.text()), loadTex(D.shade)]);
      const u16 = new Uint16Array(b64ToU8(b64).buffer), step = 2, nx = Math.floor(D.cols / step), ny = Math.floor(D.rows / step);
      const W = (nx - 1) * step * D.step_m, Hh = (ny - 1) * step * D.step_m, kdeg = D.step_m / D.dlon;
      const hAt = (col, row) => { const c = Math.min(D.cols - 1, Math.max(0, Math.round(col))), r = Math.min(D.rows - 1, Math.max(0, Math.round(row))), v = u16[r * D.cols + c]; return v === 65535 ? 0 : v / 10; };
      const mid = hAt(D.cols / 2, D.rows / 2);
      const geom = new THREE.PlaneGeometry(W, Hh, nx - 1, ny - 1); geom.rotateX(-Math.PI / 2);
      const pos = geom.attributes.position;
      for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) pos.setY(j * nx + i, hAt(i * step, j * step) - mid);
      geom.computeVertexNormals();
      tex.anisotropy = 8;
      const o = makeOrbit($('gale3d'));
      o.cam.far = 400000; o.cam.near = 20; o.cam.updateProjectionMatrix();
      const holder = new THREE.Group(); holder.add(new THREE.Mesh(geom, new THREE.MeshLambertMaterial({ map: tex }))); o.scene.add(holder);
      const place = (lon, lat, lift) => { const col = (lon - D.west) * kdeg / D.step_m, row = (D.north - lat) * kdeg / D.step_m;
        return new THREE.Vector3(col * D.step_m - W / 2, hAt(col, row) - mid + lift, row * D.step_m - Hh / 2); };
      const inside = ([lon, lat]) => lon >= D.west && lat <= D.north && lon <= D.west + D.cols * D.dlon && lat >= D.north - D.rows * D.dlat;
      const pts = ctx.path.filter(inside).filter((_, k) => k % 3 === 0).map(([lon, lat]) => place(lon, lat, 25));
      holder.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), Math.min(4000, pts.length * 3), 22, 5), new THREE.MeshBasicMaterial({ color: new THREE.Color(css('--accent')) })));
      const w = S.data.window, half = w.size_m / 2 / kdeg, box = [[-half, -half], [half, -half], [half, half], [-half, half], [-half, -half]].map(([dx, dy]) => place(w.lon + dx, w.lat + dy, 40));
      holder.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(box), new THREE.LineBasicMaterial({ color: new THREE.Color(css('--route')), linewidth: 2 })));
      const first = ctx.path[0], last = ctx.path[ctx.path.length - 1], lastSol = Math.max(...S.data.stops.map((x) => x.sol), 0);
      labeler(o, $('gale-labels'), [
        { text: 'Landing · Aug 2012', pos: place(first[0], first[1], 60), group: holder },
        { text: 'Curiosity today', pos: place(last[0], last[1], 60), group: holder, main: true },
        { text: 'Planning area', pos: place(w.lon, w.lat + half, 80), group: holder },
        { text: 'Mount Sharp (Aeolis Mons)', pos: place(137.48, -4.95, 200), group: holder },
      ], false);
      const ex = () => { holder.scale.y = +$('gexag').value; $('gexagv').textContent = $('gexag').value + '×'; };
      $('gexag').addEventListener('input', ex); ex();
      const c = place(w.lon, w.lat, 0);
      o.ctl.target.copy(c); o.cam.position.set(c.x - 9000, 11000, c.z + 14000); o.ctl.maxDistance = 120000; o.ctl.minDistance = 600;
      S.gale = { o }; $('statusgale').hidden = true;
      document.dispatchEvent(new CustomEvent('marswalk:scene', {detail:'gale'}));
    } catch (e) { $('statusgale').textContent = 'Gale Crater could not load. Reload the page to try again.'; }
  }

  // ---------------- zoom level 4: Marswalk Street View (Google Street View for Mars) ----------------
  function showStreetLoadError(error, sol) {
    console.error(error);
    const status = $('statusstreet'); status.hidden = false;
    status.textContent = `Viewpoint ${sol || ''} could not load. ${error.message || 'Check the local data connection.'}`;
    const retry = document.createElement('button'); retry.className = 'action'; retry.textContent = 'Retry viewpoint ↗';
    retry.onclick = () => initStreet(sol);
    status.append(retry);
    $('street-load-note').textContent = 'Select another viewpoint or retry.';
  }
  function initStreet(solKey) {
    if (!S.data) {
      setTimeout(() => initStreet(solKey), 100);
      return;
    }
    if (!S.street) {
      const slot = $('street3d');
      let streetView;
      const loadStreetArea = (sol, entry) => typeof streetView.loadArea === 'function'
        ? streetView.loadArea(sol, entry) : streetView.loadSol(sol);
      const paintStreetArea = () => {
        const scenes = streetView.terrainEntries || (streetView.activeScene ? [{ scene: streetView.activeScene }] : []);
        scenes.forEach((item) => paintStop(item.scene, $('street-mode-select').value));
      };
      streetView = new MarswalkStreetView.MarswalkStreetView({
        renderer: glRenderer(),
        container: slot.parentElement,
        loadStop: loadStop,
        appState: S,
        onSkyReady: () => { GL.dirty = 3; },
        onLoadProgress: progress => {
          if (progress.phase === 'selected') {
            $('statusstreet').hidden = false;
            $('statusstreet').textContent = `Loading reconstructed ground / Sol ${progress.sol}…`;
            $('street-load-note').textContent = 'Downloading selected viewpoint';
          } else {
            $('street-load-note').textContent = progress.unavailable
              ? `${progress.loaded}/${progress.total} stops ready · some neighbors unavailable; select a viewpoint to retry`
              : progress.phase === 'complete' ? `${progress.loaded} connected stops ready` : `Streaming neighboring ground / ${progress.loaded} of ${progress.total} stops ready`;
          }
        },
        onAreaUpdate: event => {
          const entries = streetView.terrainEntries || [];
          entries.forEach(item => paintStop(item.scene, $('street-mode-select').value));
          if (event.phase === 'ready') { $('statusstreet').hidden = true; GL.fit(); }
          GL.dirty = 3;
        },
        onLoadError: showStreetLoadError,
        onStopChange: (sol) => {
          if ($('street-stop-pick')) $('street-stop-pick').value = String(sol);
          if ($('street-banner')) {
            $('street-banner').innerHTML = `<b>SOL ${sol}</b><span>Follow ground chevrons to the next viewpoint</span>`;
          }
        },
        onBoundaryOptions: (choices, point, heading) => {
          const panel = $('street-boundary'), list = $('street-destinations');
          panel.hidden = false;
          if (!choices.length) {
            $('street-boundary-copy').textContent = 'There is no mapped ground ahead in this direction.';
            list.innerHTML = '';
            return;
          }
          $('street-boundary-copy').textContent = 'Choose a reconstructed stop ahead to continue your walk.';
          list.innerHTML = choices.map(({ stop, distance }) => `<button class="street-destination" data-sol="${stop.sol}"><b>Continue to Sol ${stop.sol}</b><small>${Math.round(distance)} m ahead</small></button>`).join('');
          list.querySelectorAll('button').forEach((button) => button.addEventListener('click', () => {
            const target = S.data.closeups.find((c) => c.sol === Number(button.dataset.sol));
            if (!target) return;
            const angle = Math.atan2(target.x - point.x, target.y - point.z);
            panel.hidden = true;
            $('statusstreet').hidden = false; $('statusstreet').textContent = `Loading Sol ${target.sol}…`;
            loadStreetArea(target.sol, { x: target.x - Math.sin(angle) * 21, z: target.y - Math.cos(angle) * 21, heading }).then(() => {
              paintStreetArea();
              $('statusstreet').hidden = true; GL.dirty = 3;
            }).catch(error => showStreetLoadError(error, target.sol));
          }));
        },
        onInspectPoi: (poi) => {
          const card = $('target-inspector');
          if (!card) return;
          card.hidden = false;
          $('poi-tag').textContent = poi.type === 'hazard' ? '⚠ Obstacle Warning' : '🔬 Science Observation';
          $('poi-name').textContent = poi.name || poi.title || 'Surface Feature';
          $('poi-desc').textContent = poi.description || 'Geological surface observation.';
          $('poi-inst').textContent = poi.instruments || 'Curiosity Navcam stereo';
          const hazEl = $('poi-hazard');
          if (hazEl) {
            hazEl.textContent = poi.slip_hazard || 'Nominal';
            hazEl.style.color = poi.type === 'hazard' ? 'var(--bad)' : 'var(--ok)';
          }
        },
        onTelemetryUpdate: (telem) => {
          const cardDirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
          const dirIdx = Math.round(telem.headingDeg / 45) % 8;
          if ($('street-heading')) $('street-heading').textContent = `${telem.headingDeg}° ${cardDirs[dirIdx]}`;
          if ($('street-pitch')) $('street-pitch').textContent = `${telem.pitchDeg > 0 ? '+' : ''}${telem.pitchDeg}°`;
          
          if ($('st-speed')) $('st-speed').textContent = `${telem.speed.toFixed(1)} m/s`;
          const isWalking = telem.speed > 0;
          const powerWatts = isWalking ? Math.round(280 + telem.speed * 85) : 120;
          const o2Rate = isWalking ? (0.65 + telem.speed * 0.35).toFixed(2) : '0.42';
          if ($('st-power')) $('st-power').textContent = `${powerWatts} W`;
          if ($('st-o2')) $('st-o2').textContent = `${o2Rate} L/min`;
          if ($('st-elev')) $('st-elev').textContent = `${Math.round(telem.elev)} m`;
        }
      });

      streetView.setCuriosityData(S.data, closeCache);
      if (S.route) streetView.setPlannedRoute(S.route);

      const o = {
        scene: streetView.scene,
        cam: streetView.camera,
        ctl: {
          enabled: true,
          update: () => {
            if (streetView.active) streetView.update(0.016);
            return streetView.active;
          }
        },
        slot: slot,
        onFrame: () => {
          streetView.drawMiniMap($('street-minimap'), S.img);
        }
      };

      GL.views.push(o);
      S.street = { streetView, o };

      // Toolbar event listeners
      $('street-stop-pick').innerHTML = S.data.closeups.map((c) => `<option value="${c.sol}">sol ${c.sol}</option>`).join('');
      $('street-stop-pick').addEventListener('change', () => {
        const sol = Number($('street-stop-pick').value);
        $('statusstreet').hidden = false;
        $('statusstreet').textContent = 'Loading Sol ' + sol + '…';
        loadStreetArea(sol).then(() => {
          $('statusstreet').hidden = true;
          paintStreetArea();
          GL.dirty = 3;
        }).catch((err) => {
          showStreetLoadError(err, sol);
        });
      });

      $('street-mode-select').addEventListener('change', (e) => {
        const mode = e.target.value;
        paintStreetArea();
        GL.dirty = 2;
        updateCloseLegend(mode, 'street-key');
      });

      const speedControl = $('street-speed');
      const updateStreetSpeed = () => {
        const multiplier = streetView.setSpeedMultiplier(speedControl.value);
        $('street-speedv').textContent = multiplier.toFixed(2).replace(/0+$/, '').replace(/\.$/, '') + '×';
      };
      speedControl.addEventListener('input', updateStreetSpeed);
      updateStreetSpeed();

      document.addEventListener('marswalk:focuschange', event => {
        if (event.detail.target !== $('view-street')) return;
        streetView.arPinsGroup.visible = !event.detail.active && !immersive;
        GL.dirty = 2;
      });

      let immersive = false;
      const setImmersive = (enabled) => {
        immersive = enabled;
        $('view-street').classList.toggle('immersive', immersive);
        streetView.arPinsGroup.visible = !immersive;
        $('street-immersive-toggle').setAttribute('aria-pressed', String(immersive));
        GL.dirty = 2;
      };
      $('street-immersive-toggle').addEventListener('click', () => setImmersive(!immersive));
      $('street-interface-restore').addEventListener('click', () => setImmersive(false));

      const syncFullscreen = () => {
        const isFull = document.fullscreenElement === $('view-street') || document.webkitFullscreenElement === $('view-street');
        $('street-fullscreen-toggle').textContent = isFull ? 'Exit full screen' : 'Full screen';
        $('street-fullscreen-toggle').setAttribute('aria-pressed', String(isFull));
        setTimeout(GL.fit, 0);
      };
      $('street-fullscreen-toggle').addEventListener('click', () => {
        const view = $('view-street');
        if (document.fullscreenElement === view || document.webkitFullscreenElement === view) {
          (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        } else {
          const request = view.requestFullscreen || view.webkitRequestFullscreen;
          if (request) {
            const result = request.call(view);
            if (result && result.catch) result.catch(() => {});
          }
        }
      });
      document.addEventListener('fullscreenchange', syncFullscreen);
      document.addEventListener('webkitfullscreenchange', syncFullscreen);

      $('street-boundary-dismiss').addEventListener('click', () => { $('street-boundary').hidden = true; });

      const miniC = $('street-minimap');
      if (miniC) {
        miniC.addEventListener('click', (e) => {
          const rect = miniC.getBoundingClientRect();
          const mx = e.clientX - rect.left;
          const my = e.clientY - rect.top;
          const mapSize = 350;
          const zoom = miniC.width / mapSize;
          const worldDx = (mx - miniC.width / 2) / zoom;
          const worldDz = (my - miniC.height / 2) / zoom;
          streetView.astronautPos.x += worldDx;
          streetView.astronautPos.z += worldDz;
          GL.dirty = 2;
        });
      }

      let miniExpanded = true;
      $('toggle-minimap-btn').addEventListener('click', () => {
        miniExpanded = !miniExpanded;
        $('mini-map-body').style.display = miniExpanded ? '' : 'none';
        $('toggle-minimap-btn').textContent = miniExpanded ? '_' : '□';
      });

      $('close-inspector-btn').addEventListener('click', () => {
        $('target-inspector').hidden = true;
      });
    }

    S.street.streetView.active = true;
    activateView(S.street.o);

    const initialSol = solKey ? Number(solKey) : (S.data.closeups.find(c => c.sol === 1100)?.sol || S.data.closeups[0].sol);
    $('statusstreet').hidden = false;
    $('statusstreet').textContent = 'Loading Mars Street View…';
    const loadStreetArea = (sol, entry) => typeof S.street.streetView.loadArea === 'function'
      ? S.street.streetView.loadArea(sol, entry) : S.street.streetView.loadSol(sol);
    loadStreetArea(initialSol).then(() => {
      $('statusstreet').hidden = true;
      const scenes = S.street.streetView.terrainEntries || (S.street.streetView.activeScene ? [{ scene: S.street.streetView.activeScene }] : []);
      scenes.forEach((item) => paintStop(item.scene, $('street-mode-select').value));
      updateCloseLegend($('street-mode-select').value, 'street-key');
      GL.dirty = 3;
    }).catch((err) => {
      showStreetLoadError(err, initialSol);
    });
  }

  // ---------------- measured results ----------------
  function showResults() {
    const r = S.data.results; if (!r) { $('results-section').hidden = true; return; }
    const pct = (v) => (v * 100).toFixed(1) + '%';
    const tile = (label, val, note) => `<div class="stat"><span class="label">${label}</span><b>${val}</b><small>${note}</small></div>`;
    $('res-stats').innerHTML = tile('Stops in 3D', r.stops, 'Curiosity parking spots') + tile('Rover photos', r.photos, 'Navcam stereo frames used') +
      tile('Ground in 3D', Math.round(r.area_m2).toLocaleString() + ' m²', 'at 15 cm detail') + tile('Rocks and ledges', r.rocks, `${r.tall_rocks} taller than 50 cm`) +
      tile('Depth error', pct(r.median_error), 'median vs NASA stereo, unseen pixels');
    const f = r.finetune;
    $('res-ft').innerHTML = f ? `<b>Fine-tuning Depth Anything on Navcam:</b> trained on ${f.train_photos} photos from sols ${f.train_sols.join(', ')}, tested on ${f.test_photos} photos from sols ${f.test_sols.join(', ')} it never saw. Error ${pct(f.pretrained)} before training → <b>${pct(f.finetuned)}</b> after (fit chosen per photo); with one plain fit ${pct(f.pretrained_plain)} → ${pct(f.finetuned_plain)}. Better on ${f.better_on} of ${f.test_photos} test photos.`
      : 'Fine-tuning results appear here after <span class="mono">navcam3d/finetune_depth.py</span> runs.';
  }

  // The presentation owns routing; the scientific engine owns scene activation.
  function openTab(view) {
    if (view !== window.MARS_INITIAL_VIEW) { window.MarsExperience.go(view); return; }
    try { activateInstrument(view); } catch (error) { console.error(error); document.dispatchEvent(new Event('marswalk:error')); }
  }
  $('to-gale').addEventListener('click', flyToGale);
  $('to-area').addEventListener('click', () => openTab('map'));
  function activateInstrument(view) {
    ['globe', 'gale', 'map', 'terrain', 'close', 'street'].forEach(v => { $('view-' + v).hidden = v !== view; });
    if (view === 'globe') initGlobe();
    if (view === 'gale') initGale();
    if (view === 'terrain' && S.H) setTimeout(() => { try { initTerrain3d(); } catch (error) { console.error(error); document.dispatchEvent(new Event('marswalk:error')); } }, 10);
    if (view === 'close') {
      const requested = new URLSearchParams(location.search).get('sol');
      const valid = requested === 'all' || closeEntries().some(c => closeKey(c) === requested);
      initClose(valid ? requested : undefined);
    }
    if (view === 'street') initStreet();
    if (view === 'map') fitMap();
  }
  $('maxslope').addEventListener('input', () => { $('maxslopev').textContent = $('maxslope').value + '°'; });
  $('maxslope').addEventListener('change', () => { if (S.picks.length === 2) runPlan(); });
  $('sol').addEventListener('change', conditions);
  $('start').addEventListener('change', () => { if (S.route) showResult(S.route); });
  $('lay-slope').addEventListener('change', drawMap); $('lay-path').addEventListener('change', drawMap); $('lay-haz').addEventListener('change', drawMap);
  if ($('lay-cost')) $('lay-cost').addEventListener('change', drawMap);
  $('clear').addEventListener('click', () => { S.picks = []; S.route = null; showResult(null); drawMap(); if (S.t3d) S.t3d.setRoute(null); if (S.street && S.street.streetView) S.street.streetView.setPlannedRoute(null); });
  $('example').addEventListener('click', example);
  function example() {
    const s = S.data.stops, home = S.data.closeups.find((c) => c.sol === 1100) || S.data.closeups[0] || s[0], a = s.find((p) => p.sol === home.sol) || home;
    const far = s.filter((p) => Math.hypot(p.x - a.x, p.y - a.y) > 350).sort((p, q) => Math.abs(Math.hypot(p.x - a.x, p.y - a.y) - 600) - Math.abs(Math.hypot(q.x - a.x, q.y - a.y) - 600))[0] || s[0];
    S.picks = [{ x: a.x + 6, y: a.y + 6 }, { x: far.x, y: far.y }]; runPlan();
  }

  // ---------------- load ----------------
  async function start() {
    if (!window.THREE || !THREE.OrbitControls) { $('status').textContent = 'The 3D library did not load. Check your connection and reload the page.'; return; }
    const terrainSize = new URL(location.href).searchParams.get('terrain') === '5' ? '5' : '2';
    if ($('terrain-size')) {
      $('terrain-size').value = terrainSize;
      $('terrain-size').addEventListener('change', event => {
        const url = new URL(location.href);
        if (event.target.value === '5') url.searchParams.set('terrain', '5');
        else url.searchParams.delete('terrain');
        location.assign(url.href);
      });
    }
    const extentLabel = $('terrain-extent-label');
    if (extentLabel) extentLabel.textContent = `${terrainSize} × ${terrainSize} km of orbital terrain, brought into the light.`;
    try {
      const terrainBase = window.MARS_INITIAL_VIEW === 'terrain' && terrainSize === '5' ? 'terrain-5km/' : '';
      const costB64Resp = checkedAsset(terrainBase + 'cost.b64.txt', 'text', true);
      const [data, b64, costB64, extendedWindow] = await Promise.all([
        checkedAsset('data.json', 'json'),
        checkedAsset(terrainBase + 'terrain.b64.txt', 'text'),
        costB64Resp,
        terrainBase ? checkedAsset(terrainBase + 'window.json', 'json') : null,
      ]);
      if (extendedWindow) {
        const { offset_x: dx, offset_y: dy } = extendedWindow;
        const shiftStop = c => ({ ...c, x: c.x + dx, y: c.y + dy,
          ...(c.hazards ? { hazards: c.hazards.map(([x, y, ...rest]) => [x + dx, y + dy, ...rest]) } : {}) });
        data.closeups = data.closeups.map(shiftStop);
        data.closeup_variants = (data.closeup_variants || []).map(shiftStop);
        data.stops = data.stops.map(shiftStop);
        data.path = data.path.map(segment => segment.map(([x, y]) => [x + dx, y + dy]));
        data.window = { ...data.window, ...extendedWindow };
      }
      S.data = data; S.closeSols = new Set(data.closeups.map((c) => c.sol)); S.cols = data.window.cols; S.rows = data.window.rows; S.g = data.window.grid_m;
      prepareTerrain(b64ToU8(b64));
      if (costB64) prepareCostOverlay(b64ToU8(costB64));
      S.img = await new Promise((res, rej) => { const im = new Image(); const timer = setTimeout(() => rej(new Error('Orbital image request timed out.')), 25000); im.onload = () => { clearTimeout(timer); res(im); }; im.onerror = error => { clearTimeout(timer); rej(error); }; im.src = terrainBase + 'map-color.jpg?v=terrain-5km-20261115'; });
    } catch (e) { document.querySelectorAll('.status').forEach(el => { el.hidden = false; el.textContent = 'Orbital data unavailable. Reload to retry; serve this project over HTTP.'; }); document.dispatchEvent(new Event('marswalk:error')); return; }
    $('status').remove();
    S.hazards = S.data.closeups.flatMap((c) => c.hazards || []);
    S.hazardCost = new Float32Array(S.cols * S.rows).fill(1);
    for (const [hx, hy, hh] of S.hazards) {
      const i = Math.round(hy / S.g) * S.cols + Math.round(hx / S.g);
      if (i >= 0 && i < S.hazardCost.length) S.hazardCost[i] = hh >= 0.5 ? Infinity : Math.max(S.hazardCost[i], 3);
    }
    showResults();
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem('marswalk:eva')); } catch (_) { /* storage may be disabled */ }
    if (saved) {
      for (const id of ['sol', 'start', 'maxslope']) {
        if (typeof saved[id] === 'string' && saved[id].length < 20) $(id).value = saved[id];
      }
      if (!$('maxslope').value || !Number.isFinite(+$('maxslope').value)) $('maxslope').value = '25';
      $('maxslopev').textContent = $('maxslope').value + '°';
    }
    listCloseups(); bindMap(); new ResizeObserver(fitMap).observe($('view-map')); fitMap(); conditions();
    if (saved && Array.isArray(saved.picks) && saved.picks.length <= 2 && saved.picks.every(p => Number.isFinite(p.x) && Number.isFinite(p.y) && p.x >= 0 && p.y >= 0 && p.x < S.data.window.size_m && p.y < S.data.window.size_m)) {
      S.picks = saved.picks;
      if (S.picks.length === 2) runPlan(); else { drawMap(); if (S.picks.length) $('stepline').textContent = 'Now tap the end of the walk.'; }
    } else example();
    addEventListener('pagehide', () => {
      try { sessionStorage.setItem('marswalk:eva', JSON.stringify({picks:S.picks, sol:$('sol').value, start:$('start').value, maxslope:$('maxslope').value})); } catch (_) { /* optional persistence */ }
    });
    window.marswalk = { plan, S, example, tapAt, toScreen, GL, fitMap };
    document.dispatchEvent(new Event('marswalk:ready'));
    openTab(window.MARS_INITIAL_VIEW || 'globe');
  }
  start();
})();
