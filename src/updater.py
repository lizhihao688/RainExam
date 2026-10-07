"""
RainExam 自动更新模块

功能：
- 向 GitHub Releases 查询最新版本，与当前版本比较（语义化比较，支持 v2.1.0 / 2.1.0-beta.1）
- 查看更新说明、打开下载页面
- 下载新版 exe 并校验 SHA256（若 Release 提供了 .sha256 文件）
- Windows 下自动替换自身并重启（打包为 exe 运行时）

设计要点：
- 只用标准库 + 可选 httpx（项目本就依赖 httpx，打包进 exe；缺失时自动回退到 urllib）
- 自动检查结果会缓存到 .rainexam_update.json，默认 6 小时内不重复联网，
  用户选择「稍后再说」的版本不会每次启动都弹窗
- 环境变量 RAINEXAM_DISABLE_UPDATE_CHECK=1 可完全关闭自动检查
- 环境变量 RAINEXAM_REPO=owner/repo 可切换更新源（fork 场景）

命令行自检：
    python src/updater.py check            # 检查更新
    python src/updater.py check --force    # 忽略缓存强制检查
    python src/updater.py download --out . # 下载新版 exe 到指定目录
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

try:  # httpx 是项目依赖，打包时一定存在；源码环境可能没有
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

APP_NAME = "RainExam"
EXE_NAME = "RainExam.exe"                 # Release 中作为更新源的资源名
UPDATE_FILE_NAME = "RainExam_update.exe"  # 下载后的临时文件名（避免覆盖运行中的自身）
BACKUP_SUFFIX = ".old"                    # 替换时旧版本的备份后缀，用于失败回滚
ERROR_LOG_NAME = "RainExam_update.log"    # 替换脚本失败时写下的日志（程序启动时读取并提示）
DEFAULT_REPO = "lizhihao688/RainExam"
API_RELEASES_URL = "https://api.github.com/repos/{repo}/releases?per_page=20"
USER_AGENT = "RainExam-Updater"
STATE_FILE = ".rainexam_update.json"
CHECK_INTERVAL_SECONDS = 6 * 3600   # 自动检查的最小间隔
META_TIMEOUT = 8.0                  # 查询 Release 元信息的超时（秒）
DOWNLOAD_TIMEOUT = 30.0             # 下载连接超时（秒）

DISABLE_ENV = "RAINEXAM_DISABLE_UPDATE_CHECK"
REPO_ENV = "RAINEXAM_REPO"

# Windows 进程创建标志（用隐藏控制台运行替换脚本，且不随本进程退出而结束）
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

ProgressCallback = Optional[Callable[[int, int], None]]


class UpdateError(Exception):
    """更新过程中的可预期错误（网络失败、校验失败等）"""


# ──────────────────────────────────────────────
# 版本号解析与比较
# ──────────────────────────────────────────────

_VER_RE = re.compile(r"^\s*[vV]?(\d+(?:\.\d+)*)\s*(?:[-_+.]?\s*(.*))?$")


def parse_version(text: str) -> Tuple[Tuple[int, ...], str]:
    """
    解析版本号字符串。

    'v2.0.1'        -> ((2, 0, 1), '')
    '2.1.0-beta.1'  -> ((2, 1, 0), 'beta.1')
    'dev'           -> ((), '')
    """
    if not text:
        return (), ""
    m = _VER_RE.match(str(text).strip())
    if not m:
        return (), ""
    nums = tuple(int(p) for p in m.group(1).split(".") if p != "")
    pre = (m.group(2) or "").strip()
    return nums, pre


def normalize_version(text: str) -> str:
    """统一成不带 v 前缀的字符串，便于展示"""
    nums, pre = parse_version(text)
    if not nums:
        return str(text or "").strip()
    base = ".".join(str(n) for n in nums)
    return f"{base}-{pre}" if pre else base


def is_newer(latest: str, current: str) -> bool:
    """
    判断 latest 是否比 current 新。
    数字段逐位比较（缺位补 0，因此 2.1 == 2.1.0）；
    数字相同时，正式版比同号预发布版新（2.1.0 > 2.1.0-beta）。
    任一侧无法解析时返回 False（宁可不提示，也不误提示）。
    """
    lv, lpre = parse_version(latest)
    cv, cpre = parse_version(current)
    if not lv or not cv:
        return False
    n = max(len(lv), len(cv))
    a = lv + (0,) * (n - len(lv))
    b = cv + (0,) * (n - len(cv))
    if a != b:
        return a > b
    return (not lpre) and bool(cpre)


# ──────────────────────────────────────────────
# Release 信息
# ──────────────────────────────────────────────

@dataclass
class ReleaseInfo:
    tag: str = ""
    version: str = ""
    name: str = ""
    notes: str = ""
    html_url: str = ""
    published_at: str = ""
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    checksum_url: str = ""
    prerelease: bool = False

    @property
    def has_asset(self) -> bool:
        return bool(self.asset_url)

    def download_page(self) -> str:
        """优先指向 exe 直链，其次 Release 页面"""
        return self.asset_url or self.html_url


def get_current_version() -> str:
    """当前程序版本：优先 src/version.py（打包时已注入），其次包元数据"""
    raw = ""
    try:
        from version import __version__ as raw  # type: ignore
    except Exception:
        try:
            from importlib.metadata import version as _pkg_version
            raw = _pkg_version("rainexam")
        except Exception:
            raw = "0.0.0"
    return normalize_version(str(raw))


def plain_notes(text: str, limit: int = 900) -> str:
    """把 Release 说明的 Markdown 粗略转成纯文本，供消息框展示"""
    if not text:
        return ""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)            # 图片
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)           # 链接
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)      # 标题
    t = t.replace("**", "").replace("__", "").replace("`", "")
    t = re.sub(r"^\s*[-*+]\s+", "· ", t, flags=re.M)         # 列表
    # 表格：去掉分隔行，单元格之间用空格连接
    t = re.sub(r"^[ \t]*\|?[ \t:|-]{5,}\|?[ \t]*$", "", t, flags=re.M)
    t = re.sub(r"^[ \t]*\|(.+?)\|[ \t]*$",
               lambda m: "  ".join(c.strip() for c in m.group(1).split("|")),
               t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if limit and len(t) > limit:
        t = t[:limit].rstrip() + "\n…（完整说明见 Release 页面）"
    return t


def is_check_disabled() -> bool:
    return str(os.environ.get(DISABLE_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


# ──────────────────────────────────────────────
# HTTP 基础
# ──────────────────────────────────────────────

def _headers(accept: str = "application/vnd.github+json") -> Dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": accept}


def _describe_http_error(exc: Exception) -> str:
    # httpx 用 response.status_code，urllib 的 HTTPError 用 .code
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if status == 404:
        return "更新源不存在（Release 尚未发布或仓库地址有误）"
    if status in (403, 429):
        return "GitHub 接口访问受限（可能是请求过于频繁），请稍后再试"
    if status:
        return f"GitHub 返回 HTTP {status}"
    return f"网络请求失败：{exc}"


def http_get_text(url: str, timeout: float = META_TIMEOUT,
                  accept: str = "application/vnd.github+json") -> str:
    """GET 一个文本资源，httpx 优先，回退 urllib"""
    if httpx is not None:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True,
                              headers=_headers(accept)) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:  # noqa: BLE001 - 统一转成 UpdateError
            raise UpdateError(_describe_http_error(exc)) from exc

    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers=_headers(accept))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise UpdateError(_describe_http_error(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"网络请求失败：{exc}") from exc


def _pick_assets(assets) -> Tuple[dict, dict]:
    """从 Release 资源里挑出 exe 和校验文件"""
    exe, checksum = {}, {}
    for asset in assets or []:
        name = str(asset.get("name") or "")
        low = name.lower()
        if low.endswith(".exe"):
            if not exe or name == EXE_NAME:
                exe = asset
        elif "sha256" in low:
            checksum = asset
    return exe, checksum


def _version_key(tag: str):
    """用于在多个 Release 中挑出「版本号最大」的那个"""
    nums, pre = parse_version(tag)
    if not nums:
        return None
    return (nums, 0 if pre else 1)


def _parse_release(data: dict) -> ReleaseInfo:
    tag = str(data.get("tag_name") or "")
    exe, checksum = _pick_assets(data.get("assets"))
    return ReleaseInfo(
        tag=tag,
        version=normalize_version(tag),
        name=str(data.get("name") or tag),
        notes=str(data.get("body") or ""),
        html_url=str(data.get("html_url") or ""),
        published_at=str(data.get("published_at") or ""),
        asset_name=str(exe.get("name") or ""),
        asset_url=str(exe.get("browser_download_url") or ""),
        asset_size=int(exe.get("size") or 0),
        checksum_url=str(checksum.get("browser_download_url") or ""),
        prerelease=bool(data.get("prerelease")),
    )


def fetch_latest_release(repo: str = DEFAULT_REPO, timeout: float = META_TIMEOUT,
                         include_prerelease: bool = False) -> ReleaseInfo:
    """
    取版本号最高的一个正式版 Release。

    这里用列表接口而不是 /releases/latest：后者是按「发布时间」排序的，
    如果先发了 v2.1.0、之后又给老版本发一个 v2.0.3 热修，
    /releases/latest 会返回 v2.0.3，把真正更新的版本遮住。
    """
    url = API_RELEASES_URL.format(repo=repo)
    raw = http_get_text(url, timeout=timeout)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise UpdateError("更新源返回内容无法解析") from exc
    if not isinstance(data, list):
        raise UpdateError("更新源返回内容格式不正确")

    best: Optional[dict] = None
    best_key = None
    for item in data:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        if item.get("prerelease") and not include_prerelease:
            continue
        key = _version_key(str(item.get("tag_name") or ""))
        if key is None:
            continue
        if best_key is None or key > best_key:
            best, best_key = item, key

    if best is None:
        raise UpdateError("该仓库还没有正式发布过版本")
    return _parse_release(best)


# ──────────────────────────────────────────────
# 校验下载文件
# ──────────────────────────────────────────────

def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _expected_sha256(text: str) -> str:
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text or "")
    return m.group(1).lower() if m else ""


def _dir_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(path), prefix=".rainexam_wtest_", delete=True):
            return True
    except Exception:
        return False


def _fallback_state_path() -> Path:
    """程序目录不可写时（例如装在 Program Files），把状态放到用户目录"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or tempfile.gettempdir()
    return Path(base) / APP_NAME / STATE_FILE


