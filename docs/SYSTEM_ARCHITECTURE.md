# Gale Marswalk Planner — Simplified System Architecture

## 1. System purpose

Gale Marswalk Planner turns NASA orbital and Curiosity rover data into an interactive Mars exploration system. A user can move from a global view of Mars to Gale Crater, inspect a detailed planning area, calculate a walking route, and open 3D ground reconstructions at rover stops.

The scientific processing is performed offline. The browser receives compact, precomputed terrain, route, hazard, weather, and 3D-mesh files, so the deployed application does not need to run large AI models.

## 2. Architecture at a glance

```text
NASA / USGS data
   ├── Orbital DEM, orthophoto and rover telemetry
   ├── Navcam images, stereo XYZ and camera labels
   └── Mastcam colour images
                 │
                 ▼
       Offline Python pipelines
   ├── Orbital terrain and map builder
   ├── Stereo + AI depth reconstruction
   ├── Ground classification and hazard extraction
   └── Mastcam colour projection
                 │
                 ▼
       Precomputed application assets
   ├── Height and traversability grids
   ├── Rover path, weather and stop catalogue
   └── 3D close-up meshes and colour layers
                 │
                 ▼
       Browser application (Three.js)
   ├── Mars and Gale visualizations
   ├── A* walking-route planner
   ├── EVA time, energy and oxygen estimates
   └── Interactive ground close-ups
```

## 3. Main components

| Component | Responsibility |
|---|---|
| `orbital/` | Downloads and prepares orbital elevation, imagery, rover locations, traverse data, weather, and the route-planning grid. |
| `navcam3d/` | Downloads Navcam stereo products, estimates missing depth, constructs local terrain meshes, detects hazards, classifies the surface, and projects Mastcam colour. |
| `app/` | Stores the generated scientific assets and runs the Three.js visualizations and browser route planner. |
| `services/` | Provides optional static hosting and read-only APIs for precomputed Navcam metadata. It does not run model inference. |
| `tests/` and `app/tests/` | Check the camera geometry, depth alignment, data processing, and route-planning behavior. |

## 4. Data and processing pipelines

### 4.1 Orbital terrain pipeline

1. Obtain the Gale Crater digital elevation model and orbital imagery from NASA/USGS products.
2. Extract the selected map window rather than downloading the full products into the application.
3. Convert elevation into browser-friendly terrain grids at the required zoom levels.
4. Add Curiosity’s traverse, stops, weather, and mission context from NASA feeds.
5. Calculate slope, roughness, and traversability values for route planning.
6. Export the map, terrain grid, cost grid, and metadata to `app/`.

This pipeline supplies the Mars globe, Gale Crater view, planning-area terrain, and the base data used by the walking planner.

### 4.2 Navcam depth and 3D reconstruction pipeline

1. Find a Curiosity stop and download its left Navcam images, stereo XYZ products, and PDS camera labels.
2. Read NASA’s measured stereo depth wherever a reliable stereo match exists.
3. Run Depth Anything V2 Small on each Navcam image to predict relative depth across the full image.
4. Fit the prediction to metric stereo measurements using held-out validation pixels.
5. Preserve measured stereo points and use AI depth only to fill missing regions.
6. Remove rover-body pixels, transform points through the JPL CAHV camera model, and place all frames in one rover-site coordinate system.
7. Merge the frames into a 15 cm ground grid, remove spikes, fill small gaps, calculate slope, and construct a triangle mesh.
8. Store the mesh, depth-source flags, slope values, hazards, metrics, and preview images under `app/stops/`.

The key design decision is that NASA stereo remains authoritative. The neural model completes holes; it does not replace measured geometry.

### 4.3 Surface segmentation pipeline

The current application displays four surface classes: soil, bedrock, sand, and big rock. The layer is produced per mesh vertex from image brightness, local slope, and detected obstacle geometry.

- Light, relatively flat surfaces are classified as bedrock.
- Dark or moderately sloped terrain is classified as sand.
- Ordinary traversable material remains soil.
- Hazard regions and very steep vertices are classified as big rock.

