import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# 别在跑测试时往用户家里写 token / 日志
os.environ.setdefault("SKILLHUB_TOKEN", "test-token")
os.environ.setdefault("SKILLHUB_LOG_DIR", tempfile.mkdtemp(prefix="skillhub-log-"))

import serve     # noqa: E402
import skillhub  # noqa: E402


class FrontmatterTest(unittest.TestCase):
    def test_parses_yaml_frontmatter(self):
        text = (
            "---\n"
            "name: demo\n"
            "description: 做一件具体的事\n"
            "tags: [alpha, beta]\n"
            "---\n"
            "# 正文\n"
            "内容\n"
        )
        meta, body = skillhub.parse_frontmatter(text)
        self.assertEqual(meta.get("name"), "demo")
        self.assertIn("正文", body)

    def test_without_frontmatter_returns_body(self):
        meta, body = skillhub.parse_frontmatter("# 只有正文\n")
        self.assertEqual(meta, {})
        self.assertTrue(body.startswith("# 只有正文"))

    def test_broken_yaml_falls_back_to_regex(self):
        text = "---\nname: broken\ntags: [unclosed\n---\nbody\n"
        meta, body = skillhub.parse_frontmatter(text)
        self.assertIsInstance(meta, dict)
        self.assertIn("body", body)


class CleanDescTest(unittest.TestCase):
    def test_truncates_and_strips(self):
        out = skillhub.clean_desc("  " + "x" * 900 + "  ", limit=50)
        self.assertLessEqual(len(out), 51)
        self.assertFalse(out.startswith(" "))

    def test_empty_input(self):
        self.assertEqual(skillhub.clean_desc(""), "")


class AllowedPathTest(unittest.TestCase):
    """删除 / 归档只能落在 config.json 声明的目录里。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="skillhub-allowed-")
        self.root = os.path.join(self.tmp, "skills")
        self.sibling = os.path.join(self.tmp, "skills-evil")   # 前缀相同但不同目录
        os.makedirs(os.path.join(self.root, "a"))
        os.makedirs(self.sibling)
        self.cfg = {"roots": [{"path": self.root, "agent": "X", "label": "X"}],
                    "pool": {"path": os.path.join(self.tmp, "pool")}}

    def test_child_is_allowed(self):
        self.assertTrue(serve.is_allowed(os.path.join(self.root, "a"), self.cfg))

    def test_root_itself_is_allowed(self):
        self.assertTrue(serve.is_allowed(self.root, self.cfg))

    def test_sibling_with_same_prefix_is_rejected(self):
        self.assertFalse(serve.is_allowed(self.sibling, self.cfg))

    def test_outside_is_rejected(self):
        self.assertFalse(serve.is_allowed("/etc", self.cfg))

    def test_traversal_is_rejected(self):
        self.assertFalse(serve.is_allowed(os.path.join(self.root, "..", "skills-evil"), self.cfg))


class RequestGuardTest(unittest.TestCase):
    """Host / Origin 白名单：挡 DNS rebinding 与跨站写操作。"""

    class Fake:
        _host_ok = serve.Handler._host_ok
        _origin_ok = serve.Handler._origin_ok

        def __init__(self, headers):
            self.headers = headers

    def test_host_localhost_ok(self):
        for host in ("127.0.0.1:8778", "localhost:8778", "[::1]:8778", "127.0.0.1"):
            self.assertTrue(self.Fake({"Host": host})._host_ok(), host)

    def test_host_rebinding_rejected(self):
        for host in ("evil.com", "evil.com:8778", "127.0.0.1.evil.com"):
            self.assertFalse(self.Fake({"Host": host})._host_ok(), host)

    def test_same_origin_post_without_origin_header_ok(self):
        self.assertTrue(self.Fake({})._origin_ok())

    def test_cross_site_origin_rejected(self):
        self.assertFalse(self.Fake({"Origin": "http://evil.com"})._origin_ok())
        self.assertFalse(self.Fake({"Origin": "null"})._origin_ok())

    def test_local_origin_ok(self):
        self.assertTrue(self.Fake({"Origin": "http://127.0.0.1:8778"})._origin_ok())
        self.assertTrue(self.Fake({"Origin": "http://localhost:8778"})._origin_ok())


class ConfigTest(unittest.TestCase):
    def test_falls_back_to_example(self):
        tmp = tempfile.mkdtemp(prefix="skillhub-cfg-")
        with open(os.path.join(tmp, "config.example.json"), "w", encoding="utf-8") as f:
            json.dump({"roots": []}, f)
        with mock.patch.object(skillhub, "HERE", tmp):
            self.assertEqual(skillhub.load_config(), {"roots": []})

    def test_missing_everything_exits(self):
        tmp = tempfile.mkdtemp(prefix="skillhub-cfg2-")
        with mock.patch.object(skillhub, "HERE", tmp):
            with self.assertRaises(SystemExit):
                skillhub.load_config()


class LlmEndpointTest(unittest.TestCase):
    def test_env_key_wins(self):
        env = {"SKILLHUB_LLM_KEY": "k-env", "SKILLHUB_LLM_BASE_URL": "https://example.test/",
               "SKILLHUB_LLM_MODEL": "m-env"}
        with mock.patch.dict(os.environ, env, clear=False):
            key, base, model = skillhub.llm_endpoint()
        self.assertEqual((key, base, model), ("k-env", "https://example.test", "m-env"))

    def test_no_key_means_offline(self):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("SKILLHUB_LLM_KEY", "DEEPSEEK_API_KEY")}
        clean["SKILLHUB_HERMES_CONFIG"] = "/nonexistent/hermes.yaml"
        with mock.patch.dict(os.environ, clean, clear=True):
            self.assertEqual(skillhub.llm_endpoint(), (None, None, None))


class TrashTest(unittest.TestCase):
    """删除必须真的落到回收站：非 macOS 不能变成静默空操作。"""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="skillhub-home-")
        self.target = os.path.join(self.home, "skills", "thing")
        os.makedirs(self.target)
        with open(os.path.join(self.target, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: thing\n---\n")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _trashed(self):
        return os.path.join(self.home, ".Trash", "thing")

    def test_non_darwin_moves_into_trash(self):
        with mock.patch.dict(os.environ, {"HOME": self.home}, clear=False), \
                mock.patch.object(serve.sys, "platform", "linux"):
            self.assertTrue(serve.to_trash(self.target))
        self.assertFalse(os.path.exists(self.target))
        self.assertTrue(os.path.exists(self._trashed()))

    def test_darwin_timeout_falls_back_instead_of_hanging(self):
        with mock.patch.dict(os.environ, {"HOME": self.home}, clear=False), \
                mock.patch.object(serve.sys, "platform", "darwin"), \
                mock.patch.object(serve.subprocess, "run",
                                  side_effect=serve.subprocess.TimeoutExpired("osascript", 8)):
            self.assertTrue(serve.to_trash(self.target))
        self.assertTrue(os.path.exists(self._trashed()))

    def test_trash_name_collision_gets_suffix(self):
        os.makedirs(self._trashed())
        with mock.patch.dict(os.environ, {"HOME": self.home}, clear=False), \
                mock.patch.object(serve.sys, "platform", "linux"):
            serve.to_trash(self.target)
        self.assertTrue(os.path.exists(self._trashed() + "-2"))


if __name__ == "__main__":
    unittest.main()