def _looks_like_exe(path: Path) -> bool:
    """粗略检查是否为 Windows 可执行文件（MZ 头）"""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


# ──────────────────────────────────────────────
# Windows 自替换脚本（纯 ASCII，避免编码问题）
#
# 约定（与 Python 侧配合）：
#   - 旧版本先改名为 <exe>.old 作为备份；新版本启动成功后会删掉它，
#     替换脚本以此确认"新版本真的起来了"，30 秒等不到就自动回滚
#   - 任何失败都会把原因写入 <exe 所在目录>/RainExam_update.log，
#     下次启动时 GUI 读取并提示用户，而不是让程序静默消失
# ──────────────────────────────────────────────

_UPDATE_BAT = r"""@echo off
setlocal
set "TARGET=%~1"
set "NEW=%~2"
set "OLDPID=%~3"
set "BAK=%TARGET%.old"
set "LOG=%~dp1RainExam_update.log"

rem clear the failure log of a previous run; it is only written on failure
if exist "%LOG%" del /F /Q "%LOG%" >nul 2>&1

rem ---- 1) wait for the old process to exit (max ~60s); on timeout change NOTHING ----
set /a WAIT=0
:waitloop
tasklist /FI "PID eq %OLDPID%" 2>nul | findstr /C:"%OLDPID%" >nul
if not errorlevel 1 (
    set /a WAIT+=1
    if %WAIT% LSS 60 (
        call :sleep
        goto waitloop
    )
    call :fail "old process %OLDPID% did not exit within 60 seconds; no files were changed"
    goto quit_fail
)

rem ---- 2) sanity check the downloaded file ----
if not exist "%NEW%" (
    call :fail "the downloaded update file was not found"
    goto restart_old
)

rem ---- 3) rename the old exe away; a running exe can be renamed, not overwritten ----
set /a TRY=0
:moveloop
move /Y "%TARGET%" "%BAK%" >nul 2>&1
if not errorlevel 1 goto place
set /a TRY+=1
if %TRY% LSS 30 (
    call :sleep
    goto moveloop
)
call :fail "cannot rename the old exe, locked by antivirus or another program"
goto restart_old

rem ---- 4) install the new exe and restart ----
:place
move /Y "%NEW%" "%TARGET%" >nul 2>&1
if errorlevel 1 (
    move /Y "%BAK%" "%TARGET%" >nul 2>&1
    call :fail "cannot write the new exe; the previous version has been restored"
    goto restart_old
)
cd /d "%~dp1"
start "" "%TARGET%"

rem the new version deletes the backup during startup: that is the success signal
set /a VWAIT=0
:verify
if not exist "%BAK%" goto quit_ok
set /a VWAIT+=1
if %VWAIT% LSS 30 (
    call :sleep
    goto verify
)
rem no signal after 30s: if the target is locked, the new version IS running
del /F /Q "%TARGET%" >nul 2>&1
if exist "%TARGET%" goto quit_ok
call :fail "the new version did not start within 30 seconds; the previous version has been restored"
move /Y "%BAK%" "%TARGET%" >nul 2>&1
start "" "%TARGET%"
goto quit_fail

:restart_old
start "" "%TARGET%"

:quit_fail
del "%~f0" >nul 2>&1
exit /b 1

:quit_ok
del "%~f0" >nul 2>&1
exit /b 0

:fail
>>"%LOG%" echo [%DATE% %TIME%] RainExam auto-update failed: %~1
>>"%LOG%" echo The previous version is kept as: %BAK%
exit /b 0

rem ~1s sleep; ping works even without a console (timeout does not)
:sleep
ping -n 2 127.0.0.1 >nul 2>&1
exit /b 0
"""


