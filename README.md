# VoicePitch — 목소리 피치(F0) 분석기

사람 목소리의 **기본 주파수(F0, fundamental frequency)** 를 시간에 따라 검출하고,
음이름·옥타브 눈금의 그래프로 보여주는 Windows용 독립 실행 데스크톱 프로그램입니다.
모든 분석은 사용자 PC에서 **로컬로만** 수행됩니다. 외부 서버·계정·API·GPU가 필요 없습니다.

## 두 가지 모드

1. **실시간 마이크 모드** — 마이크 입력을 실시간으로 받아 F0를 검출하고,
   최근 10초 구간을 오른쪽→왼쪽으로 흐르는 스크롤 그래프로 표시합니다.
2. **녹음 파일 분석 모드** — MP3 / WAV / M4A 파일을 불러와 전체 구간의 F0를
   분석한 뒤, 파일 전체 길이에 대한 피치 그래프를 한 번에 표시합니다.

## 피치 검출 방식과 선택 이유

| 용도 | 알고리즘 | 이유 |
|------|----------|------|
| 실시간 마이크 | **YIN** (`voicepitch/pitch.py`, 포물선 보간 포함) | 블록 단위로 가볍게 동작하여 저지연 실시간 처리에 적합하고, 목소리 F0에 대한 정확도가 높음 |
| 파일 분석 | **pYIN** (`librosa.pyin`) | YIN을 확률적으로 개선한 검증된 방법. 목소리 F0를 가장 신뢰성 있게 검출하며 **옥타브 오류가 현저히 적음**. 오프라인이라 연산 비용은 문제되지 않음 |

- AI 모델·GPU를 사용하지 않습니다.
- 검출된 피치를 특정 음으로 **양자화하지 않습니다**. 실제 연속 F0 값을 그대로 사용합니다.
- **smoothing / moving average를 적용하지 않습니다.** 비브라토·미세 변화가 그대로 그래프에 나타납니다.
- 별도의 음성/무음/잡음 분류기를 넣지 않았습니다. 알고리즘이 반환한 값은 그대로 표시합니다.

## 그래프

- **X축**: 시간 (실시간 모드는 최근 10초, 파일 모드는 전체 길이).
- **Y축**: Hz가 아니라 **음이름 + 옥타브**(A2, A#2, B2, C3 …)를 반음 간격 눈금으로 표시.
  - 데이터는 반음으로 양자화하지 않고 `midi = 69 + 12·log2(f/440)` 로 매핑된
    **연속 위치**에 찍힙니다. 예: A3보다 약간 높으면 A3와 A#3 사이에 표시됩니다.
  - 반음마다 수평 기준선을 그리지 않습니다(음이름은 Y축 눈금으로만 표시).
- **Y축 자동 범위**: 현재 검출되는 음역을 따라가되, 히스테리시스 + 이동량 제한 +
  퍼센타일(2/98%) 기반으로 조정하여 축이 과하게 떨리지 않습니다.

## 개발 환경에서 실행

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

> 요구사항: Python 3.10–3.12 (Windows 권장). MP3/M4A 디코딩에는 ffmpeg 필요(아래 참고).

## 테스트

- **서드파티 없이 실행 가능한 정확도 검증** (numpy 등 미설치 환경에서도 동작):
  ```bash
  python tests/run_core_tests.py
  ```
  220 Hz(A3), 261.63 Hz(C4), 440 Hz(A4) 합성 신호로 정확도(<0.4 반음),
  옥타브 오류 부재, 비브라토 보존을 검증합니다.
- **전체 pytest 스위트** (numpy/librosa 설치 후):
  ```bash
  pytest -q
  ```
  순음 정확도, 하모닉 포함 시 옥타브 오류 부재, 음이름 매핑,
  연속 위치(비양자화), 실시간 스트리밍 검출기, 자동 Y범위 안정성 검증.

검증된 항목(사양 요구사항 대응):
- 마이크 시작/종료 정상 동작 (`MicStream.start/stop`, GUI Start/Stop)
- 10초 스크롤 그래프 유지 (`_redraw_live`의 10초 트림 + X범위 고정)
- MP3/WAV/M4A 로딩 (`load_audio_file`)
- 알려진 주파수가 올바른 음정 위치에 표시됨 (정확도 테스트)
- 옥타브 오류가 비정상적으로 자주 발생하지 않음 (하모닉 테스트 + pYIN 채택)
- 실시간 중 UI 멈춤 없음 (오디오는 PortAudio 스레드, 검출은 30fps 타이머)
- 파일 분석 중 응답 유지 (`FileAnalyzer` QThread 워커)
- Y축 자동 범위가 과하게 흔들리지 않음 (`AutoRangeY` 안정성 테스트)

## Windows 실행 파일(.exe) 빌드

프로젝트 루트에서:

```bat
build_windows.bat
```

또는 수동으로:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pyinstaller --noconfirm VoicePitch.spec
```

완성물: **`dist\VoicePitch.exe`** — Python 설치 없이 독립 실행됩니다.

### ffmpeg 번들 (MP3/M4A용)

WAV는 추가 구성 없이 열립니다. MP3/M4A는 `librosa`가 ffmpeg를 통해 디코딩하므로,
다음 중 하나가 필요합니다.

1. `ffmpeg.exe` 를 `VoicePitch.exe` 와 **같은 폴더**에 두기, 또는
2. `ffmpeg` 를 시스템 **PATH** 에 추가하기.

ffmpeg는 https://www.gyan.dev/ffmpeg/builds/ 등에서 받은 정적 빌드의
`ffmpeg.exe` 하나만 있으면 됩니다.

## 프로젝트 구조

```
voicepitch/
  __init__.py
  __main__.py        # python -m voicepitch
  notes.py           # Hz <-> MIDI(연속) 변환, 음이름 눈금 생성
  pitch.py           # YIN(실시간) + pYIN(파일) 검출
  audio.py           # 마이크 캡처 + 파일 로딩
  plot.py            # 음이름 Y축, 자동 Y범위 컨트롤러
  gui.py             # PySide6 메인 윈도우 (두 모드)
main.py              # 실행 진입점
tests/
  yin_reference.py   # 순수 파이썬 YIN(테스트용)
  run_core_tests.py  # 서드파티 없이 실행되는 정확도 검증
  test_pitch.py      # pytest 스위트
VoicePitch.spec      # PyInstaller 설정
build_windows.bat    # 빌드 스크립트
requirements.txt
```
