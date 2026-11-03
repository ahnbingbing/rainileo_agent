"""RF structure regression — clip reuse, closer position, length budget.

The RF writer used to split one clip across non-adjacent beats; the same-clip collapse then
merged them into the FIRST slot (dragging the closer's captions mid-episode) and the body
cap dropped trailing cuts (losing the kick/closer). Known-bad cases below must stay fixed.

  .venv/bin/python -m scripts._rf_structure_regress
"""
import sys, os, copy; sys.path.insert(0, os.getcwd())
from agents.producer import _collapse_rf_same_clip_segments as col, _rf_structure_issues as iss
def C(*spec):  # (aid, beat, dur, [ko...])
    return {"cuts":[{"asset_id":a,"beat":b,"duration_seconds":d,"captions":[{"start":0,"end":1,"ko":k,"en":k} for k in ks]} for a,b,d,ks in spec]}
FAILS = []
def ok(n, cond):
    print(("PASS " if cond else "FAIL ") + n)
    if not cond:
        FAILS.append(n)
# 1 real case: closer reuses cut2's clip (non-adjacent)
c=C(("A","intro",3,["a"]),("B","develop",4,["b"]),("C","hook",3,["c"]),("D","kick",3,["d"]),("B","closer",7,["끝","" ]))
ok("gate flags non-adjacent reuse", any("다시 쓴다" in x for x in iss(c)))
col(c); beats=[x["beat"] for x in c["cuts"]]
ok("closer kept last, earlier B dropped", beats==["intro","hook","kick","closer"])
ok("empty caption removed", [k["ko"] for k in c["cuts"][-1]["captions"]]==["끝"])
# 2 adjacent run still merged
c=C(("A","intro",3,["a"]),("B","develop",4,["b1"]),("B","develop",4,["b2"]),("C","closer",7,["z"]))
col(c); ok("adjacent merged", [x["beat"] for x in c["cuts"]]==["intro","develop","closer"] and c["cuts"][1]["duration_seconds"]==8 and len(c["cuts"][1]["captions"])==2)
ok("adjacent not flagged", iss(C(("A","i",3,["a"]),("A","d",3,["b"]),("C","c",7,["z"])))==[])
# 3 non-adjacent, last not involved -> keep first
c=C(("A","intro",3,["a"]),("B","develop",4,["b"]),("A","hook",3,["c"]),("D","closer",7,["z"]))
col(c); ok("keep first run when ending not involved", [x["beat"] for x in c["cuts"]]==["intro","develop","closer"])
# 4 budget
ok("over budget flagged", any("예산" in x for x in iss(C(("A","i",8,["a"]),("B","d",8,["b"]),("C","c",8,["z"])))))
ok("within budget clean", iss(C(("A","i",4,["a"]),("B","d",5,["b"]),("C","c",7,["z"])))==[])
# 5 distinct clean concept unchanged
c=C(("A","i",4,["a"]),("B","d",5,["b"]),("C","c",7,["z"])); c0=copy.deepcopy(c); n=col(c); ok("clean unchanged", n==0 and c==c0)
from agents.producer import _rf_cap_cuts as cap
b=lambda ks:[x["beat"] for x in ks]
k,_=cap(C(("A","intro",4,["a"]),("B","develop",5,["b"]),("C","hook",4,["c"]),("D","kick",4,["d"]),("E","closer",7,["z"]))["cuts"],18)
ok("cap keeps closer, drops middle", b(k)==["intro","develop","closer"])
k,_=cap(C(("A","intro",19.2,["a"]),("B","d",4,["b"]),("E","closer",7,["z"]))["cuts"],18)
ok("huge first cut -> first only (old behaviour)", b(k)==["intro"])
k,_=cap(C(("A","intro",4,["a"]),("E","closer",7,["z"]))["cuts"],18)
ok("under cap untouched", b(k)==["intro","closer"])
k,_=cap(C(("A","only",20,["a"]))["cuts"],18); ok("single cut kept", b(k)==["only"])
print("\nRF STRUCTURE REGRESS: " + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAIL"))
raise SystemExit(1 if FAILS else 0)