# ──────────────────────────────────────────────
# 更新遗留物：确认成功 / 提示失败
# ──────────────────────────────────────────────

def get_started_exe() -> Optional[Path]:
    """以打包后的 Windows exe 运行时返回自身路径，否则 None"""
    if os.name == "nt" and getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None


def clear_update_backup() -> None:
    """
    启动成功时删除上次更新留下的 .old 备份。
    这同时是替换脚本判断「新版本是否真的起来了」的信号，应在启动早期调用。
    """
    exe = get_started_exe()
    if exe is None:
        return
    backup = exe.with_name(exe.name + BACKUP_SUFFIX)
    try:
        if backup.is_file():
            backup.unlink()
    except Exception:
        pass  # 删不掉不影响使用，下次启动再试


def take_update_error_log() -> str:
    """读取并删除上次更新失败留下的日志（只提示一次），返回日志内容"""
    exe = get_started_exe()
    if exe is None:
        return ""
    log = exe.with_name(ERROR_LOG_NAME)
    try:
        if log.is_file():
            text = log.read_text(encoding="utf-8", errors="replace").strip()
            log.unlink()
            return text
    except Exception:
        pass
    return ""


# ──────────────────────────────────────────────
# 对外主入口：UpdateManager
# ──────────────────────────────────────────────

