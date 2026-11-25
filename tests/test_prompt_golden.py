# -*- coding: utf-8 -*-
"""prompt 段落顺序黄金测试：固定输入，锁住差异化注入的顺序与降级。"""
from __future__ import annotations

import unittest

from pai import orchestrator as O


class PromptGoldenTest(unittest.TestCase):
    def test_full_prompt_section_order(self):
        text = O._build_prompt(
            "你好",
            recalled=[{"item": {"type": "episode", "id": 1, "role": "user",
                                "content": "旧事", "ts": "2026-11-17T08:00:00"}}],
            facts=[{"subject": "项目", "predicate": "主线", "object": "陆吾"}],
            principles=[{"text": "先给结论"}],
            culture=[{"ref": "《论语》", "text": "学而时习之"}],
            affect={"mood": "平静", "intensity": 0.2, "heavy": False},
            self_desc="[我是谁] 我是陆吾里的吹雪",
            extra_rule="只回答当前问题",
            memory_ctx="记忆档案正文",
            wiki_ctx="Wiki 正文",
            router_ctx="Router 正文",
            poetry_ctx="诗词正文",
        )
        headers = [
            "[系统]",
            "[人格 ·",
            "[我恪守的原则]",
            "[本轮行为规则]",
            "[已知事实]",
            "[记忆档案（带来源）]",
            "[Wiki 知识（带来源）]",
            "[诗词库（检索所得，非我生成；引用须标作者/篇名）]",
            "[Router 检索（带来源）]",
            "[相关记忆]",
            "[可引用的文化背景（检索所得，非我生成；引用时须标出处）]",
            "[我是谁]",
            "[对方此刻的情绪基调",
            "[用户] 你好",
        ]
        positions = [text.find(h) for h in headers]
        self.assertTrue(all(p >= 0 for p in positions), (headers, positions))
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(text.endswith("[用户] 你好"))

    def test_optional_sections_degrade(self):
        text = O._build_prompt("只回一句", [], [])
        self.assertNotIn("[记忆档案（带来源）]", text)
        self.assertNotIn("[Wiki 知识（带来源）]", text)
        self.assertNotIn("[Router 检索（带来源）]", text)
        self.assertIn("[相关记忆]", text)
        self.assertIn("[可引用的文化背景", text)
        self.assertTrue(text.endswith("[用户] 只回一句"))


if __name__ == "__main__":
    unittest.main()
