# -*- coding: utf-8 -*-
"""接线测试：webapp 的模块别名不能再串。"""
import unittest

try:
    import webapp
except Exception as e:  # 缺依赖时不让整个测试文件 collection 失败
    webapp = None
    _IMPORT_ERROR = e


@unittest.skipIf(webapp is None, "webapp import requires runtime deps")
class WebappAliasTest(unittest.TestCase):
    def test_modules_are_wired_to_intended_targets(self):
        self.assertEqual(webapp.CSCORE.__name__, "pai.c_score")
        self.assertEqual(webapp.CONV.__name__, "pai.conversation_store")
        self.assertEqual(webapp.LSEARCH.__name__, "pai.local_search")
        self.assertEqual(webapp.LOWSPEC.__name__, "pai.low_spec")
        self.assertEqual(webapp.MMEM.__name__, "pai.mirrai_memory")
        self.assertEqual(webapp.MMODE.__name__, "pai.model_mode")

    def test_default_route_is_chat(self):
        src = open("webapp.py", encoding="utf-8").read()
        self.assertIn('self.path in ("/", "/m1", "/m1/")', src)
        self.assertIn('Location", "/chat"', src)


if __name__ == "__main__":
    unittest.main()
