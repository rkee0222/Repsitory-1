"""사용자에게 명확한 메시지로 변환할 수 있는 예외 계층.

각 예외는 GUI 에서 사람이 읽을 수 있는 한국어 메시지로 표시된다.
파이프라인은 이 예외들을 잡아 해당 단계만 재시도할 수 있도록 설계된다.
"""

from __future__ import annotations


class PipelineError(Exception):
    """파이프라인 단계에서 발생하는 모든 오류의 기반 클래스.

    Attributes:
        stage: 오류가 발생한 단계 이름 (재시도용).
        user_message: 사용자에게 보여줄 한국어 메시지.
    """

    stage: str = "unknown"

    def __init__(self, user_message: str, *, cause: Exception | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.cause = cause


# --- 입력 / 오디오 ---
class InputError(PipelineError):
    stage = "input"


class LocalFileError(InputError):
    """영상 파일 읽기 실패."""


class YouTubeError(InputError):
    """YouTube 다운로드/메타데이터 실패."""


class AudioExtractionError(InputError):
    """ffmpeg 오디오 추출 실패."""


# --- Whisper 전사 ---
class TranscribeError(PipelineError):
    stage = "transcribe"


class CudaUnavailableError(TranscribeError):
    """CUDA/GPU 를 사용할 수 없음 (조용한 CPU fallback 금지)."""


class WhisperLoadError(TranscribeError):
    """Whisper 모델 로딩 실패."""


# --- GPT 번역 ---
class TranslateError(PipelineError):
    stage = "translate"


class OpenAIAuthError(TranslateError):
    """OpenAI API 인증 실패."""


class OpenAIQuotaError(TranslateError):
    """OpenAI 사용량/결제 문제."""


class TranslationStructureError(TranslateError):
    """GPT 번역 결과 구조 불일치 (id/개수 불일치 등)."""


# --- Notion ---
class NotionError(PipelineError):
    stage = "notion"


class NotionAuthError(NotionError):
    """Notion 인증 실패."""


class NotionPermissionError(NotionError):
    """Notion 페이지 접근 권한 없음."""


class NotionSaveError(NotionError):
    """Notion 저장 실패 / 검증 실패."""


# --- 공통 ---
class NetworkError(PipelineError):
    """인터넷 연결 문제."""

    stage = "network"


class ConfigError(PipelineError):
    """설정(인증정보 등) 누락/오류."""

    stage = "config"
