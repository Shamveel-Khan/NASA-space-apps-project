# Current homepage and Street View presentation

The Mars orbital observatory is now the homepage at `/`. `/mars/` redirects to `/`, preserving query and fragment values. The former cinematic terrain landing is no longer loaded. Its lower mission, imagery/depth reconstruction, dataset ledger, exploration directory and closing sections now appear below the globe, using the observatory's charcoal, copper and muted blue-grey palette.

`app/ui/orbit-story.html` contains the migrated content. `app/ui/orbit-story.css` styles it, and `app/ui/landing-interactions.js` connects the reconstruction sequence and real dataset metrics to the existing application data. `app/ui/boot.js` loads these only for the globe experience. The globe rotation speed is 1.5; reduced motion and pause controls remain available.

Street View's individual heading, sol banner, telemetry, map, imagery legend, loading note, control toolbar, expedition title, keyboard help, brief, boundary and loading windows have × controls. Stable window wrappers keep close buttons intact when the scientific engine changes their contents. `Restore panels` restores individually dismissed windows. The observation inspector retains its existing × control. These interactions live in `app/ui/street-panels.js` and retain the existing scientific DOM hooks.

No verification was run for this update, as requested. No new dependencies or server setup are required.

Serve the repository root with:

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000/`.
