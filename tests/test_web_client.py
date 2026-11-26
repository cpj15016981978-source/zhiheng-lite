# -*- coding: utf-8 -*-
"""M7 第二形态（本机网页界面）的验收测试。

为什么要单独测它：命令行是【人在终端里、看得见异常】，网页是【隔一层 HTTP】——
绑定地址、Host 校验、异常文本回显这些底线在 CLI 里根本不经过，只能显式打一遍。
这里刻意复现三条安全底线各自是否真的成立，而不是只测"接口返回 200"。
"""
import http.client
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import webapp


def request(port, method, path, body=None, host=None, raw=None, headers=None):
    """发一个请求，返回 (status, 解析后的 JSON 或文本)。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hd = dict(headers or {})
    if host:
        hd["Host"] = host
    data = raw if raw is not None else (
        json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
    if data is not None:
        hd.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=hd)
    r = conn.getresponse()
    payload = r.read().decode("utf-8", "replace")
    conn.close()
    if r.getheader("Content-Type", "").startswith("application/json"):
        return r.status, json.loads(payload)
    return r.status, payload


class WebCase(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "web.db")
        self.httpd, self.thread = webapp.start(port=0, db_path=self.db, quiet=True)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        """用当前实例而非 setUp 时抓到的引用：重启类测试会把 self.httpd 换成新的。"""
        if self.thread.is_alive():
            webapp.stop(self.httpd, self.thread)

    def chat(self, text):
        return request(self.port, "POST", "/api/chat", {"text": text})


class TestLoopbackOnly(WebCase):
    def test_page_serves_on_localhost(self):
        status, _ = request(self.port, "GET", "/")
        self.assertEqual(302, status, "根路径应 302 到 /chat")
        status, html = request(self.port, "GET", "/chat")
        self.assertEqual(200, status)
        self.assertIn("时中", html)

    def test_foreign_host_header_is_rejected(self):
        """DNS rebinding / CSRF：浏览器把 Host 换成外域，必须 403 而不是照常服务。"""
        for path in ("/", "/api/state"):
            status, body = request(self.port, "GET", path, host="evil.example.com")
            self.assertEqual(403, status, path)
            self.assertIn("本机", body["error"])

    def test_localhost_with_port_is_accepted(self):
        """Host 常带端口（127.0.0.1:8753），不能因为多了 :端口 就误杀。"""
        status, _ = request(self.port, "GET", "/api/state", host=f"127.0.0.1:{self.port}")
        self.assertEqual(200, status)

    def test_unknown_path_is_404_not_a_crash(self):
        status, body = request(self.port, "GET", "/api/nope")
        self.assertEqual(404, status)
        status, body = request(self.port, "POST", "/api/nope", {})
        self.assertEqual(404, status)


class TestNoHtmlInjection(WebCase):
    def test_page_never_uses_innerhtml(self):
        """记忆内容里可能有 <img onerror>，页面只许 textContent 渲染。"""
        self.assertNotIn("innerHTML", webapp.PAGE)
        self.assertIn("textContent", webapp.PAGE)

    def test_markdown_like_memory_round_trips_as_plain_data(self):
        """后端只负责把内容当【数据】原样回传；解析成什么是页面的事，而页面用 textContent。"""
        nasty = '我叫<b>危险</b><img src=x onerror=alert(1)>\r\nCRLF_IN_HEADER'
        status, out = self.chat(nasty)
        self.assertEqual(200, status)
        _, hist = request(self.port, "GET", "/api/history")
        self.assertEqual(nasty, hist["episodes"][0]["content"])   # 原样存、原样回，没被截断
        self.assertIn("onerror", json.dumps(hist, ensure_ascii=False))
        # 内容里的 CRLF 没变成响应头：这一发仍是干净的 200 + JSON
        status2, state2 = request(self.port, "GET", "/api/state")
        self.assertEqual(200, status2)
        self.assertIsInstance(state2["facts"], list)


class TestSecretGuardOverHttp(WebCase):
    def test_pasted_key_is_blocked_and_not_stored(self):
        """CLI 里的护栏到了 HTTP 必须同样成立：不回答、不落库、不复述。"""
        key = "sk-api-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        status, out = self.chat(f"我的密钥是 {key}")
        self.assertEqual(200, status)
        self.assertTrue(out["trace"]["blocked_secret"])
        self.assertNotIn(key, json.dumps(out, ensure_ascii=False))
        _, state = request(self.port, "GET", "/api/state")
        _, hist = request(self.port, "GET", "/api/history")
        self.assertNotIn(key, json.dumps(hist, ensure_ascii=False))
        self.assertEqual(0, len(state["facts"]))

    def test_error_body_does_not_leak_exception_text(self):
        """参数错的请求：回类型名可以，回 traceback/路径不行。"""
        status, body = request(self.port, "POST", "/api/forget", {"id": "abc"})
        self.assertEqual(400, status)
        self.assertNotIn(self.db, body["error"])
        self.assertNotIn("Traceback", body["error"])


class TestBadInputRejected(WebCase):
    def test_malformed_json(self):
        status, body = request(self.port, "POST", "/api/chat", raw=b"{oops",
                               headers={"Content-Type": "application/json"})
        self.assertEqual(400, status)

    def test_missing_content(self):
        status, body = self.chat("   ")
        self.assertEqual(400, status)

    def test_oversized_body_is_refused_and_never_stored(self):
        """413 时服务端【不读这批字节】就直接关连接（RFC 允许，也不给烂请求喂内存）。
        所以客户端可能读到 RST 而不是 413——两种都是"被拒"。真正要钉的是：这么大
        一段内容一个字也不许进记忆库。"""
        big = "x" * (webapp.MAX_BODY + 10)
        raw = json.dumps({"text": big}, ensure_ascii=False).encode("utf-8")
        try:
            status, body = request(self.port, "POST", "/api/chat", raw=raw,
                                   headers={"Content-Type": "application/json"})
            self.assertEqual(413, status)
            self.assertIn("过大", body["error"])
        except (http.client.RemoteDisconnected, ConnectionResetError):
            pass                       # 连接被直接掐断，同样算拒收
        _, hist = request(self.port, "GET", "/api/history")
        self.assertEqual(0, len(hist["episodes"]))
        self.assertNotIn(big, json.dumps(hist, ensure_ascii=False))

    def test_negative_content_length(self):
        status, body = request(self.port, "POST", "/api/chat", raw=b"{}",
                               headers={"Content-Type": "application/json",
                                        "Content-Length": "-5"})
        self.assertEqual(400, status)


class TestEndToEndCapabilities(WebCase):
    """六大能力在界面这一层真的通：记住、反思、主动、忘掉、作废。"""

    def test_remembers_and_survives_reopen(self):
        """界面这一层只验【通路】：说过的话进了库、关掉重开还在。
        谓词叫什么归 M4 反思引擎，测试里不替它锁名字（那是 reflection 自己的契约）。"""
        self.chat("我爷爷1959年在挨饿")
        _, state = request(self.port, "GET", "/api/state")
        self.assertTrue(any("1959" in f["object"] for f in state["facts"]), state["facts"])
        _, hist = request(self.port, "GET", "/api/history")
        self.assertTrue(len(hist["episodes"]) >= 1)

        webapp.stop(self.httpd, self.thread)             # 关掉
        self.httpd, self.thread = webapp.start(port=0, db_path=self.db, quiet=True)
        self.port = self.httpd.server_address[1]
        _, state2 = request(self.port, "GET", "/api/state")
        self.assertTrue(any("1959" in f["object"] for f in state2["facts"]))

    def test_reflect_then_forget_then_retire(self):
        self.chat("我叫阿明")
        status, r = request(self.port, "POST", "/api/reflect", {})
        self.assertEqual(200, status)
        _, state = request(self.port, "GET", "/api/state")
        fid = state["facts"][0]["id"]

        status, out = request(self.port, "POST", "/api/forget", {"id": fid})
        self.assertEqual(200, status)
        self.assertTrue(out["forgotten"])
        _, after = request(self.port, "GET", "/api/state")
        self.assertNotIn(fid, [f["id"] for f in after["facts"]])

        if after["principles"]:
            pid = after["principles"][0]["id"]
            status, out = request(self.port, "POST", "/api/retire",
                                  {"id": pid, "reason": "用户觉得这条不对"})
            self.assertEqual(200, status)
            _, closed = request(self.port, "GET", "/api/state")
            self.assertNotIn(pid, [x["id"] for x in closed["principles"]])
        else:
            self.skipTest("本轮没合成出原则")

    def test_retire_without_reason_is_refused(self):
        """作废必须留理由——界面漏传时后端不能自己编一条。"""
        self.chat("人要靠自己的双腿走路，不能什么都等人扶一把")
        request(self.port, "POST", "/api/reflect", {})
        _, state = request(self.port, "GET", "/api/state")
        if not state["principles"]:
            self.skipTest("本轮没合成出原则")
        status, body = request(self.port, "POST", "/api/retire",
                               {"id": state["principles"][0]["id"]})
        self.assertEqual(400, status)

    def test_tick_returns_fired_pushes(self):
        status, out = request(self.port, "POST", "/api/tick", {})
        self.assertEqual(200, status)
        self.assertIsInstance(out["fired"], list)

    def test_model_provider_is_visible_in_state(self):
        """默认必须是 mock：界面不许在用户没配密钥时偷偷打外网。"""
        _, state = request(self.port, "GET", "/api/state")
        self.assertEqual("mock", state["model"])


class TestAffectAndSelfOverHttp(WebCase):
    """Phase5.6 并入的 M5 情感层 + 自我模型，在界面这一层可见、可审计。"""

    def test_trace_carries_mood_and_identity(self):
        _, out = self.chat("我爷爷去年去世了，我心里空落落的")
        tr = out["trace"]
        self.assertEqual("丧痛", tr["affect"]["mood"])
        self.assertTrue(tr["affect"]["heavy"], "重情绪必须能被界面看出来")
        self.assertIn("principles_active", tr["self"])

    def test_state_exposes_mood_as_estimation(self):
        """心境进 /api/state 只是【读】：不许在展示时改写它。"""
        self.chat("最近老是睡不着，心里发慌")
        _, before = request(self.port, "GET", "/api/state")
        _, after = request(self.port, "GET", "/api/state")
        self.assertEqual("焦虑", before["affect"]["mood"])
        self.assertEqual(before["affect"]["mood"], after["affect"]["mood"])
        self.assertEqual(before["affect"]["intensity"], after["affect"]["intensity"])

    def test_identity_is_stable_across_turns_and_restarts(self):
        self.chat("以后我叫你阿伴好不好")
        _, s1 = request(self.port, "GET", "/api/state")
        ident1 = s1["self"]["identity"]
        self.assertTrue(ident1 and ident1["role"], "第一轮就该建立身份记录")
        self.chat("今天天气不错")
        _, s2 = request(self.port, "GET", "/api/state")
        self.assertEqual(ident1["created_ts"], s2["self"]["identity"]["created_ts"],
                         "身份不许被一句话悄悄改写")

        webapp.stop(self.httpd, self.thread)
        self.httpd, self.thread = webapp.start(port=0, db_path=self.db, quiet=True)
        self.port = self.httpd.server_address[1]
        _, s3 = request(self.port, "GET", "/api/state")
        self.assertEqual(ident1["created_ts"], s3["self"]["identity"]["created_ts"],
                         "关掉重开还是同一个我（这是记录，不是它“记得”）")

    def test_gui_retire_lands_in_the_self_ledger(self):
        """★两份真相的直接钉：界面作废原则走的是 M2，不经过自我模型。

        账若记在自我模型的 meta 里，这条路的改口就查不到——评审时实测过。
        """
        self.chat("人要靠自己的双腿走路，不能什么都等人扶一把")
        request(self.port, "POST", "/api/reflect", {})
        _, state = request(self.port, "GET", "/api/state")
        if not state["principles"]:
            self.skipTest("本轮没合成出原则")
        pid = state["principles"][0]["id"]
        status, _ = request(self.port, "POST", "/api/retire",
                            {"id": pid, "reason": "这条把我当小孩了"})
        self.assertEqual(200, status)
        _, after = request(self.port, "GET", "/api/state")
        hit = [r for r in after["self_revisions"] if r["id"] == pid and r["kind"] == "retire"]
        self.assertEqual(1, len(hit), "界面改的口也要进改口流水")
        self.assertEqual("这条把我当小孩了", hit[0]["reason"])

    def test_blocked_turn_has_no_mood_write(self):
        """密钥被拦下时整条链路都不该动：心境也不许跟着记一笔。"""
        _, out = self.chat("sk-api-abcdef0123456789abcdef0123456789")
        self.assertTrue(out["trace"]["blocked_secret"])
        self.assertIsNone(out["trace"]["affect"])
        _, state = request(self.port, "GET", "/api/state")
        self.assertEqual("平静", state["affect"]["mood"])
        self.assertIsNone(state["affect"]["updated_ts"])


class TestAssembly(unittest.TestCase):
    def test_serve_signature_unchanged_for_cli(self):
        import inspect
        for fn in (webapp.serve, webapp.start):
            ns = inspect.signature(fn).parameters
            self.assertIn("port", ns)
            self.assertIn("db_path", ns)
