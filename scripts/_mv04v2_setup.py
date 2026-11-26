#!/usr/bin/env python3
"""Seed mv04v2 (fresh slot) with the PD-approved hand-authored storyboard:
neon night drive + mood-fit AV imagination (light trails, synthwave grid, beat-sync pulse,
dreamy reflections), a mid-song FLIGHT centerpiece on the '별빛이 네온으로 번져와' hook, and the
aviator + moon finale. Keeps shipped mv04 + mv04e2e intact. Durations are beat-mapped via
set_cut_durations (each cut's `lyric` = its karaoke line). Then: stills → full QC → i2v."""
import json, shutil
from pathlib import Path
from agents.mv_producer import load_manifest, save_manifest, mv_dir, _new_cut
from agents.mv_rf_matcher import set_cut_durations

SRC, DST = "mv04", "mv04v2"
L1 = "네온이 흐르는 미드나잇, 위이잉 붕붕 요동치는 심장"
L2 = "랴니가 쿨하게 운전대를 잡고, 도시의 불빛 따라 달려가"
L3 = "레오는 조수석, 창밖 바람슝슝 속 두리번 두 눈 반짝"
L4 = "무꼬리 랴니의 위글, 뒷좌석엔 조용한 온기만 가득해"
L5 = "붕붕 바람이 춤을 추고, 별빛이 네온으로 번져와"
L6 = "네온 미드나잇 크루즈, 골골골 속삭여 봐"
L7 = "교차로 위 살짝 미소 짓는 우리, 밤을 가르며 흘러"
L8 = "엔진과 얽힌 숨소리, 도시는 여전히 반짝 (골골골)"

SKY = "s2_sky"; RD = "s1_boulevard"
# (tag, lyric, subject, shot, cam, scene, still_prompt, motion_prompt, killpoint)
CUTS = [
 ("c01_heartbeat_cu", L1, "both", "CU", "static hold", RD,
  "Ryani at the wheel and Leo beside her in the navy convertible, the dashboard neon glowing and pulsing like a heartbeat, blue and magenta city neon on their faces",
  "The dashboard neon pulses to the beat and the whole car bounces gently on the bass; both pets' ears perk", False),
 ("c02_equalizer_insert", L1, "both", "insert", "static hold", RD,
  "Insert of the car dashboard — a glowing neon equalizer / VU bar display in cyan and magenta",
  "The neon equalizer bars flash and pulse in time with the music", False),
 ("c03_launch_ws_trails", L1, "both", "WS", "lateral tracking", RD,
  "The navy convertible pulling away down a neon-drenched midnight boulevard, its wheels leaving glowing neon light trails on the wet asphalt",
  "The car surges forward leaving neon light trails on the road", False),
 ("c04_ryani_drive_ms", L2, "ryani", "MS", "lateral tracking", RD,
  "Ryani the black French bulldog driving the convertible, cool and focused, neon light flowing across her face, shoulders grooving",
  "Ryani steadies the wheel and bobs her shoulders to the beat", False),
 ("c05_paw_wheel_insert", L2, "ryani", "insert", "static hold", RD,
  "Close insert of Ryani's black paw on the chrome steering wheel, neon magenta and blue rippling across the chrome",
  "Neon reflections ripple across the chrome as her paw taps lightly", False),
 ("c06_neonriver_ws", L2, "ryani", "WS", "lateral tracking", RD,
  "The convertible side-on racing along the boulevard, the neon city mirrored in the wet road below like a flowing river of light",
  "Camera tracks laterally as the glowing city streams by, reflections rippling below", False),
 ("c07_leo_window_cu", L3, "leo", "CU", "static hold", RD,
  "Leo the orange tabby cat at the passenger window, fur and whiskers streaming in the neon wind, eyes wide and bright",
  "Leo's fur ruffles in the wind and his eyes dart, tracking the lights", False),
 ("c08_leo_eye_ecu", L3, "leo", "ECU", "push-in", RD,
  "Extreme close-up of Leo's amber cat eye, the neon city reflected and strobing inside his iris",
  "Neon reflections strobe across his glistening eye, then a slow blink", False),
 ("c09_ryani_wink_cu", L4, "ryani", "CU", "static hold", RD,
  "Ryani the black French bulldog at the wheel, turning to the camera with a charming wink, neon glow on her face",
  "Ryani winks one eye at the camera with a playful glint", False),
 ("c10_blanket_insert", L4, "ryani", "insert", "tilt-up", RD,
  "Insert of a soft blanket rumpled on the empty back seat, gentle neon glints on its folds",
  "Neon light gently shifts across the blanket's folds", False),
 ("c11_liftoff_ws", L5, "both", "WS", "tilt-up", SKY,
  "The navy convertible lifting UP off the neon boulevard into the air, its wheels trailing upward streaks of neon light, the glowing city below",
  "The car rises off the road, neon trails streaming upward; camera tilts up following it", False),
 ("c12_soar_aerial", L5, "both", "WS", "crane", SKY,
  "Aerial wide shot — the convertible flying high over the glowing neon city grid that shrinks far below, neon stars streaking past",
  "The car glides through the night sky, the neon city shrinking below, stars streaking", False),
 ("c13_flight_ms_trails", L5, "both", "MS", "lateral tracking", SKY,
  "Inside the flying convertible high in the night sky, neon light trails streaming from the pets' ears and fur, starlight bleeding into neon all around them",
  "Both pets bob their heads to the beat, light trailing from their ears as they soar", False),
 ("c14_nebula_cruise_ws", L6, "both", "WS", "lateral tracking", SKY,
  "The convertible cruising through a dreamy field of neon nebula and stars high above the city, a huge glowing neon moon in the sky",
  "The car drifts smoothly through glowing neon clouds; gentle gliding", False),
 ("c15_equalizer_beat_insert", L6, "both", "insert", "static hold", SKY,
  "Insert of the dashboard neon equalizer bars pulsing in time with the music, cyan and magenta",
  "The equalizer bars pulse rhythmically to the purr-beat", False),
 ("c16_hook_faces_cu", L6, "both", "CU", "push-in", SKY,
  "Close-up of Ryani and Leo's faces side by side, neon glow on them, eyes soft and half-closed, mouths barely parted as if whispering the song",
  "Both softly mouth the lyric to the camera, eyes gently narrowing", False),
 ("c17_descend_ws", L7, "both", "WS", "crane", RD,
  "The convertible gliding gently back DOWN to the neon boulevard, settling onto the wet road, neon trails easing",
  "The car descends smoothly to the road; camera follows it down", False),
 ("c18_crossroad_kill_ws", L7, "both", "WS", "static hold", RD,
  "The convertible paused at a glowing neon intersection, Ryani and Leo both looking to the camera with a soft content expression, headlights blazing",
  "The pets look to camera with a subtle warm expression; headlights flare", True),
 ("c19_pawtap_ms", L7, "both", "MS", "push-in", RD,
  "Inside the car as it flows through the night, Ryani's and Leo's front paws tapping side by side on the neon-lit dashboard to the groove",
  "Their front paws tap the dashboard in sync to the beat", False),
 ("c20_aviator_reveal_cu", L8, "both", "CU", "static hold", RD,
  "Ryani and Leo now wearing Ray-Ban aviator sunglasses, cool and content, neon reflections across the dark lenses, faint warm breath in the cold neon air",
  "Both sit cool in their aviators; a wisp of breath curls; a slight nod to the beat", False),
 ("c21_moon_rear_ws", L8, "both", "WS", "static hold", SKY,
  "Rear view — the convertible lifting off again and flying toward a huge glowing neon moon, the two pets as silhouettes, taillights trailing",
  "The car rises toward the moon, taillights streaking; camera holds on the rear silhouette", False),
 ("c22_lookback_ms", L8, "both", "MS", "static hold", SKY,
  "From behind, Ryani and Leo bring their faces together between the driver and passenger seats and look back over their shoulders toward the camera, both in aviator sunglasses, the giant neon moon growing behind them",
  "The two lean their faces together and turn to look back at the camera", False),
 ("c23_glassesoff_wink_cu", L8, "both", "CU", "static hold", SKY,
  "Tight close-up of Ryani and Leo together taking off their aviator sunglasses in unison and giving a wink to the camera, neon moonlight on their faces",
  "They slip off their sunglasses together and wink at the camera, then hold", False),
]

