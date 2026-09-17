"""전사문 조립: 영어 원문 + 한국어 번역(1:1), 2분 단위 타임스탬프 구분선.

형식:
    English sentence.
    자연스러운 한국어 번역.

    English sentence.
    자연스러운 한국어 번역.

    ────────── 2:00 ──────────

    ...

- 영어(위) / 한국어(아래) 한 쌍, 쌍 사이 빈 줄.
- 발화 순서 유지, 삭제/병합 없음.
- segment.start 가 2분 경계(120, 240, ...)를 넘어가는 첫 지점 앞에 구분선 삽입.
- 구분선은 문장 순서/개수에 영향을 주지 않는다.
"""

from __future__ import annotations

from .models import Segment, TranslationUnit

DIVIDER_INTERVAL = 120  # 초 (2분)


def _fmt_mmss(total_seconds: int) -> str:
    m, s = divmod(int(total_seconds), 60)
    return f"{m}:{s:02d}"


def make_divider(total_seconds: int) -> str:
    return f"────────── {_fmt_mmss(total_seconds)} ──────────"


def build_lines(
    units: list[TranslationUnit], segments: list[Segment]
) -> list[str]:
    """전사문을 라인(문단) 리스트로 조립한다.

    각 원소는 하나의 '문단' 이며, Notion 블록/텍스트 조립 시 그대로 사용한다.
    문단 종류:
      - "pair": 영어\n한국어  (한 문장 쌍)
      - "divider": 2분 구분선
    """
    start_by_id = {s.id: s.start for s in segments}
    lines: list[str] = []
    next_divider = DIVIDER_INTERVAL

    for u in units:
        start = start_by_id.get(u.id, 0.0)
        # 이 문장이 시작되기 전에 넘긴 2분 경계마다 구분선 삽입
        while start >= next_divider:
            lines.append(make_divider(next_divider))
            next_divider += DIVIDER_INTERVAL
        # 영어(위) / 한국어(아래)
        lines.append(f"{u.english}\n{u.korean}")

    return lines


def build_text(units: list[TranslationUnit], segments: list[Segment]) -> str:
    """사람이 읽는 전체 전사문 문자열 (문단 사이 빈 줄)."""
    return "\n\n".join(build_lines(units, segments))
