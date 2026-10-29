"""Scratch assembler for the '그래, 포레스티아' music-video hook draft (PD 2026-10-29).

v2 (PD feedback): captions are KARAOKE-SYNCED to the SONG's vocal timestamps (NOT tied to
cut length), the section runs to 1:12 so "행복하자" fully rings out + fades (no abrupt cut),
and cut2 is re-rendered tailless. Cuts provide visuals only; a polaroid "memory stack"
grows top-left; song hook 0:36→1:12.5.

  python3 scripts/_mv_assemble.py
"""
import subprocess
from pathlib import Path

ROOT = Path("/Users/ahnbingbing/code/rianileo-agent")
MV = ROOT / "data/tmp/mv"
FONT = Path.home() / "Library/Fonts/Pretendard-Bold.otf"
W, H = 1080, 1920
SONG_START = 36.0          # hook section start in the song
TOTAL = 36.5               # section length → ends 1:12.5, after "행복하자" resolves
SRC_LEN = 5.0              # every Seedance cut is 5s

# Visual cuts only (name, source, on-screen seconds). Durations sum to TOTAL; a slot longer
# than the 5s source is gently slowed to fill (setpts), never frozen.
CUTS = [
    ("c1", MV/"sample_cut1.mp4", 4.5),
    ("c2", MV/"cut2.mp4",        4.5),
    ("c3", MV/"sample_walk.mp4", 4.5),
    ("c4", MV/"cut4.mp4",        6.5),
    ("c5", MV/"cut5.mp4",        5.0),
    ("c6", MV/"cut6.mp4",        5.5),
    ("c7", MV/"cut7.mp4",        6.0),
]
FALLBACK = MV/"sample_walk.mp4"

# Captions synced to the SONG vocal (start,end in SECTION time = song time − 36.0), from the
# ko auto-caption timestamps. Each shows exactly while that lyric is sung.
CAPTIONS = [
    ("그래, 포레스티아",        0.00,  4.00),
    ("추억이 쌓여가네",         4.08,  8.80),
    ("그래, 바로 여기야",       8.80, 12.28),
    ("참 잘 왔다, 이곳에",     12.28, 18.48),
    ("하루하루 지나가고 계절도", 18.48, 22.04),
    ("흘러가고, 사랑도 변해가지만", 22.04, 27.28),
    ("오래오래",               27.28, 30.80),
    ("행복하자",               30.80, 36.30),
]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FFMPEG ERR:", " ".join(str(c) for c in cmd)[:180]); print(r.stderr[-900:])
        raise SystemExit(1)


def main():
    work = MV/"asm"; work.mkdir(exist_ok=True)
    segs, pols, starts, durs = [], [], [], []
    t = 0.0
    for i, (name, src, dur) in enumerate(CUTS):
        src = src if src.exists() else FALLBACK
        seg = work/f"{name}_seg.mp4"
        base = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
        if dur <= SRC_LEN - 0.1:                       # trim to slot (normal speed)
            run(["ffmpeg","-y","-ss","0.15","-t",f"{dur}","-i",str(src),"-vf",base,
                 "-an","-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p",str(seg)])
        else:                                          # slow the full clip to fill the slot
            run(["ffmpeg","-y","-i",str(src),
                 "-vf",f"setpts=({dur}/{SRC_LEN})*PTS,{base}","-t",f"{dur}",
                 "-an","-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p",str(seg)])
        segs.append(seg)
        pol = work/f"{name}_pol.png"
        rot = 6 if i % 2 == 0 else -6
        run(["ffmpeg","-y","-ss",f"{dur/2:.2f}","-i",str(seg),"-frames:v","1",
             "-vf",(f"scale=150:-1,pad=iw+14:ih+14:7:7:white,pad=iw+26:ih+46:13:9:white,"
                    f"format=rgba,rotate={rot}*PI/180:c=none:ow=rotw({rot}*PI/180):oh=roth({rot}*PI/180)"),
             str(pol)])
        pols.append(pol); starts.append(t); durs.append(dur); t += dur

    # concat visuals
    cl = work/"concat.txt"; cl.write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")
    body = work/"body.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(cl),
         "-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p",str(body)])

    # overlay growing polaroid memory-stack (top-left, fanning down-right)
    inputs = ["-i", str(body)]
    for p in pols:
        inputs += ["-loop","1","-i",str(p)]
    fc, last = [], "[0:v]"
    for i, st in enumerate(starts):
        appear = st + 0.8; x = 26 + i*18; y = 40 + i*26
        fc.append(f"[{i+1}:v]format=rgba,fade=t=in:st={appear:.2f}:d=0.25:alpha=1[p{i}]")
        out = f"[v{i}]"
        fc.append(f"{last}[p{i}]overlay=x={x}:y={y}:enable='gte(t,{appear:.2f})'{out}")
        last = f"[v{i}]"
    withpol = work/"withpol.mp4"
    run(["ffmpeg","-y",*inputs,"-filter_complex",";".join(fc),"-map",last,
         "-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p","-t",f"{TOTAL}",str(withpol)])

    # karaoke captions synced to the song + song hook audio with end fade
    draws = []
    for j,(txt,s,e) in enumerate(CAPTIONS):
        cf = work/f"cap{j}.txt"; cf.write_text(txt, encoding="utf-8")
        a = (f"if(lt(t,{s+0.22}),(t-{s})/0.22,if(gt(t,{e-0.25}),max(0,({e}-t)/0.25),1))")
        draws.append(f"drawtext=fontfile='{FONT}':textfile='{cf}':fontsize=60:fontcolor=white:"
                     f"borderw=6:bordercolor=black:shadowcolor=black@0.5:shadowx=4:shadowy=4:"
                     f"box=1:boxcolor=black@0.42:boxborderw=18:line_spacing=10:"
                     f"x=(w-text_w)/2:y=h-text_h-205:enable='between(t,{s},{e})':alpha='{a}'")
    final = MV/"forestia_hook_draft.mp4"
    run(["ffmpeg","-y","-i",str(withpol),"-ss",f"{SONG_START}","-t",f"{TOTAL}","-i",str(MV/'song.mp3'),
         "-filter_complex",f"[0:v]{','.join(draws)}[v]",
         "-map","[v]","-map","1:a:0",
         "-af",f"afade=t=in:st=0:d=0.4,afade=t=out:st={TOTAL-2.2:.2f}:d=2.2",
         "-c:v","libx264","-preset","medium","-crf","18","-pix_fmt","yuv420p",
         "-c:a","aac","-b:a","192k","-t",f"{TOTAL}","-movflags","+faststart",str(final)])
    print("DONE →", final, f"({TOTAL}s, captions song-synced, cut2 tailless)")


if __name__ == "__main__":
    main()
