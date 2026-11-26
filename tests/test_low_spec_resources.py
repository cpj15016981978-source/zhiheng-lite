# -*- coding: utf-8 -*-
"""低配资源闸与分级启动报告。"""
import unittest

from pai import low_spec as LS

try:
    import webapp
except Exception as e:
    webapp = None
    _IMPORT_ERROR = e


class LowSpecResourceTest(unittest.TestCase):
    def test_memory_status_has_budget(self):
        st = LS.memory_status()
        for key in ("total_mb", "free_mb", "budget_mb", "can_load", "concurrency"):
            self.assertIn(key, st)

    def test_concurrency_slot_releases(self):
        LS.acquire(timeout=1)
        LS.release()
        LS.acquire(timeout=1)
        LS.release()


@unittest.skipIf(webapp is None, "webapp import requires runtime deps")
class StartupReportTest(unittest.TestCase):
    def test_noncritical_failure_is_reported_not_raised(self):
        ok = webapp._startup_step("_test_bad", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertFalse(ok)
        self.assertFalse(webapp.STARTUP_REPORT["_test_bad"]["ok"])

    def test_critical_failure_raises(self):
        with self.assertRaises(RuntimeError):
            webapp._startup_step("_test_critical", lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                                 critical=True)


if __name__ == "__main__":
    unittest.main()
