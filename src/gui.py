"""
RainExam GUI 入口
- 基于 tkinter（Python 内置，无需额外依赖）
- 通过启动 Chrome/Edge + CDP 自动获取雨课堂 Cookie
- 替代 run.bat，Windows 用户直接双击 RainExam.exe 运行
"""

import base64
import json
import os
import queue
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.request
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
# Chrome CDP 自动获取 Cookie
# ──────────────────────────────────────────────

_XT_LOGIN_URL = "https://www.yuketang.cn/v2/web/index"
_LOGIN_COOKIE_KEY = "x_access_token"


def find_chrome() -> str | None:
    """在系统中查找 Chrome 或 Edge 浏览器可执行文件路径"""
    candidates = []
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def find_free_port() -> int:
    """找一个可用的本地端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_chrome(port: int, url: str) -> tuple[subprocess.Popen | None, str | None]:
    """启动 Chrome/Edge 并开启 CDP 调试端口，返回 (进程, 错误信息)"""
    chrome = find_chrome()
    if not chrome:
        return None, "未找到 Chrome 或 Edge 浏览器，请先安装"

    profile_dir = tempfile.mkdtemp(prefix="rainexam_")
    try:
        proc = subprocess.Popen([
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-popup-blocking",
            url,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return None, f"启动浏览器失败: {e}"

    return proc, None


# ── 最小 WebSocket 客户端（仅用于 CDP 单次通信）──

def _ws_handshake(sock: socket.socket, host: str, port: int, path: str):
    """完成 WebSocket 握手"""
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(request.encode())

    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("WebSocket 握手失败：连接关闭")
        response += chunk

    if b"101" not in response.split(b"\r\n")[0]:
        raise ConnectionError("WebSocket 握手失败：服务端未返回 101")


def _ws_send(sock: socket.socket, data: str):
    """发送 WebSocket 文本帧（客户端→服务端需 mask）"""
    payload = data.encode("utf-8")
    mask_key = os.urandom(4)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    header = bytearray()
    header.append(0x81)  # FIN + text opcode
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack(">Q", length))
    header.extend(mask_key)

    sock.sendall(header + masked)


def _ws_recv(sock: socket.socket) -> str:
    """接收一个 WebSocket 文本帧"""
    header = bytearray()
    while len(header) < 2:
        header.extend(sock.recv(2 - len(header)))

    opcode = header[0] & 0x0F
    masked = (header[1] & 0x80) != 0
    length = header[1] & 0x7F

    if length == 126:
        raw = sock.recv(2)
        length = struct.unpack(">H", raw)[0]
    elif length == 127:
        raw = sock.recv(8)
        length = struct.unpack(">Q", raw)[0]

    mask_key = sock.recv(4) if masked else None

    data = b""
    while len(data) < length:
        chunk = sock.recv(min(length - len(data), 65536))
        if not chunk:
            break
        data += chunk

    if masked and mask_key:
        data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))

    # 忽略非文本帧（ping/close 等）
    if opcode == 0x8:  # close
        return ""
    if opcode == 0x9:  # ping
        _ws_send(sock, data.decode("utf-8", errors="ignore"))  # pong
        return _ws_recv(sock)
    if opcode != 0x1:  # 非 text
        return ""

    return data.decode("utf-8")


def get_cookies_cdp(port: int) -> list[dict]:
    """通过 CDP 获取当前页面所有 Cookie"""
    # 1. 通过 HTTP 获取页面列表，找到 WebSocket 调试 URL
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3)
    pages = json.loads(resp.read())

    if not pages:
        return []

    # 优先找雨课堂相关页面，否则取第一个
    target = None
    for page in pages:
        url = page.get("url", "")
        if "xuetangx" in url or "yuketang" in url:
            target = page
            break
    if not target:
        target = pages[0]

    ws_url = target.get("webSocketDebuggerUrl", "")
    if not ws_url:
        return []

    # 2. 解析 ws://127.0.0.1:port/path
    match = re.match(r"ws://([^:]+):(\d+)(/.*)", ws_url)
    if not match:
        return []

    host, ws_port, path = match.group(1), int(match.group(2)), match.group(3)

    # 3. 连接 WebSocket 并发送 CDP 命令获取 Cookie
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((host, ws_port))
        _ws_handshake(sock, host, ws_port, path)

        cmd = {"id": 1, "method": "Network.getCookies", "params": {}}
        _ws_send(sock, json.dumps(cmd))

        # 读取响应（可能收到多个帧，找 id=1 的那个）
        for _ in range(10):
            raw = _ws_recv(sock)
            if not raw:
                continue
            try:
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    return msg.get("result", {}).get("cookies", [])
            except json.JSONDecodeError:
                continue

        return []
    finally:
        sock.close()


def open_login_browser(callback):
    """
    启动 Chrome/Edge 登录雨课堂，自动检测登录成功后获取 Cookie。
    callback(cookies, error) 在子线程中被调用。
    """
    port = find_free_port()
    proc, error = start_chrome(port, _XT_LOGIN_URL)

    if error:
        callback(None, error=error)
        return

    # 等待 Chrome 启动，CDP 就绪
    time.sleep(2)

    # 轮询检测 Cookie（最多等 5 分钟）
    max_wait = 300
    interval = 2
    for _ in range(max_wait // interval):
        try:
            cookies = get_cookies_cdp(port)
            if any(c["name"] == _LOGIN_COOKIE_KEY for c in cookies):
                # 登录成功！拼接 Cookie 字符串
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                proc.terminate()
                callback(cookie_str, error=None)
                return
        except Exception:
            # CDP 可能还没就绪，或页面还在加载，忽略异常继续轮询
            pass
        time.sleep(interval)

    # 超时
    try:
        proc.terminate()
    except Exception:
        pass
    callback(None, error="登录超时（5分钟），请重试")


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
        cookie_entry = ttk.Entry(cfg_frame, textvariable=self.cookie_var, width=50, show="*")
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
        ak_entry = ttk.Entry(cfg_frame, textvariable=self.api_key_var, width=50, show="*")
        ak_entry.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(4, 0))
        ttk.Button(cfg_frame, text="显示/隐藏", width=9,
                   command=lambda: ak_entry.config(
                       show="" if ak_entry.cget("show") == "*" else "*"
                   )).grid(row=1, column=2, padx=(4, 0), pady=(4, 0))

        # AI Base URL
        ttk.Label(cfg_frame, text="AI_BASE_URL:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.base_url_var = tk.StringVar()
        ttk.Entry(cfg_frame, textvariable=self.base_url_var, width=50).grid(
            row=2, column=1, sticky="ew", padx=(4, 0), pady=(4, 0))
        ttk.Label(cfg_frame, text="(可留空，默认 OpenAI)").grid(
            row=2, column=2, columnspan=2, sticky="w", padx=(4, 0))

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

    # ── 登录自动获取 Cookie ──

    def _open_login_browser(self):
        """在独立线程中启动 Chrome 登录雨课堂，登录后自动回填 Cookie"""
        self.status_var.set("正在启动浏览器...")
        self._log("正在启动浏览器，请在弹出的 Chrome/Edge 中登录雨课堂...")

        def callback(cookies: str | None, error: str | None):
            # 此回调在子线程，需切回主线程操作 tkinter
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

        self.cookie_var.set(cookies)
        self.status_var.set("Cookie 已自动获取，请点「保存配置」")
        self._log(f"Cookie 已自动获取（{len(cookies)} 字符）")
        messagebox.showinfo("获取成功", "Cookie 已自动填入！\n请点击「保存配置」保存后再运行。")

    # ── 运行逻辑 ──

    def _run(self):
        exam_id = self.exam_id_var.get().strip()
        if not exam_id:
            messagebox.showwarning("提示", "请先填写试卷 ID")
            return

        cookie = self.cookie_var.get().strip()
        if not cookie:
            messagebox.showwarning("提示", "请先填写或自动获取 XT_COOKIE\n\n点击「登录自动获取」按钮即可")
            return

        if self.answer_var.get() and not self.api_key_var.get().strip():
            messagebox.showwarning("提示", "启用 AI 解答需要填写 AI_API_KEY")
            return

        self.run_btn.config(state="disabled")
        self.status_var.set(f"正在处理试卷 {exam_id}...")
        self._log(f"[开始] 试卷 ID={exam_id}  AI解答={'开启' if self.answer_var.get() else '关闭'}")

        t = threading.Thread(target=self._run_in_thread, args=(exam_id,), daemon=True)
        t.start()

    def _run_in_thread(self, exam_id: str):
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
    # app = App()
    # app.mainloop()
    s = find_chrome()
    print(s)
    start_chrome(find_free_port(), _XT_LOGIN_URL)


if __name__ == "__main__":
    main()
