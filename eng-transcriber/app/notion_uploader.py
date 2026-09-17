"""Notion 저장: '영어듣기' 페이지 아래 하위 페이지 생성 + 전사문 블록 추가.

- 부모 페이지(영어듣기) 아래에 제목이 영상 제목인 새 하위 페이지를 만든다.
- 전사문은 문단(paragraph) 블록으로 추가한다.
- Notion 제한: rich_text 하나당 2000자, block append 한 번에 100블록.
  → 긴 문단은 여러 rich_text 로, 전체 블록은 100개씩 나눠 순서대로 추가.
- 저장 후 블록 개수를 조회하여 누락이 없는지 검증한다.
"""

from __future__ import annotations

import json

import requests

from .config import NOTION_BLOCK_CHAR_LIMIT, AppConfig
from .errors import (
    NetworkError,
    NotionAuthError,
    NotionError,
    NotionPermissionError,
    NotionSaveError,
)
from .models import Job

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
APPEND_BATCH = 100  # Notion 한 번에 최대 100 블록


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": VERSION,
        "Content-Type": "application/json",
    }


def _request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    try:
        resp = requests.request(
            method, url, headers=_headers(token), data=json.dumps(body) if body else None, timeout=60
        )
    except requests.exceptions.ConnectionError as e:
        raise NetworkError("Notion 에 연결할 수 없습니다. 인터넷 연결을 확인하세요.", cause=e)
    except requests.exceptions.Timeout as e:
        raise NetworkError("Notion 요청이 시간 초과되었습니다.", cause=e)

    if resp.status_code == 401:
        raise NotionAuthError("Notion 인증에 실패했습니다. Integration Token 을 확인하세요.")
    if resp.status_code == 403:
        raise NotionPermissionError(
            "Notion 페이지 접근 권한이 없습니다.\n"
            "'영어듣기' 페이지의 ... 메뉴 → 연결(Connections) 에서 이 Integration 을 추가했는지 확인하세요."
        )
    if resp.status_code == 404:
        raise NotionPermissionError(
            "Notion 페이지를 찾을 수 없습니다. Page ID 가 올바른지, Integration 이 해당 페이지에 연결되어 있는지 확인하세요."
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("message", resp.text)
        except Exception:
            detail = resp.text
        raise NotionSaveError(f"Notion 요청 실패 (HTTP {resp.status_code}): {detail}")
    return resp.json()


def _rich_text_chunks(text: str) -> list[dict]:
    """긴 문단 텍스트를 2000자 제한에 맞춰 여러 rich_text 로 나눈다."""
    parts = []
    for i in range(0, len(text), NOTION_BLOCK_CHAR_LIMIT):
        parts.append({"type": "text", "text": {"content": text[i : i + NOTION_BLOCK_CHAR_LIMIT]}})
    if not parts:
        parts = [{"type": "text", "text": {"content": ""}}]
    return parts


def _paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text_chunks(text)},
    }


def upload(job: Job, config: AppConfig, lines: list[str], progress) -> None:
    """하위 페이지 생성 후 전사문 블록을 추가하고 검증한다.

    체크포인트: notion_page.json 에 page_id 저장. 재실행 시 이미 생성된 페이지가
    있고 블록 검증이 통과하면 재업로드하지 않는다.
    """
    token = config.notion_token.strip()
    parent = config.notion_parent_page_id.strip().replace("-", "")
    if not token:
        raise NotionAuthError("Notion Integration Token 이 설정되지 않았습니다.")
    if not parent:
        raise NotionError("Notion '영어듣기' Page ID 가 설정되지 않았습니다.")

    from pathlib import Path

    ckpt = Path(job.workdir) / "notion_page.json"

    blocks = [_paragraph_block(line) for line in lines]
    total_blocks = len(blocks)

    # 1) 하위 페이지 생성 (제목만, 본문은 첫 배치로)
    if job.notion_page_id is None and ckpt.exists():
        try:
            job.notion_page_id = json.loads(ckpt.read_text(encoding="utf-8")).get("page_id")
        except Exception:
            job.notion_page_id = None

    first_batch = blocks[:APPEND_BATCH]
    rest = blocks[APPEND_BATCH:]

    if not job.notion_page_id:
        progress("Notion 업로드", 0.1, "하위 페이지 생성 중...")
        body = {
            "parent": {"type": "page_id", "page_id": parent},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": job.title[:2000]}}]}},
            "children": first_batch,
        }
        created = _request("POST", f"{API}/pages", token, body)
        job.notion_page_id = created["id"]
        ckpt.write_text(json.dumps({"page_id": job.notion_page_id}, ensure_ascii=False), encoding="utf-8")
        appended = len(first_batch)
    else:
        # 페이지가 이미 있으면 현재 블록 수 확인 후 부족분만 추가
        appended = _count_blocks(job.notion_page_id, token)
        rest = blocks[appended:]

    progress("Notion 업로드", 0.3, f"{appended}/{total_blocks} 블록 추가됨")

    # 2) 나머지 블록을 100개씩 순서대로 append
    idx = 0
    while idx < len(rest):
        batch = rest[idx : idx + APPEND_BATCH]
        _request(
            "PATCH",
            f"{API}/blocks/{job.notion_page_id}/children",
            token,
            {"children": batch},
        )
        appended += len(batch)
        idx += APPEND_BATCH
        progress("Notion 업로드", min(0.95, 0.3 + 0.65 * appended / total_blocks),
                 f"{appended}/{total_blocks} 블록 추가됨")

    # 3) 검증: 실제 블록 수가 기대치와 일치하는지 확인 (누락 방지)
    actual = _count_blocks(job.notion_page_id, token)
    if actual < total_blocks:
        raise NotionSaveError(
            f"Notion 저장 검증 실패: 기대 {total_blocks}블록 중 {actual}블록만 확인되었습니다.\n"
            "다시 시작하면 이미 생성된 페이지에 부족한 블록만 추가됩니다."
        )
    progress("Notion 업로드", 1.0, f"저장 완료 ({actual}블록)")


def _count_blocks(page_id: str, token: str) -> int:
    """페이지의 자식 블록 개수를 페이지네이션 처리하며 센다."""
    count = 0
    cursor = None
    while True:
        url = f"{API}/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        data = _request("GET", url, token)
        count += len(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return count
