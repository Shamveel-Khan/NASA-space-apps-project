/* MarswalkStreetView – Google Street View for Mars
   First-person astronaut navigation, 360° Gale Crater skybox, ground chevrons,
   AR science & hazard pins, and synchronized 2D mini-map.
   Compatible with Three.js r128.
*/
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MarswalkStreetView = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Physical EVA constants
  const ASTRONAUT_EYE_HEIGHT_M = 1.75;
  const WALK_SPEED_MS = 0.95; // ~3.4 km/h EVA walking speed
  const RUN_SPEED_MS = 2.10;  // EVA brisk traverse
  const DEFAULT_SPEED_MULTIPLIER = 1.75;
  const TURN_SPEED = 0.0028;
  const COVERAGE_RADIUS_M = 24;
  const NASA_SKY = Object.freeze({
    texture: 'world/custom_stars_sky.jpg?v=six-images-20261115',
    sourceUrl: null,
    product: 'Custom star panorama',
    credit: 'User-provided images'
  });

  // Kept independent of Three.js so the navigation rules are easy to test.
  function overlapGroups(stops, radius = COVERAGE_RADIUS_M) {
    const parent = stops.map((_, i) => i);
    const find = (i) => parent[i] === i ? i : (parent[i] = find(parent[i]));
    const join = (a, b) => { a = find(a); b = find(b); if (a !== b) parent[b] = a; };
    for (let i = 0; i < stops.length; i++) for (let j = i + 1; j < stops.length; j++) {
      if (Math.hypot(stops[i].x - stops[j].x, stops[i].y - stops[j].y) <= radius * 2) join(i, j);
    }
    const groups = new Map();
    stops.forEach((stop, i) => { const root = find(i); if (!groups.has(root)) groups.set(root, []); groups.get(root).push(stop); });
    return [...groups.values()];
  }

  function forwardDestinations(stops, activeGroup, point, heading, limit = 3) {
    const active = new Set(activeGroup.map((s) => s.sol));
    const fx = Math.sin(heading), fz = Math.cos(heading);
    return stops.filter((s) => !active.has(s.sol)).map((s) => {
      const dx = s.x - point.x, dz = s.y - point.z, distance = Math.hypot(dx, dz);
      return { stop: s, distance, forwardness: distance ? (dx * fx + dz * fz) / distance : 1 };
    }).filter((x) => x.forwardness >= 0.15).sort((a, b) => a.distance - b.distance).slice(0, limit);
  }

  // Science POIs in Gale Crater within/near Curiosity traverse
  const SCIENCE_POIS = [
    {
      id: 'yellowknife_bay',
      name: 'Yellowknife Bay Lacustrine Basin',
      type: 'science',
      sol: 125,
      x: 1045.0, y: 712.0, z_lift: 1.8,
      category: 'Ancient Lakebed Mudstone',
      description: 'Habitable ancient lacustrine lakebed. Drill samples revealed smectite clay minerals, neutral pH water history, sulfur, nitrogen, oxygen, and carbon.',
      instruments: 'CheMin X-ray Diffraction, SAM Gas Chromatograph, APXS',
      slip_hazard: 'Low (Firm mudstone outcrop)'
    },
    {
      id: 'dingo_gap',
      name: 'Dingo Gap Eolian Dune Crossing',
      type: 'hazard',
      sol: 535,
      x: 1058.0, y: 698.0, z_lift: 1.5,
      category: 'Active Basaltic Sand Ripple Field',
      description: 'Transverse Aeolian Ridge (TAR) dune field with high wheel sinkage. Traversed between two 1-meter scarps with terramechanic slip monitoring.',
      instruments: 'Navcam Stereo, Hazcam slip telemetry',
      slip_hazard: 'High (Slip multiplier mu = 5.0, sinkage hazard)'
    },
    {
      id: 'pahrump_hills',
      name: 'Pahrump Hills Mount Sharp Base',
      type: 'science',
      sol: 753,
      x: 1032.0, y: 725.0, z_lift: 2.2,
      category: 'Murray Formation Basal Mudstone',
      description: 'Basal layered mudstone sequence of Mount Sharp (Aeolis Mons), recording multiple lake cycles and groundwater alteration.',
      instruments: 'MAHLI Microscopic Imager, ChemCam LIBS Laser',
      slip_hazard: 'Moderate (Layered flaggy mudstone)'
    },
    {
      id: 'smectite_clay',
      name: 'Smectite Clay Member',
      type: 'science',
      sol: 1100,
      x: 1052.0, y: 706.0, z_lift: 1.9,
      category: 'Phyllosilicate Clay Sediments',
      description: 'Rich in iron-magnesium smectite clays formed in calm, neutral-pH standing lake water with high biosignature preservation potential.',
      instruments: 'APXS Alpha Particle X-Ray, SAM Oven pyrolysis',
      slip_hazard: 'Low (Duricrust over clay shale)'
    }
  ];

  /**
   * Generates a 3D procedural chevron navigation ring texture for ground steps.
   */
  function createChevronTexture() {
    const S = 256;
    const canvas = document.createElement('canvas');
    canvas.width = S; canvas.height = S;
    const ctx = canvas.getContext('2d');

    ctx.clearRect(0, 0, S, S);

    // Glowing outer ring
    ctx.strokeStyle = 'rgba(79, 195, 247, 0.85)';
    ctx.lineWidth = 10;
    ctx.beginPath();
    ctx.arc(S / 2, S / 2, S / 2 - 16, 0, Math.PI * 2);
    ctx.stroke();

    // Inner filled pulsating circle
    ctx.fillStyle = 'rgba(79, 195, 247, 0.22)';
    ctx.fill();

    // Bold chevron arrow pointing up (forward)
    ctx.fillStyle = '#ffffff';
    ctx.shadowColor = 'rgba(79, 195, 247, 0.9)';
    ctx.shadowBlur = 12;
    ctx.beginPath();
    ctx.moveTo(S / 2, S * 0.22);
    ctx.lineTo(S * 0.76, S * 0.58);
    ctx.lineTo(S * 0.64, S * 0.64);
    ctx.lineTo(S / 2, S * 0.42);
    ctx.lineTo(S * 0.36, S * 0.64);
    ctx.lineTo(S * 0.24, S * 0.58);
    ctx.closePath();
    ctx.fill();

    return canvas;
  }

  /**
   * Generates a 3D AR pin billboard texture.
   */
  function createPinTexture(type, label, subtitle) {
    const W = 320, H = 140;
    const canvas = document.createElement('canvas');
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d');

    const isHazard = type === 'hazard';
    const mainCol = isHazard ? '#ff4d4f' : '#e3a254';
    const bgCol   = isHazard ? 'rgba(40, 10, 10, 0.88)' : 'rgba(20, 24, 32, 0.88)';

    // Rounded card background
    ctx.fillStyle = bgCol;
    ctx.strokeStyle = mainCol;
    ctx.lineWidth = 3;
    const r = 10;
    ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(8, 8, W - 16, H - 36, r) : ctx.rect(8, 8, W - 16, H - 36);
    ctx.fill();
    ctx.stroke();

    // Pointing bottom triangle
    ctx.fillStyle = mainCol;
    ctx.beginPath();
    ctx.moveTo(W / 2 - 12, H - 28);
    ctx.lineTo(W / 2 + 12, H - 28);
    ctx.lineTo(W / 2, H - 8);
    ctx.closePath();
    ctx.fill();

    // Title text
    ctx.font = 'bold 20px "Archivo", "Arial", sans-serif';
    ctx.fillStyle = mainCol;
    ctx.textAlign = 'center';
    ctx.fillText(label, W / 2, 42);

    // Subtitle text
    ctx.font = '14px "Martian Mono", monospace';
    ctx.fillStyle = '#e6ebf0';
    ctx.fillText(subtitle, W / 2, 74);

    return canvas;
  }

  /**
   * Main MarswalkStreetView Controller class.
   */
  class MarswalkStreetView {
    constructor(options) {
      this.renderer = options.renderer;
      this.domContainer = options.container;
      this.loadStopFn = options.loadStop || (typeof window !== 'undefined' ? window.loadStop : null);
      this.appState = options.appState || (typeof window !== 'undefined' ? window.S : null);
      this.onStopChange = options.onStopChange || (() => {});
      this.onInspectPoi = options.onInspectPoi || (() => {});
      this.onTelemetryUpdate = options.onTelemetryUpdate || (() => {});
      this.onBoundaryOptions = options.onBoundaryOptions || (() => {});
      this.onLoadProgress = options.onLoadProgress || (() => {});
      this.onAreaUpdate = options.onAreaUpdate || (() => {});
      this.onLoadError = options.onLoadError || (() => {});
      this.onSkyReady = options.onSkyReady || (() => {});
      this.skyTextureUrl = options.skyTextureUrl || NASA_SKY.texture;
      this.textureLoader = options.textureLoader || null;

      this.scene = new THREE.Scene();
      this.scene.background = new THREE.Color(0xb56d47); // Authentic Martian sky color
      this.camera = new THREE.PerspectiveCamera(72, 1, 0.1, 4000);
      this.raycaster = new THREE.Raycaster();
      this.mouse = new THREE.Vector2();

      // State
      this.active = false;
      this.currentStop = null;
      this.currentSol = 1100;
      this.closeCache = {};
      this.allCloseups = [];
      this.allStops = [];
      this.route = null;
      this.groundMesh = null;
      this.activeScene = null;
      this.areaStops = [];
      this.areaOrigin = null;
      this.loading = false;
      this.loadRequest = 0;
      this.terrainEntries = [];
      this.boundaryOpen = false;
      this.lastValidPos = null;

      // Camera coordinates in local stop space
      this.astronautPos = new THREE.Vector3(0, ASTRONAUT_EYE_HEIGHT_M, 0);
      this.yaw = 0.0;
      this.pitch = 0.0;
      this.keys = {};
      this.isMouseDown = false;
      this.lastMouse = { x: 0, y: 0 };
      this.transitioning = false;
      this.speedMultiplier = DEFAULT_SPEED_MULTIPLIER;

      // Group containers
      this.terrainGroup = new THREE.Group();
      this.chevronsGroup = new THREE.Group();
      this.arPinsGroup = new THREE.Group();
      this.skyGroup = new THREE.Group();
      this.scene.add(this.terrainGroup);
      this.scene.add(this.chevronsGroup);
      this.scene.add(this.arPinsGroup);
      this.scene.add(this.skyGroup);

      // Lights
      this.hemiLight = new THREE.HemisphereLight(0xffeedd, 0x3d281a, 0.95);
      this.sunLight = new THREE.DirectionalLight(0xffffff, 1.1);
      this.sunLight.position.set(-100, 180, -80);
      this.scene.add(this.hemiLight);
      this.scene.add(this.sunLight);

      this.initSkybox();
      this.bindControls();
    }

    initSkybox() {
      const loader = this.textureLoader || new THREE.TextureLoader();
      const base = typeof document !== 'undefined' ? document.baseURI : undefined;
      const textureUrl = base ? new URL(this.skyTextureUrl, base).href : this.skyTextureUrl;
      loader.load(textureUrl, (skyTex) => {
        skyTex.encoding = THREE.sRGBEncoding;
        skyTex.wrapS = THREE.RepeatWrapping;
        const maxAnisotropy = this.renderer?.capabilities?.getMaxAnisotropy?.() || 1;
        skyTex.anisotropy = Math.min(8, maxAnisotropy);
        const skyGeom = new THREE.SphereGeometry(1500, 64, 40);
        const skyMat = new THREE.MeshBasicMaterial({
          map: skyTex,
          depthWrite: false,
          side: THREE.BackSide
        });
        this.skyMesh = new THREE.Mesh(skyGeom, skyMat);
        this.skyMesh.frustumCulled = false;
        this.skyMesh.position.copy(this.astronautPos);
        this.skyGroup.add(this.skyMesh);
        this.onSkyReady({ loaded: true, source: NASA_SKY });
      }, undefined, (error) => {
        console.warn('Custom sky texture could not load; using solid fallback.', error);
        this.onSkyReady({ loaded: false, source: NASA_SKY, error });
      });
    }

    setCuriosityData(data, closeCache) {
      // Ground Closeups stores Mastcam RGB in separate variants of each sol.
      // Prefer those same assets for both selected and streamed terrain.
      this.allCloseups = (data.closeups || []).map(stop => {
        const colored = (data.closeup_variants || []).filter(candidate =>
          candidate.sol === stop.sol && candidate.variant === 'mastcam-color'
        ).sort((a, b) => (b.color_coverage || 0) - (a.color_coverage || 0))[0];
        return colored || stop;
      });
      this.allStops = data.stops || [];
      this.closeCache = closeCache || {};
    }

    setPlannedRoute(route) {
      this.route = route;
      this.rebuildRoutePathVisual();
    }

    getOverlapGroup(solNum) {
      const group = overlapGroups(this.allCloseups).find((items) => items.some((c) => c.sol === solNum));
      return group || [];
    }

    /**
     * Samples the mesh height at a given local (x, z) coordinate.
     */
    getGroundHeightAt(x, z) {
      if (!this.terrainEntries.length) return 0;
      let entry = this.terrainEntries[0], nearest = Infinity;
      for (const candidate of this.terrainEntries) {
        const dx = candidate.mesh.position.x - x, dz = candidate.mesh.position.z - z, d = dx * dx + dz * dz;
        if (d < nearest) { nearest = d; entry = candidate; }
      }
      const pos = entry.scene.geom.attributes.position.array;
      const count = entry.scene.n;

      // Find nearest vertex elevation
      let bestDistSq = Infinity;
      let bestY = 0;

      // Sample every 4th vertex for high-speed collision lookups
      for (let i = 0; i < count; i += 4) {
        const vx = pos[i * 3] + entry.mesh.position.x;
        const vz = pos[i * 3 + 2] + entry.mesh.position.z;
        const dSq = (vx - x) * (vx - x) + (vz - z) * (vz - z);
        if (dSq < bestDistSq) {
          bestDistSq = dSq;
          bestY = pos[i * 3 + 1] + entry.mesh.position.y;
          if (bestDistSq < 0.15) break; // close enough
        }
      }
      return isFinite(bestY) ? bestY : 0;
    }

    /**
     * Loads and teleports to a specific Sol stop in Street View.
     */
    async loadArea(solNum, entry) {
      const c = this.allCloseups.find(it => it.sol === Number(solNum)) || this.allCloseups[0];
      if (!c) throw new Error('No reconstructed rover stops are available.');
      const request = this.loadRequest = (this.loadRequest || 0) + 1;
      this.loading = true;
      try {
        const connected = this.getOverlapGroup(c.sol);
        this.onLoadProgress?.({ phase: 'selected', sol: c.sol, loaded: 0, total: connected.length });
        // Show the requested stop without waiting for every neighboring download.
        const scene = this.closeCache[c.folder] || await this.loadStopFn(c);
        if (request !== this.loadRequest) return;
        if (!scene) throw new Error(`Scene could not be loaded for sol ${c.sol}`);
        this.currentSol = c.sol;
        this.currentStop = c;
        this.areaOrigin = c;
        this.areaStops = [c];
        this.clearAreaVisuals();
        this.terrainEntries = [];
        this.activeScene = scene;
        this.groundMesh = this.addTerrainStop(c, scene);
        const headingRad = (scene.meta.rover_heading || 0) * Math.PI / 180;
        const startX = entry ? entry.x - c.x : Math.sin(headingRad) * 3.5;
        const startZ = entry ? entry.z - c.y : Math.cos(headingRad) * 3.5;
        this.astronautPos.set(startX, this.getGroundHeightAt(startX, startZ) + ASTRONAUT_EYE_HEIGHT_M, startZ);
        this.yaw = entry && isFinite(entry.heading) ? entry.heading : headingRad + Math.PI;
        this.pitch = -0.05;
        this.lastValidPos = this.astronautPos.clone();
        this.boundaryOpen = false;
        this.updateCamera();
        this.buildNavigationChevrons();
        this.buildARPins(scene);
        this.rebuildRoutePathVisual();
        this.onStopChange(this.currentSol);
        this.onAreaUpdate?.({ phase: 'ready', sol: c.sol });
        // Bound parallelism and admit only downloaded terrain into walkable coverage.
        this.neighborsReady = this.streamNeighborTerrain(connected.filter(stop => stop.sol !== c.sol), request);
      } finally {
        if (request === this.loadRequest) this.loading = false;
      }
    }

    clearAreaVisuals() {
      for (const group of [this.terrainGroup, this.chevronsGroup, this.arPinsGroup]) {
        for (const object of [...group.children]) {
          group.remove(object);
          object.traverse(child => {
            // Shared reconstructed geometry belongs to closeCache, not this scene.
            if (child.geometry && !this.terrainEntries.some(item => item.scene.geom === child.geometry)) child.geometry.dispose();
            for (const material of Array.isArray(child.material) ? child.material : [child.material]) {
              if (!material) continue;
              material.map?.dispose(); material.dispose();
            }
          });
        }
      }
    }

    addTerrainStop(stop, scene) {
      const origin = this.areaOrigin;
      const mesh = new THREE.Mesh(scene.geom, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }));
      mesh.position.set(stop.x - origin.x, (stop.elev || 0) - (origin.elev || 0), stop.y - origin.y);
      this.terrainGroup.add(mesh);
      this.terrainEntries.push({ stop, scene, mesh });
      const rover = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(3, 2.2, 2.8)), new THREE.LineBasicMaterial({ color: 0xe3a254 }));
      rover.position.set(mesh.position.x, mesh.position.y + 1.1, mesh.position.z);
      rover.rotation.y = Math.PI / 2 - (scene.meta.rover_heading || 0) * Math.PI / 180;
      this.terrainGroup.add(rover);
      return mesh;
    }

    async streamNeighborTerrain(stops, request) {
      let unavailable = 0;
      for (let i = 0; i < stops.length; i += 2) {
        if (request !== this.loadRequest) return;
        await Promise.all(stops.slice(i, i + 2).map(async stop => {
          try {
            const scene = this.closeCache[stop.folder] || await this.loadStopFn(stop);
            if (request !== this.loadRequest) return;
            if (!scene) throw new Error(`Missing scene for sol ${stop.sol}`);
            this.addTerrainStop(stop, scene);
            this.areaStops.push(stop);
            this.onAreaUpdate?.({ phase: 'neighbor', sol: stop.sol });
          } catch (error) {
            if (request !== this.loadRequest) return;
            unavailable++;
          }
          this.onLoadProgress?.({ phase: 'neighbors', loaded: this.areaStops.length, total: stops.length + 1, unavailable });
        }));
      }
      if (request === this.loadRequest) this.onLoadProgress?.({ phase: 'complete', loaded: this.areaStops.length, total: stops.length + 1, unavailable });
    }

    async loadSol(solNum) { return this.loadArea(solNum); }

    /**
     * Builds interactive on-ground chevrons pointing toward neighboring stops.
     */
    buildNavigationChevrons() {
      if (!this.currentStop) return;
      const chevCanvas = createChevronTexture();
      const chevTex = new THREE.CanvasTexture(chevCanvas);

      const makeChevron = (targetSol, label) => {
        const targetStop = this.allCloseups.find((c) => c.sol === targetSol);
        if (!targetStop) return;

        // Relative vector in map meters
        const dx = targetStop.x - this.currentStop.x;
        const dz = targetStop.y - this.currentStop.y;
        const angle = Math.atan2(dx, dz);
        const distM = Math.hypot(dx, dz);

        // Place chevron 7 meters out in that direction on the ground
        const placeDist = Math.min(distM * 0.45, 8.5);
        const cx = Math.sin(angle) * placeDist;
        const cz = Math.cos(angle) * placeDist;
        const cy = this.getGroundHeightAt(cx, cz) + 0.15;

        const geom = new THREE.PlaneGeometry(2.4, 2.4);
        geom.rotateX(-Math.PI / 2);
        const mat = new THREE.MeshBasicMaterial({
          map: chevTex,
          transparent: true,
          side: THREE.DoubleSide,
          depthWrite: false
        });
        const mesh = new THREE.Mesh(geom, mat);
        mesh.position.set(cx, cy, cz);
        mesh.rotation.y = angle;
        mesh.userData = { targetSol, label, distM: Math.round(distM) };
        this.chevronsGroup.add(mesh);
      };

      forwardDestinations(this.allCloseups, this.areaStops, { x: this.currentStop.x, z: this.currentStop.y }, this.yaw).forEach((item) => makeChevron(item.stop.sol, `Sol ${item.stop.sol} →`));
    }

    /**
     * Builds 3D AR floating spatial pins for hazards and science targets.
     */
    buildARPins(sc) {
      const rawHaz = (sc.meta && sc.meta.hazards) || (this.currentStop && this.currentStop.hazards) || [];
      const hazards = rawHaz.map((h) => {
        if (Array.isArray(h)) {
          return {
            east: h[0] - (this.currentStop ? this.currentStop.x : 0),
            south: h[1] - (this.currentStop ? this.currentStop.y : 0),
            height: h[2]
          };
        }
        return h;
      });

      // 1. Hazard Pins (Rocks >= 20 cm)
      hazards.slice(0, 12).forEach((h) => {
        const hx = h.east, hz = h.south, hHeightM = h.height;
        if (hHeightM < 0.22) return;
        const hy = this.getGroundHeightAt(hx, hz) + hHeightM + 0.7;

        const canvas = createPinTexture('hazard', `Rock ${Math.round(hHeightM * 100)} cm`, 'Obstacle Hazard');
        const tex = new THREE.CanvasTexture(canvas);
        const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: true });
        const sprite = new THREE.Sprite(mat);
        sprite.position.set(hx, hy, hz);
        sprite.scale.set(1.8, 0.8, 1.0);
        sprite.userData = {
          title: `Boulder Obstacle (${Math.round(hHeightM * 100)} cm)`,
          category: 'Geometric Mobility Hazard',
          description: `Per-vertex obstacle analysis: rock relief is ${Math.round(hHeightM * 100)} cm above surrounding regolith. Impassable for rover drive actuators and high suit trip hazard.`,
          slip_hazard: 'Infinite impedance (Avoidance required)'
        };
        this.arPinsGroup.add(sprite);
      });

      // 2. Science POIs (Yellowknife Bay, Smectite, etc.)
      SCIENCE_POIS.forEach((poi) => {
        if (!this.currentStop) return;
        const dx = poi.x - this.currentStop.x;
        const dz = poi.y - this.currentStop.y;
        if (Math.hypot(dx, dz) > 28.0) return; // Only within or near current stop

        const gy = this.getGroundHeightAt(dx, dz) + poi.z_lift;
        const canvas = createPinTexture('science', poi.name.split(' ')[0], poi.category);
        const tex = new THREE.CanvasTexture(canvas);
        const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
        const sprite = new THREE.Sprite(mat);
        sprite.position.set(dx, gy, dz);
        sprite.scale.set(2.2, 0.95, 1.0);
        sprite.userData = poi;
        this.arPinsGroup.add(sprite);
      });
    }

    /**
     * Builds the glowing trail visual for the planned A* route.
     */
    rebuildRoutePathVisual() {
      const old = this.terrainGroup.getObjectByName('streetview_route_line');
      if (old) this.terrainGroup.remove(old);
      const state = this.appState || (typeof window !== 'undefined' ? window.S : null);
      if (!this.route || !this.currentStop || !state || !state.cols) return;

      const C = state.cols, g = state.g;
      const refX = this.currentStop.x, refZ = this.currentStop.y;

      const pts = [];
      for (const i of this.route.cells) {
        const mx = (i % C) * g, mz = ((i / C) | 0) * g;
        const lx = mx - refX, lz = mz - refZ;
        if (Math.abs(lx) < 40 && Math.abs(lz) < 40) {
          const ly = this.getGroundHeightAt(lx, lz) + 0.12;
          pts.push(new THREE.Vector3(lx, ly, lz));
        }
      }

      if (pts.length > 1) {
        const curve = new THREE.CatmullRomCurve3(pts);
        const geom = new THREE.TubeGeometry(curve, Math.min(200, pts.length * 2), 0.22, 6, false);
        const mat = new THREE.MeshBasicMaterial({ color: 0x4fc3f7, transparent: true, opacity: 0.85 });
        const mesh = new THREE.Mesh(geom, mat);
        mesh.name = 'streetview_route_line';
        this.terrainGroup.add(mesh);
      }
    }

    bindControls() {
      window.addEventListener('keydown', (e) => { this.keys[e.code] = true; });
      window.addEventListener('keyup', (e) => { this.keys[e.code] = false; });

      const el = this.domContainer;
      el.addEventListener('mousedown', (e) => {
        if (e.target.closest && e.target.closest('.street-window, .street-overlay-restore, .view-controls, .street-minimap-card, .inspector-card, .mapkey, .street-persistent-controls, .street-boundary')) return;
        if (e.button === 0) {
          this.isMouseDown = true;
          this.lastMouse = { x: e.clientX, y: e.clientY };
          this.handleClickRaycast(e);
        }
      });
      window.addEventListener('mousemove', (e) => {
        if (!this.active || !this.isMouseDown) return;
        const dx = e.clientX - this.lastMouse.x;
        const dy = e.clientY - this.lastMouse.y;
        this.lastMouse = { x: e.clientX, y: e.clientY };

        this.yaw -= dx * TURN_SPEED;
        this.pitch -= dy * TURN_SPEED;
        this.pitch = Math.max(-Math.PI * 0.42, Math.min(Math.PI * 0.42, this.pitch));
      });
      window.addEventListener('mouseup', () => { this.isMouseDown = false; });

      // Touch controls
      el.addEventListener('touchstart', (e) => {
        if (e.target.closest && e.target.closest('.street-window, .street-overlay-restore, .view-controls, .street-minimap-card, .inspector-card, .mapkey, .street-persistent-controls, .street-boundary')) return;
        if (e.touches.length === 1) {
          this.isMouseDown = true;
          this.lastMouse = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        }
      }, { passive: true });
      el.addEventListener('touchmove', (e) => {
        if (!this.active || !this.isMouseDown || e.touches.length !== 1) return;
        const dx = e.touches[0].clientX - this.lastMouse.x;
        const dy = e.touches[0].clientY - this.lastMouse.y;
        this.lastMouse = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        this.yaw -= dx * TURN_SPEED * 1.2;
        this.pitch -= dy * TURN_SPEED * 1.2;
        this.pitch = Math.max(-Math.PI * 0.42, Math.min(Math.PI * 0.42, this.pitch));
      }, { passive: true });
      el.addEventListener('touchend', () => { this.isMouseDown = false; });
    }

    handleClickRaycast(e) {
      if (!this.active || this.transitioning) return;
      const rect = this.domContainer.getBoundingClientRect();
      this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      this.raycaster.setFromCamera(this.mouse, this.camera);

      // Check Chevrons
      const chevHits = this.raycaster.intersectObjects(this.chevronsGroup.children, true);
      if (chevHits.length > 0) {
        const targetSol = chevHits[0].object.userData.targetSol;
        if (targetSol) {
          this.transitionToSol(targetSol);
          return;
        }
      }

      // Check AR Pins
      const pinHits = this.raycaster.intersectObjects(this.arPinsGroup.children, true);
      if (pinHits.length > 0) {
        this.onInspectPoi(pinHits[0].object.userData);
      }
    }

    /**
     * Smoothly glides camera forward toward target Sol stop.
     */
    transitionToSol(targetSol) {
      this.transitioning = true;
      const startPos = this.astronautPos.clone();
      const t0 = performance.now();
      const durMs = 1100;

      const step = (now) => {
        const k = Math.min(1.0, (now - t0) / durMs);
        const ease = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        // Float forward
        this.astronautPos.y += 0.05 * Math.sin(k * Math.PI);
        if (k < 1.0) {
          requestAnimationFrame(step);
        } else {
          this.transitioning = false;
          this.loadArea(targetSol).catch(error => this.onLoadError(error, targetSol));
        }
      };
      requestAnimationFrame(step);
    }

    updateCamera() {
      this.camera.position.copy(this.astronautPos);
      const lookTarget = new THREE.Vector3(
        this.astronautPos.x + Math.sin(this.yaw) * Math.cos(this.pitch),
        this.astronautPos.y + Math.sin(this.pitch),
        this.astronautPos.z + Math.cos(this.yaw) * Math.cos(this.pitch)
      );
      this.camera.lookAt(lookTarget);
      if (this.skyMesh) {
        this.skyMesh.position.copy(this.astronautPos);
      }
    }

    setSpeedMultiplier(value) {
      const speed = Number(value);
      this.speedMultiplier = Number.isFinite(speed) ? Math.max(0.5, Math.min(4, speed)) : DEFAULT_SPEED_MULTIPLIER;
      return this.speedMultiplier;
    }

    /**
     * Main simulation tick.
     */
    update(deltaSec) {
      if (!this.active || this.transitioning || this.loading || !this.areaOrigin || !this.lastValidPos || !this.areaStops.length || !this.terrainEntries.length) return;

      const multiplier = Number.isFinite(this.speedMultiplier) ? this.speedMultiplier : 1;
      const baseSpeed = this.keys['ShiftLeft'] || this.keys['ShiftRight'] ? RUN_SPEED_MS : WALK_SPEED_MS;
      const speed = baseSpeed * multiplier;
      const moveDist = speed * deltaSec;

      // Forward/backward & strafe relative to yaw
      let dx = 0, dz = 0;
      if (this.keys['KeyW'] || this.keys['ArrowUp']) {
        dx += Math.sin(this.yaw) * moveDist;
        dz += Math.cos(this.yaw) * moveDist;
      }
      if (this.keys['KeyS'] || this.keys['ArrowDown']) {
        dx -= Math.sin(this.yaw) * moveDist;
        dz -= Math.cos(this.yaw) * moveDist;
      }
      if (this.keys['KeyA'] || this.keys['ArrowLeft']) {
        dx += Math.sin(this.yaw + Math.PI / 2) * moveDist;
        dz += Math.cos(this.yaw + Math.PI / 2) * moveDist;
      }
      if (this.keys['KeyD'] || this.keys['ArrowRight']) {
        dx -= Math.sin(this.yaw + Math.PI / 2) * moveDist;
        dz -= Math.cos(this.yaw + Math.PI / 2) * moveDist;
      }

      this.astronautPos.x += dx;
      this.astronautPos.z += dz;

      const worldPoint = { x: this.areaOrigin.x + this.astronautPos.x, z: this.areaOrigin.y + this.astronautPos.z };
      const inside = this.areaStops.some((stop) => Math.hypot(worldPoint.x - stop.x, worldPoint.z - stop.y) <= COVERAGE_RADIUS_M);
      if (!inside) {
        this.astronautPos.copy(this.lastValidPos);
        if (!this.boundaryOpen && (dx || dz)) {
          this.boundaryOpen = true;
          this.onBoundaryOptions(forwardDestinations(this.allCloseups, this.areaStops, worldPoint, this.yaw), worldPoint, this.yaw);
        }
      } else {
        this.lastValidPos.copy(this.astronautPos);
        const nearest = this.areaStops.reduce((best, stop) => Math.hypot(worldPoint.x - stop.x, worldPoint.z - stop.y) < Math.hypot(worldPoint.x - best.x, worldPoint.z - best.y) ? stop : best, this.areaStops[0]);
        if (nearest && nearest.sol !== this.currentSol) { this.currentSol = nearest.sol; this.currentStop = nearest; this.onStopChange(nearest.sol); }
      }

      // Ground collision & elevation snapping
      const groundY = this.getGroundHeightAt(this.astronautPos.x, this.astronautPos.z);
      this.astronautPos.y = groundY + ASTRONAUT_EYE_HEIGHT_M;

      // Update camera and sky dome
      this.updateCamera();

      // Telemetry feed
      const headingDeg = Math.round(((this.yaw * 180 / Math.PI) % 360 + 360) % 360);
      const pitchDeg = Math.round(this.pitch * 180 / Math.PI);
      const isMoving = Math.abs(dx) > 1e-4 || Math.abs(dz) > 1e-4;

      this.onTelemetryUpdate({
        headingDeg,
        pitchDeg,
        isMoving,
        speed: isMoving ? speed : 0,
        x: this.areaOrigin ? this.areaOrigin.x + this.astronautPos.x : 0,
        y: this.areaOrigin ? this.areaOrigin.y + this.astronautPos.z : 0,
        elev: this.areaOrigin && isFinite(this.areaOrigin.elev) ? Math.round(this.areaOrigin.elev + this.astronautPos.y) : Math.round(this.astronautPos.y)
      });
    }

    /**
     * Draws the synchronized 2D mini-map on a canvas.
     */
    drawMiniMap(canvas, bgImg) {
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const W = canvas.width, H = canvas.height;

      ctx.fillStyle = '#10151b';
      ctx.fillRect(0, 0, W, H);

      const state = this.appState || (typeof window !== 'undefined' ? window.S : null);
      if (!this.currentStop || !this.areaOrigin || !state || !state.data) return;

      const mapSize = 350; // Show 350m neighborhood around astronaut
      const cx = this.areaOrigin.x + this.astronautPos.x;
      const cz = this.areaOrigin.y + this.astronautPos.z;
      const zoom = W / mapSize;

      const toM = (mx, mz) => [
        W / 2 + (mx - cx) * zoom,
        H / 2 + (mz - cz) * zoom
      ];

      // Draw background orthophoto tile
      const img = bgImg || (state && state.img);
      if (img && img.complete) {
        const fullSize = state.data.window.size_m;
        const sx = (cx - mapSize / 2) / fullSize * img.width;
        const sy = (cz - mapSize / 2) / fullSize * img.height;
        const sw = (mapSize / fullSize) * img.width;
        const sh = (mapSize / fullSize) * img.height;
        try {
          ctx.drawImage(img, sx, sy, sw, sh, 0, 0, W, H);
        } catch (_) {}
      }

      // Draw Planned Route
      if (this.route && state.cols) {
        const C = state.cols, g = state.g;
        ctx.strokeStyle = '#4fc3f7';
        ctx.lineWidth = 3;
        ctx.beginPath();
        let first = true;
        for (const i of this.route.cells) {
          const [px, py] = toM((i % C) * g, ((i / C) | 0) * g);
          if (first) { ctx.moveTo(px, py); first = false; } else { ctx.lineTo(px, py); }
        }
        ctx.stroke();
      }

      // Draw Neighboring Stops
      this.allCloseups.forEach((c) => {
        const [px, py] = toM(c.x, c.y);
        ctx.fillStyle = c.sol === this.currentSol ? '#e3a254' : '#ffffff';
        ctx.beginPath();
        ctx.arc(px, py, c.sol === this.currentSol ? 6 : 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#ffffff';
        ctx.font = '600 9px monospace';
        ctx.fillText(`sol ${c.sol}`, px + 8, py - 4);
      });

      // Draw Astronaut Pegman position + 60 deg Field-of-View Cone
      const [ax, ay] = [W / 2, H / 2];
      const headingRad = this.yaw;
      const fovRad = Math.PI / 3; // 60 degrees

      ctx.fillStyle = 'rgba(255, 235, 59, 0.28)';
      ctx.strokeStyle = '#ffd54f';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.arc(ax, ay, 46, headingRad - fovRad / 2, headingRad + fovRad / 2);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();

      // Astronaut center dot
      ctx.fillStyle = '#ff5722';
      ctx.beginPath();
      ctx.arc(ax, ay, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }

  return {
    MarswalkStreetView,
    SCIENCE_POIS,
    NASA_SKY,
    ASTRONAUT_EYE_HEIGHT_M,
    overlapGroups,
    forwardDestinations
  };
});
