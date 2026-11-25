# -*- coding: utf-8 -*-
import unittest

from pai import local_search as LS


class FakeMem:
    def recall(self, q, k=5):
        return [{"item": {"type": "episode", "content": "工厂对账流程"}}]

    def recall_items(self, q, k=5):
        return [{"kind": "note", "text": "报价单要核对"}]


class LocalSearchTest(unittest.TestCase):
    def test_reads_memory_without_network(self):
        out = LS.search(FakeMem(), "对账")
        self.assertEqual(len(out), 2)
        self.assertIn("工厂对账", out[0]["content"])
        self.assertTrue(all(x["source"].startswith("memory") for x in out))

    def test_empty_query(self):
        self.assertEqual(LS.search(FakeMem(), ""), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
