"""English video transcriber & translator → Notion.

로컬/YouTube 영상을 로컬 faster-whisper 로 전사하고, GPT 로 한국어 번역한 뒤
Notion 의 '영어듣기' 페이지 아래에 하위 페이지로 저장하는 독립 실행 프로그램.
"""

__version__ = "1.0.0"
APP_NAME = "EngTranscriber"