class UpdateManager:
    """
    更新检查 / 下载 / 安装的对外接口。

    典型用法（GUI）：
        mgr = UpdateManager(base_dir=get_base_dir())
        release = mgr.check()          # None 表示无更新或本次跳过
        if mgr.last_error: ...
        path = mgr.download(release)   # 下载并校验
        mgr.install_and_restart(path)  # 替换自身并重启（仅打包后的 exe）
    """

    def __init__(self, base_dir: Path, repo: Optional[str] = None,
                 current_version: Optional[str] = None,
                 state_path: Optional[Path] = None):
        self.base_dir = Path(base_dir)
        self.repo = (repo or os.environ.get(REPO_ENV) or DEFAULT_REPO).strip()
        self.current_version = current_version or get_current_version()
        if state_path is not None:
            self.state_path = Path(state_path)
        else:
            candidate = self.base_dir / STATE_FILE
            # 装在只读目录时缓存会写不进去，退到用户目录，否则每次启动都要联网
            self.state_path = candidate if _dir_writable(self.base_dir) else _fallback_state_path()
        self.last_error: str = ""
        self.last_release: Optional[ReleaseInfo] = None
        self.skipped: bool = False
        self.skip_reason: str = ""   # "" / "interval"（未到检查间隔）/ "disabled"（已关闭）
        self._state: Dict[str, object] = self._load_state()

    # ── 状态缓存 ──

    def _load_state(self) -> Dict[str, object]:
        try:
            if self.state_path.is_file():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_state(self, **kwargs) -> None:
        self._state.update(kwargs)
        try:
            self.state_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass  # 目录只读时静默忽略

    def auto_check_due(self) -> bool:
        """距离上次成功检查是否已超过间隔"""
        try:
            last = float(self._state.get("last_check") or 0)
        except (TypeError, ValueError):
            last = 0
        return (time.time() - last) >= CHECK_INTERVAL_SECONDS

    def skipped_version(self) -> str:
        return str(self._state.get("skipped_version") or "")

    def mark_skipped(self, version: str) -> None:
        """记住用户本次忽略的版本，避免每次启动都弹窗"""
        self._save_state(skipped_version=version, skipped_at=time.time())

    def clear_skipped(self) -> None:
        self._save_state(skipped_version="")

    # ── 检查更新 ──

    def check(self, force: bool = False, include_prerelease: bool = False) -> Optional[ReleaseInfo]:
        """
        返回 ReleaseInfo 表示有新版本；返回 None 表示：
        已是最新 / 自动检查被关闭 / 未到检查间隔 / 用户已忽略该版本（此时 self.skipped=True）
        出错时返回 None 并把原因写入 self.last_error
        未真正联网的两种情况记录在 self.skip_reason（"interval" / "disabled"）

        注意：force=True（用户手动点「检查更新」）会忽略「已关闭」和「检查间隔」限制，
        用户明确要求时一定联网检查。
        """
        self.last_error = ""
        self.last_release = None
        self.skipped = False
        self.skip_reason = ""

        if not force:
            if is_check_disabled():
                self.skip_reason = "disabled"
                return None
            if not self.auto_check_due():
                self.skip_reason = "interval"
                return None

        try:
            release = fetch_latest_release(repo=self.repo, include_prerelease=include_prerelease)
        except UpdateError as exc:
            self.last_error = str(exc)
            return None
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"检查更新失败：{exc}"
            return None

        # 检查成功，记录时间（失败不记录，下次启动可重试）
        self._save_state(last_check=time.time(), last_seen_version=release.version)

        if release.prerelease and not include_prerelease:
            return None
        if not release.version or not is_newer(release.version, self.current_version):
            return None

        self.last_release = release
        if self.skipped_version() == release.version:
            self.skipped = True
        return release

    # ── 下载 ──

    def download(self, release: ReleaseInfo, dest_dir: Optional[Path] = None,
                 progress: ProgressCallback = None) -> Path:
        """下载新版 exe 并校验，返回下载后的文件路径"""
        if not release.asset_url:
            raise UpdateError("该版本没有提供可下载的 exe 文件")

        target_dir = Path(dest_dir or self.base_dir)
        if not _dir_writable(target_dir):
            target_dir = Path(tempfile.gettempdir()) / "RainExamUpdate"
            target_dir.mkdir(parents=True, exist_ok=True)

        dest = target_dir / UPDATE_FILE_NAME
        part = dest.with_name(dest.name + ".part")
        for stale in (dest, part):
            try:
                if stale.exists():
                    stale.unlink()
            except OSError:
                pass

        try:
            self._stream_to_file(release.asset_url, part, release.asset_size, progress)
            self._verify(release, part)
        except Exception:
            try:
                part.unlink()
            except OSError:
                pass
            raise

        os.replace(part, dest)
        return dest

    def _stream_to_file(self, url: str, dest: Path, expected_size: int,
                        progress: ProgressCallback) -> None:
        if httpx is not None:
            try:
                timeout = httpx.Timeout(DOWNLOAD_TIMEOUT, read=120.0)
                with httpx.Client(timeout=timeout, follow_redirects=True,
                                  headers=_headers("application/octet-stream")) as client:
                    with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        total = int(resp.headers.get("content-length") or expected_size or 0)
                        done = 0
                        with open(dest, "wb") as f:
                            for chunk in resp.iter_bytes(1 << 16):
                                f.write(chunk)
                                done += len(chunk)
                                if progress:
                                    progress(done, total)
                        return
            except Exception as exc:  # noqa: BLE001
                raise UpdateError(_describe_http_error(exc)) from exc

        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, headers=_headers("application/octet-stream"))
        try:
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                total = int(resp.headers.get("content-length") or expected_size or 0)
                done = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except Exception as exc:  # noqa: BLE001
            raise UpdateError(f"下载失败：{exc}") from exc

    def _verify(self, release: ReleaseInfo, path: Path) -> None:
        """优先比对 Release 提供的 .sha256；退而校验文件大小；最后做 PE 头检查"""
        if release.checksum_url:
            try:
                text = http_get_text(release.checksum_url, accept="text/plain")
                expected = _expected_sha256(text)
            except UpdateError:
                expected = ""
            if expected:
                actual = sha256_file(path)
                if actual != expected:
                    raise UpdateError("下载文件校验失败（SHA256 不匹配），可能文件损坏，请重试")
            else:
                self._verify_size(release, path)
        else:
            self._verify_size(release, path)

        if not _looks_like_exe(path):
            raise UpdateError("下载的文件不是有效的 Windows 程序（可能被网络劫持或文件损坏）")

    @staticmethod
    def _verify_size(release: ReleaseInfo, path: Path) -> None:
        if release.asset_size:
            actual_size = path.stat().st_size
            if actual_size != release.asset_size:
                raise UpdateError(
                    f"下载文件不完整（{actual_size} / {release.asset_size} 字节），请重试")

    # ── 安装 ──

    @staticmethod
    def is_frozen() -> bool:
        return bool(getattr(sys, "frozen", False))

    def can_self_update(self) -> bool:
        """只有在 Windows 上以 exe 方式运行时才能自替换"""
        return self.is_frozen() and os.name == "nt"

    def current_exe(self) -> Optional[Path]:
        return get_started_exe()

    def update_dir_writable(self) -> bool:
        """自动更新需要能写 exe 所在目录（只读目录只能引导用户手动替换）"""
        exe = self.current_exe()
        return _dir_writable(exe.parent if exe else self.base_dir)

    def install_and_restart(self, new_exe: Path) -> None:
        """
        启动一个批处理脚本：等当前进程退出 → 替换 exe → 重新启动。
        调用成功后应立即关闭当前程序。
        """
        if not self.can_self_update():
            raise UpdateError("当前不是 Windows exe 运行方式，无法自动替换，请手动下载新版本")
        target = self.current_exe()
        if target is None:
            raise UpdateError("无法定位当前程序路径")
        new_exe = Path(new_exe)
        if not new_exe.is_file():
            raise UpdateError(f"更新文件不存在：{new_exe}")
        if not _dir_writable(target.parent):
            raise UpdateError(f"程序目录不可写，无法自动更新：{target.parent}\n请手动替换 exe")

        bat = Path(tempfile.gettempdir()) / f"rainexam_update_{os.getpid()}.bat"
        # 用 bytes 写入并强制 CRLF，避免编码/换行问题（Windows 批处理对 LF 敏感）
        bat.write_bytes(_UPDATE_BAT.replace("\n", "\r\n").encode("ascii"))

        # 注意：这里必须手工拼接命令行并传字符串（而不是列表）。
        # subprocess 在 Windows 上会原样传递字符串，而列表会被 list2cmdline 转义；
        # cmd /c 对首字符是引号的命令行会剥掉最外层引号，因此
        #   "C:\Windows\System32\cmd.exe" /c ""C:\路径 含空格\x.bat" "参数1" ..."
        # 才能保证含空格的路径（如 C:\Users\张三\Downloads）正确执行。
        # 用 %COMSPEC% 的绝对路径，避免被 exe 所在目录/当前目录里的假 cmd.exe 劫持。
        comspec = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
        cmdline = f'"{comspec}" /c ""{bat}" "{target}" "{new_exe}" {os.getpid()}"'
        subprocess.Popen(
            cmdline,
            creationflags=_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )

    def open_release_page(self, release: Optional[ReleaseInfo] = None) -> bool:
        """用浏览器打开下载页面"""
        rel = release or self.last_release
        url = rel.download_page() if rel else ""
        if not url:
            url = f"https://github.com/{self.repo}/releases/latest"
        try:
            return bool(webbrowser.open(url))
        except Exception:
            return False