AD = ("The SAME single retro 1980s open-top convertible in every shot — deep navy blue body, chrome "
      "trim and chrome wire wheels, LEFT-HAND DRIVE (steering wheel on the LEFT). Ryani the black "
      "TAILLESS French bulldog always in the driver seat (LEFT), Leo the orange tabby in the passenger "
      "seat (RIGHT). Always deep neon midnight — blue/violet/cyan city glow with magenta accents, never "
      "sunset/day/gold horizon. When the car flies it is the SAME car lifting into the same neon night "
      "— the world never changes, only the car leaves the road. (Pets wear NO sunglasses unless a shot "
      "explicitly says so.)")
SCENES = [
 {"scene_id": RD, "setting": "a neon-drenched downtown boulevard at deep midnight, wet asphalt reflecting cyan and magenta neon signage"},
 {"scene_id": SKY, "setting": "high in the night sky above the neon city at deep midnight, the glowing city grid shrinking far below, neon stars and a huge neon moon"},
]

m = load_manifest(SRC)
m["slug"] = DST
m["art_direction"] = AD
m["scenes"] = SCENES
m["output"] = None
m["gates"] = {}
m["cuts"] = [_new_cut(tag, subject=subj, shot_size=shot, camera_move=cam, scene_id=scene,
                      cut_type="av", still_strategy="av_ref_lock", action=sp,
                      still_prompt=sp, motion_prompt=mp, is_killpoint=kp, lyric=lyr)
             for (tag, lyr, subj, shot, cam, scene, sp, mp, kp) in CUTS]
set_cut_durations(m)
m["stage"] = "director"   # cuts seeded; resume runs rf_match(→beat already set)→board→render→assemble
dst_dir = mv_dir(DST); dst_dir.mkdir(parents=True, exist_ok=True)
src_dir = mv_dir(SRC)
for f in ("edit_template.json", "title.png"):
    if (src_dir / f).exists():
        shutil.copy(src_dir / f, dst_dir / f)
save_manifest(m)
print(f"mv04v2 seeded: {len(m['cuts'])} cuts, body={sum(c['duration'] for c in m['cuts']):.1f}s")
for c in m["cuts"]:
    print(f"  {c['tag']:26} {c['shot_size']:6} {c['scene_id']:12} dur={c['duration']:.2f} {'★' if c['is_killpoint'] else ''}")
