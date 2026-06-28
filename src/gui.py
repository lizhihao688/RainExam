"""
RainExam GUI 入口
- 基于 tkinter（Python 内置，无需额外依赖）
- 支持通过内嵌浏览器（pywebview）自动获取雨课堂 Cookie
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

    for key, val in data.items():
        if key not in written_keys:
            lines.append(f'{key}={val}\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ──────────────────────────────────────────────
# Cookie 自动获取（pywebview）
# ──────────────────────────────────────────────

# 雨课堂登录入口页（进入后跳转考试系统）
_XT_LOGIN_URL = "https://passport.yuketang.cn/user-fe/login?next=https://examination.xuetangx.com"

# 登录成功标志：Cookie 中包含 x_access_token
_LOGIN_COOKIE_KEY = "x_access_token"

# 轮询检测登录的 JS（注入到页面中）
_POLL_JS = """
(function startLoginPoll() {
    var timer = setInterval(function() {
        var cookies = document.cookie;
        if (cookies.indexOf('x_access_token') !== -1) {
            clearInterval(timer);
            window.pywebview.api.on_login_detected(cookies);
        }
    }, 800);
})();
"""


def open_login_browser(callback):
    """
    在独立线程里打开 pywebview 浏览器窗口，用户登录后自动回调 callback(cookie_str)。
    callback 在 pywebview 线程中被调用，需自行切换到主线程（通过 tkinter.after）。
    """
    try:
        import webview
    except ImportError:
        callback(None, error="未安装 pywebview，请先执行: pip install pywebview")
        return

    class Api:
        """暴露给 JS 调用的 Python 对象"""
        def __init__(self):
            self._window = None

        def set_window(self, w):
            self._window = w

        def on_login_detected(self, cookies: str):
            """JS 检测到登录成功后调用此方法"""
            callback(cookies, error=None)
            # 延迟关闭窗口，避免 JS 调用还没返回就销毁
            if self._window:
                threading.Timer(0.5, self._window.destroy).start()

    api = Api()
    window = webview.create_window(
        title="登录雨课堂 - 登录成功后窗口将自动关闭",
        url=_XT_LOGIN_URL,
        js_api=api,
        width=1024,
        height=700,
    )
    api.set_window(window)

    def on_loaded():
        # 每次页面加载完成后注入轮询脚本
        try:
            window.evaluate_js(_POLL_JS)
        except Exception:
            pass

    window.events.loaded += on_loaded

    # webview.start() 会阻塞直到窗口关闭
    webview.start(debug=False)


# ──────────────────────────────────────────────
# 主界面
# ──────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("RainExam - 雨课堂考题提取 & AI 解答")
        self.resizable(True, True)
        self.minsize(640, 520)

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

        # Cookie 行
        ttk.Label(cfg_frame, text="XT_COOKIE:").grid(row=0, column=0, sticky="w")
        self.cookie_var = tk.StringVar()
        cookie_entry = ttk.Entry(cfg_frame, textvariable=self.cookie_var, width=52, show="*")
        cookie_entry.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(cfg_frame, text="显示/隐藏", width=9,
                   command=lambda: cookie_entry.config(
                       show="" if cookie_entry.cget("show") == "*" else "*"
                   )).grid(row=0, column=2, padx=(4, 0))
        # 登录获取按钮
        ttk.Button(cfg_frame, text="登录自动获取", width=12,
                   command=self._open_login_browser).grid(row=0, column=3, padx=(4, 0))

        # AI API Key
        ttk.Label(cfg_frame, text="AI_API_KEY:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.api_key_var = tk.StringVar()
        ak_entry = ttk.Entry(cfg_frame, textvariable=self.api_key_var, width=52, show="*")
        ak_entry.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(4, 0))
        ttk.Button(cfg_frame, text="显示/隐藏", width=9,
                   command=lambda: ak_entry.config(
                       show="" if ak_entry.cget("show") == "*" else "*"
                   )).grid(row=1, column=2, padx=(4, 0), pady=(4, 0))

        # AI Base URL
        ttk.Label(cfg_frame, text="AI_BASE_URL:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.base_url_var = tk.StringVar()
        ttk.Entry(cfg_frame, textvariable=self.base_url_var, width=52).grid(
            row=2, column=1, sticky="ew", padx=(4, 0), pady=(4, 0))
        ttk.Label(cfg_frame, text="(可留空，默认 OpenAI)").grid(row=2, column=2, columnspan=2, sticky="w", padx=(4, 0))

        # AI Model + 保存按钮
        ttk.Label(cfg_frame, text="AI_MODEL:").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.model_var = tk.StringVar(value="gpt-4o-mini")
        ttk.Entry(cfg_frame, textvariable=self.model_var, width=30).grid(
            row=3, column=1, sticky="w", padx=(4, 0), pady=(4, 0))
        ttk.Button(cfg_frame, text="保存配置", command=self._save_config).grid(
            row=3, column=3, padx=(4, 0), pady=(4, 0))

        cfg_frame.columnconfigure(1, weight=1)

        # ── 运行区 ──
        run_frame = ttk.LabelFrame(self, text="运行", padding=8)
        run_frame.pack(fill="x", **pad)

        # 模式选择
        ttk.Label(run_frame, text="模式:").grid(row=0, column=0, sticky="w")
        self.mode_var = tk.StringVar(value="考试")
        mode_cb = ttk.Combobox(
            run_frame, textvariable=self.mode_var, width=8, state="readonly",
            values=["考试", "Quiz"],
        )
        mode_cb.grid(row=0, column=1, sticky="w", padx=(4, 0))
        mode_cb.bind("<<ComboboxSelected>>", self._on_mode_change)

        ttk.Label(run_frame, text="ID:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.exam_id_var = tk.StringVar()
        self.id_entry = ttk.Entry(run_frame, textvariable=self.exam_id_var, width=20)
        self.id_entry.grid(row=0, column=3, sticky="w", padx=(4, 0))

        # Classroom ID（仅 Quiz 模式显示）
        self._cid_label = ttk.Label(run_frame, text="课堂 ID:")
        self.classroom_id_var = tk.StringVar()
        self._cid_entry = ttk.Entry(run_frame, textvariable=self.classroom_id_var, width=15)

        self.answer_var = tk.BooleanVar(value=False)
        self.ai_cb = ttk.Checkbutton(run_frame, text="启用 AI 解答", variable=self.answer_var)
        self.ai_cb.grid(row=0, column=4, padx=(16, 0))

        self.run_btn = ttk.Button(run_frame, text="开始运行", command=self._run)
        self.run_btn.grid(row=0, column=5, padx=(16, 0))

        run_frame.columnconfigure(3, weight=1)

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
        if env.get("QUIZ_ID"):
            self.exam_id_var.set(env["QUIZ_ID"])
        if env.get("CLASSROOM_ID"):
            self.classroom_id_var.set(env["CLASSROOM_ID"])

    def _save_config(self):
        env_path = get_env_path()
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
        # 保存当前模式对应的 ID
        if self.mode_var.get() == "Quiz" and self.exam_id_var.get().strip():
            data["QUIZ_ID"] = self.exam_id_var.get().strip()
        if self.classroom_id_var.get().strip():
            data["CLASSROOM_ID"] = self.classroom_id_var.get().strip()

        save_env(env_path, data)
        self.status_var.set("配置已保存到 .env")
        messagebox.showinfo("保存成功", f"配置已保存到:\n{env_path}")

    # ── 登录自动获取 Cookie ──

    def _open_login_browser(self):
        """在独立线程中打开 pywebview 浏览器，登录后自动回填 Cookie"""
        self.status_var.set("正在打开登录窗口...")
        self._log("正在打开雨课堂登录窗口，请在弹出的浏览器中完成登录...")

        def callback(cookies: str | None, error: str | None):
            # 此回调在 webview 线程，需切回主线程操作 tkinter
            self.after(0, lambda: self._on_login_result(cookies, error))

        t = threading.Thread(target=open_login_browser, args=(callback,), daemon=True)
        t.start()

    def _on_login_result(self, cookies: str | None, error: str | None):
        """登录结果回调（已切回主线程）"""
        if error:
            self.status_var.set("获取 Cookie 失败")
            messagebox.showerror("错误", error)
            return

        if not cookies:
            self.status_var.set("未检测到登录")
            messagebox.showwarning("提示", "未检测到登录 Cookie，请重试")
            return

        # document.cookie 返回 "key=val; key2=val2" 格式，已足够使用
        self.cookie_var.set(cookies)
        self.status_var.set("Cookie 已自动获取，请点「保存配置」")
        self._log(f"Cookie 已自动获取（{len(cookies)} 字符）")
        messagebox.showinfo("获取成功", "Cookie 已自动填入！\n请点击「保存配置」保存后再运行。")

    # ── 模式切换 ──

    def _on_mode_change(self, _event=None):
        """切换模式时调整 UI 状态"""
        is_quiz = self.mode_var.get() == "Quiz"
        if is_quiz:
            # Quiz 模式：显示课堂 ID 输入框，禁用 AI 解答
            self.answer_var.set(False)
            self.ai_cb.config(state="disabled")
            self._cid_label.grid(row=1, column=0, sticky="w", pady=(6, 0))
            self._cid_entry.grid(row=1, column=1, columnspan=2, sticky="w", padx=(4, 0), pady=(6, 0))
            self.status_var.set("Quiz 模式：答案从 API 直接获取，无需 AI 解答")
        else:
            # 考试模式：隐藏课堂 ID，启用 AI 解答
            self.ai_cb.config(state="normal")
            self._cid_label.grid_forget()
            self._cid_entry.grid_forget()
            self.status_var.set("考试模式")

    # ── 运行逻辑 ──

    def _run(self):
        mode = self.mode_var.get()
        id_val = self.exam_id_var.get().strip()
        is_quiz = mode == "Quiz"

        id_label = "Quiz ID" if is_quiz else "试卷 ID"
        if not id_val:
            messagebox.showwarning("提示", f"请先填写{id_label}")
            return

        cookie = self.cookie_var.get().strip()
        if not cookie:
            messagebox.showwarning("提示", "请先填写或自动获取 XT_COOKIE\n\n点击「登录自动获取」按钮即可")
            return

        if not is_quiz and self.answer_var.get() and not self.api_key_var.get().strip():
            messagebox.showwarning("提示", "启用 AI 解答需要填写 AI_API_KEY")
            return

        self.run_btn.config(state="disabled")
        self.status_var.set(f"正在处理 {id_label} {id_val}...")
        self._log(f"[开始] 模式={mode}  {id_label}={id_val}"
                  f"{'  AI解答=开启' if self.answer_var.get() else ''}")

        t = threading.Thread(target=self._run_in_thread, args=(id_val, mode), daemon=True)
        t.start()

    def _run_in_thread(self, id_val: str, mode: str):
        import io

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
            os.environ["XT_COOKIE"] = self.cookie_var.get().strip()
            if self.api_key_var.get().strip():
                os.environ["AI_API_KEY"] = self.api_key_var.get().strip()
            if self.base_url_var.get().strip():
                os.environ["AI_BASE_URL"] = self.base_url_var.get().strip()
            if self.model_var.get().strip():
                os.environ["AI_MODEL"] = self.model_var.get().strip()

            base = get_base_dir()
            os.chdir(base)

            src_dir = str(Path(__file__).parent if not getattr(sys, "frozen", False) else base / "src")
            if src_dir not in sys.path:
                sys.path.insert(0, src_dir)

            if mode == "Quiz":
                self._run_quiz(id_val, base)
            else:
                self._run_exam(id_val, base)

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

    def _run_exam(self, exam_id: str, base: Path):
        """考试模式：原有逻辑"""
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

    def _run_quiz(self, quiz_id: str, base: Path):
        """Quiz 模式：拉取图片化试卷并生成 HTML 报告"""
        from quiz import process_quiz

        classroom_id = self.classroom_id_var.get().strip()
        params: dict = {"quiz_id": quiz_id}
        if classroom_id:
            params["classroom_id"] = classroom_id

        output_dir = str(base)
        html_path = process_quiz(
            cookie=os.environ["XT_COOKIE"],
            output_dir=output_dir,
            **params,
        )

        if html_path:
            self._log_queue.put(f"[完成] 报告: {html_path}")
            self.after(0, lambda: self.status_var.set("完成！"))
            self.after(0, lambda: messagebox.showinfo(
                "完成",
                f"Quiz 报告生成完成！\n文件: {html_path}"
            ))
        else:
            self._log_queue.put("[完成] 未生成报告")
            self.after(0, lambda: self.status_var.set("完成（无数据）"))

    # ── 日志 ──

    def _log(self, msg: str):
        self._log_queue.put(msg)

    def _poll_log_queue(self):
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
