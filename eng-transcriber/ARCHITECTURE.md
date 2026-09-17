# English Video Transcriber & Translator (Windows)

로컬 영상/YouTube URL → 로컬 faster-whisper 전사 → GPT 문맥 번역 → Notion 하위 페이지 자동 저장.

Kiro 없이 독립 실행 가능한 Windows `.exe` 로 패키징.

---

## 1. 요구사항 요약

| # | 요구사항 | 구현 위치 |
|---|---------|----------|
| 입력 | 로컬(MP4/MKV/MOV/WEBM) + YouTube URL | `input_source.py` |
| 오디오 | ffmpeg 로 16kHz mono wav 추출 | `input_source.py` |
| 전사 | faster-whisper large-v3-turbo, English, CUDA(RTX 4060) | `transcribe.py` |
| GPU 강제 | CUDA 불가 시 조용히 CPU 전환 금지 → 명시적 표시 | `transcribe.py` |
| 번역 | GPT, chunk 묶음, 1:1 대응, 원문 불변 | `translate.py` |
| 모델명 | 하드코딩 금지, 설정에서 변경 | `config.py` |
| 형식 | 영어문장 / 한국어 번역 (줄바꿈), 2분 타임스탬프 구분선 | `transcript_builder.py` |
| 안정성 | chunk id 매칭, 개수 검증, chunk 단위 재시도 | `translate.py` |
| Notion | "영어듣기" 하위 페이지 생성, 블록 분할, 길이 제한, 검증 | `notion_uploader.py` |
| 인증 | OpenAI Key / GPT 모델 / Notion Token / Page ID 설정 저장 | `config.py` |
| GUI | 입력/실행/진행단계/설정, background thread | `gui.py` |
| 정리 | 성공+검증 후 임시파일 삭제, 실패 시 보존 | `pipeline.py` |
| 재시도 | 실패 단계부터 재시도 (checkpoint) | `pipeline.py` |
| 패키징 | PyInstaller `.exe` | `build.spec`, `README.md` |

**사용자 PC**: Ryzen 5 5600 / RTX 4060 8GB / 16GB / Windows.

---

## 2. 아키텍처 (모듈 경계)

```
                        ┌────────────────────────────┐
                        │            gui.py            │  Tkinter, 메인스레드
                        │  입력 / 시작 / 진행 / 설정   │
                        └──────────────┬───────────────┘
                                       │ background thread
                                       ▼
                        ┌────────────────────────────┐
                        │          pipeline.py         │  오케스트레이션 + 체크포인트
                        └──────────────┬───────────────┘
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼               ▼              ▼
 input_source   transcribe        translate    transcript_builder  notion_uploader
   (ffmpeg,     (faster-whisper)  (OpenAI)      (형식/타임스탬프)   (Notion API)
    yt-dlp)
        └──────────────┴───────────────┴───────────────┴──────────────┘
                                   models.py  (데이터 모델)
                                   config.py  (설정/인증 저장)
                                   errors.py  (예외 계층)
```

모든 단계 함수는 순수하게 데이터만 주고받고, 진행 상황은 `ProgressCallback` 으로 GUI 에 통보한다.

---

## 3. 데이터 모델 (models.py)

파이프라인 전 구간에서 **문장(segment)의 안정적 정수 id** 를 유지해 GPT 번역 결과와 원문을 정확히 재매칭한다.

```python
@dataclass
class Segment:
    id: int          # 0부터 순차. 파이프라인 전체에서 불변 (매칭 키)
    start: float     # 초 (Whisper segment 시작)
    end: float       # 초
    text: str        # Whisper 영어 원문 (source of truth, 절대 수정 금지)

@dataclass
class TranslationUnit:
    id: int          # Segment.id 와 1:1
    english: str     # Segment.text 복사본 (불변)
    korean: str      # GPT 번역 결과

@dataclass
class Chunk:
    index: int
    segments: list[Segment]   # 이 chunk 로 GPT 에 보낼 문장들

@dataclass
class TranscriptResult:
    title: str
    units: list[TranslationUnit]   # id 순 정렬, 개수 == Segment 개수
    segments: list[Segment]        # 타임스탬프 참조용

@dataclass
class Job:
    source_kind: "local" | "youtube"
    source: str            # 파일 경로 또는 URL
    title: str
    workdir: str           # 임시 폴더
    audio_path: str | None
    segments: list[Segment]
    units: list[TranslationUnit]
    notion_page_id: str | None
```

