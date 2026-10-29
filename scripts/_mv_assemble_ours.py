"""Assemble the FIRST fully-original MV: our Suno song '랴니와 레오' + reused Seedance cuts.

PD 2026-10-29. Reuses the 7 photoreal cuts rendered for the Forestia MV (warm Ryani&Leo
scenes), swaps in OUR song (data/tmp/mv/song_ours.mp3, Suno, lyrics = their real story), and
burns KARAOKE captions at Suno's word-aligned timestamps (grouped to lines). Polaroid memory
stack. Instrumental outro (~8s) held on a freeze of the final cozy frame.
"""
import subprocess
from pathlib import Path

ROOT = Path("/Users/ahnbingbing/code/rianileo-agent")
MV = ROOT / "data/tmp/mv"
FONT = Path.home() / "Library/Fonts/Pretendard-Bold.otf"
W, H = 1080, 1920
SONG = MV / "song_ours.mp3"
TOTAL = 39.6

# visual segments (reused cuts, on-screen seconds). Last cut is held frozen through the outro.
CUTS = [
    ("s1", MV/"sample_cut1.mp4", 5.0, False),
    ("s2", MV/"cut6.mp4",        3.5, False),
    ("s3", MV/"cut2.mp4",        4.0, False),
    ("s4", MV/"cut4.mp4",        4.3, False),
    ("s5", MV/"sample_walk.mp4", 3.8, False),
    ("s6", MV/"cut5.mp4",        4.2, False),
    ("s7", MV/"sample_cut1.mp4", 2.5, False),   # cozy nap reprise
    ("s8", MV/"cut7.mp4",       12.3, True),     # 5s play + freeze tail through the outro
]
FALLBACK = MV/"sample_walk.mp4"

# karaoke lines (cleaned from Suno alignedWords → data/tmp/mv/lines.json), start,end in seconds
CAPTIONS = [
    ("까만 털에 꼬리 없는 랴니",      2.20,  5.50),
    ("창가에서 하루를 보내",         5.65,  8.54),
    ("어느 날 찾아온 주황 고양이",    8.70, 12.10),
    ("레오가 우리 집 막내가 됐어",   12.20, 16.60),
    ("랴니와 레오 둘이 함께",        16.60, 19.90),
    ("햇살 아래 나란히 낮잠 자",     20.10, 23.20),
    ("생각은 달라도 마음은 하나",     23.30, 27.10),
    ("오래오래 함께 행복하자",       27.30, 31.40),
]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("ERR:", " ".join(str(c) for c in cmd)[:160]); print(r.stderr[-900:]); raise SystemExit(1)


def main():
    work = MV/"asm2"; work.mkdir(exist_ok=True)
    segs, pols, starts = [], [], []
    t = 0.0
    for name, src, dur, freeze in CUTS:
        src = src if src.exists() else FALLBACK
        seg = work/f"{name}.mp4"
        base = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
        if freeze:   # play ~5s then clone the last frame to fill the rest (outro hold)
            run(["ffmpeg","-y","-ss","0.1","-t","5.0","-i",str(src),
                 "-vf",f"{base},tpad=stop_mode=clone:stop_duration={dur-5.0:.2f}","-t",f"{dur}",
                 "-an","-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p",str(seg)])
        elif dur <= 4.9:
            run(["ffmpeg","-y","-ss","0.15","-t",f"{dur}","-i",str(src),"-vf",base,
                 "-an","-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p",str(seg)])
        else:
            run(["ffmpeg","-y","-i",str(src),"-vf",f"setpts=({dur}/5.0)*PTS,{base}","-t",f"{dur}",
                 "-an","-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p",str(seg)])
        segs.append(seg)
        if len(pols) < 7:   # a polaroid per scene (skip the long freeze tail)
            pol = work/f"{name}_pol.png"; rot = 6 if len(pols) % 2 == 0 else -6
            run(["ffmpeg","-y","-ss","1.0","-i",str(seg),"-frames:v","1",
                 "-vf",(f"scale=150:-1,pad=iw+14:ih+14:7:7:white,pad=iw+26:ih+46:13:9:white,"
                        f"format=rgba,rotate={rot}*PI/180:c=none:ow=rotw({rot}*PI/180):oh=roth({rot}*PI/180)"),
                 str(pol)])
            pols.append(pol); starts.append(t)
        t += dur

    cl = work/"concat.txt"; cl.write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")
    body = work/"body.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(cl),
         "-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p",str(body)])

    inputs = ["-i", str(body)]
    for p in pols: inputs += ["-loop","1","-i",str(p)]
    fc, last = [], "[0:v]"
    for i, st in enumerate(starts):
        appear = st + 0.6; x = 26 + i*18; y = 40 + i*26
        fc.append(f"[{i+1}:v]format=rgba,fade=t=in:st={appear:.2f}:d=0.25:alpha=1[p{i}]")
        fc.append(f"{last}[p{i}]overlay=x={x}:y={y}:enable='gte(t,{appear:.2f})'[v{i}]")
        last = f"[v{i}]"
    withpol = work/"withpol.mp4"
    run(["ffmpeg","-y",*inputs,"-filter_complex",";".join(fc),"-map",last,
         "-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p","-t",f"{TOTAL}",str(withpol)])

    draws = []
    for j,(txt,s,e) in enumerate(CAPTIONS):
        cf = work/f"cap{j}.txt"; cf.write_text(txt, encoding="utf-8")
        a = f"if(lt(t,{s+0.2}),(t-{s})/0.2,if(gt(t,{e-0.25}),max(0,({e}-t)/0.25),1))"
        draws.append(f"drawtext=fontfile='{FONT}':textfile='{cf}':fontsize=60:fontcolor=white:"
                     f"borderw=6:bordercolor=black:shadowcolor=black@0.5:shadowx=4:shadowy=4:"
                     f"box=1:boxcolor=black@0.42:boxborderw=18:line_spacing=10:"
                     f"x=(w-text_w)/2:y=h-text_h-205:enable='between(t,{s},{e})':alpha='{a}'")
    final = MV/"ryani_leo_mv_draft.mp4"
    run(["ffmpeg","-y","-i",str(withpol),"-i",str(SONG),
         "-filter_complex",f"[0:v]{','.join(draws)}[v]","-map","[v]","-map","1:a:0",
         "-af",f"afade=t=in:st=0:d=0.3,afade=t=out:st={TOTAL-2.5:.2f}:d=2.5",
         "-c:v","libx264","-preset","medium","-crf","18","-pix_fmt","yuv420p",
         "-c:a","aac","-b:a","192k","-t",f"{TOTAL}","-movflags","+faststart",str(final)])
    print("DONE →", final, f"({TOTAL}s, our song + karaoke sync)")


if __name__ == "__main__":
    main()
