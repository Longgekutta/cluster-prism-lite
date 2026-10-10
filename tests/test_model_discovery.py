#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_model_discovery.py: 模型自动发现验收神谕（红灯）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_discovery import (
    filter_text_models, filter_non_text_models, parse_models_response,
    TEXT_MODES,
)


class TestFilterTextModels(unittest.TestCase):
    def test_llm_mode_selected(self):
        """mode=llm 是文本模型"""
        models = [
            {"id": "a", "mode": "llm"},
            {"id": "b", "mode": "stt"},
            {"id": "c", "mode": "tts"},
        ]
        text = filter_text_models(models)
        self.assertEqual([m["id"] for m in text], ["a"])

    def test_text_mode_selected(self):
        models = [{"id": "x", "mode": "text"}, {"id": "y", "mode": "llm"}]
        self.assertEqual(len(filter_text_models(models)), 2)

    def test_voice_models_excluded(self):
        """stt/tts/voice_*/image/video/music 全部排除"""
        models = [{"id": f"m{i}", "mode": m} for i, m in enumerate(
            ["stt", "tts", "voice_clone", "voice_isolation", "voice_changer",
             "voice_design", "image", "video", "music", "sound_effect",
             "audio_generation", "realtime_voice_dialog", "video_translation"])]
        self.assertEqual(filter_text_models(models), [])

    def test_missing_mode_excluded(self):
        """无 mode 字段 → 排除（保守，不猜测）"""
        models = [{"id": "unknown"}]
        self.assertEqual(filter_text_models(models), [])


class TestFilterNonText(unittest.TestCase):
    def test_complement(self):
        models = [{"id": "a", "mode": "llm"}, {"id": "b", "mode": "stt"}]
        non_text = filter_non_text_models(models)
        self.assertEqual([m["id"] for m in non_text], ["b"])


class TestParseResponse(unittest.TestCase):
    def test_parse_openai_list(self):
        """解析 OpenAI /v1/models 格式 {"object":"list","data":[...]}"""
        resp = {"object": "list", "data": [
            {"id": "m1", "mode": "llm"},
            {"id": "m2", "mode": "stt"},
        ]}
        models = parse_models_response(resp)
        self.assertEqual(len(models), 2)

    def test_parse_invalid(self):
        with self.assertRaises(ValueError):
            parse_models_response({"error": "bad"})

    def test_text_modes_constant(self):
        """文本模式常量：llm + text"""
        self.assertIn("llm", TEXT_MODES)
        self.assertIn("text", TEXT_MODES)
        self.assertNotIn("stt", TEXT_MODES)


if __name__ == "__main__":
    unittest.main()
