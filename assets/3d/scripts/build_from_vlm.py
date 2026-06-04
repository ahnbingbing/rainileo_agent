"""
assets/3d/scripts/build_from_vlm.py — build Blender scene from vlm_layout.json.

Reads the JSON produced by `agents/room_extractor.py` and constructs
the Blender model with walls, doors, windows, and anchor boxes placed at
the VLM-extracted coordinates. Replaces hand-coded constants.

Run:
    blender --background --python assets/3d/scripts/build_from_vlm.py

Optional: pass `--pd-east-west-swap living_room` to flip a room's E/W
anchors after extraction (used when PD's verbal correction differs from
the floor-plan-driven Gemini reading).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path("/Users/ahnbingbing/code/rianileo-agent")
LAYOUT = ROOT / "assets" / "3d" / "scripts" / "vlm_layout.json"

# PD corrections are now applied directly to vlm_layout.json (more precise
# than blanket E/W swap) — keeping empty until a future blanket override is
# needed.
PD_OVERRIDES: dict = {}

try:
    import bpy
    INSIDE_BLENDER = True
except ImportError:
    INSIDE_BLENDER = False
    print("[warning] not running inside Blender — exiting after syntax check")
    sys.exit(0)


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────
CARD_MATS: dict = {}


def add_mat(name: str, color: tuple, emissive_strength: float = 0.0):
    if name in CARD_MATS:
        return CARD_MATS[name]
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for n in nodes:
        nodes.remove(n)
    out = nodes.new("ShaderNodeOutputMaterial")
    if emissive_strength > 0:
        emit = nodes.new("ShaderNodeEmission")
        emit.inputs["Color"].default_value = (*color, 1.0)
        emit.inputs["Strength"].default_value = emissive_strength
        mat.node_tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    else:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.6
        mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    CARD_MATS[name] = mat
    return mat


def make_box(name: str, location: tuple, size: tuple, material=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0]/2, size[1]/2, size[2]/2)
    bpy.ops.object.transform_apply(scale=True)
    if material:
        obj.data.materials.append(material)
    return obj


def clean_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    CARD_MATS.clear()


# ────────────────────────────────────────────────────────────────────────
# Coordinate conversion: VLM JSON → Blender world coords
# ────────────────────────────────────────────────────────────────────────
# Convention: each room has its own local origin at the center of the floor.
# +X = east, +Y = north, +Z = up. We place rooms in world coords by tracking
# room offsets from the floor plan.
ROOM_OFFSETS: dict = {}   # room_id → (offset_x, offset_y) in world coords


def apply_overrides(rooms: dict):
    """Apply PD's hand corrections (E/W swap etc.) before building."""
    for rid, ops in PD_OVERRIDES.items():
        room = rooms.get(rid)
        if not room:
            continue
        for op in ops:
            if op == "swap_east_west":
                walls = room.get("walls", {})
                east = walls.get("EAST", {"anchors": [], "doors": [], "windows": []})
                west = walls.get("WEST", {"anchors": [], "doors": [], "windows": []})
                walls["EAST"] = west
                walls["WEST"] = east
                room["walls"] = walls
                print(f"  ⇄ {rid}: swapped EAST ↔ WEST anchors")


def wall_anchor_world(room_id: str, room: dict, wall: str, pos_pct: float,
                       depth_from_wall: float) -> tuple:
    """Convert (wall, pos_pct, depth_from_wall) to world (x, y, z=floor)."""
    w = room["shape_rect"]["width_m"]
    d = room["shape_rect"]["depth_m"]
    offset_x, offset_y = ROOM_OFFSETS.get(room_id, (0.0, 0.0))
    if wall == "SOUTH":
        x = offset_x + (pos_pct - 0.5) * w
        y = offset_y - d/2 + depth_from_wall
    elif wall == "NORTH":
        x = offset_x + (pos_pct - 0.5) * w
        y = offset_y + d/2 - depth_from_wall
    elif wall == "WEST":
        x = offset_x - w/2 + depth_from_wall
        y = offset_y + (pos_pct - 0.5) * d
    elif wall == "EAST":
        x = offset_x + w/2 - depth_from_wall
        y = offset_y + (pos_pct - 0.5) * d
    else:
        x, y = offset_x, offset_y
    return (x, y)


