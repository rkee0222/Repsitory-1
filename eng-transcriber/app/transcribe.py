"""로컬 faster-whisper 전사 (large-v3-turbo, English, CUDA).

핵심 원칙:
- 클라우드가 아닌 사용자 PC 에서 완전히 로컬 실행.
- CUDA/GPU 를 사용할 수 없으면 '조용히' CPU 로 전환하지 않고 명시적으로 알린다.
- Whisper 영어 출력은 최종 전사문의 source of truth 이며 이후 절대 수정하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from .config import WHISPER_LANGUAGE, WHISPER_MODEL, AppConfig
from .errors import CudaUnavailableError, TranscribeError, WhisperLoadError
from .models import Job, Segment


def cuda_available() -> tuple[bool, str]:
    """CUDA 사용 가능 여부와 사람이 읽을 수 있는 설명을 반환한다."""
    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
        if count > 0:
            return True, f"CUDA GPU {count}개 감지됨"
        return False, "CUDA GPU 가 감지되지 않았습니다."
    except Exception as e:
        return False, f"CUDA 확인 실패: {e}"


def _load_model(config: AppConfig, progress):
    """faster-whisper 모델 로딩. force_gpu 이면 CUDA 없을 때 예외."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise WhisperLoadError("faster-whisper 가 설치되어 있지 않습니다.", cause=e)

    ok, detail = cuda_available()
    if not ok:
        if config.force_gpu:
            # 조용한 CPU fallback 금지
            raise CudaUnavailableError(
                "CUDA/GPU 를 사용할 수 없어 전사를 중단했습니다.\n\n"
                f"상세: {detail}\n\n"
                "RTX 4060 GPU 가속을 사용하려면 NVIDIA 드라이버와 CUDA 12용 라이브러리가 필요합니다.\n"
                "  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12\n\n"
                "GPU 없이 CPU 로라도 진행하려면 설정에서 'GPU 강제 사용'을 끄세요."
            )
        device, compute_type = "cpu", "int8"
        progress("Whisper 전사", 0.05, "경고: CUDA 미사용 → CPU 모드로 진행합니다(느립니다).")
    else:
        device, compute_type = "cuda", "float16"  # RTX 4060 8GB 에 적합
        progress("Whisper 전사", 0.05, f"{detail} → GPU(float16) 사용")

    try:
        progress("Whisper 전사", 0.1, f"모델 로딩 중: {WHISPER_MODEL} ({device})")
        model = WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type)
    except Exception as e:
        raise WhisperLoadError(
            f"Whisper 모델({WHISPER_MODEL}) 로딩에 실패했습니다.\n상세: {e}", cause=e
        )
    return model, device


def transcribe(job: Job, config: AppConfig, progress) -> None:
    """job.audio_path 를 전사하여 job.segments 를 채운다.

    체크포인트: segments.json 이 있으면 재사용.
    """
    ckpt = Path(job.workdir) / "segments.json"
    if ckpt.exists():
        import json

        data = json.loads(ckpt.read_text(encoding="utf-8"))
        job.segments = [Segment.from_dict(d) for d in data]
        if job.segments:
            progress("Whisper 전사", 1.0, f"기존 전사 재사용 ({len(job.segments)}문장)")
            return

    if not job.audio_path:
        raise TranscribeError("전사할 오디오가 없습니다.")

    model, device = _load_model(config, progress)

    try:
        seg_iter, info = model.transcribe(
            job.audio_path,
            language=WHISPER_LANGUAGE,  # 영어 고정
            beam_size=5,
            vad_filter=True,  # 무음 구간 제거로 정확도 향상
            word_timestamps=False,
        )
    except Exception as e:
        raise TranscribeError(f"전사 중 오류가 발생했습니다: {e}", cause=e)

    total = float(getattr(info, "duration", 0.0)) or 0.0
    segments: list[Segment] = []
    try:
        for i, s in enumerate(seg_iter):
            text = (s.text or "").strip()
            if not text:
                continue
            segments.append(Segment(id=len(segments), start=float(s.start), end=float(s.end), text=text))
            if total > 0:
                frac = min(0.99, 0.1 + 0.9 * (float(s.end) / total))
                if i % 5 == 0:
                    progress("Whisper 전사", frac, f"{len(segments)}문장 전사됨")
    except Exception as e:
        raise TranscribeError(f"전사 처리 중 오류가 발생했습니다: {e}", cause=e)

    if not segments:
        raise TranscribeError("전사 결과가 비어 있습니다. 오디오에 음성이 없을 수 있습니다.")

    job.segments = segments

    import json

    ckpt.write_text(
        json.dumps([s.to_dict() for s in segments], ensure_ascii=False), encoding="utf-8"
    )
    progress("Whisper 전사", 1.0, f"전사 완료 ({len(segments)}문장, {device})")