### 데이터 흐름
```
Job(source)
  → input_source:  audio_path, title
  → transcribe:    segments[]  (id, start, end, english text)
  → translate:     units[]     (id, english, korean)  ── 1:1 검증
  → transcript_builder: 최종 텍스트 라인 + 2분 구분선
  → notion_uploader: Notion 블록[] (2000자 분할) → 하위 페이지
  → cleanup: workdir 삭제
```

---

## 4. 전사문 형식 규칙

```
English sentence.
자연스러운 한국어 번역.

English sentence.
자연스러운 한국어 번역.

────────── 2:00 ──────────

English sentence.
자연스러운 한국어 번역.
```

- 영어 원문(위) + 한국어 번역(아래) 한 쌍, 쌍 사이 빈 줄.
- 발화 순서 유지, 삭제/병합 금지.
- 영상 시간이 2:00, 4:00 … 경계를 넘어가는 위치에 구분선 삽입.
  - `divider_time = 120 * n`. 어떤 segment.start 가 `divider_time` 이상이 되는 첫 지점 **앞**에 삽입.
  - 구분선은 문장 순서/개수에 영향 없음 (사이에 끼워넣기만).

---

## 5. 오류 / 재시도 전략

### 체크포인트 (workdir 내 JSON 저장)
| 단계 | 산출물 파일 | 재시도 시 |
|------|-----------|----------|
| audio | `audio.wav` + `meta.json`(title) | wav 있으면 재추출 생략 |
| transcribe | `segments.json` | 있으면 전사 생략 |
| translate | `units.json` (부분 저장: 완료된 chunk 병합) | 완료 chunk 재사용, 실패 chunk 만 재시도 |
| notion | `notion_page.json`(page_id) | 이미 업로드 검증되면 skip |

→ 실패 시 workdir 를 **삭제하지 않음**. 같은 소스로 재실행하면 완료 단계는 건너뛴다.
→ 전체 성공 + Notion 검증 통과 후에만 workdir 삭제.

### GPT 번역 검증/재시도
- chunk 단위 요청. 응답은 `{"translations":[{"id":N,"ko":"..."}]}` JSON.
- 검증: (a) 모든 요청 id 가 정확히 1번씩 존재, (b) 추가/누락 id 없음, (c) ko 비어있지 않음.
- 실패 시 해당 chunk 만 최대 N회 재시도. 재시도 시 "id 를 정확히 유지" 지시 강화.
- 최종 실패 chunk 는 사용자에게 보고 (부분 성공 저장 후 재실행 가능).

### 사용자에게 명확히 표시하는 오류
CUDA 불가 / 모델 로딩 실패 / 파일 읽기 실패 / YouTube 실패 / OpenAI 인증·결제 / GPT 실패·구조불일치 / 인터넷 / Notion 인증·권한·저장 실패. 각각 `errors.py` 의 예외 타입 → GUI 에서 사람이 읽을 메시지로 변환.

---

## 6. 기술 선정

| 목적 | 선택 | 이유 |
|------|------|------|
| 전사 | `faster-whisper` | large-v3-turbo, CTranslate2, CUDA 지원, 유지보수 활발 |
| GPU 확인 | `ctranslate2.get_cuda_device_count()` | 조용한 CPU fallback 방지 |
| 오디오/추출 | `ffmpeg` (imageio-ffmpeg 번들) | 코덱 광범위, exe 에 동봉 |
| YouTube | `yt-dlp` | 활발히 유지보수, 제목/오디오 |
| 번역 | `openai` (>=1.x) | 공식 SDK, 모델명 설정 주입 |
| Notion | `requests` 로 REST 직접 호출 | 의존성 최소화, 블록 append 제어 |
| 설정 저장 | `platformdirs` + JSON | `%APPDATA%` 에 저장 |
| GUI | `tkinter` (표준 라이브러리) | 추가 의존성 없음, exe 크기 절감 |
| 패키징 | `PyInstaller` | Windows onedir/onefile exe |

웹서버/클라우드 서버 없음. 인터넷은 OpenAI/Notion/YouTube 에만 사용.
