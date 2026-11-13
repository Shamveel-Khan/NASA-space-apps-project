/* Walking-route planner for the Gale Marswalk Planner. Works in the browser (window.MarswalkPlanner) and in Node
   (module.exports) so it can be tested on its own: `node --test app/tests/`.

   The terrain is a grid of square cells (`g` metres each) with heights H (metres), a slope per cell (degrees) and a
   validity flag. A cell is off-limits when it has no data, is steeper than the walker's limit, or its extra cost is
   Infinity (for example a rock too tall to step over). The route minimises walking time, with speed set by grade.

   Bioenergetics (Minetti 0.38g partial gravity):
     C_w(i) = 280.5·i⁵ − 58.7·i⁴ − 76.8·i³ + 51.9·i² + 19.6·i + 2.5   [J/(kg·m)]
   where i = gradient (rise/run, signed). Scaled for 0.38g Mars gravity (eta_grav = 0.62) and the xEMU
   pressurized spacesuit system (130 kg suit + 80 kg astronaut = 210 kg total mass).
   Source: Minetti et al. (2002) J. Exp. Biol. 205, 3461-3470; Minetti (2001) Nature 409, 467-468. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MarswalkPlanner = api;
})(typeof self !== 'undefined' ? self : this, function () {
  const VFLAT = 3.0 / 3.6;   // m/s on flat ground in a suit (example value, not a NASA EVA rule)

  // ---- Minetti 0.38g partial-gravity bioenergetic constants ----
  // (Assumption: 80 kg astronaut + 130 kg xEMU suit = 210 kg total EVA mass)
  const M_TOTAL_KG = 210.0;  // Total EVA system mass [kg]
  const ETA_GRAV   = 0.62;   // Partial-gravity mechanical work scaling for 0.38g (Minetti Nature 2001)
  const W_SUIT     = 0.8;    // xEMU pressurized-suit joint resistance [J/(kg·m)]
  const KJ_PER_L_O2 = 20.1; // Human metabolic oxygen equivalency [kJ / L O2]
  const J_TO_KCAL  = 1.0 / 4184.0;
  // NASA xEMU primary life-support O2 capacity for an 8-hour EVA (reference assumption)
  const EVA_O2_LIMIT_L = 720.0;

  /**
   * Minetti (2002) 5th-order locomotion cost polynomial.
   * i = gradient (rise/run); returns cost in J/(kg·m).
   * Valid range: −0.5 ≤ i ≤ +0.5. Clipped outside that range.
   */
  function minettiCostPerMeter(i) {
    const g = Math.max(-0.5, Math.min(0.5, i));
    return 280.5 * Math.pow(g, 5) - 58.7 * Math.pow(g, 4) - 76.8 * Math.pow(g, 3) +
           51.9 * Math.pow(g, 2) + 19.6 * g + 2.5;
  }

  /**
   * Bioenergetics for one route step.
   * gradient: rise/run (signed, uphill positive)
   * distM: horizontal distance of step [m]
   * slipMu: terramechanic slip multiplier (1.0=bedrock, 1.5=soil, 5.0=sand, Inf=impassable)
   * Returns { joules, kcal, o2Liters }
   */
  function stepBioenergetics(gradient, distM, slipMu) {
    const mu = Math.min(slipMu || 1.0, 10.0);  // cap sand slip to avoid runaway numbers
    const C_w = Math.max(0.5, minettiCostPerMeter(gradient));  // floor at a minimum cost
    const joules = M_TOTAL_KG * (ETA_GRAV * C_w + W_SUIT) * distM * mu;
    return { joules, kcal: joules * J_TO_KCAL, o2Liters: (joules / 1000.0) / KJ_PER_L_O2 };
  }

  /** Tobler's hiking rule, scaled so flat ground = VFLAT. grade = rise / run (uphill positive). */
  function walkSpeed(grade) {
    return VFLAT * Math.exp(-3.5 * Math.abs(grade + 0.05)) / Math.exp(-3.5 * 0.05);
  }
  const VMAX = walkSpeed(-0.05);

  /** Binary min-heap on parallel typed arrays (fast for ~1 M cells). */
  function Heap(capacity) {
    let key = new Float64Array(capacity), val = new Int32Array(capacity), size = 0;
    return {
      get size() { return size; },
      push(k, v) {
        if (size === key.length) { const k2 = new Float64Array(size * 2), v2 = new Int32Array(size * 2); k2.set(key); v2.set(val); key = k2; val = v2; }
        let i = size++;
        while (i > 0) { const p = (i - 1) >> 1; if (key[p] <= k) break; key[i] = key[p]; val[i] = val[p]; i = p; }
        key[i] = k; val[i] = v;
      },
      pop() {
        const top = val[0], k = key[--size], v = val[size];
        let i = 0;
        for (;;) {
          const l = 2 * i + 1, r = l + 1; let m = i, mk = k;
          if (l < size && key[l] < mk) { m = l; mk = key[l]; }
          if (r < size && key[r] < mk) { m = r; }
          if (m === i) break;
          key[i] = key[m]; val[i] = val[m]; i = m;
        }
        key[i] = k; val[i] = v;
        return top;
      },
    };
  }

  /**
   * A* search for the quickest walk from a to b ({x, y} in metres from the grid's top-left corner).
   * grid: { H: Float32Array, valid: Uint8Array, slope: Float32Array, cols, rows, g }
   * cost (optional): Float32Array of extra time multipliers per cell (1 = normal, Infinity = blocked).
   * Returns { cells, time, dist, up, down, maxs, prof, hazardCells } or { error }.
   */
  function planRoute(grid, a, b, maxSlope, cost) {
    const C = grid.cols, R = grid.rows, g = grid.g, n = C * R, H = grid.H;
    const cell = (p) => {
      const x = Math.round(p.x / g), y = Math.round(p.y / g);
      return x < 0 || y < 0 || x >= C || y >= R ? -1 : y * C + x;
    };
    const ia = cell(a), ib = cell(b);
    const blocked = (i) => !grid.valid[i] || grid.slope[i] > maxSlope || (cost && cost[i] === Infinity);
    if (ia < 0 || ib < 0) return { error: 'That point is outside the map.' };
    if (blocked(ia) || blocked(ib)) return { error: 'That point is steeper than your limit, blocked by a hazard, or has no data. Pick another spot.' };
    const gScore = new Float64Array(n).fill(Infinity), came = new Int32Array(n).fill(-1), closed = new Uint8Array(n);
    const bx = ib % C, by = (ib / C) | 0;
    const h = (i) => Math.hypot((i % C) - bx, ((i / C) | 0) - by) * g / VMAX;
    const heap = Heap(1 << 16);
    gScore[ia] = 0; heap.push(h(ia), ia);
    const N8 = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];
    while (heap.size) {
      const i = heap.pop();
      if (closed[i]) continue;
      closed[i] = 1;
      if (i === ib) break;
      const x = i % C, y = (i / C) | 0;
      for (const [dx, dy] of N8) {
        const nx = x + dx, ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= C || ny >= R) continue;
        const j = ny * C + nx;
        if (closed[j] || blocked(j)) continue;
        const d = (dx && dy ? Math.SQRT2 : 1) * g;
        const t = gScore[i] + (d / walkSpeed((H[j] - H[i]) / d)) * (cost ? cost[j] : 1);
        if (t < gScore[j]) { gScore[j] = t; came[j] = i; heap.push(t + h(j), j); }
      }
    }
    if (!isFinite(gScore[ib])) return { error: 'No route stays under that slope limit. Raise the limit or pick closer points.' };
    const cells = [];
    for (let i = ib; i !== -1; i = came[i]) cells.push(i);
    cells.reverse();
    let dist = 0, up = 0, down = 0, maxs = 0, hazardCells = 0;
    const prof = [[0, H[cells[0]]]];
    let totalJoules = 0, totalKcal = 0, totalO2 = 0;
    for (let k = 1; k < cells.length; k++) {
      const i = cells[k - 1], j = cells[k], dh = H[j] - H[i];
      const run = Math.hypot((j % C) - (i % C), ((j / C) | 0) - ((i / C) | 0)) * g;
      dist += Math.hypot(run, dh);
      if (dh > 0) up += dh; else down -= dh;
      maxs = Math.max(maxs, grid.slope[j]);
      if (cost && cost[j] > 1) hazardCells++;
      prof.push([dist, H[j]]);
      // ---- Minetti 0.38g partial-gravity bioenergetics per step ----
      const gradient = run > 0 ? dh / run : 0;
      const slipMu = (grid.slipMu && grid.slipMu[j]) ? grid.slipMu[j] : 1.0;
      const bio = stepBioenergetics(gradient, run || 1, slipMu);
      totalJoules += bio.joules; totalKcal += bio.kcal; totalO2 += bio.o2Liters;
    }
    const o2PctEvaSuit = (totalO2 / EVA_O2_LIMIT_L) * 100;
    return { cells, time: gScore[ib], dist, up, down, maxs, prof, hazardCells,
             bioenergetics: { totalKcal: +totalKcal.toFixed(2), totalO2L: +totalO2.toFixed(2),
                              totalJoules: +totalJoules.toFixed(0),
                              o2PctEvaSuit: +o2PctEvaSuit.toFixed(1) } };
  }

  /** Slope (degrees) per cell from heights, by central differences. Cells without data get 99. */
  function slopes(H, valid, cols, rows, g) {
    const out = new Float32Array(cols * rows);
    for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
      const i = y * cols + x;
      if (!valid[i]) { out[i] = 99; continue; }
      const xl = x > 0 && valid[i - 1] ? i - 1 : i, xr = x < cols - 1 && valid[i + 1] ? i + 1 : i;
      const yu = y > 0 && valid[i - cols] ? i - cols : i, yd = y < rows - 1 && valid[i + cols] ? i + cols : i;
      const dx = xr === xl ? 0 : (H[xr] - H[xl]) / ((xr - xl) * g);
      const dy = yd === yu ? 0 : (H[yd] - H[yu]) / (((yd - yu) / cols) * g);
      out[i] = Math.atan(Math.hypot(dx, dy)) * 180 / Math.PI;
    }
    return out;
  }

  return { planRoute, walkSpeed, slopes, VFLAT, minettiCostPerMeter, stepBioenergetics, EVA_O2_LIMIT_L };
});