def build_room(room_id: str, room: dict):
    print(f"  Building room: {room_id} ({room.get('korean_name','?')})")
    w = room["shape_rect"]["width_m"]
    d = room["shape_rect"]["depth_m"]
    h = room["shape_rect"]["height_m"]
    offx, offy = ROOM_OFFSETS.get(room_id, (0.0, 0.0))

    mat_floor = add_mat(f"Floor_{room_id}", (0.92, 0.88, 0.82))
    mat_wall = add_mat(f"Wall_{room_id}", (0.94, 0.93, 0.91))
    mat_ceiling = add_mat(f"Ceil_{room_id}", (0.97, 0.97, 0.96))

    # Floor + ceiling
    make_box(f"Floor_{room_id}", (offx, offy, -0.01), (w, d, 0.02), mat_floor)
    make_box(f"Ceiling_{room_id}", (offx, offy, h + 0.01), (w, d, 0.02), mat_ceiling)

    # Walls
    THICK = 0.15
    make_box(f"WallS_{room_id}", (offx, offy - d/2, h/2), (w, THICK, h), mat_wall)
    make_box(f"WallN_{room_id}", (offx, offy + d/2, h/2), (w, THICK, h), mat_wall)
    make_box(f"WallE_{room_id}", (offx + w/2, offy, h/2), (THICK, d, h), mat_wall)
    make_box(f"WallW_{room_id}", (offx - w/2, offy, h/2), (THICK, d, h), mat_wall)

    # Anchors per wall
    for wall_name, info in (room.get("walls") or {}).items():
        # ── Windows on this wall (drawn first so anchors layer in front) ──
        for j, win in enumerate(info.get("windows", []) or []):
            wpct = win.get("wall_pos_pct", 0.5)
            ww = win.get("width_m", 1.0)
            sill = win.get("sill_height_m", 1.0)
            wh = max(0.4, h - sill - 0.2)
            wtype = win.get("type", "regular")
            # World position of the window's wall center
            if wall_name == "SOUTH":
                wx = offx + (wpct - 0.5) * w
                wy = offy - d/2 + 0.05
            elif wall_name == "NORTH":
                wx = offx + (wpct - 0.5) * w
                wy = offy + d/2 - 0.05
            elif wall_name == "WEST":
                wx = offx - w/2 + 0.05
                wy = offy + (wpct - 0.5) * d
            elif wall_name == "EAST":
                wx = offx + w/2 - 0.05
                wy = offy + (wpct - 0.5) * d
            else:
                continue
            # Single large window pane (통창), low glow because adjacent
            # building 1m away blocks most outside light per PD.
            emit_strength = 1.2 if wtype == "frosted_high" else 1.0
            glass_color = (0.88, 0.87, 0.82) if wtype == "frosted_high" else (0.85, 0.88, 0.95)
            mat_glass = add_mat(f"Win_{room_id}_{wall_name}_{j}",
                                 glass_color, emissive_strength=emit_strength)
            mat_frame = add_mat(f"WinFrame_{room_id}_{wall_name}_{j}",
                                 (0.92, 0.90, 0.86))
            frame_w = 0.06
            # Single pane (통창)
            if wall_name in ("NORTH", "SOUTH"):
                make_box(
                    f"WinPane_{room_id}_{wall_name}_{j}",
                    (wx, wy, sill + wh/2),
                    (ww - frame_w*2, 0.04, wh - frame_w*2), mat_glass,
                )
                # Top + bottom frame strips
                make_box(f"WinFrameTop_{room_id}_{wall_name}_{j}",
                         (wx, wy, sill + wh - frame_w/2),
                         (ww, 0.06, frame_w), mat_frame)
                make_box(f"WinFrameBot_{room_id}_{wall_name}_{j}",
                         (wx, wy, sill + frame_w/2),
                         (ww, 0.06, frame_w), mat_frame)
            else:
                make_box(
                    f"WinPane_{room_id}_{wall_name}_{j}",
                    (wx, wy, sill + wh/2),
                    (0.04, ww - frame_w*2, wh - frame_w*2), mat_glass,
                )

            # Small linen privacy screen ("가림막") with embroidery — hangs over
            # upper portion of the window, semi-translucent off-white cream
            mat_linen = add_mat(f"Linen_{room_id}_{wall_name}_{j}",
                                 (0.94, 0.92, 0.86))
            screen_h = wh * 0.55  # covers about top-half of window
            if wall_name in ("NORTH", "SOUTH"):
                screen_y = wy - 0.05 if wall_name == "SOUTH" else wy + 0.05
                make_box(
                    f"LinenScreen_{room_id}_{wall_name}_{j}",
                    (wx, screen_y, sill + wh - screen_h/2),
                    (ww * 0.92, 0.015, screen_h),
                    mat_linen,
                )
            else:
                screen_x = wx + 0.05 if wall_name == "WEST" else wx - 0.05
                make_box(
                    f"LinenScreen_{room_id}_{wall_name}_{j}",
                    (screen_x, wy, sill + wh - screen_h/2),
                    (0.015, ww * 0.92, screen_h),
                    mat_linen,
                )

        for i, a in enumerate(info.get("anchors", [])):
            pos = a.get("position_along_wall_pct", 0.5)
            depth = a.get("depth_from_wall_m", 0.0) or 0.0
            dim_w = a.get("dim_w_m", 0.5) or 0.5
            dim_d = a.get("dim_d_m", 0.5) or 0.5
            dim_h = a.get("dim_h_m", 0.5) or 0.5
            base_x, base_y = wall_anchor_world(
                room_id, room, wall_name, pos, depth + max(dim_d, dim_w)/2
            )
            nm = (a.get("name_ko", "") + " " + a.get("name_en", "")).lower()

            # Special compound: bench = wooden cabinet base + blue cushion on top + tall wooden backrest
            # PD: "쇼파 아래 공간은 닫혀 있고 나무로" — base is a closed wooden cabinet, NOT open legs
            if any(t in nm for t in ("bench", "sofa", "쇼파", "벤치", "daybed")):
                seat_total_h = 0.55       # cabinet (0.40) + cushion (0.15)
                cabinet_h = 0.40
                cushion_h = 0.15
                bench_d = 0.78            # depth from wall

                # Determine y based on which wall
                if wall_name == "SOUTH":
                    y_anchor = -room["shape_rect"]["depth_m"]/2 + 0.15 + bench_d/2 + offy
                elif wall_name == "NORTH":
                    y_anchor = room["shape_rect"]["depth_m"]/2 - 0.15 - bench_d/2 + offy
                else:
                    y_anchor = base_y

                # 1) Wooden cabinet base (closed under seat)
                mat_cab = add_mat(f"BenchCabinet_{room_id}_{i}", (0.72, 0.56, 0.36))
                make_box(
                    f"BenchCabinet_{room_id}_{i}",
                    (base_x, y_anchor, cabinet_h/2),
                    (dim_w, bench_d, cabinet_h), mat_cab,
                )
                # 2) Blue fabric cushion on top
                mat_cush = add_mat(f"BenchCushion_{room_id}_{i}", (0.18, 0.30, 0.50))
                make_box(
                    f"BenchCushion_{room_id}_{i}",
                    (base_x, y_anchor, cabinet_h + cushion_h/2),
                    (dim_w - 0.05, bench_d - 0.05, cushion_h), mat_cush,
                )
                # 3) Tall wooden backrest panel (against wall, up to window sill)
                back_h = max(dim_h, 1.4)
                back_d = 0.12
                if wall_name == "SOUTH":
                    back_y = -room["shape_rect"]["depth_m"]/2 + 0.15 + back_d/2 + offy
                elif wall_name == "NORTH":
                    back_y = room["shape_rect"]["depth_m"]/2 - 0.15 - back_d/2 + offy
                else:
                    back_y = base_y
                make_box(
                    f"BenchBack_{room_id}_{i}",
                    (base_x, back_y, back_h/2),
                    (dim_w + 0.2, back_d, back_h), mat_cab,
                )
                continue

            color = _color_for_category(a.get("category", ""))
            mat = add_mat(f"Anc_{room_id}_{wall_name}_{i}", color)
            if wall_name in ("NORTH", "SOUTH"):
                size = (dim_w, dim_d, dim_h)
            else:
                size = (dim_d, dim_w, dim_h)
            make_box(
                f"Anc_{room_id}_{wall_name}_{a.get('name_en','x')[:24]}_{i}",
                (base_x, base_y, dim_h/2),
                size, mat,
            )

    # Freestanding anchors (placed at room center as a placeholder; PD will
    # refine via subsequent JSON edits — this gets the shape on the floor).
    for i, a in enumerate(room.get("notable_freestanding", [])):
        dim_w = a.get("dim_w_m", 0.5) or 0.5
        dim_d = a.get("dim_d_m", 0.5) or 0.5
        dim_h = a.get("dim_h_m", 0.5) or 0.5
        color = _color_for_category(a.get("category", ""))
        mat = add_mat(f"Free_{room_id}_{i}", color)
        make_box(
            f"Free_{room_id}_{a.get('name_en','x')[:24]}_{i}",
            (offx + (i - 0.5) * 0.8, offy, dim_h/2),
            (dim_w, dim_d, dim_h), mat,
        )


