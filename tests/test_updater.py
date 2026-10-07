"""
自动更新模块的单元测试（仅用标准库，无需安装任何依赖）

运行方式（在项目根目录）：
    python -m unittest discover -s tests -v
    # 或
    python tests/test_updater.py

覆盖：版本号比较、Release 解析与资源选择、检查缓存与忽略逻辑、
      下载 + SHA256/大小校验、失败文案。不联网（用本地 HTTP 服务与打桩数据）。
"""

import hashlib
import functools
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import updater  # noqa: E402


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """只服务指定目录，且不往测试输出里写访问日志"""

    def log_message(self, *args, **kwargs):
        pass


class VersionTest(unittest.TestCase):
    def test_newer(self):
        cases = [
            ("2.0.2", "2.0.1", True),
            ("v2.1.0", "2.0.1", True),
            ("2.0.1", "2.0.1", False),
            ("2.0.0", "2.0.1", False),
            ("2.1", "2.1.0", False),          # 缺位补 0，视为相同
            ("v2.0.10", "v2.0.9", True),      # 数字比较而非字符串
            ("2.1.0", "2.1.0-beta.1", True),  # 正式版 > 同号预发布版
            ("2.1.0-beta.1", "2.1.0", False),
            ("2.2.0-rc.1", "2.1.0", True),
            ("dev", "2.0.1", False),          # 无法解析时宁可不提示
            ("2.0.1", "dev", False),
        ]
        for latest, current, expected in cases:
            with self.subTest(latest=latest, current=current):
                self.assertEqual(updater.is_newer(latest, current), expected)

    def test_normalize(self):
        self.assertEqual(updater.normalize_version("v2.1.0-beta.1"), "2.1.0-beta.1")
        self.assertEqual(updater.normalize_version("2.0.1"), "2.0.1")

    def test_plain_notes_strips_markdown(self):
        text = "## 标题\n\n![img](a.png)\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n[链接](https://x)\n**粗体**"
        out = updater.plain_notes(text)
        self.assertNotIn("![", out)
        self.assertNotIn("**", out)
        self.assertNotIn("|---|", out)
        self.assertIn("标题", out)
        self.assertIn("链接", out)


