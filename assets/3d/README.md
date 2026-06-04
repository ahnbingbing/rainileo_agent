# Grandma's House 3D Model

**Status:** Foundational (2026-05-31). Built after 25 iterations of text-prompt-only Seedance backgrounds failed to keep furniture positions consistent. Pivot to geometric 3D anchor.

## Pipeline (target)

```
PD floor plan + photos
        ↓
Blender 3D model  (this directory)
        ↓
Render canonical POVs (POV-A/B/C)  →  assets/3d/renders/*.png
        ↓
Cameraman scene_ref = Blender render (geometrically perfect background)
        ↓
Seedance ref mode  →  pets animated against fixed bg
        ↓
Captions + assemble + Giri
```

## Files

| Path | Purpose |
|---|---|
| `scripts/build_grandma_livingroom.py` | Blender Python — builds the model from scratch. |
| `models/grandma_livingroom.blend` | The Blender file (output of the script). PD opens + adjusts. |
| `renders/` | Rendered POV stills used as scene_ref by Cameraman. |
| `textures/` | (future) Texture maps for floor/walls/cushion fabric. |

## How to (re)build

### Option A: PD opens Blender + edits visually
1. `brew install --cask blender` (or download from blender.org)
2. Open Blender → File → Open → `assets/3d/models/grandma_livingroom.blend`
3. Iterate: move furniture to match PD's actual floor plan + photos.
4. Save.
5. Re-run `render_all_povs` from the scripting tab.

### Option B: headless Python rebuild
```bash
blender --background --python assets/3d/scripts/build_grandma_livingroom.py
```
This wipes + rebuilds + renders all canonical POVs.

## Current limitations
- Furniture proxies are simple boxes (placeholder geometry).
- No texture maps yet — solid colors only.
- Camera positions are approximate to PD's POV descriptions.
- Lighting = single sun + ambient. Will refine with HDRI later.

## Next steps
1. PD reviews `renders/Cam_POV_A_facing_sofa.png` against real photos.
2. Adjust dimensions / positions until POV-A looks right.
3. Iterate POV-B/C.
4. Optionally add texture maps (white wood plank floor, blue cushion fabric).
5. Add procedural variation (different times of day) once geometry locked.