The class array is stored in `regolith.b64.txt` and coloured in the 3D viewer. Although the classes follow the AI4Mars-style taxonomy, the active repository implementation is a deterministic terrain classifier. An external neural segmentation checkpoint is not part of the deployed pipeline and is therefore not treated as a production model here.

### 4.4 Mastcam colour pipeline

1. Audit the NASA Mastcam archive for colour images taken at the same rover site and drive as a Navcam reconstruction.
2. Download the selected calibrated RGB images and their camera metadata.
3. Transform the existing mesh vertices into each Mastcam camera frame.
4. Project visible vertices into the images using the CAHV model and reject occluded points.
5. Combine valid observations into an RGB vertex-colour layer.
6. Keep uncovered areas grayscale and publish the result as a separate colour variant, preserving the original scientific geometry.

Colour coverage depends on Mastcam pointing and overlap; it is not artificially painted onto unseen terrain.

### 4.5 Hazard and traversability pipeline

Local close-up hazards are extracted from terrain relief. Rocks and ledges at least 25 cm above nearby ground are recorded, while tall obstacles can be treated as blocked route cells.

At orbital scale, the application uses a precomputed traversability-cost grid derived from slope, roughness, aspect, and curvature. The builder supports a LightGBM ground-to-orbit model and a Bekker–Wong-inspired slope fallback. The browser consumes the resulting cost grid rather than running either calculation live.

### 4.6 Route and EVA estimation pipeline

1. The user selects start and end points and a maximum allowed slope.
2. A* searches the 2 m terrain grid for the lowest-time valid path.
3. Tobler’s walking rule changes speed according to uphill or downhill grade.
4. Terrain costs and known rock hazards penalize or block unsafe cells.
5. The route is summarized with distance, time, ascent, descent, maximum slope, hazards, energy, and oxygen estimates.
6. Minetti-based walking energetics are adjusted for Mars gravity and suit resistance.

These are planning estimates and are not official NASA EVA limits.

### 4.7 Application and delivery pipeline

The frontend loads only the assets needed for the selected view. Three.js renders the orbital terrain and ground meshes, while JavaScript performs route search and user interaction. The optional FastAPI services expose static files and precomputed metadata; model training, downloads, and terrain generation remain offline tasks.

## 5. Models and technical methods currently used

| Model or method | Current role |
|---|---|
| Depth Anything V2 Small | Active depth-completion model for the generated ground close-ups. Its output is scaled to NASA stereo depth for every image. |
| CAHV camera model | Converts between image pixels, camera rays, and rover/site 3D coordinates for Navcam and Mastcam products. |
| Four-class terrain classifier | Active rule-based soil, bedrock, sand, and big-rock classification for close-up mesh vertices. |
| LightGBM/Bekker–Wong traversability stage | Produces the precomputed orbital hazard-cost layer, with the physical slope model used as the supported fallback. |
| A* search | Finds the lowest-time route while respecting slope, missing data, terrain cost, and obstacles. |
| Tobler and Minetti models | Estimate grade-dependent walking speed and approximate EVA energy/oxygen demand. |

MoGe-2 and Depth Anything 3 are comparison or migration candidates in this repository, not models used by the current generated viewer assets. The fine-tuned Depth Anything checkpoint is also not deployed: the application uses the pretrained model because the fine-tuning experiment did not demonstrate a reliable overall improvement on unseen terrain.

## 6. Main outputs

- Interactive Mars, Gale Crater, planning-area, and ground-close-up views.
- A terrain-aware walking route with slope and hazard avoidance.
- Route distance, duration, elevation profile, energy, and oxygen estimates.
- Measured-versus-estimated depth visualization.
- Soil, bedrock, sand, and rock visualization.
- Original grayscale and Mastcam-colour 3D variants where colour overlap exists.

## 7. Important limits

- Depth-completed areas are predictions and are less certain than stereo-measured areas.
- Orbital elevation cannot resolve small rocks; detailed hazards are available only at reconstructed rover stops.
- The current four-class segmentation is rule-based, not a trained neural segmentation network.
- Colour appears only where real Mastcam images observe the reconstructed surface.
- Walking, energy, oxygen, weather, and radiation results are engineering demonstrations rather than operational NASA recommendations.
