# Extended orbital terrain

The `/3d-terrain/` page defaults to the original 2 × 2 km planning window.
Its Terrain coverage selector reloads the page with `?terrain=5` to load
the 5 × 5 km window centered on that original window. Extended assets are
requested only for that selection. Other pages keep their original coordinate system.
The terrain page shifts rover positions, paths, and hazards by 1,500 m on
each axis so they remain registered to the expanded imagery.

Sources: USGS `MSL_Gale_DEM_Mosaic_1m_v3.tif` and
`MSL_Gale_HiRISE-LRGB_78quads_sharp_cog.tif` from
<https://astrogeology.usgs.gov/search/map/mars_msl_gale_merged_orthophoto_mosaic_25cm>.
Only the required source regions are requested. The elevation TIFF uses
full-width strips, so extracting the window transfers about 418 MB.

Rebuild with `.venv/bin/python orbital/build_extended_terrain.py` after
installing rasterio, NumPy, Pillow, and SciPy. The script caches downloaded
arrays in `data/cache/terrain_5km_*.npy` and writes browser assets under
`app/terrain-5km/`: a 2 m elevation grid, a 4096-pixel color texture, a
slope/roughness hazard approximation, and window/provenance metadata.
Missing elevation is encoded explicitly, not fabricated.
