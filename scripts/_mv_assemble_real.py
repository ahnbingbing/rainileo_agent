"""Real-footage original MV: actual Ryani&Leo clips (from GCS, RAG/SQL-picked) + our Suno song
+ karaoke captions + polaroid stack + channel outro bumper. PD 2026-10-29 (A+B + bumper).

DRAFT: trim windows on the long source clips are crude (fixed offset) — smarter salient-moment
selection is the next iteration. Finale reuses the nap-together clip (reliably both-in-frame).
"""
import subprocess
from pathlib import Path

ROOT = Path("/Users/ahnbingbing/code/rianileo-agent")
MV = ROOT / "data/tmp/mv"; R = MV/"real2"
FONT = Path.home() / "Library/Fonts/Pretendard-Bold.otf"
BUMPER = ROOT / "assets/branding/outro_bumper.mp4"
W, H = 1080, 1920
SONG = MV / "song_ours.mp3"
TOTAL = 32.3          # end right after "행복하자" (31.4) — no dead instrumental tail at all

CUTS = [   # (name, source, seconds, freeze_tail)
    ("r1", R/"seg1.mp4", 5.5, False),
    ("r2", R/"seg2.mp4", 3.2, False),
    ("r3", R/"seg3.mp4", 3.5, False),
    ("r4", R/"seg4.mp4", 4.4, False),
    ("r5", R/"seg5.mp4", 3.5, False),
    ("r6", R/"seg6.mp4", 3.2, False),
    ("r7", R/"seg7.mp4", 4.0, False),
    ("r8", R/"seg8.mp4", 5.0, False),   # '둘이 포근 쉬기' finale — real motion + slow push-in, then fade
]
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
        print("ERR:", " ".join(str(c) for c in cmd)[:150]); print(r.stderr[-800:]); raise SystemExit(1)


def main():
    work = MV/"asm3"; work.mkdir(exist_ok=True)
    segs, pols, starts = [], [], []; t = 0.0
    # ONLY geometry: 9:16 scale/crop (format) + CFR fps=30 (concat needs it; VFR breaks caption
    # sync). ★NO color/saturation/grade of any kind — PD wants the ORIGINAL clip look untouched.
    GEOM = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30"
    for name, src, dur, freeze in CUTS:
        src = src if src.exists() else R/"seg1.mp4"
        seg = work/f"{name}.mp4"
        d = min(dur, 4.9)
        run(["ffmpeg","-y","-ss","0.1","-t",f"{d}","-i",str(src),"-vf",GEOM,
             "-an","-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuv420p",str(seg)])
        segs.append(seg)
        if len(pols) < 7:
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
        fc.append(f"{last}[p{i}]overlay=x={x}:y={y}:enable='gte(t,{appear:.2f})'[v{i}]"); last=f"[v{i}]"
    withpol = work/"withpol.mp4"
    run(["ffmpeg","-y",*inputs,"-filter_complex",";".join(fc),"-map",last,
         "-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p","-r","30","-fps_mode","cfr","-t",f"{TOTAL}",str(withpol)])

    draws = []
    for j,(txt,s,e) in enumerate(CAPTIONS):
        cf = work/f"cap{j}.txt"; cf.write_text(txt, encoding="utf-8")
        a = f"if(lt(t,{s+0.2}),(t-{s})/0.2,if(gt(t,{e-0.25}),max(0,({e}-t)/0.25),1))"
        draws.append(f"drawtext=fontfile='{FONT}':textfile='{cf}':fontsize=60:fontcolor=white:"
                     f"borderw=6:bordercolor=black:shadowcolor=black@0.5:shadowx=4:shadowy=4:"
                     f"box=1:boxcolor=black@0.42:boxborderw=18:x=(w-text_w)/2:y=h-text_h-205:"
                     f"enable='between(t,{s},{e})':alpha='{a}'")
    mv = work/"mv_nobumper.mp4"
    run(["ffmpeg","-y","-i",str(withpol),"-i",str(SONG),
         "-filter_complex",f"[0:v]{','.join(draws)},fade=t=out:st={TOTAL-1.2:.2f}:d=1.2[v]","-map","[v]","-map","1:a:0",
         "-af",f"afade=t=in:st=0:d=0.3,afade=t=out:st={TOTAL-2.2:.2f}:d=2.2",
         "-c:v","libx264","-preset","medium","-crf","18","-pix_fmt","yuv420p","-r","30","-fps_mode","cfr",
         "-c:a","aac","-b:a","192k","-t",f"{TOTAL}","-movflags","+faststart",str(mv)])

    # append channel outro bumper (normalize to 1080x1920 + ensure an audio track)
    final = MV/"ryani_leo_mv_real.mp4"
    if BUMPER.exists():
        bmp = work/"bumper_norm.mp4"
        run(["ffmpeg","-y","-i",str(BUMPER),
             "-f","lavfi","-t","3","-i","anullsrc=channel_layout=stereo:sample_rate=44100",
             "-vf",GEOM,"-map","0:v:0","-map","1:a:0","-shortest",   # GEOM = geometry only, NO color on the bumper
             "-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k",str(bmp)])
        cl2 = work/"concat2.txt"; cl2.write_text(f"file '{mv}'\nfile '{bmp}'\n", encoding="utf-8")
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(cl2),
             "-c:v","libx264","-preset","medium","-crf","18","-pix_fmt","yuv420p",
             "-c:a","aac","-b:a","192k","-movflags","+faststart",str(final)])
    else:
        final.write_bytes(mv.read_bytes())
    print("DONE →", final)


if __name__ == "__main__":
    main()
