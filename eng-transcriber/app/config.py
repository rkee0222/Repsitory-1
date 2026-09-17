"""설정 및 인증정보 관리.

- API Key / Token 은 소스코드에 하드코딩하지 않는다.
- 최초 설정 시 입력받아 %APPDATA%/EngTranscriber/config.json 에 저장한다.
- GPT 모델명은 코드에 고정하지 않고 설정에서 변경 가능하게 한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from . import APP_NAME

# 구현 시점 기준 비용/성능 균형이 좋은 기본 모델. 설정에서 언제든 변경 가능.
DEFAULT_GPT_MODEL = "gpt-4o-mini"

# Whisper 설정 (요구사항 고정값)
WHISPER_MODEL = "large-v3-turbo"
WHISPER_LANGUAGE = "en"

# GPT 로 한 번에 보낼 문장 개수(대략). 긴 영상 안정 처리를 위한 청크 크기.
DEFAULT_CHUNK_SIZE = 40

# Notion 텍스트 블록 하나의 최대 길이 (API 제한 2000자보다 여유있게).
NOTION_BLOCK_CHAR_LIMIT = 1900


def _config_path() -> Path:
    d = Path(user_config_dir(APP_NAME, appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d / "config.json"


def workspace_root() -> Path:
    """체크포인트/임시파일을 저장할 루트 폴더."""
    d = Path(user_data_dir(APP_NAME, appauthor=False)) / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class AppConfig:
    openai_api_key: str = ""
    gpt_model: str = DEFAULT_GPT_MODEL
    notion_token: str = ""
    notion_parent_page_id: str = ""  # "영어듣기" 페이지 ID
    chunk_size: int = DEFAULT_CHUNK_SIZE

    # 실행 옵션
    force_gpu: bool = True  # CUDA 불가 시 조용히 CPU 로 전환하지 않음

    def is_complete(self) -> list[str]:
        """설정이 완전하지 않으면 부족한 항목 이름 목록을 반환한다."""
        missing = []
        if not self.openai_api_key.strip():
            missing.append("OpenAI API Key")
        if not self.gpt_model.strip():
            missing.append("GPT 모델명")
        if not self.notion_token.strip():
            missing.append("Notion Integration Token")
        if not self.notion_parent_page_id.strip():
            missing.append("Notion 영어듣기 Page ID")
        return missing


def load_config() -> AppConfig:
    path = _config_path()
    if not path.exists():
        return AppConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppConfig()
    cfg = AppConfig()
    for k, v in data.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    # 환경변수 override (선택적, 하드코딩 아님)
    if os.environ.get("OPENAI_API_KEY"):
        cfg.openai_api_key = os.environ["OPENAI_API_KEY"]
    return cfg


def save_config(cfg: AppConfig) -> None:
    path = _config_path()
    path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")


def config_location() -> str:
    return str(_config_path())
