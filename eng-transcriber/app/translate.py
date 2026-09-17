"""GPT 문맥 기반 한국어 번역.

원칙:
- 한 문장마다 개별 요청하지 않고, 여러 문장을 chunk 로 묶어 문맥을 제공한다.
- 출력은 각 영어 문장과 1:1 로 대응되는 한국어 번역이어야 한다 (id 매칭).
- 영어 원문은 절대 수정하지 않는다. GPT 역할은 '교정'이 아니라 '번역'.
- 검증: 응답의 id 집합이 요청 id 집합과 정확히 일치해야 한다.
- 실패한 chunk 만 재시도한다. 이미 번역된 chunk 는 재사용(체크포인트).
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import AppConfig
from .errors import (
    NetworkError,
    OpenAIAuthError,
    OpenAIQuotaError,
    TranslateError,
    TranslationStructureError,
)
from .models import Chunk, Job, Segment, TranslationUnit

MAX_CHUNK_RETRIES = 4

SYSTEM_PROMPT = (
    "You are a professional English→Korean subtitle translator.\n"
    "You will receive a JSON array of English sentences, each with a numeric 'id'.\n"
    "Translate EACH sentence into natural, context-aware Korean.\n\n"
    "STRICT RULES:\n"
    "1. Return translations for EVERY id, exactly once. Do not add, drop, merge, or split ids.\n"
    "2. Never modify, correct, or rewrite the English source. You only produce Korean.\n"
    "3. Prefer natural Korean that fits the video's context over literal word-for-word translation.\n"
    "4. Do not add meaning that is not present, and do not summarize or omit.\n"
    "5. Keep proper nouns / technical terms / game names / people / product names in their original\n"
    "   form when Korean-izing them would be unnatural.\n"
    "6. Even short or repeated utterances must be translated (never omitted).\n"
    "7. Consider the surrounding sentences as context, but output one Korean line per id.\n\n"
    'Respond ONLY with JSON: {"translations":[{"id":<int>,"ko":"<korean>"}, ...]}\n'
    "No markdown, no commentary."
)


def make_chunks(segments: list[Segment], chunk_size: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for i in range(0, len(segments), chunk_size):
        chunks.append(Chunk(index=len(chunks), segments=segments[i : i + chunk_size]))
    return chunks


def _client(config: AppConfig):
    try:
        from openai import OpenAI
    except ImportError as e:
        raise TranslateError("openai 패키지가 설치되어 있지 않습니다.", cause=e)
    if not config.openai_api_key.strip():
        raise OpenAIAuthError("OpenAI API Key 가 설정되지 않았습니다.")
    return OpenAI(api_key=config.openai_api_key)


def _translate_chunk(client, model: str, chunk: Chunk) -> dict[int, str]:
    """한 chunk 를 번역하고 {id: korean} 을 반환. 구조 검증 포함."""
    payload = [{"id": s.id, "en": s.text} for s in chunk.segments]
    expected_ids = {s.id for s in chunk.segments}

    user_prompt = (
        "Translate these English sentences to Korean. Keep the ids exactly.\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception as e:  # SDK 예외를 사용자 메시지로 변환
        _raise_openai_error(e)

    content = resp.choices[0].message.content or ""
    try:
        data = json.loads(content)
        items = data["translations"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise TranslationStructureError(
            "GPT 번역 응답이 예상한 JSON 구조가 아닙니다.", cause=e
        )

    result: dict[int, str] = {}
    for it in items:
        try:
            _id = int(it["id"])
            ko = str(it["ko"]).strip()
        except (KeyError, TypeError, ValueError) as e:
            raise TranslationStructureError("번역 항목의 id/ko 형식이 잘못되었습니다.", cause=e)
        if _id in expected_ids and ko:
            result[_id] = ko

    got_ids = set(result.keys())
    if got_ids != expected_ids:
        missing = sorted(expected_ids - got_ids)
        extra = sorted(got_ids - expected_ids)
        raise TranslationStructureError(
            f"번역 결과가 원문과 1:1 대응되지 않습니다. 누락 id={missing[:10]} 추가 id={extra[:10]}"
        )
    return result


def _raise_openai_error(e: Exception):
    name = type(e).__name__
    msg = str(e)
    low = msg.lower()
    if "authenticat" in low or name == "AuthenticationError" or "invalid api key" in low:
        raise OpenAIAuthError("OpenAI API 인증에 실패했습니다. API Key 를 확인하세요.", cause=e)
    if "quota" in low or "insufficient_quota" in low or "billing" in low or name == "RateLimitError":
        raise OpenAIQuotaError(
            "OpenAI 사용량/결제 한도 문제로 요청이 거부되었습니다. 결제 상태를 확인하세요.", cause=e
        )
    if "connection" in low or "timeout" in low or "network" in low:
        raise NetworkError("OpenAI API 에 연결할 수 없습니다. 인터넷 연결을 확인하세요.", cause=e)
    raise TranslateError(f"GPT 번역 요청 실패: {msg}", cause=e)


def _load_partial(job: Job) -> dict[int, str]:
    ckpt = Path(job.workdir) / "units.json"
    if not ckpt.exists():
        return {}
    try:
        data = json.loads(ckpt.read_text(encoding="utf-8"))
        return {int(u["id"]): str(u["korean"]) for u in data}
    except Exception:
        return {}


def _save_partial(job: Job, translated: dict[int, str]) -> None:
    ckpt = Path(job.workdir) / "units.json"
    by_id = {s.id: s for s in job.segments}
    units = [
        {"id": _id, "english": by_id[_id].text, "korean": ko}
        for _id, ko in sorted(translated.items())
        if _id in by_id
    ]
    ckpt.write_text(json.dumps(units, ensure_ascii=False), encoding="utf-8")


def translate(job: Job, config: AppConfig, progress) -> None:
    """job.segments 를 번역하여 job.units 를 채운다. 완료된 chunk 는 재사용."""
    if not job.segments:
        raise TranslateError("번역할 전사 결과가 없습니다.")

    translated = _load_partial(job)  # {id: korean} 체크포인트
    chunks = make_chunks(job.segments, max(5, int(config.chunk_size)))
    client = _client(config)
    model = config.gpt_model.strip()

    total = len(chunks)
    for chunk in chunks:
        # 이 chunk 의 모든 id 가 이미 번역되어 있으면 skip
        need = [s for s in chunk.segments if s.id not in translated]
        if not need:
            progress("GPT 번역", (chunk.index + 1) / total, f"chunk {chunk.index+1}/{total} (재사용)")
            continue

        sub = Chunk(index=chunk.index, segments=need)
        last_err: Exception | None = None
        for attempt in range(1, MAX_CHUNK_RETRIES + 1):
            try:
                result = _translate_chunk(client, model, sub)
                translated.update(result)
                _save_partial(job, translated)  # 부분 저장
                progress(
                    "GPT 번역",
                    (chunk.index + 1) / total,
                    f"chunk {chunk.index+1}/{total} 완료",
                )
                last_err = None
                break
            except TranslationStructureError as e:
                last_err = e
                progress(
                    "GPT 번역",
                    (chunk.index + 0.5) / total,
                    f"chunk {chunk.index+1} 구조 불일치 → 재시도 {attempt}/{MAX_CHUNK_RETRIES}",
                )
            except (OpenAIAuthError, OpenAIQuotaError):
                raise  # 재시도 무의미 → 즉시 사용자에게
            except (NetworkError, TranslateError) as e:
                last_err = e
                progress(
                    "GPT 번역",
                    (chunk.index + 0.5) / total,
                    f"chunk {chunk.index+1} 실패 → 재시도 {attempt}/{MAX_CHUNK_RETRIES}",
                )
        if last_err is not None:
            raise TranslateError(
                f"chunk {chunk.index+1}/{total} 번역을 {MAX_CHUNK_RETRIES}회 재시도했으나 실패했습니다.\n"
                f"이미 완료된 번역은 저장되었으니, 다시 시작하면 실패 지점부터 재시도합니다.\n"
                f"상세: {getattr(last_err, 'user_message', str(last_err))}",
                cause=last_err,
            )

    # 최종 units 구성 + 전체 검증
    by_id = {s.id: s for s in job.segments}
    if set(translated.keys()) != set(by_id.keys()):
        raise TranslationStructureError("최종 번역 개수가 원문 문장 개수와 일치하지 않습니다.")

    job.units = [
        TranslationUnit(id=_id, english=by_id[_id].text, korean=translated[_id])
        for _id in sorted(by_id.keys())
    ]
    progress("GPT 번역", 1.0, f"번역 완료 ({len(job.units)}문장)")
