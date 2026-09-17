"""Tkinter GUI: 입력 / 실행 / 진행 상태 / 설정.

- 긴 작업은 백그라운드 스레드에서 실행하여 UI 가 멈추지 않게 한다.
- 진행 상태는 큐를 통해 메인 스레드로 전달하여 표시한다(스레드 안전).
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, pipeline
from .config import DEFAULT_GPT_MODEL, AppConfig, config_location, load_config, save_config
from .errors import PipelineError
from .input_source import LOCAL_EXTS
from .transcribe import cuda_available

STAGES = pipeline.STAGES


class SettingsDialog(tk.Toplevel):
    """설정 창: OpenAI Key / GPT 모델 / Notion Token / Notion Page ID."""

    def __init__(self, master, config: AppConfig, on_save):
        super().__init__(master)
        self.title("설정")
        self.config_obj = config
        self.on_save = on_save
        self.resizable(False, False)
        self.grab_set()

        frm = ttk.Frame(self, padding=16)
        frm.grid(sticky="nsew")

        self.vars = {}
        rows = [
            ("OpenAI API Key", "openai_api_key", True),
            ("GPT 모델명", "gpt_model", False),
            ("Notion Integration Token", "notion_token", True),
            ("Notion 영어듣기 Page ID", "notion_parent_page_id", False),
        ]
        for i, (label, key, secret) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=6, padx=(0, 10))
            var = tk.StringVar(value=str(getattr(config, key) or ""))
            ent = ttk.Entry(frm, textvariable=var, width=52, show="*" if secret else "")
            ent.grid(row=i, column=1, pady=6)
            self.vars[key] = var

        # chunk size + force gpu
        ttk.Label(frm, text="번역 청크 크기(문장)").grid(row=4, column=0, sticky="w", pady=6)
        self.chunk_var = tk.StringVar(value=str(config.chunk_size))
        ttk.Entry(frm, textvariable=self.chunk_var, width=10).grid(row=4, column=1, sticky="w", pady=6)

        self.force_gpu_var = tk.BooleanVar(value=config.force_gpu)
        ttk.Checkbutton(
            frm, text="GPU 강제 사용 (CUDA 없으면 CPU 로 조용히 전환하지 않음)", variable=self.force_gpu_var
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=6)

        ttk.Label(frm, text=f"설정 저장 위치: {config_location()}", foreground="#666").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, pady=(16, 0), sticky="e")
        ttk.Button(btns, text="취소", command=self.destroy).grid(row=0, column=0, padx=6)
        ttk.Button(btns, text="저장", command=self._save).grid(row=0, column=1)

    def _save(self):
        for key, var in self.vars.items():
            setattr(self.config_obj, key, var.get().strip())
        if not self.config_obj.gpt_model.strip():
            self.config_obj.gpt_model = DEFAULT_GPT_MODEL
        try:
            self.config_obj.chunk_size = max(5, int(self.chunk_var.get()))
        except ValueError:
            self.config_obj.chunk_size = 40
        self.config_obj.force_gpu = self.force_gpu_var.get()
        save_config(self.config_obj)
        self.on_save(self.config_obj)
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — 영어 영상 전사·번역·Notion 저장  v{__version__}")
        self.geometry("720x620")
        self.minsize(680, 560)

        self.config_obj = load_config()
        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()

        self._build_ui()
        self._refresh_gpu_status()
        self.after(100, self._drain_queue)

    # ---------- UI ----------
    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")

        ttk.Button(top, text="⚙ 설정", command=self._open_settings).pack(side="right")
        self.gpu_label = ttk.Label(top, text="GPU 상태 확인 중...")
        self.gpu_label.pack(side="left")

        # 입력 방식
        inp = ttk.LabelFrame(self, text="입력", padding=12)
        inp.pack(fill="x", **pad)

        self.mode = tk.StringVar(value="local")
        ttk.Radiobutton(inp, text="로컬 영상 파일", variable=self.mode, value="local",
                        command=self._update_mode).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(inp, text="YouTube URL", variable=self.mode, value="youtube",
                        command=self._update_mode).grid(row=0, column=1, sticky="w", padx=(20, 0))

        self.file_var = tk.StringVar()
        self.file_entry = ttk.Entry(inp, textvariable=self.file_var, width=60)
        self.file_entry.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        self.browse_btn = ttk.Button(inp, text="파일 선택...", command=self._browse)
        self.browse_btn.grid(row=1, column=2, padx=(8, 0), pady=(8, 0))

        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(inp, textvariable=self.url_var, width=70)
        self.url_entry.grid(row=2, column=0, columnspan=3, sticky="we", pady=(8, 0))
        inp.columnconfigure(0, weight=1)

        # 실행
        run_frm = ttk.Frame(self, padding=(12, 0))
        run_frm.pack(fill="x")
        self.start_btn = ttk.Button(run_frm, text="▶ 전사 및 저장 시작", command=self._start)
        self.start_btn.pack(side="left", pady=8)
        self.cancel_btn = ttk.Button(run_frm, text="취소", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)

        # 진행 단계
        prog = ttk.LabelFrame(self, text="진행 상태", padding=12)
        prog.pack(fill="both", expand=True, **pad)

        self.stage_labels = {}
        for i, s in enumerate(STAGES):
            lbl = ttk.Label(prog, text=f"○ {s}")
            lbl.grid(row=i, column=0, sticky="w", pady=2)
            self.stage_labels[s] = lbl

        self.progressbar = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.progressbar.grid(row=len(STAGES), column=0, sticky="we", pady=(10, 4))
        prog.columnconfigure(0, weight=1)

        self.detail_label = ttk.Label(prog, text="대기 중", foreground="#333")
        self.detail_label.grid(row=len(STAGES) + 1, column=0, sticky="w")

        self._update_mode()

    def _update_mode(self):
        local = self.mode.get() == "local"
        state_file = "normal" if local else "disabled"
        state_url = "disabled" if local else "normal"
        self.file_entry.configure(state=state_file)
        self.browse_btn.configure(state=state_file)
        self.url_entry.configure(state=state_url)

    def _browse(self):
        exts = " ".join(f"*{e}" for e in sorted(LOCAL_EXTS))
        path = filedialog.askopenfilename(
            title="영상 파일 선택",
            filetypes=[("영상 파일", exts), ("모든 파일", "*.*")],
        )
        if path:
            self.file_var.set(path)

    def _refresh_gpu_status(self):
        ok, detail = cuda_available()
        if ok:
            self.gpu_label.configure(text=f"● CUDA 사용 가능 — {detail}", foreground="#0a0")
        else:
            self.gpu_label.configure(text=f"● CUDA 사용 불가 — {detail}", foreground="#c00")

    def _open_settings(self):
        SettingsDialog(self, self.config_obj, on_save=lambda c: self._refresh_gpu_status())

    # ---------- 실행 ----------
    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        missing = self.config_obj.is_complete()
        if missing:
            messagebox.showwarning("설정 필요", "다음 설정이 필요합니다:\n- " + "\n- ".join(missing))
            self._open_settings()
            return

        if self.mode.get() == "local":
            source = self.file_var.get().strip()
            if not source:
                messagebox.showwarning("입력 필요", "영상 파일을 선택하세요.")
                return
            kind = "local"
        else:
            source = self.url_var.get().strip()
            if not source:
                messagebox.showwarning("입력 필요", "YouTube URL 을 입력하세요.")
                return
            kind = "youtube"

        self._reset_progress()
        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        self.worker = threading.Thread(
            target=self._run_pipeline, args=(kind, source), daemon=True
        )
        self.worker.start()

    def _run_pipeline(self, kind, source):
        def progress(stage, fraction, detail):
            self.msg_queue.put(("progress", stage, fraction, detail))

        try:
            page_id = pipeline.run(
                kind, source, self.config_obj, progress,
                should_cancel=self.cancel_flag.is_set,
            )
            self.msg_queue.put(("done", page_id))
        except PipelineError as e:
            self.msg_queue.put(("error", e.stage, e.user_message))
        except Exception as e:  # 최후 방어: 절대 프로세스가 죽지 않게
            self.msg_queue.put(("error", "unknown", f"예상치 못한 오류: {e}"))

    def _cancel(self):
        self.cancel_flag.set()
        self.detail_label.configure(text="취소 요청됨... 현재 단계 종료 후 중단됩니다.")

    # ---------- 진행 표시 ----------
    def _reset_progress(self):
        for s, lbl in self.stage_labels.items():
            lbl.configure(text=f"○ {s}", foreground="#000")
        self.progressbar["value"] = 0
        self.detail_label.configure(text="시작 중...")

    def _mark_stage(self, active_stage):
        reached = False
        for s in STAGES:
            lbl = self.stage_labels[s]
            if s == active_stage:
                lbl.configure(text=f"▶ {s}", foreground="#06c")
                reached = True
            elif not reached:
                lbl.configure(text=f"✔ {s}", foreground="#0a0")
            else:
                lbl.configure(text=f"○ {s}", foreground="#000")

    def _drain_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, stage, fraction, detail = msg
                    self._mark_stage(stage)
                    # 전체 진행률 = 완료 단계 + 현재 단계 내부 진행
                    try:
                        idx = STAGES.index(stage)
                    except ValueError:
                        idx = 0
                    overall = (idx + max(0.0, min(1.0, fraction))) / (len(STAGES) - 1)
                    self.progressbar["value"] = min(100, overall * 100)
                    self.detail_label.configure(text=detail)
                elif kind == "done":
                    for s in STAGES:
                        self.stage_labels[s].configure(text=f"✔ {s}", foreground="#0a0")
                    self.progressbar["value"] = 100
                    self.detail_label.configure(text="완료! Notion 에 저장되었습니다.")
                    self.start_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    messagebox.showinfo("완료", "Notion '영어듣기' 아래에 전사문 페이지가 생성되었습니다.")
                elif kind == "error":
                    _, stage, message = msg
                    self.detail_label.configure(text=f"오류({stage}): 재시작하면 실패 단계부터 재시도합니다.")
                    self.start_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    messagebox.showerror("오류", message)
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)


def main():
    app = App()
    app.mainloop()
