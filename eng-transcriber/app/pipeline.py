"""파이프라인 오케스트레이션 + 체크포인트/재시도 + 임시파일 정리.

단계: 영상 준비 → 오디오 추출 → Whisper 전사 → GPT 번역 → 전사문 구성 → Notion 업로드 → 완료
- 각 단계는 workdir 에 산출물을 남겨 재실행 시 완료 단계를 건너뛴다.
- 전체 성공 + Notion 검증 통과 후에만 workdir 를 삭제한다(정리).
- 실패 시 workdir 를 보존하여 실패 지점부터 재시도 가능.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Callable

from . import input_source, notion_uploader, transcribe, transcript_builder, translate
from .config import AppConfig, workspace_root
from .errors import ConfigError, PipelineError
from .models import Job

# progress(stage: str, fraction: float(0..1), detail: str) -> None
ProgressCallback = Callable[[str, float, str], None]

STAGES = [
    "영상 준비",
    "오디오 추출",
    "Whisper 전사",
    "GPT 번역",
    "전사문 구성",
    "Notion 업로드",
    "완료",
]


def _workdir_for(source_kind: str, source: str) -> Path:
    key = hashlib.sha1(f"{source_kind}:{source}".encode("utf-8")).hexdigest()[:16]
    d = workspace_root() / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def run(
    source_kind: str,
    source: str,
    config: AppConfig,
    progress: ProgressCallback,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """전체 파이프라인 실행. 성공 시 Notion 페이지 ID 를 반환한다.

    실패 시 PipelineError 를 raise 한다 (workdir 는 보존됨 → 재시도 가능).
    """
    missing = config.is_complete()
    if missing:
        raise ConfigError("설정이 완료되지 않았습니다. 다음 항목을 설정에서 입력하세요:\n- " + "\n- ".join(missing))

    def check_cancel():
        if should_cancel and should_cancel():
            raise PipelineError("사용자가 작업을 취소했습니다.")

    workdir = _workdir_for(source_kind, source)
    job = Job(source_kind=source_kind, source=source, workdir=str(workdir))

    # 이전 실행에서 저장된 제목 복원(YouTube 재실행 대비)
    meta = workdir / "meta.json"
    if meta.exists():
        import json

        try:
            job.title = json.loads(meta.read_text(encoding="utf-8")).get("title", "")
        except Exception:
            pass

    try:
        # 1) 영상 준비
        progress("영상 준비", 0.0, "시작")
        if source_kind == "local":
            input_source.prepare_local(job, progress)
        else:
            input_source.prepare_youtube(job, progress)
        # 제목 저장(체크포인트)
        import json

        meta.write_text(json.dumps({"title": job.title}, ensure_ascii=False), encoding="utf-8")
        check_cancel()

        # 2) 오디오 추출
        input_source.extract_audio(job, progress)
        check_cancel()

        # 3) Whisper 전사
        transcribe.transcribe(job, config, progress)
        check_cancel()

        # 4) GPT 번역
        translate.translate(job, config, progress)
        check_cancel()

        # 5) 전사문 구성
        progress("전사문 구성", 0.2, "영어/한국어 + 타임스탬프 조립 중...")
        lines = transcript_builder.build_lines(job.units, job.segments)
        progress("전사문 구성", 1.0, f"{len(lines)}개 문단 구성 완료")
        check_cancel()

        # 6) Notion 업로드
        notion_uploader.upload(job, config, lines, progress)
        check_cancel()

        # 7) 완료 + 정리 (성공 + 검증 통과 후에만 삭제)
        _cleanup(workdir)
        progress("완료", 1.0, f"Notion 페이지 생성 완료: {job.title}")
        return job.notion_page_id or ""

    except PipelineError:
        # workdir 보존 → 재시도 시 완료 단계 건너뜀
        raise
    except Exception as e:  # 예기치 못한 오류도 프로그램이 죽지 않게 변환
        raise PipelineError(f"예상치 못한 오류가 발생했습니다: {e}", cause=e)


def _cleanup(workdir: Path) -> None:
    """성공적으로 완료된 작업의 임시 폴더 전체 삭제."""
    try:
        shutil.rmtree(workdir, ignore_errors=True)
    except Exception:
        pass
