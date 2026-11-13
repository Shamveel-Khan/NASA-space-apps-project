const test = require('node:test');
const assert = require('node:assert');
const Street = require('../streetview.js');

const stops = [
  { sol: 1, x: 0, y: 0 },
  { sol: 2, x: 30, y: 0 },
  { sol: 3, x: 77, y: 0 }, // Transitive overlap: 1 -> 2 -> 3
  { sol: 4, x: 180, y: 0 },
  { sol: 5, x: 0, y: -60 }
];

test('overlap groups include transitive terrain coverage', () => {
  const groups = Street.overlapGroups(stops, 24).map((group) => group.map((stop) => stop.sol).sort());
  assert.deepStrictEqual(groups, [[1, 2, 3], [4], [5]]);
});

test('boundary choices prefer stops in the direction of travel', () => {
  const choices = Street.forwardDestinations(stops, stops.slice(0, 3), { x: 77, z: 0 }, Math.PI / 2);
  assert.deepStrictEqual(choices.map((choice) => choice.stop.sol), [4]);
});

test('Street View tick is safe before the first area has loaded', () => {
  const view = Object.create(Street.MarswalkStreetView.prototype);
  Object.assign(view, { active: true, transitioning: false, loading: false, areaOrigin: null });
  assert.doesNotThrow(() => view.update(0.016));
});

test('failed terrain load clears loading state without exposing an uninitialized origin', async () => {
  const view = Object.create(Street.MarswalkStreetView.prototype);
  const stop = { sol: 1100, folder: 'sol1100', x: 0, y: 0 };
  let rejectLoad;
  Object.assign(view, {
    active: true, transitioning: false, loading: false, areaOrigin: null,
    allCloseups: [stop], closeCache: {}, getOverlapGroup: () => [stop],
    loadStopFn: () => new Promise((resolve, reject) => { rejectLoad = reject; })
  });
  const load = view.loadArea(1100);
  assert.strictEqual(view.loading, true);
  assert.doesNotThrow(() => view.update(0.016));
  rejectLoad(new Error('Terrain fetch failed'));
  await assert.rejects(load, /Terrain fetch failed/);
  assert.strictEqual(view.loading, false);
  assert.strictEqual(view.areaOrigin, null);
  assert.doesNotThrow(() => view.update(0.016));
});

test('a superseded terrain request cannot replace the current area', async () => {
  const view = Object.create(Street.MarswalkStreetView.prototype);
  const first = { sol: 1, folder: 'first' }, second = { sol: 2, folder: 'second' };
  let resolveFirst, rejectSecond;
  Object.assign(view, {
    allCloseups: [first, second], closeCache: {}, areaOrigin: null,
    getOverlapGroup: sol => [sol === 1 ? first : second],
    loadStopFn: stop => stop.sol === 1
      ? new Promise(resolve => { resolveFirst = resolve; })
      : new Promise((resolve, reject) => { rejectSecond = reject; })
  });
  const older = view.loadArea(1), newer = view.loadArea(2);
  resolveFirst({});
  await older;
  assert.strictEqual(view.areaOrigin, null);
  assert.strictEqual(view.loading, true);
  rejectSecond(new Error('New request failed'));
  await assert.rejects(newer, /New request failed/);
  assert.strictEqual(view.loading, false);
});

test('walking and telemetry resume when the reconstructed area is ready', () => {
  const view = Object.create(Street.MarswalkStreetView.prototype);
  const origin = { sol: 1100, x: 100, y: 200, elev: -4400 };
  let telemetry;
  Object.assign(view, {
    active: true, transitioning: false, loading: false,
    areaOrigin: origin, areaStops: [origin], terrainEntries: [{}], currentSol: 1100,
    astronautPos: { x: 0, y: 1.75, z: 0 }, lastValidPos: { copy() {} },
    keys: { KeyW: true }, yaw: 0, pitch: 0,
    getGroundHeightAt: () => 0, updateCamera() {},
    onTelemetryUpdate: value => { telemetry = value; }
  });
  view.loading = true;
  view.update(1);
  assert.strictEqual(view.astronautPos.z, 0);
  view.loading = false;
  view.update(1);
  assert.strictEqual(view.astronautPos.z, 0.95);
  assert.strictEqual(telemetry.y, 200.95);
  assert.strictEqual(telemetry.isMoving, true);
});

function streamingController() {
  const view = Object.create(Street.MarswalkStreetView.prototype);
  const selected = { sol: 1100, folder: 'selected', x: 100, y: 200 };
  const neighbor = { sol: 1104, folder: 'neighbor', x: 120, y: 200 };
  Object.assign(view, {
    allCloseups: [selected, neighbor], closeCache: { selected: { meta: { rover_heading: 0 } } },
    getOverlapGroup: () => [selected, neighbor], terrainEntries: [],
    clearAreaVisuals() { this.terrainEntries = []; },
    addTerrainStop(stop, scene) { this.terrainEntries.push({ stop, scene }); return {}; },
    astronautPos: { set(x,y,z) { Object.assign(this,{x,y,z}); }, clone() { return {x:this.x,y:this.y,z:this.z}; } },
    getGroundHeightAt: () => 0, updateCamera() {}, buildNavigationChevrons() {},
    buildARPins() {}, rebuildRoutePathVisual() {}, onStopChange() {}
  });
  return view;
}

test('the selected Street View stop is ready before its neighbor download finishes', async () => {
  const view = streamingController();
  let finishNeighbor;
  view.loadStopFn = () => new Promise(resolve => { finishNeighbor = resolve; });
  await view.loadArea(1100);
  assert.strictEqual(view.loading, false);
  assert.strictEqual(view.areaOrigin.sol, 1100);
  assert.deepStrictEqual(view.areaStops.map(stop => stop.sol), [1100]);
  assert.strictEqual(view.terrainEntries.length, 1);
  finishNeighbor({ meta: { rover_heading: 0 } });
  await view.neighborsReady;
  assert.deepStrictEqual(view.areaStops.map(stop => stop.sol), [1100, 1104]);
  assert.strictEqual(view.terrainEntries.length, 2);
});

test('a failed neighbor leaves the selected stop usable and reports incomplete coverage', async () => {
  const view = streamingController();
  let finalProgress;
  view.loadStopFn = async () => { throw new Error('Neighbor unavailable'); };
  view.onLoadProgress = progress => { finalProgress = progress; };
  await view.loadArea(1100);
  await view.neighborsReady;
  assert.strictEqual(view.loading, false);
  assert.strictEqual(view.areaOrigin.sol, 1100);
  assert.strictEqual(view.terrainEntries.length, 1);
  assert.deepStrictEqual(view.areaStops.map(stop => stop.sol), [1100]);
  assert.strictEqual(finalProgress.phase, 'complete');
  assert.strictEqual(finalProgress.unavailable, 1);
});