class ReleaseParseTest(unittest.TestCase):
    PAYLOAD = [{
        "tag_name": "v3.1.0",
        "name": "RainExam v3.1.0",
        "body": "notes",
        "html_url": "https://github.com/x/y/releases/tag/v3.1.0",
        "published_at": "2026-01-01T00:00:00Z",
        "prerelease": False,
        "assets": [
            {"name": "other-tool.exe", "browser_download_url": "https://x/other.exe", "size": 11},
            {"name": "RainExam.exe.sha256", "browser_download_url": "https://x/sha", "size": 70},
            {"name": "RainExam.exe", "browser_download_url": "https://x/RainExam.exe", "size": 100},
        ],
    }]

    def _fetch_with(self, payload, **kwargs):
        original = updater.http_get_text
        updater.http_get_text = lambda url, timeout=8, accept="": json.dumps(payload)
        try:
            return updater.fetch_latest_release(**kwargs)
        finally:
            updater.http_get_text = original

    def test_picks_exe_and_checksum(self):
        rel = self._fetch_with(self.PAYLOAD)
        self.assertEqual(rel.version, "3.1.0")
        self.assertEqual(rel.asset_name, "RainExam.exe")   # 优先同名 exe
        self.assertEqual(rel.checksum_url, "https://x/sha")
        self.assertEqual(rel.asset_size, 100)
        self.assertEqual(rel.download_page(), "https://x/RainExam.exe")

    def test_falls_back_to_any_exe(self):
        payload = [dict(self.PAYLOAD[0], assets=[
            {"name": "foo.exe", "browser_download_url": "https://x/foo.exe", "size": 5}])]
        self.assertEqual(self._fetch_with(payload).asset_name, "foo.exe")

    def test_no_exe_falls_back_to_release_page(self):
        payload = [dict(self.PAYLOAD[0], assets=[
            {"name": "source.zip", "browser_download_url": "https://x/s.zip", "size": 5}])]
        rel = self._fetch_with(payload)
        self.assertFalse(rel.has_asset)
        self.assertEqual(rel.download_page(), self.PAYLOAD[0]["html_url"])

    def test_picks_highest_version_not_latest_published(self):
        """后发布的老版本热修不能遮住版本号更高的 Release"""
        hotfix = {"tag_name": "v2.0.3", "assets": [], "prerelease": False}
        newer = {"tag_name": "v2.1.0", "assets": [], "prerelease": False}
        self.assertEqual(self._fetch_with([hotfix, newer]).version, "2.1.0")
        self.assertEqual(self._fetch_with([newer, hotfix]).version, "2.1.0")

    def test_prerelease_and_draft_skipped(self):
        stable = {"tag_name": "v3.0.0", "assets": [], "prerelease": False}
        beta = {"tag_name": "v9.0.0-beta.1", "assets": [], "prerelease": True}
        draft = {"tag_name": "v8.0.0", "assets": [], "prerelease": False, "draft": True}
        self.assertEqual(self._fetch_with([stable, beta, draft]).version, "3.0.0")
        self.assertEqual(self._fetch_with([stable, beta], include_prerelease=True).version, "9.0.0-beta.1")

    def test_empty_release_list(self):
        with self.assertRaises(updater.UpdateError):
            self._fetch_with([])

    def test_bad_json_raises(self):
        original = updater.http_get_text
        updater.http_get_text = lambda url, timeout=8, accept="": "<html>not json</html>"
        try:
            with self.assertRaises(updater.UpdateError):
                updater.fetch_latest_release()
        finally:
            updater.http_get_text = original


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rainexam_test_"))
        self.release = updater.ReleaseInfo(
            tag="v9.9.9", version="9.9.9", asset_name="RainExam.exe", asset_size=3)
        self._real_fetch = updater.fetch_latest_release
        updater.fetch_latest_release = (
            lambda repo=updater.DEFAULT_REPO, timeout=8.0, include_prerelease=False: self.release)

    def tearDown(self):
        updater.fetch_latest_release = self._real_fetch

    def _manager(self, name="a", current="2.0.1"):
        return updater.UpdateManager(self.tmp / name, current_version=current,
                                     state_path=self.tmp / f"{name}.json")

    def test_finds_update_and_caches(self):
        mgr = self._manager()
        self.assertIsNotNone(mgr.check())
        self.assertEqual(mgr.last_error, "")
        # 检查成功后 6 小时内不再联网
        self.assertIsNone(mgr.check())
        self.assertEqual(mgr.skip_reason, "interval")

    def test_same_version_no_update(self):
        self.assertIsNone(self._manager(current="9.9.9").check(force=True))

    def test_prerelease_ignored(self):
        self.release.prerelease = True
        try:
            self.assertIsNone(self._manager().check(force=True))
        finally:
            self.release.prerelease = False

    def test_skip_remembered(self):
        mgr = self._manager()
        mgr.mark_skipped("9.9.9")
        self.assertIsNotNone(mgr.check(force=True))
        self.assertTrue(mgr.skipped)
        mgr.clear_skipped()
        self.assertIsNotNone(mgr.check(force=True))
        self.assertFalse(mgr.skipped)

    def test_network_error_is_silent(self):
        def boom(repo=updater.DEFAULT_REPO, timeout=8.0, include_prerelease=False):
            raise updater.UpdateError("模拟断网")
        updater.fetch_latest_release = boom
        mgr = self._manager()
        self.assertIsNone(mgr.check(force=True))
        self.assertEqual(mgr.last_error, "模拟断网")

    def test_disable_env_skips_auto_but_allows_manual(self):
        os.environ[updater.DISABLE_ENV] = "1"
        try:
            mgr = self._manager()
            self.assertIsNone(mgr.check())
            self.assertEqual(mgr.skip_reason, "disabled")
            self.assertIsNotNone(mgr.check(force=True))
        finally:
            del os.environ[updater.DISABLE_ENV]


class DownloadTest(unittest.TestCase):
    """用本地 HTTP 服务验证下载与校验"""

    @classmethod
    def setUpClass(cls):
        cls.payload = b"MZ" + os.urandom(200_000)   # 需通过 PE(MZ) 头检查
        cls.serve_dir = Path(tempfile.mkdtemp(prefix="rainexam_srv_"))
        (cls.serve_dir / "fake.exe").write_bytes(cls.payload)
        cls.hash = hashlib.sha256(cls.payload).hexdigest()
        cls._write_checksum(cls.hash)

        cwd = os.getcwd()
        os.chdir(cls.serve_dir)
        try:
            handler = functools.partial(_QuietHandler, directory=str(cls.serve_dir))
            cls.server = socketserver.TCPServer(("127.0.0.1", 0), handler)
        finally:
            os.chdir(cwd)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    @classmethod
    def _write_checksum(cls, value):
        (cls.serve_dir / "fake.exe.sha256").write_text(f"{value}  RainExam.exe\n", encoding="ascii")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rainexam_dl_"))
        self.mgr = updater.UpdateManager(self.tmp, current_version="2.0.1",
                                         state_path=self.tmp / "state.json")

    def _release(self, checksum=True, size=None):
        return updater.ReleaseInfo(
            tag="v9.9.9", version="9.9.9", asset_name="RainExam.exe",
            asset_url=f"{self.base}/fake.exe",
            checksum_url=f"{self.base}/fake.exe.sha256" if checksum else "",
            asset_size=len(self.payload) if size is None else size)

    def test_download_and_verify_sha256(self):
        self._write_checksum(self.hash)
        path = self.mgr.download(self._release(), dest_dir=self.tmp / "dl")
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertFalse(list((self.tmp / "dl").glob("*.part")))

    def test_checksum_mismatch_fails(self):
        self._write_checksum("0" * 64)
        try:
            with self.assertRaises(updater.UpdateError) as ctx:
                self.mgr.download(self._release(), dest_dir=self.tmp / "dl2")
            self.assertIn("SHA256", str(ctx.exception))
        finally:
            self._write_checksum(self.hash)

    def test_size_fallback(self):
        with self.assertRaises(updater.UpdateError) as ctx:
            self.mgr.download(self._release(checksum=False, size=len(self.payload) + 5),
                              dest_dir=self.tmp / "dl3")
        self.assertIn("不完整", str(ctx.exception))
        # 大小一致时通过
        self.assertTrue(self.mgr.download(self._release(checksum=False),
                                          dest_dir=self.tmp / "dl4").is_file())

    def test_rejects_non_exe_payload(self):
        """校验和/大小都对，但不是有效 exe（MZ 头）也要拒绝"""
        junk = b"not an exe" + os.urandom(200_000 - 10)
        (self.serve_dir / "fake.exe").write_bytes(junk)
        ck = hashlib.sha256(junk).hexdigest()
        self._write_checksum(ck)
        try:
            with self.assertRaises(updater.UpdateError) as ctx:
                self.mgr.download(self._release(size=len(junk)), dest_dir=self.tmp / "dl5")
            self.assertIn("有效的 Windows 程序", str(ctx.exception))
        finally:
            (self.serve_dir / "fake.exe").write_bytes(self.payload)
            self._write_checksum(self.hash)


