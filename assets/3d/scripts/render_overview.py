"""
assets/3d/scripts/render_overview.py — Top-down floor plan + isometric
overview renders for PD to verify spatial layout at a glance.

Run:
    blender --background \\
        assets/3d/models/grandma_livingroom.blend \\
        --python assets/3d/scripts/render_overview.py
"""
from __future__ import annotations

import math
import sys

try:
    import bpy
except ImportError:
    print("must run inside Blender"); sys.exit(0)


ROOM_W = 6.0
ROOM_D = 4.5
ROOM_H = 2.7


def add_floor_plan_camera():
    """Top-down orthographic view, like an architectural floor plan."""
    bpy.ops.object.camera_add(
        location=(0, 0, 10),
        rotation=(0, 0, 0),
    )
    cam = bpy.context.active_object
    cam.name = "Cam_TopDown_FloorPlan"
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(ROOM_W, ROOM_D) * 1.2
    return cam


def add_isometric_camera():
    """3/4 isometric — see the whole room with depth."""
    bpy.ops.object.camera_add(
        location=(ROOM_W * 1.2, -ROOM_D * 1.2, ROOM_H * 1.5),
        rotation=(1.0, 0, 0.78),
    )
    cam = bpy.context.active_object
    cam.name = "Cam_Isometric_Overview"
    cam.data.lens = 35
    return cam


def add_iso_opposite():
    """Isometric from the opposite corner for second perspective."""
    bpy.ops.object.camera_add(
        location=(-ROOM_W * 1.2, ROOM_D * 1.2, ROOM_H * 1.5),
        rotation=(1.0, 0, math.pi + 0.78),
    )
    cam = bpy.context.active_object
    cam.name = "Cam_Isometric_OppositeCorner"
    cam.data.lens = 35
    return cam


def annotate_anchors():
    """Place text labels at anchor positions so PD can identify them."""
    labels = [
        ("BENCH (S)", (0, -ROOM_D/2 + 0.6, 1.0)),
        ("WINDOW (S)", (0, -ROOM_D/2 + 0.4, 2.2)),
        ("PIANO (W)", (-ROOM_W/2 + 0.6, -0.5, 1.6)),
        ("TV STAND (N)", (0, ROOM_D/2 - 0.4, 1.3)),
        ("CONSOLE (E)", (ROOM_W/2 - 0.6, -1.5, 1.0)),
        ("SCRATCHER", (-ROOM_W/2 + 1.0, -1.0, 0.4)),
        ("HENGWAN ←", (ROOM_W/2 - 0.4, -ROOM_D/2 + 0.4, 0.5)),
        ("KITCHEN ↑", (0, ROOM_D/2 - 0.2, 0.4)),
    ]
    for text, loc in labels:
        bpy.ops.object.text_add(location=loc)
        obj = bpy.context.active_object
        obj.data.body = text
        obj.data.size = 0.25
        obj.name = f"Label_{text.split()[0]}"


def main():
    cams = []
    cams.append(add_floor_plan_camera())
    cams.append(add_isometric_camera())
    cams.append(add_iso_opposite())
    annotate_anchors()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 256
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 1280
    scene.render.image_settings.file_format = "PNG"
    for cam in cams:
        scene.camera = cam
        out_path = f"/Users/ahnbingbing/code/rianileo-agent/assets/3d/renders/{cam.name}.png"
        scene.render.filepath = out_path
        bpy.ops.render.render(write_still=True)
        print(f"  rendered {cam.name}")
    # Save with new cameras + labels for PD to use later
    bpy.ops.wm.save_as_mainfile(
        filepath="/Users/ahnbingbing/code/rianileo-agent/assets/3d/models/grandma_livingroom.blend"
    )


if __name__ == "__main__":
    main()
