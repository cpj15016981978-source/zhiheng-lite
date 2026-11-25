# -*- coding: utf-8 -*-
import os
import pathlib
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pai.memory import Memory  # noqa: E402
from pai import self_model as SM  # noqa: E402
from tools import c_self_probe as P  # noqa: E402


def _web_up(port=8753):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class SelfProbeTest(unittest.TestCase):
    def setUp(self):
        os.environ["PAI_SELF_FACTS"] = "on"
        os.environ["PAI_MANIFEST"] = "on"

    def tearDown(self):
        os.environ.pop("PAI_SELF_FACTS", None)
        os.environ.pop("PAI_MANIFEST", None)

    def test_twenty_probes_pass_with_identity(self):
        m = Memory(str(pathlib.Path(tempfile.mkdtemp()) / "probe.db"))
        SM.ensure_identity(m)
        rows, facts = P.run(m, ports={8753})
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({r["id"] for r in rows}), 20)
        failed = [r["id"] for r in rows if not r["ok"]]
        self.assertEqual(failed, [])
        self.assertIn("existence", facts)

    def test_missing_identity_is_not_hidden(self):
        m = Memory(str(pathlib.Path(tempfile.mkdtemp()) / "probe.db"))
        rows, _ = P.run(m, ports=set())
        failed = [r["id"] for r in rows if not r["ok"]]
        self.assertEqual(failed, ["C1-02", "C2-01", "C2-02", "C2-03", "C2-04", "C2-05"])


    @unittest.skipUnless(_web_up(), "8753 未监听：C1-02 现场条件不满足，显式 skip")
    def test_main_cli_path_runs(self):
        import contextlib
        import io
        db = pathlib.Path(tempfile.mkdtemp()) / "probe.db"
        m = Memory(str(db))
        SM.ensure_identity(m)
        m.conn.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = P.main(["--db", str(db), "--json"])
        self.assertEqual(rc, 0)
        self.assertIn('"passed": %d' % len(P.PROBES), buf.getvalue())

    def test_probe_refuses_default_real_db(self):
        with self.assertRaises(SystemExit):
            P.main([])

    def test_probe_refuses_real_db_path(self):
        root = pathlib.Path(P.__file__).resolve().parents[1]
        with self.assertRaises(SystemExit):
            P.main(["--db", str(root / "data" / "memory.db")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