def _color_for_category(cat: str) -> tuple:
    return {
        "furniture": (0.55, 0.42, 0.30),
        "appliance": (0.85, 0.85, 0.87),
        "decor": (0.40, 0.30, 0.20),
        "fixture": (0.18, 0.32, 0.55),
    }.get(cat, (0.6, 0.6, 0.6))


def lay_out_rooms(rooms: dict, connections: list):
    """Position rooms next to each other based on connections (simple walk)."""
    # Living room at origin
    placed = set()
    queue = []
    # Pick living_room as seed
    seed = "living_room" if "living_room" in rooms else next(iter(rooms))
    ROOM_OFFSETS[seed] = (0.0, 0.0)
    placed.add(seed)
    queue.append(seed)
    while queue:
        cur = queue.pop(0)
        for c in connections:
            if c.get("from") == cur and c.get("to") not in placed:
                other = c["to"]
                cur_room = rooms[cur]
                oth_room = rooms.get(other)
                if not oth_room:
                    continue
                cur_w = cur_room["shape_rect"]["width_m"]
                cur_d = cur_room["shape_rect"]["depth_m"]
                oth_d = oth_room["shape_rect"]["depth_m"]
                wall = c.get("wall_of_from")
                ox, oy = ROOM_OFFSETS[cur]
                if wall == "NORTH":
                    new_off = (ox, oy + cur_d/2 + oth_d/2)
                elif wall == "SOUTH":
                    new_off = (ox, oy - cur_d/2 - oth_d/2)
                elif wall == "EAST":
                    new_off = (ox + cur_w/2 + oth_room["shape_rect"]["width_m"]/2, oy)
                elif wall == "WEST":
                    new_off = (ox - cur_w/2 - oth_room["shape_rect"]["width_m"]/2, oy)
                else:
                    new_off = (ox, oy)
                ROOM_OFFSETS[other] = new_off
                placed.add(other)
                queue.append(other)


