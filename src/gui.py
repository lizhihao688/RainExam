"""
RainExam GUI 入口
- 基于 tkinter（Python 内置，无需额外依赖）
- 替代 run.bat，Windows 用户直接双击 RainExam.exe 运行
"""

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk


# ──────────────────────────────────────────────
# 辅助：找到项目根目录 & .env 路径
# ──────────────────────────────────────────────

def get_base_dir() -> Path:
    """打包为 exe 后 sys.executable 指向 exe 所在目录；开发模式下用脚本所在目录的上一级"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def get_env_path() -> Path:
    return get_base_dir() / ".env"


def load_env_to_dict(env_path: Path) -> dict:
    """读取 .env 文件，返回 key->value 字典"""
    result = {}
    if not env_path.is_file():
        return result
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip("\"'")
            result[key.strip()] = val
    return result


def save_env(env_path: Path, data: dict):
    """将字典写回 .env 文件（追加/更新指定 key）"""
    lines = []
    written_keys = set()

    # 更新已存在的 key
    if env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in data:
                        lines.append(f'{key}={data[key]}\n')
                        written_keys.add(key)
                        continue
                lines.append(line)

    # 追加新 key
    for key, val in data.items():
        if key not in written_keys:
            lines.append(f'{key}={val}\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ──────────────────────────────────────────────
# 主界面
# ──────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("RainExam - 雨课堂考题提取 & AI 解答")
        self.resizable(True, True)
        self.minsize(620, 480)

        # 队列用于线程安全地更新日志
        self._log_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._load_saved_config()
        self._poll_log_queue()

    # ── UI 构建 ──

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # ── 配置区 ──
        cfg_frame = ttk.LabelFrame(self, text="配置", padding=8)
        cfg_frame.pack(fill="x", **pad)

        # Cookie
        ttk.Label(cfg_frame, text="XT_COOKIE:").grid(row=0, column=0, sticky="w")
        self.cookie_var = tk.StringVar()
        cookie_entry = ttk.Entry(cfg_frame, textvariable=self.cookie_var, width=60, show="*")
        cookie_entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(4, 0))
        ttk.Button(cfg_frame, text="显示/隐藏", width=9,
                   command=lambda: cookie_entry.config(
                       show="" if cookie_entry.cget("show") == "*" else "*"
                   )).grid(row=0, column=4, padx=(4, 0))

        # AI API Key
        ttk.Label(cfg_frame, text="AI_API_KEY:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.api_key_var = tk.StringVar()
        ak_entry = ttk.Entry(cfg_frame, textvariable=self.api_key_var, width=60, show="*")
        ak_entry.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(4, 0), pady=(4, 0))
        ttk.Button(cfg_frame, text="显示/隐藏", width=9,
                   command=lambda: ak_entry.config(
                       show="" if ak_entry.cget("show") == "*" else "*"
                   )).grid(row=1, column=4, padx=(4, 0), pady=(4, 0))

        # AI Base URL
        ttk.Label(cfg_frame, text="AI_BASE_URL:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.base_url_var = tk.StringVar()
        ttk.Entry(cfg_frame, textvariable=self.base_url_var, width=60).grid(
            row=2, column=1, columnspan=3, sticky="ew", padx=(4, 0), pady=(4, 0))
        ttk.Label(cfg_frame, text="(可留空，默认 OpenAI)").grid(row=2, column=4, sticky="w", padx=(4, 0))

        # AI Model
        ttk.Label(cfg_frame, text="AI_MODEL:").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.model_var = tk.StringVar(value="gpt-4o-mini")
        ttk.Entry(cfg_frame, textvariable=self.model_var, width=30).grid(
            row=3, column=1, sticky="ew", padx=(4, 0), pady=(4, 0))

        ttk.Button(cfg_frame, text="保存配置", command=self._save_config).grid(
            row=3, column=4, padx=(4, 0), pady=(4, 0))

        cfg_frame.columnconfigure(1, weight=1)

        # ── 运行区 ──
        run_frame = ttk.LabelFrame(self, text="运行", padding=8)
        run_frame.pack(fill="x", **pad)

        ttk.Label(run_frame, text="试卷 ID:").grid(row=0, column=0, sticky="w")
        self.exam_id_var = tk.StringVar()
        ttk.Entry(run_frame, textvariable=self.exam_id_var, width=20).grid(
            row=0, column=1, sticky="w", padx=(4, 0))

        self.answer_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(run_frame, text="启用 AI 解答", variable=self.answer_var).grid(
            row=0, column=2, padx=(16, 0))

        self.run_btn = ttk.Button(run_frame, text="开始运行", command=self._run)
        self.run_btn.grid(row=0, column=3, padx=(16, 0))

        run_frame.columnconfigure(1, weight=1)

        # ── 日志区 ──
        log_frame = ttk.LabelFrame(self, text="运行日志", padding=8)
        log_frame.pack(fill="both", expand=True, **pad)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, state="disabled", height=15, font=("Consolas", 9),
            wrap="word", bg="#1e1e1e", fg="#d4d4d4", insertbackground="white"
        )
        self.log_text.pack(fill="both", expand=True)

        ttk.Button(log_frame, text="清空日志", command=self._clear_log).pack(
            anchor="e", pady=(4, 0))

        # ── 状态栏 ──
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, anchor="w",
                  relief="sunken").pack(fill="x", side="bottom", ipady=2)

    # ── 配置加载 / 保存 ──

    def _load_saved_config(self):
        env = load_env_to_dict(get_env_path())
        if env.get("XT_COOKIE"):
            self.cookie_var.set(env["XT_COOKIE"])
        if env.get("AI_API_KEY"):
            self.api_key_var.set(env["AI_API_KEY"])
        if env.get("AI_BASE_URL"):
            self.base_url_var.set(env["AI_BASE_URL"])
        if env.get("AI_MODEL"):
            self.model_var.set(env["AI_MODEL"])

    def _save_config(self):
        env_path = get_env_path()

        # 如果 .env 不存在，从 .env.example 复制
        if not env_path.is_file():
            example = get_base_dir() / ".env.example"
            if example.is_file():
                import shutil
                shutil.copy(example, env_path)
            else:
                env_path.touch()

        data = {}
        if self.cookie_var.get().strip():
            data["XT_COOKIE"] = self.cookie_var.get().strip()
        if self.api_key_var.get().strip():
            data["AI_API_KEY"] = self.api_key_var.get().strip()
        if self.base_url_var.get().strip():
            data["AI_BASE_URL"] = self.base_url_var.get().strip()
        if self.model_var.get().strip():
            data["AI_MODEL"] = self.model_var.get().strip()

        save_env(env_path, data)
        self.status_var.set("配置已保存到 .env")
        messagebox.showinfo("保存成功", f"配置已保存到:\n{env_path}")

    # ── 运行逻辑 ──

    def _run(self):
        exam_id = self.exam_id_var.get().strip()
        if not exam_id:
            messagebox.showwarning("提示", "请先填写试卷 ID")
            return

        cookie = self.cookie_var.get().strip()
        if not cookie:
            messagebox.showwarning("提示", "请先填写 XT_COOKIE\n\n获取方式：\n1. 登录雨课堂在线，进入考试页面\n2. 按 F12 → Network → 刷新\n3. 点击任意请求 → 复制 Cookie 值")
            return

        if self.answer_var.get() and not self.api_key_var.get().strip():
            messagebox.showwarning("提示", "启用 AI 解答需要填写 AI_API_KEY")
            return

        self.run_btn.config(state="disabled")
        self.status_var.set(f"正在处理试卷 {exam_id}...")
        self._log(f"[开始] 试卷 ID={exam_id}  AI解答={'开启' if self.answer_var.get() else '关闭'}")

        # 在子线程中运行，避免阻塞 UI
        t = threading.Thread(target=self._run_in_thread, args=(exam_id,), daemon=True)
        t.start()

    def _run_in_thread(self, exam_id: str):
        import io

        # 将 print 输出重定向到日志队列
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        class QueueWriter(io.TextIOBase):
            def __init__(self, q: queue.Queue):
                self._q = q
            def write(self, s: str):
                if s and s != "\n":
                    self._q.put(s)
                return len(s)
            def flush(self):
                pass

        sys.stdout = QueueWriter(self._log_queue)
        sys.stderr = QueueWriter(self._log_queue)

        try:
            # 设置环境变量（从界面取值）
            os.environ["XT_COOKIE"] = self.cookie_var.get().strip()
            if self.api_key_var.get().strip():
                os.environ["AI_API_KEY"] = self.api_key_var.get().strip()
            if self.base_url_var.get().strip():
                os.environ["AI_BASE_URL"] = self.base_url_var.get().strip()
            if self.model_var.get().strip():
                os.environ["AI_MODEL"] = self.model_var.get().strip()

            # 切换到项目根目录（保证 .env 和输出文件路径正确）
            base = get_base_dir()
            os.chdir(base)

            # 调用核心逻辑
            # 动态 import，确保 sys.path 里有 src/
            src_dir = str(Path(__file__).parent if not getattr(sys, "frozen", False) else base / "src")
            if src_dir not in sys.path:
                sys.path.insert(0, src_dir)

            from extract_questions import (
                fetch_exam_paper,
                extract_questions,
                answer_questions,
                write_pages,
                resolve_ai_config,
            )
            import argparse

            json_path = str(base / f"exam_{exam_id}.json")
            fetch_exam_paper(exam_id, os.environ["XT_COOKIE"], json_path)

            print("正在提取题目...")
            questions = extract_questions(json_path)
            if not questions:
                print("未提取到任何题目，请检查 Cookie 或试卷 ID")
                self._log_queue.put("[失败] 未提取到题目")
                return

            print(f"共提取到 {len(questions)} 道题")

            answers = None
            if self.answer_var.get():
                args_ns = argparse.Namespace(
                    ai_api_key=self.api_key_var.get().strip() or None,
                    ai_base_url=self.base_url_var.get().strip() or None,
                    ai_model=self.model_var.get().strip() or None,
                )
                ai_cfg = resolve_ai_config(args_ns)
                answers = answer_questions(
                    questions,
                    api_key=ai_cfg["api_key"],
                    base_url=ai_cfg["base_url"],
                    model=ai_cfg["model"],
                )

            output_dir = str(base)
            write_pages(questions, output_dir, exam_id, answers)

            self._log_queue.put(f"[完成] 输出目录: {base}")
            self.after(0, lambda: self.status_var.set("完成！"))
            self.after(0, lambda: messagebox.showinfo(
                "完成",
                f"运行完成！\n共 {len(questions)} 道题\n输出目录: {base}"
            ))

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._log_queue.put(f"[错误] {e}\n{tb}")
            self.after(0, lambda: self.status_var.set("发生错误"))
            self.after(0, lambda: messagebox.showerror("错误", str(e)))
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            self.after(0, lambda: self.run_btn.config(state="normal"))

    # ── 日志 ──

    def _log(self, msg: str):
        self._log_queue.put(msg)

    def _poll_log_queue(self):
        """每 100ms 检查队列，将新消息写入日志控件"""
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
