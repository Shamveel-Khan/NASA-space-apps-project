# Vercel Services deployment

The repository now has a two-service deployment adapter. The existing website, NASA data, offline reconstruction code and local static-hosting workflow remain in place.

## Service graph awaiting confirmation

| Service | Source root | Public ingress | Role |
| --- | --- | --- | --- |
| `app` | `services/app` | `/(.*)` | CDN website assets and `/api/navcam/*` facade |
| `navcam3d` | `services/navcam3d` | None | Internal read-only rover metadata API |

`app` calls `navcam3d` through the caller-side `NAVCAM3D_URL` service binding. Vercel injects this runtime variable. Do not create or set it manually. There are no hardcoded internal origins, deployment domains, localhost ports or build-time reads of this binding.

Public routes owned by `app`:

- `/` and the existing exploration page directories.
- `/app/**`: bundled Three.js, original NASA-derived binary terrain, imagery and static JSON. Static files in the service's generated `public/` directory are served by Vercel's CDN.
- `/api/navcam/stops`: proxies the private stop catalog.
- `/api/navcam/stops/{sol}`: proxies that stop's original `scene.json`.
- `/api/health`: reports that the public service is running and whether a binding exists; it does not probe the upstream service.

Private `navcam3d` routes: `/health`, `/stops`, `/stops/{sol}`. No top-level rewrite targets this service, so those endpoints do not have public ingress. The browser calls the public facade; only the server reads the private binding.

This is an explicit deployment design choice: the existing `navcam3d/` directory contains offline Python commands, not an HTTP app. The adapter serves their already-generated metadata. It does not launch downloads, GPU depth estimation, model training, or persistent filesystem writes inside Vercel Functions. Regenerate terrain with the existing offline pipeline and redeploy to publish changed data.

## Build and dependency isolation

Both service roots contain separate `pyproject.toml` files with FastAPI and Uvicorn dependencies. The scientific packages in root `requirements.txt` are not installed into either deployment service. There is no frontend npm installation or compilation step.

Vercel executes these short staging commands during the cloud build:

```sh
python ../../tools/stage_vercel.py app
python ../../tools/stage_vercel.py navcam3d
```

Each command runs from its respective service root. The shared script locates the repository independently of the working directory. It copies the existing website into `services/app/site/` and scene metadata into `services/navcam3d/dataset/`. It never downloads NASA data, reconstructs geometry or starts inference. The internal service explicitly includes `dataset/**` in its function bundle. Generated directories are ignored in Git and initial Vercel source uploads; Vercel builds regenerate them.

The app staging step adds the public, same-origin `/api/navcam/` facade URL to the deployed copy of the browser bootstrap. `explorer.js` then reads stop metadata through that API, falling back to the original static scene JSON if the facade is unavailable. Plain `python3 -m http.server` and offline demonstrations continue reading the original scene files directly, without probing nonexistent API routes. Geometry, classifications and imagery remain the original static products.

The repository root must be the Vercel project root. Do not set the Vercel project's Root Directory to `app/` or a single service. All services must receive the repository's shared sources and data for their staging commands.

`.vercelignore` excludes local environments, credentials/config directories, scientific downloads, notebooks, offline processing source and test content from deployment source uploads. Staging publishes only the website and its shipped scientific products, rather than the whole repository.

## Local multi-service development (run yourself)

The Vercel CLI must be installed and authenticated. No install, build, or development server was executed during preparation.

From the repository root, stage the local development artifacts and then use Vercel's services-aware dev server:

```sh
python3 tools/stage_vercel.py app
python3 tools/stage_vercel.py navcam3d
vercel dev
```

Vercel resolves service dependencies and injects the binding for the local services. The existing static-only workflow remains:

```sh
python3 -m http.server 8000
```

## Deployment and confirmation

The Vercel plugin supports source-file or Git-based deployment. Read-only discovery found no Vercel project linked to `https://github.com/Shamveel-Khan/mars-terrain-explorer` and returned no teams. No deployment or project creation has been performed: the user explicitly requested confirmation of service names, public routes, and bindings first. The target account/team, project and preview/production environment also need confirmation.

A Git-based deployment uses committed remote source, not uncommitted local edits. After confirmation, deploy the prepared local source through the plugin when supported, or commit/push the reviewed changes before using a Git deployment. Do not silently deploy the old remote commit instead.

Manual CLI alternative, after confirming/linking the intended project:

```sh
vercel deploy
```

This creates a preview. Use `vercel deploy --prod` only when production is the confirmed target.

## Required deployment verification

Only lightweight syntax, configuration and source-data checks are appropriate before dependencies are installed. No cloud deployment or Vercel build has been tested yet.

After deployment, check `/api/health`, `/api/navcam/stops`, and `/api/navcam/stops/1100`. Confirm the direct internal routes (for example `/stops/1100`) are not publicly routed. Inspect the browser network panel to ensure stop metadata uses `/api/navcam/`, while binary geometry and imagery use `/app/`. Exercise every page and Street View stop switching, overlays and retry recovery. Check the remote build logs for both services.

Official references:

- https://vercel.com/docs/services
- https://vercel.com/docs/services/config-reference
- https://vercel.com/docs/services/bindings
- https://vercel.com/docs/frameworks/backend/fastapi

## Service-name schema correction

The service key is `navcam`, not `navcam3d`: Vercel service identifiers must match `^[a-z]([a-z_-]*[a-z])?$`, which excludes digits. The directory `services/navcam3d`, staging argument `navcam3d`, and runtime variable `NAVCAM3D_URL` are valid and intentionally unchanged. The binding now targets `navcam`.

In Vercel Project Settings, use repository root `.` (leave Root Directory empty), Framework Preset **Other**, and clear project-wide build/install/output overrides. This is a Services project; `fastapi` is configured only on its two actual Python service roots. Do not select FastAPI for the repository root: the root contains the offline scientific requirements and has no FastAPI entrypoint. Deploy the updated configuration and service adapters together.

## Website 404 correction

The app now explicitly mounts its staged `site/` directory after the API routes using `StaticFiles(html=True)`. The app function includes `site/**`, so requests that reach FastAPI can serve the real homepage and physical exploration pages. The staging command now writes `site/` rather than relying solely on automatic `public/` CDN discovery. Removed the service-wide trailingSlash override so FastAPI can handle API and directory canonicalization without conflicting redirects. Redeploy updated source; an existing deployment does not pick up local edits.