class LeftoverTest(unittest.TestCase):
    """更新遗留物：确认成功（删备份）/ 提示失败（读日志）"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rainexam_left_"))
        self.exe = self.tmp / "RainExam.exe"
        self.exe.write_bytes(b"MZ")
        self._real = updater.get_started_exe
        updater.get_started_exe = lambda: self.exe

    def tearDown(self):
        updater.get_started_exe = self._real

    def test_clear_backup(self):
        backup = self.tmp / "RainExam.exe.old"
        backup.write_bytes(b"MZ")
        updater.clear_update_backup()
        self.assertFalse(backup.exists())

    def test_error_log_is_reported_once(self):
        (self.tmp / updater.ERROR_LOG_NAME).write_text("update failed: test\n", encoding="utf-8")
        self.assertIn("update failed", updater.take_update_error_log())
        self.assertEqual(updater.take_update_error_log(), "")   # 只提示一次

    def test_noop_when_not_frozen_exe(self):
        updater.get_started_exe = lambda: None
        self.assertEqual(updater.take_update_error_log(), "")
        updater.clear_update_backup()   # 不应抛异常


class StatePathTest(unittest.TestCase):
    def test_state_path_when_base_dir_not_writable(self):
        """程序目录不可写时，状态文件应退到用户目录，保证缓存/忽略仍然生效"""
        readonly = Path("/rainexam-not-writable-xyz")     # 绝对不存在的只读路径
        mgr = updater.UpdateManager(readonly, current_version="2.0.1")
        self.assertNotEqual(mgr.state_path.parent, readonly)
        self.assertTrue(mgr.state_path.name.endswith(".json"))

    def test_state_path_defaults_into_base_dir(self):
        tmp = Path(tempfile.mkdtemp(prefix="rainexam_state_"))
        mgr = updater.UpdateManager(tmp, current_version="2.0.1")
        self.assertEqual(mgr.state_path, tmp / updater.STATE_FILE)


class InstallScriptTest(unittest.TestCase):
    def test_bat_is_ascii_crlf_safe(self):
        bat = updater._UPDATE_BAT
        self.assertTrue(all(ord(c) < 128 for c in bat), "批处理必须为纯 ASCII")
        self.assertIn(":sleep", bat)
        self.assertNotIn("timeout /t", bat)   # timeout 在无控制台环境不可靠
        self.assertIn("%~dp1", bat)           # 重启时切到 exe 所在目录

    def test_bat_keeps_backup_and_logs_failure(self):
        bat = updater._UPDATE_BAT
        # 备份只能由「新版本启动成功后」的 Python 代码删除，脚本本身不删
        self.assertIn("RainExam_update.log", bat)
        self.assertIn("%BAK%", bat)
        self.assertNotIn('del /F /Q "%BAK%"', bat)
        self.assertIn(":verify", bat)          # 新版本启动确认
        self.assertIn(":restart_old", bat)     # 失败时把旧版本拉起来

    def test_source_run_cannot_self_update(self):
        mgr = updater.UpdateManager(Path(tempfile.mkdtemp()), current_version="2.0.1")
        self.assertFalse(mgr.can_self_update())
        self.assertTrue(mgr.update_dir_writable())   # 临时目录可写，且不抛异常
        with self.assertRaises(updater.UpdateError):
            mgr.install_and_restart(Path("whatever.exe"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