# ──────────────────────────────────────────────
# 命令行自检
# ──────────────────────────────────────────────

def _cli(argv) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="RainExam 更新模块自检")
    sub = parser.add_subparsers(dest="cmd")

    p_check = sub.add_parser("check", help="检查是否有新版本")
    p_check.add_argument("--force", action="store_true", help="忽略 6 小时缓存")
    p_check.add_argument("--repo", default=None, help="owner/repo")
    p_check.add_argument("--current", default=None, help="假装当前版本号")

    p_dl = sub.add_parser("download", help="下载最新版 exe")
    p_dl.add_argument("--out", default=".", help="下载目录")
    p_dl.add_argument("--repo", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "download":
        mgr = UpdateManager(Path("."), repo=args.repo)
        rel = fetch_latest_release(repo=mgr.repo)
        print(f"最新版本: {rel.version}  资源: {rel.asset_name or '(无)'}")
        path = mgr.download(rel, dest_dir=Path(args.out),
                            progress=lambda d, t: print(f"\r下载中 {d}/{t or '?'}", end=""))
        print(f"\n已下载并校验: {path}")
        return 0

    mgr = UpdateManager(Path("."), repo=args.repo, current_version=args.current)
    release = mgr.check(force=True)
    if mgr.last_error:
        print(f"[错误] {mgr.last_error}")
        return 1
    if not release:
        print(f"已是最新版本（当前 {mgr.current_version}）")
        return 0
    print(f"发现新版本: {release.version}（当前 {mgr.current_version}）")
    print(f"下载地址: {release.download_page()}")
    print("更新说明:")
    print(plain_notes(release.notes, limit=2000))
    return 0


def main() -> int:
    try:
        return _cli(sys.argv[1:] or ["check"])
    except UpdateError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