def setup_cameras_and_render():
    """Add canonical POV cameras + a top-down + isometric, then render all."""
    cams = []
    lr = bpy.data.objects.get("Floor_living_room")
    if not lr:
        return
    # Get living room dimensions from data
    layout = json.loads(LAYOUT.read_text())
    lr_rect = layout["rooms"]["living_room"]["shape_rect"]
    w = lr_rect["width_m"]
    d = lr_rect["depth_m"]
    h = lr_rect["height_m"]

    # POV-A: north → south
    bpy.ops.object.camera_add(location=(0, d/2 - 0.5, 0.35),
                               rotation=(1.45, 0, 0))
    cams.append((bpy.context.active_object, "Cam_POV_A_facing_sofa", 28))
    # POV-B: south → north
    bpy.ops.object.camera_add(location=(0, -d/2 + 0.5, 0.35),
                               rotation=(1.45, 0, math.pi))
    cams.append((bpy.context.active_object, "Cam_POV_B_facing_TV", 28))
    # POV-C east
    bpy.ops.object.camera_add(location=(-w/2 + 1.0, 0, 0.35),
                               rotation=(1.45, 0, -math.pi/2))
    cams.append((bpy.context.active_object, "Cam_POV_C_facing_east", 32))
    # POV-C west
    bpy.ops.object.camera_add(location=(w/2 - 1.0, 0, 0.35),
                               rotation=(1.45, 0, math.pi/2))
    cams.append((bpy.context.active_object, "Cam_POV_C_facing_west", 32))
    # Top-down
    max_dim = max(
        ROOM_OFFSETS[r][0] + rooms[r]["shape_rect"]["width_m"]/2 +
        abs(ROOM_OFFSETS[r][1]) + rooms[r]["shape_rect"]["depth_m"]/2
        for r in ROOM_OFFSETS if r in rooms
    )
    bpy.ops.object.camera_add(location=(0, 1.0, 12),
                               rotation=(0, 0, 0))
    topcam = bpy.context.active_object
    topcam.name = "Cam_TopDown"
    topcam.data.type = "ORTHO"
    topcam.data.ortho_scale = max(max_dim * 2.5, 10)
    cams.append((topcam, "Cam_TopDown", None))
    # Isometric
    bpy.ops.object.camera_add(location=(8, -8, 10), rotation=(1.0, 0, 0.78))
    cams.append((bpy.context.active_object, "Cam_Isometric", 35))

    for cam, name, lens in cams:
        cam.name = name
        if lens:
            cam.data.lens = lens

    # Add sun light
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 6))
    sun = bpy.context.active_object
    sun.data.energy = 3.0
    sun.rotation_euler = (0.5, 0.3, 0)

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 192
    scene.render.resolution_x = 720
    scene.render.resolution_y = 1280
    scene.render.image_settings.file_format = "PNG"
    out_dir = ROOT / "assets" / "3d" / "renders"
    out_dir.mkdir(parents=True, exist_ok=True)
    for cam, name, _ in cams:
        # Top/iso get square aspect
        if name in ("Cam_TopDown", "Cam_Isometric"):
            scene.render.resolution_x = 1280
            scene.render.resolution_y = 1280
        else:
            scene.render.resolution_x = 720
            scene.render.resolution_y = 1280
        scene.camera = cam
        scene.render.filepath = str(out_dir / f"vlm_{name}.png")
        bpy.ops.render.render(write_still=True)
        print(f"  ✓ {name}")


# Main
data = json.loads(LAYOUT.read_text(encoding="utf-8"))
rooms = data.get("rooms", {})
apply_overrides(rooms)
clean_scene()
lay_out_rooms(rooms, data.get("room_connections", []))
for rid, room in rooms.items():
    build_room(rid, room)

# Save blend
blend_path = str(ROOT / "assets" / "3d" / "models" / "from_vlm.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
print(f"saved {blend_path}")

setup_cameras_and_render()
print("done.")
