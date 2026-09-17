"""입력 처리: 로컬 영상 파일 및 YouTube URL → 전사용 오디오(wav) 추출.

- 로컬: 확장자 제거한 파일명을 제목으로 사용.
- YouTube: yt-dlp 로 제목 조회 + 오디오 다운로드.
- 오디오는 ffmpeg 로 16kHz mono WAV 로 변환 (Whisper 최적).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .errors import AudioExtractionError, LocalFileError, YouTubeError
from .models import Job

LOCAL_EXTS = {".mp4", ".mkv", ".mov", ".webm"}


def _ffmpeg_exe() -> str:
    """번들된(또는 시스템) ffmpeg 실행 파일 경로를 반환한다."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        # imageio-ffmpeg 없으면 PATH 의 ffmpeg 시도
        return "ffmpeg"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    # Windows 에서 콘솔 창이 뜨지 않도록 처리
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )


def title_from_local(path: str) -> str:
    return Path(path).stem


def prepare_local(job: Job, progress) -> None:
    """로컬 파일 검증 + 제목 설정. (오디오 추출은 extract_audio 에서)"""
    p = Path(job.source)
    if not p.exists():
        raise LocalFileError(f"영상 파일을 찾을 수 없습니다: {job.source}")
    if p.suffix.lower() not in LOCAL_EXTS:
        raise LocalFileError(
            f"지원하지 않는 파일 형식입니다: {p.suffix}\n지원 형식: MP4, MKV, MOV, WEBM"
        )
    if p.stat().st_size == 0:
        raise LocalFileError("영상 파일이 비어 있습니다.")
    job.title = title_from_local(job.source)
    progress("영상 준비", 1.0, f"제목: {job.title}")


def prepare_youtube(job: Job, progress) -> None:
    """YouTube 영상 제목 조회 + 오디오 다운로드 → job.audio_path 설정."""
    try:
        import yt_dlp
    except ImportError as e:
        raise YouTubeError("yt-dlp 가 설치되어 있지 않습니다.", cause=e)

    audio_out = str(Path(job.workdir) / "yt_audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": audio_out,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ffmpeg_location": os.path.dirname(_ffmpeg_exe()),
        # bestaudio 를 wav 로 후처리 (16k mono 는 extract_audio 에서 재보장)
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "wav"},
        ],
    }

    progress("영상 준비", 0.1, "YouTube 정보 조회 중...")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(job.source, download=False)
            job.title = (info.get("title") or "youtube_video").strip()
            progress("영상 준비", 0.3, f"제목: {job.title}")
            progress("오디오 추출", 0.1, "YouTube 오디오 다운로드 중...")
            ydl.download([job.source])
    except yt_dlp.utils.DownloadError as e:
        raise YouTubeError(
            "YouTube 영상을 다운로드할 수 없습니다.\n"
            "URL 이 올바른지, 영상이 비공개/지역제한/삭제 상태가 아닌지 확인하세요.",
            cause=e,
        )
    except Exception as e:  # 네트워크 등
        raise YouTubeError(f"YouTube 처리 중 오류가 발생했습니다: {e}", cause=e)

    # 다운로드된 wav 찾기
    downloaded = None
    for f in Path(job.workdir).glob("yt_audio.*"):
        downloaded = str(f)
        if f.suffix.lower() == ".wav":
            break
    if not downloaded:
        raise YouTubeError("YouTube 오디오 파일을 찾을 수 없습니다.")
    job.audio_path = downloaded
    progress("오디오 추출", 0.6, "다운로드 완료")


def extract_audio(job: Job, progress) -> None:
    """입력(로컬 파일 또는 YouTube 다운로드 결과)을 16kHz mono WAV 로 변환.

    체크포인트: audio.wav 가 이미 있으면 재사용.
    """
    target = str(Path(job.workdir) / "audio.wav")
    if os.path.exists(target) and os.path.getsize(target) > 0:
        job.audio_path = target
        progress("오디오 추출", 1.0, "기존 오디오 재사용")
        return

    # 원본: 로컬이면 원본 파일, YouTube 면 다운로드된 audio_path
    if job.source_kind == "local":
        src = job.source
    else:
        src = job.audio_path
        if not src:
            raise AudioExtractionError("YouTube 오디오가 준비되지 않았습니다.")

    ffmpeg = _ffmpeg_exe()
    cmd = [
        ffmpeg, "-y",
        "-i", src,
        "-vn",  # 비디오 제거
        "-ac", "1",  # mono
        "-ar", "16000",  # 16kHz
        "-f", "wav",
        target,
    ]
    progress("오디오 추출", 0.7, "오디오 변환 중...")
    proc = _run(cmd)
    if proc.returncode != 0 or not os.path.exists(target):
        raise AudioExtractionError(
            "오디오 추출에 실패했습니다. 파일이 손상되었거나 지원되지 않는 코덱일 수 있습니다.\n"
            f"ffmpeg 오류: {proc.stderr[-500:] if proc.stderr else '알 수 없음'}"
        )
    job.audio_path = target
    progress("오디오 추출", 1.0, "오디오 추출 완료")
