"""Storage layer: bodies are stored once per distinct content, and the older
per-fetch zlib format stays readable and can be compacted without loss."""
import os
import shutil
import sqlite3
import tempfile
import unittest
import zlib

from twowebs import store


def fetch(identity, rep, body):
    return {"identity": identity, "rep": rep, "ts": 0.0, "status": 200,
            "final_url": "https://a.test/", "content_type": "text/html",
            "headers": {"server": "x"}, "body": body, "truncated": 0, "error": None}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "t.sqlite")
        self.db = store.connect(self.path)
        store.add_sites(self.db, "s", [(1, "a.test")])
        self.site = self.db.execute("SELECT site_id FROM sites").fetchone()[0]

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identical_bodies_are_stored_once_and_read_back(self):
        a, b = b"<html>same page</html>" * 50, b"<html>other page</html>" * 50
        fs = [fetch("browser", 1, a), fetch("ClaudeBot", 1, a), fetch("GPTBot", 1, b),
              fetch("browser", 2, a), fetch("ClaudeBot", 2, b""), ]
        store.save_site(self.db, self.site, [{"url": "https://a.test/", "kind": "home", "fetches": fs}])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM bodies").fetchone()[0], 2)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM fetches WHERE body IS NOT NULL").fetchone()[0], 0)
        got = store.page_fetches(self.db, 1)
        self.assertEqual([f["body"] for f in got], [a, a, b, a, b""])
        self.assertEqual(got[0]["headers"], {"server": "x"})
        self.assertNotIn("body_sha", got[0])

    def _insert_legacy(self, identity, rep, body):
        self.db.execute("INSERT OR IGNORE INTO pages(site_id, url, kind) VALUES(?,?,?)",
                        (self.site, "https://a.test/", "home"))
        self.db.execute(
            "INSERT INTO fetches(page_id, identity, rep, ts, status, final_url, content_type,"
            " n_bytes, truncated, error, headers, body) VALUES(1,?,?,0,200,'u','text/html',?,0,NULL,'{}',?)",
            (identity, rep, len(body), zlib.compress(body, 6)))
        self.db.commit()

    def test_legacy_rows_are_readable_and_compact_is_lossless(self):
        a, b = b"legacy page one " * 200, b"legacy page two " * 200
        for ident, rep, body in (("browser", 1, a), ("ClaudeBot", 1, a), ("GPTBot", 1, b),
                                 ("browser", 2, b"")):
            self._insert_legacy(ident, rep, body)
        before = [f["body"] for f in store.page_fetches(self.db, 1)]
        self.assertEqual(before, [a, a, b, b""])
        self.assertEqual(store.compact(self.db), 4)
        self.assertEqual([f["body"] for f in store.page_fetches(self.db, 1)], before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM bodies").fetchone()[0], 2)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM fetches WHERE body IS NOT NULL").fetchone()[0], 0)
        self.assertEqual(store.compact(self.db), 0)  # second run is a no-op

    def test_connect_upgrades_an_old_database(self):
        self.db.close()
        os.remove(self.path)
        old = sqlite3.connect(self.path)
        old.execute("CREATE TABLE fetches(fetch_id INTEGER PRIMARY KEY, page_id INTEGER,"
                    " identity TEXT, rep INTEGER, ts REAL, status INTEGER, final_url TEXT,"
                    " content_type TEXT, n_bytes INTEGER, truncated INTEGER, error TEXT,"
                    " headers TEXT, body BLOB)")
        old.commit()
        old.close()
        self.db = store.connect(self.path)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(fetches)").fetchall()]
        self.assertIn("body_sha", cols)


if __name__ == "__main__":
    unittest.main()
