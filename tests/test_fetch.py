"""Tests for layer 2 (fetch). A local HTTP server on 127.0.0.1 stands in for
the web, so nothing touches the network."""
import asyncio
import copy
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import httpx  # noqa: E402
from twowebs import cli, config, fetch  # noqa: E402

BROWSER_UA_PREFIX = "Mozilla/5.0 (Windows"

PAGE = ("# Widgets\n\nOur widgets are made of recycled steel and ship worldwide. "
        "Every order includes a two year warranty.\n")
SPONSORED = ("This page is sponsored by Acme Corp and costs $499 today only.\n")

LOG = []
LOCK = threading.Lock()


def is_browser(ua):
    return ua.startswith(BROWSER_UA_PREFIX)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, ctype="text/markdown; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Every response tries to set a cookie; a leaking client would send it back.
        self.send_header("Set-Cookie", "session=abc123; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        ua = self.headers.get("User-Agent", "")
        with LOCK:
            LOG.append({"path": self.path, "ua": ua,
                        "headers": dict((k.lower(), v) for k, v in self.headers.items())})
        if self.path == "/robots.txt":
            self._send(200, b"User-agent: PerplexityBot\nDisallow: /\n\nUser-agent: *\nDisallow: /private/\n",
                       "text/plain")
        elif self.path == "/":
            body = PAGE
            if "ClaudeBot" in ua:
                body += SPONSORED
            self._send(200, body.encode("utf-8"))
        elif self.path == "/blocked":
            if is_browser(ua):
                self._send(200, PAGE.encode("utf-8"))
            else:
                self._send(403, b"Forbidden", "text/plain")
        elif self.path == "/big":
            self._send(200, b"x" * 5000, "text/plain")
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.send_header("Set-Cookie", "r=1; Path=/")
            self.end_headers()
        else:
            self._send(404, b"not found", "text/plain")


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def free_closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        del LOG[:]
        self.cfg = config.load()
        self.cfg = copy.deepcopy(self.cfg)
        self.cfg["crawl"]["delay_s"] = 0
        self.cfg["crawl"]["timeout_s"] = 5
        # Pin GPTBot to one rep so these tests keep covering the "reps" rule
        # whatever the shipped setting is.
        for i in self.cfg["identities"]:
            if i["name"] == "GPTBot":
                i["reps"] = 1
        self.ua_to_name = dict((i["user_agent"], i["name"]) for i in self.cfg["identities"])

    def ident(self, name):
        for i in self.cfg["identities"]:
            if i["name"] == name:
                return i
        raise KeyError(name)

    def names_logged(self, path):
        return [self.ua_to_name.get(r["ua"], "?") for r in LOG if r["path"] == path]

    # ---- fetch_one -------------------------------------------------------

    def test_fetch_one_records_response(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.fetch_one(c, self.base + "/", self.ident("browser"), self.cfg)
        f = run(go())
        self.assertIsNone(f["error"])
        self.assertEqual(f["status"], 200)
        self.assertEqual(f["final_url"], self.base + "/")
        self.assertIn("text/markdown", f["content_type"])
        self.assertEqual(f["body"], PAGE.encode("utf-8"))
        self.assertEqual(f["truncated"], 0)
        self.assertEqual(f["identity"], "browser")
        self.assertEqual(f["rep"], 1)
        self.assertIn("content-type", f["headers"])
        for k, v in f["headers"].items():
            self.assertEqual(k, k.lower())
            self.assertLessEqual(len(v), 300)

    def test_redirect_followed_and_final_url_recorded(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.fetch_one(c, self.base + "/redirect", self.ident("browser"), self.cfg)
        f = run(go())
        self.assertEqual(f["status"], 200)
        self.assertEqual(f["final_url"], self.base + "/")

    def test_user_agent_changes_body(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                b = await fetch.fetch_one(c, self.base + "/", self.ident("browser"), self.cfg)
                a = await fetch.fetch_one(c, self.base + "/", self.ident("ClaudeBot"), self.cfg)
                return b, a
        b, a = run(go())
        self.assertNotIn(b"Acme", b["body"])
        self.assertIn(b"sponsored by Acme", a["body"])

    def test_403_for_bots_is_recorded_not_raised(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                b = await fetch.fetch_one(c, self.base + "/blocked", self.ident("browser"), self.cfg)
                a = await fetch.fetch_one(c, self.base + "/blocked", self.ident("GPTBot"), self.cfg)
                return b, a
        b, a = run(go())
        self.assertEqual(b["status"], 200)
        self.assertEqual(a["status"], 403)
        self.assertIsNone(a["error"])

    def test_body_cap_sets_truncated(self):
        self.cfg["crawl"]["max_body_bytes"] = 1000

        async def go():
            async with fetch.make_client(self.cfg) as c:
                big = await fetch.fetch_one(c, self.base + "/big", self.ident("browser"), self.cfg)
                small = await fetch.fetch_one(c, self.base + "/", self.ident("browser"), self.cfg)
                return big, small
        big, small = run(go())
        self.assertEqual(len(big["body"]), 1000)
        self.assertEqual(big["truncated"], 1)
        self.assertEqual(small["truncated"], 0)

    def test_body_exactly_at_cap_not_truncated(self):
        self.cfg["crawl"]["max_body_bytes"] = 5000

        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.fetch_one(c, self.base + "/big", self.ident("browser"), self.cfg)
        f = run(go())
        self.assertEqual(len(f["body"]), 5000)
        self.assertEqual(f["truncated"], 0)

    def test_error_captured_closed_port(self):
        url = "http://127.0.0.1:%d/" % free_closed_port()

        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.fetch_one(c, url, self.ident("browser"), self.cfg)
        f = run(go())
        self.assertTrue(f["error"])
        self.assertIsNone(f["status"])
        self.assertEqual(f["body"], b"")
        self.assertEqual(f["truncated"], 0)

    def test_error_captured_bad_url(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.fetch_one(c, "not a url", self.ident("browser"), self.cfg)
        f = run(go())
        self.assertTrue(f["error"])

    def test_cookies_not_carried(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                for _ in range(3):
                    await fetch.fetch_one(c, self.base + "/", self.ident("browser"), self.cfg)
                await fetch.fetch_one(c, self.base + "/redirect", self.ident("browser"), self.cfg)
                left = len(c.cookies.jar)
                return left
        left = run(go())
        self.assertEqual(left, 0)
        self.assertGreaterEqual(len(LOG), 5)
        for r in LOG:
            # a cookie set by a redirect hop may ride along inside that one
            # request; nothing may carry over between separate requests
            self.assertNotIn("session", r["headers"].get("cookie", ""))
        self.assertEqual([r["headers"].get("cookie") for r in LOG if r["path"] == "/"][:3],
                         [None] * 3)

    # ---- robots ----------------------------------------------------------

    def test_load_robots(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.load_robots(c, self.base + "/", self.cfg)
        rp = run(go())
        self.assertFalse(rp.can_fetch("PerplexityBot", self.base + "/"))
        self.assertTrue(rp.can_fetch("ClaudeBot", self.base + "/"))
        self.assertTrue(rp.can_fetch("*", self.base + "/"))
        self.assertFalse(rp.can_fetch("*", self.base + "/private/x"))

    def test_load_robots_missing_allows_all(self):
        url = "http://127.0.0.1:%d/" % free_closed_port()

        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.load_robots(c, url, self.cfg)
        rp = run(go())
        self.assertTrue(rp.can_fetch("*", url))
        self.assertTrue(rp.can_fetch("GPTBot", url))
        self.assertIsNone(rp.tw_status)

    # ---- fetch_page ------------------------------------------------------

    def expected_order(self):
        ids = [i["name"] for i in self.cfg["identities"]]
        r1 = ids
        r2 = [n for n in ids if n != "GPTBot"]
        return r1 + r2

    def test_fetch_page_interleaved_and_reps(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.fetch_page(c, self.base + "/", self.cfg)
        out = run(go())
        expected = self.expected_order()
        self.assertEqual([f["identity"] for f in out], expected)
        self.assertEqual(self.names_logged("/"), expected)
        self.assertEqual([f["identity"] for f in out].count("GPTBot"), 1)
        self.assertEqual([f["identity"] for f in out].count("browser"), 2)
        reps = [(f["identity"], f["rep"]) for f in out]
        self.assertEqual(reps[0], ("browser", 1))
        self.assertEqual(reps[len(self.cfg["identities"])], ("browser", 2))
        for f in out:
            self.assertIsNone(f["error"])
            self.assertEqual(f["status"], 200)

    def test_only_user_agent_differs(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.fetch_page(c, self.base + "/", self.cfg)
        run(go())
        reqs = [r for r in LOG if r["path"] == "/"]
        self.assertEqual(len(reqs), len(self.expected_order()))
        ref = dict(reqs[0]["headers"])
        ref.pop("user-agent")
        uas = set()
        for r in reqs:
            h = dict(r["headers"])
            uas.add(h.pop("user-agent"))
            self.assertEqual(h, ref)
        self.assertEqual(len(uas), len(self.cfg["identities"]))
        self.assertEqual(ref.get("accept-language"), "en-US,en;q=0.9")

    def test_robots_skip_without_request(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                rp = await fetch.load_robots(c, self.base + "/", self.cfg)
                del LOG[:]
                return await fetch.fetch_page(c, self.base + "/", self.cfg, robots=rp)
        out = run(go())
        skips = [f for f in out if f["error"] == "robots_skip"]
        self.assertEqual([f["identity"] for f in skips], ["PerplexityBot", "PerplexityBot"])
        self.assertEqual(sorted(f["rep"] for f in skips), [1, 2])
        for f in skips:
            self.assertIsNone(f["status"])
            self.assertEqual(f["body"], b"")
        logged = self.names_logged("/")
        self.assertNotIn("PerplexityBot", logged)
        self.assertEqual(len(logged), len(self.expected_order()) - 2)
        # order of the remaining identities is untouched
        self.assertEqual(logged, [n for n in self.expected_order() if n != "PerplexityBot"])
        # the records stay in config order too
        self.assertEqual([f["identity"] for f in out], self.expected_order())

    def test_respect_robots_false_ignores_robots(self):
        self.cfg["crawl"]["respect_robots"] = False

        async def go():
            async with fetch.make_client(self.cfg) as c:
                rp = await fetch.load_robots(c, self.base + "/", self.cfg)
                del LOG[:]
                return await fetch.fetch_page(c, self.base + "/", self.cfg, robots=rp)
        out = run(go())
        self.assertFalse([f for f in out if f["error"]])
        self.assertEqual(len(LOG), len(self.expected_order()))

    def test_prefetched_used_as_browser_rep1(self):
        async def go():
            async with fetch.make_client(self.cfg) as c:
                pre = await fetch.fetch_one(c, self.base + "/", self.ident("browser"), self.cfg, 1)
                del LOG[:]
                out = await fetch.fetch_page(c, self.base + "/", self.cfg, prefetched=pre)
                return pre, out
        pre, out = run(go())
        self.assertIs(out[0], pre)
        self.assertEqual([f["identity"] for f in out], self.expected_order())
        self.assertEqual(len(LOG), len(self.expected_order()) - 1)
        self.assertEqual(self.names_logged("/")[0], "OAI-SearchBot")

    def test_identities_subset_and_rep_override(self):
        ids = []
        for n in ("browser", "GPTBot"):
            j = dict(self.ident(n))
            j["reps"] = 2
            ids.append(j)

        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.fetch_page(c, self.base + "/", self.cfg, identities=ids)
        out = run(go())
        self.assertEqual([f["identity"] for f in out],
                         ["browser", "GPTBot", "browser", "GPTBot"])

    def test_fetch_page_error_page_does_not_raise(self):
        url = "http://127.0.0.1:%d/" % free_closed_port()
        ids = [dict(self.ident("browser"), reps=1), dict(self.ident("ClaudeBot"), reps=1)]

        async def go():
            async with fetch.make_client(self.cfg) as c:
                return await fetch.fetch_page(c, url, self.cfg, identities=ids)
        out = run(go())
        self.assertEqual([f["identity"] for f in out], ["browser", "ClaudeBot"])
        for f in out:
            self.assertTrue(f["error"])

    # ---- crawl_site (mock transport: no sockets at all) -------------------

    def mock_client_factory(self, handler, seen):
        def make(cfg):
            def wrapped(request):
                seen.append(str(request.url))
                return handler(request)
            return httpx.AsyncClient(transport=httpx.MockTransport(wrapped),
                                     follow_redirects=True, max_redirects=5)
        return make

    def crawl_with(self, handler, domain="example.test"):
        seen = []
        orig = fetch.make_client
        fetch.make_client = self.mock_client_factory(handler, seen)
        try:
            return run(fetch.crawl_site(domain, self.cfg)), seen
        finally:
            fetch.make_client = orig

    def test_crawl_site_unreachable(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)
        (pages, note), seen = self.crawl_with(handler)
        self.assertEqual(note, "unreachable")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["kind"], "home")
        self.assertEqual(len(pages[0]["fetches"]), 1)
        self.assertTrue(pages[0]["fetches"][0]["error"])
        self.assertIn("https://example.test/", seen)
        self.assertIn("https://www.example.test/", seen)

    def test_crawl_site_home_and_inner(self):
        html = ("<html><body><h1>Home</h1><a href='/about'>short</a>"
                "<a href='/products/blue-widget-deluxe'>Blue widget</a></body></html>")

        def handler(request):
            p = request.url.path
            if p == "/robots.txt":
                return httpx.Response(404, text="nope")
            if p == "/":
                return httpx.Response(200, text=html, headers={"content-type": "text/html"})
            return httpx.Response(200, text="inner page", headers={"content-type": "text/html"})
        (pages, note), seen = self.crawl_with(handler)
        self.assertIsNone(note)
        self.assertEqual([p["kind"] for p in pages], ["home", "inner"])
        self.assertEqual(pages[1]["url"], "https://example.test/products/blue-widget-deluxe")
        n = len(self.expected_order())
        self.assertEqual(len(pages[0]["fetches"]), n)
        self.assertEqual(len(pages[1]["fetches"]), n)
        # the homepage browser rep 1 was fetched once only (prefetched reused)
        self.assertEqual(seen.count("https://example.test/"), n)

    def test_crawl_site_falls_back_to_www(self):
        def handler(request):
            if request.url.host == "example.test":
                raise httpx.ConnectError("refused", request=request)
            if request.url.path == "/robots.txt":
                return httpx.Response(404, text="")
            return httpx.Response(200, text="hello", headers={"content-type": "text/html"})
        self.cfg["crawl"]["inner_page"] = False
        (pages, note), seen = self.crawl_with(handler)
        self.assertIsNone(note)
        self.assertEqual(pages[0]["url"], "https://www.example.test/")

    def test_crawl_site_robots_disallow_star_skips_site(self):
        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            return httpx.Response(200, text="should not be requested")
        (pages, note), seen = self.crawl_with(handler)
        self.assertEqual(note, "robots_disallow")
        self.assertEqual(pages, [])
        self.assertEqual([u for u in seen if not u.endswith("/robots.txt")], [])


class CrawlLoopTests(unittest.TestCase):
    """fetch.crawl with crawl_site replaced, so no requests are made."""

    def setUp(self):
        import tempfile
        from twowebs import store
        self.store = store
        self.tmp = tempfile.mkdtemp()
        self.db = store.connect(os.path.join(self.tmp, "t.sqlite"))
        self.cfg = copy.deepcopy(config.load())
        self.cfg["crawl"]["concurrency"] = 1
        self.orig = fetch.crawl_site

    def tearDown(self):
        import shutil
        fetch.crawl_site = self.orig
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def page(url, body=b"x"):
        rec = fetch._blank(config.load()["identities"][0], 1, url)
        rec.update(status=200, body=body)
        return {"url": url, "kind": "home", "fetches": [rec, dict(rec, rep=2)]}

    def crawl_quietly(self, stdout=None):
        import io
        old = sys.stdout
        sys.stdout = stdout or io.StringIO()
        try:
            run(fetch.crawl(self.db, self.cfg))
        finally:
            sys.stdout = old

    def test_failed_save_leaves_no_partial_rows(self):
        async def fake(domain, cfg):
            if domain == "bad.test":
                return [self.page("https://bad.test/"), self.page("https://bad.test/inner", None)], None
            return [self.page("https://good.test/")], None
        fetch.crawl_site = fake
        self.store.add_sites(self.db, "s", [(1, "bad.test"), (2, "good.test")])
        self.crawl_quietly()
        rows = dict((r[0], (r[1], r[2])) for r in self.db.execute(
            "SELECT s.domain, s.crawled, COUNT(f.fetch_id) FROM sites s LEFT JOIN pages p"
            " ON p.site_id=s.site_id LEFT JOIN fetches f ON f.page_id=p.page_id GROUP BY s.site_id"))
        self.assertEqual(rows["bad.test"], (0, 0))  # nothing half-saved, retried on resume
        self.assertEqual(rows["good.test"], (1, 2))

    def test_progress_line_survives_narrow_code_page(self):
        import io

        async def fake(domain, cfg):
            return [self.page("https://%s/" % domain)], None
        fetch.crawl_site = fake
        self.store.add_sites(self.db, "s", [(1, "例え.test"), (2, "after.test")])
        out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        self.crawl_quietly(out)
        self.assertEqual(self.db.execute("SELECT SUM(crawled) FROM sites").fetchone()[0], 2)


class SampleParsingTests(unittest.TestCase):
    def test_parse_sample(self):
        lines = [
            "# comment", "", "   ",
            "example.com",
            "5,Foo.ORG",
            "https://www.Bar.net/some/path?x=1",
            "7,https://baz.io/a",
            "example.com",
            "1000,www.qux.com,extra",
        ]
        rows = cli.parse_sample(lines)
        self.assertEqual(rows, [
            (1, "example.com"), (5, "foo.org"), (3, "www.bar.net"),
            (7, "baz.io"), (1000, "www.qux.com")])

    def test_header_only_file_gives_nothing(self):
        path = os.path.join(ROOT, "config", "samples", "pilot.txt")
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertTrue(lines and lines[0].startswith("#"))
        self.assertEqual(cli.parse_sample(lines), [])


if __name__ == "__main__":
    unittest.main()
