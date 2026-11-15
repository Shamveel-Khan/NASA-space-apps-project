# Gale Marswalk Planner — Four-Person Presentation Plan

This split keeps the talk technical but easy to follow. Each teammate should explain their section at system level, use one or two visuals, and avoid walking through source code.

## Speaker 1 — Introduction and problem

**Role:** Introduce the project and establish why it matters.

**Main points:**

- Astronauts on Mars need more than a normal map: they need terrain, slope, obstacles, travel time, environmental context, and close-up ground evidence.
- Gale Marswalk Planner combines NASA orbital data with Curiosity rover observations in one interactive system.
- The experience moves through four scales: Mars, Gale Crater, a detailed planning area, and 3D rover-stop close-ups.
- The main user action is selecting two points and receiving a terrain-aware walking route with safety and EVA estimates.

**Suggested visual:** Begin with the Mars view, zoom into Gale Crater, and finish on the route-planning screen.

**Handoff:** “Now that the problem and user journey are clear, our team lead will explain how the complete system connects the data, processing pipelines, and application.”

## Speaker 2 — Team lead and overall architecture

**Role:** Explain the complete system and how the major pipelines communicate.

**Main points:**

- NASA/USGS orbital products provide elevation and imagery; NASA rover feeds provide Curiosity’s path, stops, weather, Navcam stereo, and Mastcam colour.
- Offline Python pipelines turn large scientific products into compact height grids, hazard grids, 3D meshes, and metadata.
- The browser application uses Three.js for visualization and A* for route search; it does not run heavy AI inference live.
- The route pipeline combines elevation, slope, terrain cost, and rock obstacles, then estimates walking time, energy, and oxygen.
- The architecture preserves different evidence levels: NASA stereo is measured, AI depth is estimated, and colour is used only where Mastcam actually overlaps the mesh.

**Suggested visual:** Show the simple flow: `NASA data → offline processing → precomputed assets → web application`.

**Handoff:** “The most important processing happens in the AI and terrain-understanding stage, which our next teammate will explain.”

## Speaker 3 — AI, depth, segmentation, and fine-tuning

**Role:** Explain the AI-related work without going too deeply into neural-network internals.

### Depth Anything V2

- The active depth model is Depth Anything V2 Small.
- A Navcam image gives the model visual structure, but its raw prediction is not trusted as metric Mars depth.
- NASA’s stereo XYZ product supplies measured depth at valid pixels. The pipeline uses those measurements to scale the model output into metres.
- Stereo values remain unchanged, and Depth Anything fills only the missing regions. The completed depth is then converted through the CAHV camera model into a 3D point cloud and terrain mesh.
- Accuracy is checked on stereo pixels held out from the scaling step, so calibration and evaluation do not use exactly the same points.

### Fine-tuning experiment

- The team implemented a Mars-specific fine-tuning pipeline using Navcam images paired with NASA stereo depth.
- The image encoder is frozen, while the smaller depth neck and prediction head are trained with a scale-and-shift-invariant loss.
- Training and testing are separated by sol to reduce leakage from overlapping rover images.
- The experiment did not show a reliable overall improvement on unseen terrain, so the pretrained Depth Anything V2 model remains the production choice. This is an engineering result: a custom model is used only when testing proves it generalizes better.

### Surface segmentation

- Each reconstructed mesh is divided into soil, bedrock, sand, and big-rock classes.
- The current class layer is generated from image brightness, local slope, and detected obstacle geometry: lighter flat areas suggest bedrock, darker or moderately sloped areas suggest sand, and steep hazard regions become big rock.
- The output is one class per mesh vertex and is displayed as a colour layer in the 3D viewer.
- The taxonomy is AI4Mars-inspired, but the active repository implementation is a deterministic classifier rather than an external neural segmentation checkpoint. This distinction should be stated clearly.

### Other AI work

- The application includes a precomputed traversability heatmap derived from terrain features such as slope, roughness, aspect, and curvature.
- MoGe-2 and Depth Anything 3 are available for model comparison, but they do not power the current viewer and should be mentioned only as evaluated future alternatives if asked.

**Suggested visuals:** Show one Navcam photo, stereo depth, completed depth, final mesh, and the four-class surface layer.

**Handoff:** “With the terrain reconstructed and understood, the final section shows what the complete system achieves and where it can go next.”

## Speaker 4 — Demonstration ending and conclusion

**Role:** Connect the technical work to the result and close the presentation.

**Main points:**

- Demonstrate a route between two locations and point out distance, travel time, slope, hazards, energy, and oxygen.
- Open a 3D ground close-up and switch between the original image, measured-versus-estimated depth, slope, and terrain classes.
- If available, show a Mastcam-colour variant and explain that only genuinely observed vertices receive colour; the remaining surface stays grayscale.
- Summarize the value: the system unifies orbital planning with rover-scale evidence instead of treating them as separate maps.
- State the main limitations honestly: sparse rover-stop coverage, predicted depth in stereo holes, coarse orbital hazards, partial colour overlap, and non-operational EVA assumptions.
- End with future work: reconstruct more stops, obtain wider colour coverage, validate terrain classes with labelled Mars data, and compare replacement depth models before any production migration.

**Suggested final message:** “Our result is not just a visualization of Mars. It is a traceable planning pipeline that connects real orbital measurements, rover observations, AI-assisted reconstruction, and human route decisions.”

## Shared presentation rules

- Use the terms **measured**, **estimated**, and **derived** consistently.
- Say “Depth Anything,” not “Deep Anything.”
- Do not claim that MoGe-2, Depth Anything 3, or a fine-tuned checkpoint powers the current viewer.
- Do not call the current four-class layer a trained neural segmentation model; describe it as an AI4Mars-inspired deterministic terrain classifier.
- Keep equations and implementation details for questions. During the main talk, explain inputs, processing, outputs, and validation.
- Each speaker should end with a sentence that naturally introduces the next section.
