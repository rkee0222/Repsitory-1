"""파이프라인 전 구간에서 공유하는 데이터 모델.

핵심 설계: 각 문장(Segment)에 파이프라인 전체에서 불변인 정수 id 를 부여하여
GPT 번역 결과(TranslationUnit)와 Whisper 원문을 정확히 재매칭한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Segment:
    """Whisper 가 출력한 하나의 영어 문장/발화.

    text 는 최종 전사문의 English source of truth 이며 이후 단계에서 절대 수정하지 않는다.
    """

    id: int
    start: float  # 초
    end: float  # 초
    text: str  # Whisper 영어 원문 (불변)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Segment":
        return Segment(id=int(d["id"]), start=float(d["start"]), end=float(d["end"]), text=str(d["text"]))


@dataclass
class TranslationUnit:
    """Segment 와 1:1 대응되는 번역 단위."""

    id: int
    english: str  # Segment.text 복사본 (불변)
    korean: str  # GPT 번역 결과

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TranslationUnit":
        return TranslationUnit(id=int(d["id"]), english=str(d["english"]), korean=str(d["korean"]))


@dataclass
class Chunk:
    """GPT 에 한 번에 보낼 Segment 묶음."""

    index: int
    segments: list[Segment]


@dataclass
class Job:
    """하나의 영상 처리 작업 전체 상태 (체크포인트 대상)."""

    source_kind: str  # "local" | "youtube"
    source: str  # 파일 경로 또는 URL
    workdir: str  # 임시 폴더
    title: str = ""
    audio_path: str | None = None
    segments: list[Segment] = field(default_factory=list)
    units: list[TranslationUnit] = field(default_factory=list)
    notion_page_id: str | None = None
