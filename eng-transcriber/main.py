"""EngTranscriber 진입점.

Windows .exe 로 빌드되며, 실행하면 GUI 가 뜬다.
"""

from __future__ import annotations

import sys
import traceback


def main():
    try:
        from app.gui import main as gui_main

        gui_main()
    except Exception:
        # GUI 초기화 단계에서의 치명적 오류도 사용자에게 보여준다.
        err = traceback.format_exc()
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("치명적 오류", f"프로그램을 시작할 수 없습니다:\n\n{err}")
        except Exception:
            print(err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
