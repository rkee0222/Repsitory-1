# EngTranscriber — 영어 영상 전사·번역·Notion 저장 (Windows)

로컬 영상 파일 또는 YouTube URL → 로컬 **faster-whisper**(RTX 4060 CUDA) 영어 전사 →
**GPT** 문맥 기반 한국어 번역 → **Notion "영어듣기"** 페이지 아래 하위 페이지 자동 생성.

Kiro 없이 독립적으로 실행되는 일반 Windows GUI 프로그램이며, `.exe` 로 패키징할 수 있습니다.

---

## 최종 사용 흐름
프로그램 실행 → 파일 선택 또는 YouTube URL 붙여넣기 → **시작** → 기다림 →
Notion "영어듣기" 아래에 완성된 영어/한국어 전사문 페이지가 자동 생성됩니다.

---

## 1. 사전 준비 (본인 Windows PC)

1. **Python 3.11 또는 3.12** 설치 (64-bit). 설치 시 "Add to PATH" 체크.
2. **NVIDIA 최신 드라이버** 설치 (RTX 4060).
3. (선택) 프로그램 폴더에서 가상환경 생성:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```

## 2. 의존성 설치

```
pip install -r requirements.txt
```

### GPU 가속용 CUDA 라이브러리 (중요)
faster-whisper 는 CTranslate2 를 사용하며 GPU 가속에 cuBLAS/cuDNN 이 필요합니다:
```
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```
> 이 라이브러리가 없거나 드라이버가 오래되면 프로그램이 **"CUDA 사용 불가"** 를 명확히
> 표시하고 중단합니다(조용히 CPU 로 전환하지 않음). 정말 CPU 로 진행하려면
> 설정에서 "GPU 강제 사용" 을 끄세요.

## 3. 실행 (개발 모드)

```
python main.py
```

## 4. 최초 설정 (프로그램 내 ⚙ 설정)

한 번만 입력하면 `%APPDATA%\EngTranscriber\config.json` 에 저장되어 재입력이 필요 없습니다.
소스코드에 키를 넣지 않습니다.

| 항목 | 설명 |
|------|------|
| OpenAI API Key | https://platform.openai.com 에서 발급 |
| GPT 모델명 | 기본 `gpt-4o-mini`. **언제든 변경 가능** (코드 수정 불필요) |
| Notion Integration Token | https://www.notion.so/my-integrations 에서 발급 |
| Notion 영어듣기 Page ID | 아래 참고 |

### Notion Page ID 구하기 & 권한
1. Notion 에서 "영어듣기" 페이지 열기 → 우측 상단 **···** → **연결(Connections)** →
   위에서 만든 Integration 추가 (이 단계를 빼먹으면 "권한 없음" 오류가 납니다).
2. 페이지 URL 끝의 32자리 hex 가 Page ID:
   `https://www.notion.so/영어듣기-**xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx**`

## 5. Windows `.exe` 빌드

```
pip install pyinstaller
pyinstaller build.spec
```
결과: `dist\EngTranscriber\EngTranscriber.exe`
- `dist\EngTranscriber\` 폴더 전체를 복사해 사용하면 Python 설치 없이 실행됩니다.
- CUDA DLL 이 크기 때문에 **onedir**(폴더형)로 빌드합니다. 폴더째 배포하세요.
- GPU 가속을 배포본에도 넣으려면 빌드 전에 `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`
  가 같은 환경에 설치되어 있어야 합니다(spec 이 자동 수집).

---

## 동작 사양 (요구사항 매핑)

- **입력**: 로컬 MP4/MKV/MOV/WEBM (파일 선택 UI), YouTube URL (yt-dlp).
  - 제목: 로컬은 확장자 제거 파일명, YouTube 는 영상 제목 자동 사용.
- **전사**: faster-whisper `large-v3-turbo`, `language=en`, CUDA(float16).
  Whisper 영어 출력은 최종 전사문의 source of truth 이며 이후 절대 수정되지 않습니다.
- **번역**: 여러 문장을 chunk 로 묶어 GPT 에 전달(문맥 반영), 결과는 문장 id 기준 1:1 매칭.
  개수/구조 불일치 시 해당 chunk 만 자동 재시도. 원문 교정 금지.
- **형식**: `영어\n한국어` 쌍 + 2분마다 `────────── 2:00 ──────────` 구분선.
- **Notion**: "영어듣기" 아래 하위 페이지 생성, 2000자/100블록 제한을 자동 분할하여
  순서대로 추가하고, 완료 후 블록 수를 검증(누락 방지).
- **정리/재시도**: 처리 데이터는 `%APPDATA%\EngTranscriber\jobs\<hash>\` 에 체크포인트로
  저장. **성공 + Notion 검증 통과 후에만** 삭제. 실패 시 보존되어 같은 입력으로 재실행하면
  실패한 단계부터 재시도합니다(전사/번역 재사용).

---

## 최종 테스트 체크리스트 (본인 PC에서 확인)

- [ ] MP4 입력 → 전체 과정 정상 완료
- [ ] MKV / MOV / WEBM 입력
- [ ] YouTube URL 입력 (다운로드 불가 영상은 오류 메시지 표시, 프로그램 유지)
- [ ] 실행 중 상단에 "● CUDA 사용 가능" 표시 & 전사 로그에 GPU(float16) 표기
- [ ] large-v3-turbo 로딩 성공
- [ ] 영어 원문이 번역 후에도 그대로 (오타 포함 변경 없음)
- [ ] 영어/한국어 1:1 대응, 순서/개수 보존
- [ ] 2:00, 4:00 … 구분선이 올바른 위치
- [ ] 1시간+ 긴 영상에서 문장 누락/순서변경 없음
- [ ] Notion "영어듣기" 아래 올바른 제목의 하위 페이지 생성
- [ ] 긴 전사문 전체가 빠짐없이 저장 (검증 통과)
- [ ] 완료 후 `jobs\<hash>` 임시 폴더 삭제됨
- [ ] 오류 발생 시 메시지 표시 & 프로그램 비정상 종료 없음

> 자동 검증된 항목(설계 로직): 2분 구분선 위치, 영어/한국어 1:1 및 순서 보존,
> 영어 원문 불변, 청크 분할 커버리지, 번역 id 매칭 검증 — `tests/test_core_logic.py` 참조.
