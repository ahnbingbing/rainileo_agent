"""agents/title_writer.py — 랴니레오 오리지널 MV/영상의 YouTube 제목 생성 에이전트.

PD 2026-11-08: CLI가 매번 같은 템플릿("X 🐾 Y | 랴니레오 오리지널 MV")으로 제목을 찍어내는 걸
그만두고, 곡 전체(스토리·훅·바이브·가사)에 맞춰 다양한 포맷으로 제목을 뽑도록 전용 에이전트로 분리.

핵심 원칙(프롬프트에 내장):
- 곡의 구체적 스토리/반전/캐릭터 동기를 제목에 담는다(제너릭 금지).
- 포맷을 매번 바꾼다 — 질문형/반전형/의성어형/대사형 등. 같은 뼈대 반복 금지.
- 채널 승리 패턴(구체 사건 + 궁금증 + 이모지)은 지키되 상투구는 피한다.
  (데이터: '간식 파티' 댄스 MV가 16,983뷰로 채널 1위 — 재미+먹을거+댄스+구체훅.)
- 끝의 '| 랴니레오 오리지널 MV' 꼬리표는 선택(매번 달지 말 것).

사용:
  from agents.title_writer import write_titles
  res = write_titles(song_title="감꼭지", concept="레오 공중묘기 스타 vs 질투에 곶감 훔쳐먹는 랴니",
                     lyrics=open(...).read(), vibe="신나는 펑크 인디팝, 코미디")
  print(res["recommended"]); print(res["options"])

CLI:
  .venv/bin/python -m agents.title_writer --title 감꼭지 --concept "..." --lyrics-file path [--vibe "..."]
"""
from __future__ import annotations
import argparse, json, sys

SYSTEM = """너는 '랴니레오(@ryani_n_leo)' 유튜브 채널의 제목 전략가다. 이 채널은 검은 무꼬리 프렌치불독 '랴니'(암컷)와
오렌지 태비 고양이 '레오'의 오리지널 뮤직비디오/쇼츠를 올린다.

목표: 주어진 곡 하나하나에 '맞춤'인, 서로 다른 느낌의 제목을 만든다. 절대 매번 같은 템플릿을 찍어내지 마라.

지켜야 할 원칙:
1. 그 곡만의 구체적인 사건·반전·캐릭터 동기를 제목에 담아라(제너릭·시적·모호 금지). 보는 순간 "뭐지?" 궁금하게.
2. 포맷을 매 옵션마다 바꿔라 — 질문형("왜 ~게요?"), 반전형("~인데… 근데 ~?!"), 의성어/대사형("꿀꺽!"), 선언형 등.
   같은 뼈대를 반복하지 마라. '| 랴니레오 오리지널 MV' 꼬리표는 전부 달지 말고 일부만, 혹은 다른 변주로.
3. 채널 승리 공식은 '구체적 사건 + 궁금증 + 적절한 이모지'. 재미/동작/먹을거가 섞이면 강하다
   (채널 1위 영상=간식 파티 댄스 MV, 16,983뷰). 하지만 상투어·클릭베이트 과장은 피한다.
4. 길이는 유튜브에서 안 잘리게 ~40자 내외 권장(최대 70자). 이모지는 1~2개.
5. 캐릭터 사실을 지켜라: 랴니=암컷·무꼬리, 레오=오렌지 고양이. 거짓 사실 금지.

출력은 JSON만:
{"options":[{"title":"...","format":"질문형|반전형|대사형|선언형|의성어형","why":"이 곡에 왜 맞는지 한 줄"}, ... 5개],
 "recommended":"가장 강한 제목 하나(options 중 하나와 동일 문자열)",
 "reason":"recommended를 고른 이유 한 줄"}"""


def write_titles(*, song_title: str, concept: str, lyrics: str = "",
                 vibe: str = "", extra: str = "", n: int = 5) -> dict:
    """곡 정보로 제목 n개(+추천 1개)를 생성. call_text_cascade(OpenAI→Gemini→Anthropic) 사용."""
    from agents.llm_cascade import call_text_cascade
    user = f"""곡 제목(가제): {song_title}
컨셉/스토리: {concept}
바이브/장르: {vibe or '(미지정)'}
{('추가 메모: ' + extra) if extra else ''}

가사:
---
{(lyrics or '(없음)')[:3000]}
---

위 곡에 맞는 서로 다른 느낌의 제목 {n}개를 만들어라. 포맷을 매 옵션마다 바꾸고, 이 곡만의 구체적 반전/동기를 담아라."""
    raw = call_text_cascade(SYSTEM, user, max_tokens=1500)
    a, b = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[a:b + 1] if a != -1 else raw)
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="랴니레오 MV 제목 생성 에이전트")
    ap.add_argument("--title", required=True, help="곡 제목(가제)")
    ap.add_argument("--concept", required=True, help="컨셉/스토리 한두 줄")
    ap.add_argument("--lyrics-file", help="가사 .txt 경로")
    ap.add_argument("--vibe", default="", help="바이브/장르")
    ap.add_argument("--extra", default="", help="추가 메모")
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args()
    lyrics = ""
    if a.lyrics_file:
        from pathlib import Path
        lyrics = Path(a.lyrics_file).read_text(encoding="utf-8")
    res = write_titles(song_title=a.title, concept=a.concept, lyrics=lyrics,
                       vibe=a.vibe, extra=a.extra, n=a.n)
    print("=== 추천 ===")
    print(" ", res.get("recommended"), " —", res.get("reason", ""))
    print("=== 옵션 ===")
    for o in res.get("options", []):
        print(f"  [{o.get('format','')}] {o.get('title','')}")
        print(f"       ↳ {o.get('why','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
