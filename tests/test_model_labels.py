from __future__ import annotations

import unittest


class RuntimeModelLabelTests(unittest.TestCase):
    def test_model_label_map_prefers_display_names_without_secrets(self):
        from runtime.execution.dynamic import _model_label_map

        class Registry:
            def get_model_info(self, model_id):
                return {
                    "deepseek_v4_pro_model": {
                        "display_name_zh": "DeepSeek V4 Pro",
                        "model_name": "deepseek-v4-pro",
                        "api_key_env": "DEEPSEEK_API_KEY",
                    },
                    "kimi_k2_model": {
                        "display_name": "Kimi K2",
                        "model_name": "kimi-k2",
                        "api_key_env": "KIMI_API_KEY",
                    },
                }.get(model_id, {})

        labels = _model_label_map(Registry(), ["deepseek_v4_pro_model", "kimi_k2_model", "missing_model"])

        self.assertEqual(labels["deepseek_v4_pro_model"], "DeepSeek V4 Pro")
        self.assertEqual(labels["kimi_k2_model"], "Kimi K2")
        self.assertEqual(labels["missing_model"], "missing_model")
        self.assertNotIn("API_KEY", "\n".join(labels.values()))

    def test_model_label_map_prettifies_provider_model_slugs(self):
        from runtime.execution.dynamic import _model_label_map

        class Registry:
            def get_model_info(self, model_id):
                return {
                    "deepseek_v4_pro_model": {
                        "display_name_zh": "DeepSeek deepseek-v4-pro",
                        "model_name": "deepseek-v4-pro",
                        "provider": "deepseek",
                    },
                    "deepseek_v4_flash_model": {
                        "display_name_zh": "DeepSeek deepseek_v4_flash",
                        "model_name": "deepseek-v4-flash",
                        "provider": "deepseek",
                    },
                }[model_id]

        labels = _model_label_map(Registry(), ["deepseek_v4_pro_model", "deepseek_v4_flash_model"])

        self.assertEqual(labels["deepseek_v4_pro_model"], "DeepSeek V4 Pro")
        self.assertEqual(labels["deepseek_v4_flash_model"], "DeepSeek V4 Flash")


if __name__ == "__main__":
    unittest.main(verbosity=2)
