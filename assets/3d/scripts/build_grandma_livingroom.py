"""
assets/3d/scripts/build_grandma_livingroom.py — Blender Python script that
builds a foundational 3D model of 충주 grandma's living room from PD's
floor plan + reference photos.

Built 2026-05-31 as the pivot from text-prompt-driven Seedance backgrounds
(which keep drifting between cuts) to geometrically-anchored 3D renders.

Outputs:
- assets/3d/models/grandma_livingroom.blend (the .blend file PD opens)
- assets/3d/renders/POV_A_facing_sofa.png  (initial canonical POV render)
- assets/3d/renders/POV_B_facing_TV.png
- assets/3d/renders/POV_C_east.png
- assets/3d/renders/POV_C_west.png

Run headless:
    blender --background --python assets/3d/scripts/build_grandma_livingroom.py

Or open Blender, run inside Scripting tab.

Layout coordinates (from PD floor plan):
- Origin = center of living room floor
- +X = east  (toward 현관)
- +Y = north (toward TV / kitchen opening)
- +Z = up
- Room rectangle: 6.0m wide (E-W) × 4.5m deep (N-S) × 2.7m high
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

# Allow running outside Blender for syntax check
try:
    import bpy
    import bmesh
    from mathutils import Vector
    INSIDE_BLENDER = True
except ImportError:
    INSIDE_BLENDER = False
    print("[warning] not running inside Blender — exiting after syntax check")
    sys.exit(0)


# ────────────────────────────────────────────────────────────────────────
# Constants — room dimensions (meters) per PD floor plan
# ────────────────────────────────────────────────────────────────────────
ROOM_W = 6.0     # east-west (living room width)
ROOM_D = 4.5     # north-south (living room depth)
ROOM_H = 2.7

# Kitchen — north of living room, same width, ~3.5m deep
KITCHEN_W = 6.0
KITCHEN_D = 3.5
KITCHEN_OPENING_W = 2.2  # opening in the wall between living + kitchen (passage)
KITCHEN_OPENING_CENTER_X = -1.5  # west side of north wall (away from TV+halmoni door)

# Kitchen Y origin (its south wall coincides with living room north wall)
KITCHEN_Y_S = ROOM_D / 2
KITCHEN_Y_N = ROOM_D / 2 + KITCHEN_D

WALL_THICK = 0.15

# Bench (south wall, centered)
BENCH_W = 2.0
BENCH_D = 0.7
BENCH_H = 0.45  # seat height
BENCH_CUSHION_H = 0.15

# Frosted glass high windows (south wall, above bench)
WIN_W = 3.0
WIN_H = 0.55
WIN_BOTTOM_Z = 1.6  # window starts 1.6m up

# Piano (WEST wall, southernmost position per PD)
PIANO_W = 0.6
PIANO_L = 1.4
PIANO_H = 1.25
PIANO_CENTER_Y = -ROOM_D/2 + PIANO_L/2 + 0.4  # near south end of west wall

# Air conditioner — SW corner (small, near piano)
AC_W = 0.45
AC_D = 0.45
AC_H = 1.7

# Per PD floor plan IMG_4110 trace (2026-05-31 final):
# Living room rectangle. South wall (bottom of plan, where 쇼파 sits):
#   [west end]  ...  쇼파(center-east)  장식장+시계  현관(SE corner)
# North wall (top): kitchen opening (left) + TV (center) + bath opening (right)
# West wall: 피아노 (south-west area) + AC + scratcher next to piano
# East wall: 화장실 (NE) + 현관 (SE corner)

# Sofa: south wall, just slightly west-of-center per plan
BENCH_CENTER_X = -0.3

# TV — NORTH wall, RIGHT (east) side per PD; 할머니방 door east of TV
TV_STAND_W = 1.4
TV_STAND_D = 0.4
TV_STAND_H = 0.55
TV_W = 1.2
TV_H = 0.7
TV_CENTER_X = 1.6

# 할머니방 door (just a door frame placeholder, east of TV on north wall)
HALMONI_DOOR_W = 0.9
HALMONI_DOOR_H = 2.1
HALMONI_DOOR_X = ROOM_W/2 - WALL_THICK - HALMONI_DOOR_W/2 - 0.2

# Console + wall clock — SOUTH wall, east of bench, RIGHT NEXT TO 현관
CONSOLE_W = 0.9
CONSOLE_D = 0.4
CONSOLE_H = 0.85
CONSOLE_CENTER_X = BENCH_CENTER_X + BENCH_W/2 + 0.3 + CONSOLE_W/2

# 현관 door (south-east corner — east wall, south end)
HYEONGWAN_W = 0.95
HYEONGWAN_H = 2.1
HYEONGWAN_CENTER_Y = -ROOM_D/2 + HYEONGWAN_W/2 + 0.2

# Leo's oval cardboard scratcher (on floor near piano)
SCRATCHER_W = 0.5
SCRATCHER_D = 0.3
SCRATCHER_H = 0.1


def clean_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def add_material(name: str, color: tuple) -> "bpy.types.Material":
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.6
    return mat


def add_emissive(name: str, color: tuple, strength: float = 8.0) -> "bpy.types.Material":
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    for n in nodes:
        nodes.remove(n)
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (*color, 1.0)
    emit.inputs["Strength"].default_value = strength
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(emit.outputs["Emission"], out.inputs["Surface"])
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


def build_room():
    # Materials
    mat_wall = add_material("WallWhitePaint", (0.95, 0.94, 0.92))
    mat_floor = add_material("FloorWhiteWood", (0.92, 0.88, 0.82))
    mat_ceiling = add_material("CeilingWhite", (0.97, 0.97, 0.96))
    mat_bench_wood = add_material("BenchWoodFrame", (0.85, 0.72, 0.55))
    mat_bench_fabric = add_material("BenchBlueCushion", (0.18, 0.30, 0.50))
    mat_window_glass = add_emissive("WindowFrostedGlow",
                                     (1.0, 0.96, 0.88), strength=12.0)
    mat_piano = add_material("PianoBlackGlossy", (0.03, 0.03, 0.03))
    mat_tv_stand = add_material("TVStandLightWood", (0.85, 0.78, 0.68))
    mat_tv_screen = add_material("TVScreen", (0.02, 0.02, 0.02))
    mat_console = add_material("AntiqueDarkWood", (0.20, 0.10, 0.05))
    mat_scratcher = add_material("CardboardKraft", (0.78, 0.62, 0.42))

    # Floor
    floor = make_box(
        "Floor",
        location=(0, 0, -0.01),
        size=(ROOM_W, ROOM_D, 0.02),
        material=mat_floor,
    )

    # Ceiling
    ceiling = make_box(
        "Ceiling",
        location=(0, 0, ROOM_H + 0.01),
        size=(ROOM_W, ROOM_D, 0.02),
        material=mat_ceiling,
    )

    # Living room walls
    # NORTH wall — split into 3 pieces around the kitchen opening
    open_left_edge = KITCHEN_OPENING_CENTER_X - KITCHEN_OPENING_W / 2
    open_right_edge = KITCHEN_OPENING_CENTER_X + KITCHEN_OPENING_W / 2
    # west piece of north wall (from west wall to opening's left edge)
    nw_seg_w_width = (open_left_edge - (-ROOM_W/2))
    if nw_seg_w_width > 0:
        make_box(
            "WallNorth_WestSeg",
            location=((-ROOM_W/2 + open_left_edge) / 2, ROOM_D/2, ROOM_H/2),
            size=(nw_seg_w_width, WALL_THICK, ROOM_H),
            material=mat_wall,
        )
    # east piece of north wall (from opening right edge to east wall)
    nw_seg_e_width = (ROOM_W/2 - open_right_edge)
    if nw_seg_e_width > 0:
        make_box(
            "WallNorth_EastSeg",
            location=((ROOM_W/2 + open_right_edge) / 2, ROOM_D/2, ROOM_H/2),
            size=(nw_seg_e_width, WALL_THICK, ROOM_H),
            material=mat_wall,
        )
    # lintel above the opening
    make_box(
        "WallNorth_Lintel",
        location=(KITCHEN_OPENING_CENTER_X, ROOM_D/2,
                  2.1 + (ROOM_H - 2.1)/2),
        size=(KITCHEN_OPENING_W, WALL_THICK, ROOM_H - 2.1),
        material=mat_wall,
    )

    south_wall = make_box(
        "WallSouth",
        location=(0, -ROOM_D/2, ROOM_H/2),
        size=(ROOM_W, WALL_THICK, ROOM_H),
        material=mat_wall,
    )
    east_wall = make_box(
        "WallEast",
        location=(ROOM_W/2, 0, ROOM_H/2),
        size=(WALL_THICK, ROOM_D, ROOM_H),
        material=mat_wall,
    )
    west_wall = make_box(
        "WallWest",
        location=(-ROOM_W/2, 0, ROOM_H/2),
        size=(WALL_THICK, ROOM_D, ROOM_H),
        material=mat_wall,
    )

    # Bench — wooden base (shifted east per PD floor plan)
    bench_base = make_box(
        "BenchBase",
        location=(BENCH_CENTER_X, -ROOM_D/2 + BENCH_D/2 + WALL_THICK/2,
                  BENCH_H/2),
        size=(BENCH_W, BENCH_D, BENCH_H),
        material=mat_bench_wood,
    )
    bench_cushion = make_box(
        "BenchCushion",
        location=(BENCH_CENTER_X, -ROOM_D/2 + BENCH_D/2 + WALL_THICK/2,
                  BENCH_H + BENCH_CUSHION_H/2),
        size=(BENCH_W - 0.1, BENCH_D - 0.1, BENCH_CUSHION_H),
        material=mat_bench_fabric,
    )
    bench_back = make_box(
        "BenchBackWood",
        location=(BENCH_CENTER_X, -ROOM_D/2 + WALL_THICK + 0.08,
                  (BENCH_H + WIN_BOTTOM_Z)/2),
        size=(BENCH_W + 0.4, 0.05, WIN_BOTTOM_Z - BENCH_H),
        material=mat_bench_wood,
    )
    # Frosted glass high windows above bench
    win = make_box(
        "FrostedWindowBand",
        location=(BENCH_CENTER_X, -ROOM_D/2 + WALL_THICK + 0.01,
                  WIN_BOTTOM_Z + WIN_H/2),
        size=(WIN_W, 0.04, WIN_H),
        material=mat_window_glass,
    )

    # Piano (WEST wall, southernmost position per PD)
    piano = make_box(
        "PianoBlackUpright",
        location=(-ROOM_W/2 + WALL_THICK/2 + PIANO_W/2,
                  PIANO_CENTER_Y, PIANO_H/2),
        size=(PIANO_W, PIANO_L, PIANO_H),
        material=mat_piano,
    )

    # Air conditioner (SW corner — west wall, south of piano, smaller floor-standing unit)
    mat_ac = add_material("ACWhite", (0.97, 0.97, 0.95))
    ac = make_box(
        "AirConditioner",
        location=(-ROOM_W/2 + WALL_THICK + AC_W/2,
                  -ROOM_D/2 + WALL_THICK + AC_D/2 + 0.1,
                  AC_H/2),
        size=(AC_W, AC_D, AC_H),
        material=mat_ac,
    )

    # TV stand + TV (NORTH wall, RIGHT side per PD)
    tv_stand = make_box(
        "TVStand",
        location=(TV_CENTER_X, ROOM_D/2 - WALL_THICK/2 - TV_STAND_D/2,
                  TV_STAND_H/2),
        size=(TV_STAND_W, TV_STAND_D, TV_STAND_H),
        material=mat_tv_stand,
    )
    tv = make_box(
        "TVScreen",
        location=(TV_CENTER_X, ROOM_D/2 - WALL_THICK/2 - 0.05,
                  TV_STAND_H + 0.05 + TV_H/2),
        size=(TV_W, 0.08, TV_H),
        material=mat_tv_screen,
    )

    # 할머니방 door — NORTH wall, EAST of TV
    mat_door = add_material("DoorWood", (0.55, 0.40, 0.28))
    halmoni_door = make_box(
        "HalmoniBangDoor",
        location=(HALMONI_DOOR_X,
                  ROOM_D/2 - WALL_THICK/2 - 0.03,
                  HALMONI_DOOR_H/2),
        size=(HALMONI_DOOR_W, 0.06, HALMONI_DOOR_H),
        material=mat_door,
    )

    # 현관 door — EAST wall, SOUTH end
    hyeongwan = make_box(
        "HyeongwanDoor",
        location=(ROOM_W/2 - WALL_THICK/2 - 0.03,
                  HYEONGWAN_CENTER_Y,
                  HYEONGWAN_H/2),
        size=(0.06, HYEONGWAN_W, HYEONGWAN_H),
        material=mat_door,
    )

    # Antique console + wall clock (SOUTH wall, east of bench — same wall)
    console = make_box(
        "AntiqueConsoleSouth",
        location=(CONSOLE_CENTER_X,
                  -ROOM_D/2 + WALL_THICK + CONSOLE_D/2,
                  CONSOLE_H/2),
        size=(CONSOLE_W, CONSOLE_D, CONSOLE_H),
        material=mat_console,
    )
    # Wall clock above console
    clock = make_box(
        "WallClock",
        location=(CONSOLE_CENTER_X,
                  -ROOM_D/2 + WALL_THICK + 0.05,
                  CONSOLE_H + 0.4 + 0.2),
        size=(0.3, 0.04, 0.4),
        material=mat_console,
    )

    # Leo's oval cardboard scratcher (on floor near piano)
    scratcher = make_box(
        "LeoScratcherBed",
        location=(-ROOM_W/2 + WALL_THICK + PIANO_W + 0.4,
                  PIANO_CENTER_Y - 0.4,
                  SCRATCHER_H/2),
        size=(SCRATCHER_W, SCRATCHER_D, SCRATCHER_H),
        material=mat_scratcher,
    )

    # L자형 점프 스크래처 — next to piano (west wall, south area)
    make_box(
        "LeoJumpScratcherL",
        location=(-ROOM_W/2 + WALL_THICK + 0.15,
                  PIANO_CENTER_Y + 0.55,
                  1.0/2),
        size=(0.10, 0.35, 1.0),
        material=mat_scratcher,
    )

    # Robot vacuum — near 화장실 (bathroom) door per PD
    # Bathroom is east of living room — robot vacuum docks against east wall, near bathroom entrance
    mat_robot = add_material("RobotVacuumDark", (0.15, 0.15, 0.17))
    make_box(
        "RobotVacuumDock",
        location=(ROOM_W/2 - WALL_THICK - 0.2,
                  ROOM_D/2 - 0.7,
                  0.05),
        size=(0.35, 0.35, 0.10),
        material=mat_robot,
    )

    # ── Kitchen + dining + pet feeding area (north of living room) ──
    mat_floor_kitchen = add_material("KitchenFloorTile", (0.93, 0.91, 0.86))
    mat_counter = add_material("CounterWhiteGloss", (0.97, 0.96, 0.94))
    mat_subway = add_material("CobaltSubwayTile", (0.10, 0.20, 0.45))
    mat_dining_wood = add_material("DiningTableLightWood", (0.78, 0.62, 0.40))
    mat_chair = add_material("ChairWhite", (0.94, 0.92, 0.88))
    mat_bowl_metal = add_material("BowlStainless", (0.78, 0.78, 0.80))
    mat_bowl_ceramic = add_material("BowlCeramicWhite", (0.96, 0.95, 0.92))
    mat_feeder_stand = add_material("FeederStandWood", (0.85, 0.75, 0.55))

    # Kitchen floor
    make_box(
        "KitchenFloor",
        location=(0, (KITCHEN_Y_S + KITCHEN_Y_N)/2, -0.01),
        size=(KITCHEN_W, KITCHEN_D, 0.02),
        material=mat_floor_kitchen,
    )
    make_box(
        "KitchenCeiling",
        location=(0, (KITCHEN_Y_S + KITCHEN_Y_N)/2, ROOM_H + 0.01),
        size=(KITCHEN_W, KITCHEN_D, 0.02),
        material=mat_ceiling,
    )

    # Kitchen north wall (the far wall)
    make_box(
        "KitchenWallNorth",
        location=(0, KITCHEN_Y_N, ROOM_H/2),
        size=(KITCHEN_W, WALL_THICK, ROOM_H),
        material=mat_wall,
    )
    # Kitchen east + west walls (share with living room)
    make_box(
        "KitchenWallEast",
        location=(KITCHEN_W/2, (KITCHEN_Y_S + KITCHEN_Y_N)/2, ROOM_H/2),
        size=(WALL_THICK, KITCHEN_D, ROOM_H),
        material=mat_wall,
    )
    make_box(
        "KitchenWallWest",
        location=(-KITCHEN_W/2, (KITCHEN_Y_S + KITCHEN_Y_N)/2, ROOM_H/2),
        size=(WALL_THICK, KITCHEN_D, ROOM_H),
        material=mat_wall,
    )

    # PD spec (latest): ㄱ자 (L-shape) counter, CONNECTED at corner.
    # North arm + East arm meet — east arm is INTERIOR (not against east wall).
    COUNTER_D = 0.6
    COUNTER_H = 0.9
    L_CORNER_X = 1.0   # the L bends at this x-coordinate (interior of kitchen)

    # ── North arm (E-W, along north wall): sink (west) + range (east) ──
    counter_n_west_x = -KITCHEN_W/2 + WALL_THICK
    counter_n_length = L_CORNER_X - counter_n_west_x
    counter_n_center_x = (counter_n_west_x + L_CORNER_X) / 2
    counter_n_y = KITCHEN_Y_N - WALL_THICK/2 - COUNTER_D/2
    make_box(
        "KitchenCounterNorth",
        location=(counter_n_center_x, counter_n_y, COUNTER_H/2),
        size=(counter_n_length, COUNTER_D, COUNTER_H),
        material=mat_counter,
    )
    # Cobalt subway tile backsplash above north counter
    make_box(
        "CobaltSubwayBacksplashNorth",
        location=(counter_n_center_x, KITCHEN_Y_N - WALL_THICK - 0.01,
                  COUNTER_H + 0.4),
        size=(counter_n_length, 0.04, 0.8),
        material=mat_subway,
    )
    # Sink — west portion of north counter
    SINK_W = 0.8
    make_box(
        "Sink",
        location=(counter_n_center_x - counter_n_length/2 + 0.5,
                  counter_n_y, COUNTER_H + 0.02),
        size=(SINK_W, COUNTER_D - 0.1, 0.04),
        material=add_material("SinkStainless", (0.75, 0.76, 0.78)),
    )
    # Gas range — east portion of north counter
    RANGE_W = 0.6
    make_box(
        "GasRange",
        location=(counter_n_center_x + counter_n_length/2 - 0.5,
                  counter_n_y, COUNTER_H + 0.05),
        size=(RANGE_W, COUNTER_D - 0.05, 0.08),
        material=mat_subway,
    )

    # ── East arm (N-S, peninsula): joins north arm at the L corner ──
    # East arm x_center placed so its EAST edge aligns with L_CORNER_X (forming a clean inside corner)
    counter_e_center_x = L_CORNER_X - COUNTER_D/2
    # Length from corner going south, ending near opening (leave space for fridge + walking)
    counter_e_south_y = KITCHEN_Y_S + 0.9   # don't reach all the way to opening
    counter_e_north_y = counter_n_y - COUNTER_D/2 + COUNTER_D/2  # continuous with north arm
    counter_e_length = counter_e_north_y - counter_e_south_y
    counter_e_center_y = (counter_e_north_y + counter_e_south_y) / 2
    make_box(
        "KitchenCounterEast",
        location=(counter_e_center_x, counter_e_center_y, COUNTER_H/2),
        size=(COUNTER_D, counter_e_length, COUNTER_H),
        material=mat_counter,
    )
    # Cabinet (upper, hanging) over the south end of east arm
    make_box(
        "EastCabinetUpper",
        location=(counter_e_center_x, counter_e_south_y + 0.6,
                  COUNTER_H + 0.6 + 0.4),
        size=(COUNTER_D - 0.05, 1.0, 0.8),
        material=mat_counter,
    )
    # Fridge at the SOUTH end of east arm (faces south toward TV-stand wall)
    FRIDGE_W = 0.7
    FRIDGE_D = 0.7
    FRIDGE_H = 1.85
    mat_fridge = add_material("FridgeStainless", (0.82, 0.83, 0.84))
    make_box(
        "Fridge",
        location=(counter_e_center_x,
                  counter_e_south_y - FRIDGE_D/2 - 0.05,
                  FRIDGE_H/2),
        size=(FRIDGE_W, FRIDGE_D, FRIDGE_H),
        material=mat_fridge,
    )

    counter_north_y = counter_n_y  # for island clamp

    # Dining table — LONG AXIS = NORTH-SOUTH, 6-seater (3+3 per PD)
    DINING_W = 1.1    # east-west — bigger than before per PD
    DINING_D = 2.4    # north-south long axis — also bigger
    DINING_H = 0.75
    # Aligned with kitchen opening so the sofa sees the full table
    dining_center_x = KITCHEN_OPENING_CENTER_X  # = -1.5
    # Pushed north a bit to leave room for the pet mini-table south of it
    dining_center_y = KITCHEN_Y_S + 0.55 + DINING_D/2
    make_box(
        "DiningTable",
        location=(dining_center_x, dining_center_y, DINING_H),
        size=(DINING_W, DINING_D, 0.05),
        material=mat_dining_wood,
    )
    # Table legs (corners)
    for dx in (-DINING_W/2 + 0.1, DINING_W/2 - 0.1):
        for dy in (-DINING_D/2 + 0.1, DINING_D/2 - 0.1):
            make_box(
                f"DiningLeg_{dx:+.2f}_{dy:+.2f}",
                location=(dining_center_x + dx, dining_center_y + dy,
                          DINING_H/2),
                size=(0.06, 0.06, DINING_H),
                material=mat_dining_wood,
            )
    # 6 dining chairs — 3 on EAST side, 3 on WEST side
    CHAIR_W = 0.45
    CHAIR_D = 0.45
    CHAIR_H = 0.45
    CHAIR_BACK_H = 0.5
    chair_y_offsets = (-0.65, 0.0, +0.65)  # 3 chairs evenly spaced along 2.1m
    chair_positions = []
    for y_off in chair_y_offsets:
        # West side (chairs facing east toward table)
        chair_positions.append(
            (dining_center_x - DINING_W/2 - 0.35, dining_center_y + y_off, +1)
        )
        # East side (chairs facing west toward table)
        chair_positions.append(
            (dining_center_x + DINING_W/2 + 0.35, dining_center_y + y_off, -1)
        )
    for i, (cx, cy, face) in enumerate(chair_positions):
        make_box(f"Chair_{i}_Seat",
                 location=(cx, cy, CHAIR_H/2),
                 size=(CHAIR_W, CHAIR_D, 0.05),
                 material=mat_chair)
        back_offset = -face * (CHAIR_W/2 - 0.04)
        make_box(f"Chair_{i}_Back",
                 location=(cx + back_offset, cy, CHAIR_H + CHAIR_BACK_H/2),
                 size=(0.04, CHAIR_D, CHAIR_BACK_H),
                 material=mat_chair)

    # ── Pet feeding mini-table ── E-W axis, IN FRONT of dining table (south)
    # Leo's bowls go ON TOP (raised), Ryani's bowls UNDER (on the floor below).
    PET_TABLE_W = DINING_W   # aligned with dining table width per PD
    PET_TABLE_D = 0.4   # north-south short
    PET_TABLE_H = 0.35  # short table — high enough for Ryani bowls under
    pet_table_center_x = dining_center_x  # aligned with dining
    pet_table_center_y = KITCHEN_Y_S + 0.25  # very close to opening (just inside kitchen)
    make_box(
        "PetMiniTable",
        location=(pet_table_center_x, pet_table_center_y, PET_TABLE_H),
        size=(PET_TABLE_W, PET_TABLE_D, 0.04),
        material=mat_feeder_stand,
    )
    for dx in (-PET_TABLE_W/2 + 0.05, PET_TABLE_W/2 - 0.05):
        for dy in (-PET_TABLE_D/2 + 0.05, PET_TABLE_D/2 - 0.05):
            make_box(
                f"PetTableLeg_{dx:+.2f}_{dy:+.2f}",
                location=(pet_table_center_x + dx,
                          pet_table_center_y + dy, PET_TABLE_H/2),
                size=(0.04, 0.04, PET_TABLE_H),
                material=mat_feeder_stand,
            )
    # Leo's bowls ON TOP of mini-table (2 stainless = food + water)
    for dx_b in (-0.18, 0.18):
        make_box(
            f"LeoBowl_{dx_b:+.2f}",
            location=(pet_table_center_x + dx_b,
                      pet_table_center_y, PET_TABLE_H + 0.04),
            size=(0.20, 0.20, 0.07),
            material=mat_bowl_metal,
        )
    # Ryani's bowls UNDER the mini-table (on the floor)
    for dx_b in (-0.18, 0.18):
        make_box(
            f"RyaniBowl_{dx_b:+.2f}",
            location=(pet_table_center_x + dx_b,
                      pet_table_center_y, 0.04),
            size=(0.20, 0.20, 0.07),
            material=mat_bowl_ceramic,
        )

    # ── Island table — E-W axis, BEHIND dining table (north) ──
    # Per PD: island is TALLER than dining table (bar-height counter style).
    ISLAND_W = 1.6   # east-west long
    ISLAND_D = 0.7   # north-south short
    ISLAND_H = 1.05  # taller than DINING_H (0.75)
    island_center_x = dining_center_x
    island_center_y = dining_center_y + DINING_D/2 + 0.5 + ISLAND_D/2
    # Clamp so it doesn't crash into the counter on north wall
    max_island_north_y = counter_north_y - COUNTER_D/2 - 0.6 - ISLAND_D/2
    if island_center_y > max_island_north_y:
        island_center_y = max_island_north_y
    make_box(
        "IslandTable",
        location=(island_center_x, island_center_y, ISLAND_H),
        size=(ISLAND_W, ISLAND_D, 0.06),
        material=mat_dining_wood,
    )
    # Island base / cabinet under
    make_box(
        "IslandBase",
        location=(island_center_x, island_center_y, (ISLAND_H - 0.05)/2),
        size=(ISLAND_W - 0.05, ISLAND_D - 0.05, ISLAND_H - 0.05),
        material=mat_counter,
    )

    # Sun light (representing frosted window daylight)
    bpy.ops.object.light_add(type="SUN", location=(0, -3, 4))
    sun = bpy.context.active_object
    sun.data.energy = 3.0
    sun.data.color = (1.0, 0.96, 0.88)
    sun.rotation_euler = (1.0, 0, 0)  # angled toward room from south

    # Ambient world light
    world = bpy.context.scene.world
    if world and world.use_nodes:
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs["Color"].default_value = (0.85, 0.83, 0.80, 1.0)
            bg.inputs["Strength"].default_value = 0.6


def setup_cameras():
    """Add the canonical POV cameras (A/B/C)."""
    cams = []

    # POV-A: north → south, facing sofa, pet eye-level
    bpy.ops.object.camera_add(
        location=(0, ROOM_D/2 - 0.5, 0.35),
        rotation=(1.45, 0, 0),  # tilt down slightly
    )
    cam_a = bpy.context.active_object
    cam_a.name = "Cam_POV_A_facing_sofa"
    cam_a.data.lens = 28  # wide
    cams.append(cam_a)

    # POV-B: south → north, facing TV
    bpy.ops.object.camera_add(
        location=(0, -ROOM_D/2 + 0.5, 0.35),
        rotation=(1.45, 0, 3.14159),  # 180° around Z
    )
    cam_b = bpy.context.active_object
    cam_b.name = "Cam_POV_B_facing_TV"
    cam_b.data.lens = 28
    cams.append(cam_b)

    # POV-C east: center facing east (toward 현관)
    bpy.ops.object.camera_add(
        location=(-1.0, 0, 0.35),
        rotation=(1.45, 0, -1.5708),
    )
    cam_c_east = bpy.context.active_object
    cam_c_east.name = "Cam_POV_C_facing_east"
    cam_c_east.data.lens = 35
    cams.append(cam_c_east)

    # POV-C west: center facing west (toward piano)
    bpy.ops.object.camera_add(
        location=(1.0, 0, 0.35),
        rotation=(1.45, 0, 1.5708),
    )
    cam_c_west = bpy.context.active_object
    cam_c_west.name = "Cam_POV_C_facing_west"
    cam_c_west.data.lens = 35
    cams.append(cam_c_west)

    # POV-D: kitchen feeding station, pet eye-level facing east toward feeder
    bpy.ops.object.camera_add(
        location=(0, KITCHEN_Y_S + KITCHEN_D/2, 0.35),
        rotation=(1.45, 0, -math.pi/2),
    )
    cam_d = bpy.context.active_object
    cam_d.name = "Cam_POV_D_kitchen_feeding"
    cam_d.data.lens = 28
    cams.append(cam_d)
    return cams


def setup_render():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 256
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 720
    scene.render.resolution_y = 1280
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False


def render_all_povs(cameras):
    out_dir = Path(bpy.path.abspath("//")).parent / "renders"
    if not str(out_dir).startswith("/"):
        out_dir = Path("/Users/ahnbingbing/code/rianileo-agent/assets/3d/renders")
    out_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    for cam in cameras:
        scene.camera = cam
        scene.render.filepath = str(out_dir / f"{cam.name}.png")
        bpy.ops.render.render(write_still=True)
        print(f"  ✓ rendered {cam.name}")


def main():
    if not INSIDE_BLENDER:
        return
    clean_scene()
    build_room()
    cameras = setup_cameras()
    setup_render()
    # Save the blend file
    blend_path = "/Users/ahnbingbing/code/rianileo-agent/assets/3d/models/grandma_livingroom.blend"
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"saved {blend_path}")
    # Render all POVs
    render_all_povs(cameras)
    print("done.")


if __name__ == "__main__":
    main()
