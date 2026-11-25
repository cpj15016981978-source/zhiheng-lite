# -*- coding: utf-8 -*-
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import webapp  # noqa: E402


class WebappRealDbGuardTest(unittest.TestCase):
    def test_project_data_db_without_flag_is_refused(self):
        real = pathlib.Path(webapp.__file__).resolve().parent / "data" / "memory.db"
        with mock.patch.object(webapp, "serve") as serve:
            rc = webapp.main(["--db", str(real), "--no-browser"])
        self.assertEqual(rc, 2)
        serve.assert_not_called()

    def test_temp_db_still_starts(self):
        temp = pathlib.Path(tempfile.mkdtemp()) / "smoke.db"
        with mock.patch.object(webapp, "serve") as serve:
            rc = webapp.main(["--db", str(temp), "--no-browser"])
        self.assertEqual(rc, 0)
        serve.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
