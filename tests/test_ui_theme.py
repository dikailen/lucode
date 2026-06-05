from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class UiThemeTests(unittest.TestCase):
    def test_theme_presets_include_pink_and_default_cyan(self):
        from runtime.ui.theme import DEFAULT_UI_THEME, get_theme_preset, list_theme_presets

        self.assertEqual(DEFAULT_UI_THEME.name, "cyan")
        self.assertIn("pink", list_theme_presets())
        self.assertEqual(get_theme_preset("pink").brand, "magenta")
        self.assertIsNone(get_theme_preset("missing"))

    def test_theme_config_round_trip_uses_lucode_config(self):
        from runtime.config.model_config import load_lucode_config
        from runtime.config.theme_config import load_theme_name, save_theme_name

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            self.assertEqual(load_theme_name(workspace_root=workspace), "cyan")
            saved = save_theme_name("pink", workspace_root=workspace)

            config = load_lucode_config(workspace_root=workspace)

            self.assertEqual(saved, "pink")
            self.assertEqual(config["ui"]["theme"], "pink")
            self.assertEqual(load_theme_name(workspace_root=workspace), "pink")

    def test_theme_config_rejects_unknown_theme(self):
        from runtime.config.theme_config import save_theme_name

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                save_theme_name("unknown", workspace_root=Path(tmp))

    def test_theme_config_reads_legacy_flat_ui_theme(self):
        from runtime.config.theme_config import load_theme_name

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_path = workspace / ".lucode" / "config.toml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('ui_theme = "pink"\n', encoding="utf-8")

            self.assertEqual(load_theme_name(workspace_root=workspace), "pink")

    def test_theme_prompt_toolkit_style_uses_pink_brand(self):
        from runtime.ui.theme import get_theme_preset, prompt_toolkit_prompt_style

        self.assertEqual(prompt_toolkit_prompt_style(get_theme_preset("pink")), "ansimagenta bold")

    def test_slash_prompt_session_accepts_theme_prompt_style(self):
        from runtime.commands.completion import slash_prompt_session_kwargs

        kwargs = slash_prompt_session_kwargs(prompt_style="ansimagenta bold")

        self.assertIn("style", kwargs)
        self.assertIn("complete_style", kwargs)

    def test_theme_command_lists_previews_and_saves(self):
        from lucode.shell.slash_commands import _handle_theme_command
        from runtime.config.settings import RuntimeSettings
        from runtime.config.theme_config import load_theme_name
        from runtime.config.workspace import WorkspaceContext

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = WorkspaceContext(
                app_home=workspace,
                user_home=workspace / "user",
                workspace_root=workspace,
                project_config_dir=workspace / ".lucode",
                has_project_config=False,
            )

            listed = _handle_theme_command(
                "/theme list",
                workspace_context=context,
                runtime_settings=RuntimeSettings(execution_mode="solo"),
                use_color=False,
                show_logo=True,
            )
            preview = _handle_theme_command(
                "/theme preview pink",
                workspace_context=context,
                runtime_settings=RuntimeSettings(execution_mode="solo"),
                use_color=False,
                show_logo=True,
            )
            saved = _handle_theme_command(
                "/theme pink",
                workspace_context=context,
                runtime_settings=RuntimeSettings(execution_mode="solo"),
                use_color=False,
                show_logo=True,
            )

            self.assertIn("pink", listed)
            self.assertIn("主题预览：pink", preview)
            self.assertIn(str(workspace), preview)
            self.assertIn("已切换主题：pink", saved)
            self.assertEqual(load_theme_name(workspace_root=workspace, user_home=context.user_home), "pink")

    def test_theme_completion_includes_pink_preview(self):
        from runtime.commands.completion import command_completion_items

        items = command_completion_items("/theme preview p")
        texts = [item.text for item in items]

        self.assertIn("/theme preview pink", texts)

    def test_welcome_rich_uses_saved_theme_border(self):
        from runtime.config.settings import RuntimeSettings
        from runtime.config.theme_config import save_theme_name
        from runtime.config.workspace import WorkspaceContext
        from runtime.ui.welcome import render_welcome_dashboard

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            save_theme_name("pink", workspace_root=workspace)
            context = WorkspaceContext(
                app_home=workspace,
                user_home=workspace / "user",
                workspace_root=workspace,
                project_config_dir=workspace / ".lucode",
                has_project_config=True,
            )

            with patch.dict("os.environ", {"AGENTS_DYNAMIC_UI": "on"}, clear=False):
                rendered = render_welcome_dashboard(
                    context,
                    RuntimeSettings(execution_mode="full"),
                    model_catalog={"models": []},
                    use_color=False,
                    show_logo=True,
                )

            self.assertIn("\x1b[35m", rendered)

    def test_rich_live_accepts_theme_tokens(self):
        from runtime.ui.rich_live import _build_renderable_from_view
        from runtime.ui.rich_live_view import RichActorBlock, RichLiveView
        from runtime.ui.theme import get_theme_preset

        renderable = _build_renderable_from_view(
            RichLiveView(
                mode="full",
                route="team",
                attempt=1,
                plan_items=[],
                actor_blocks=[
                    RichActorBlock(
                        role="worker",
                        title="Worker 1",
                        subtitle="full",
                        model_label="DeepSeek V4 Pro",
                        current_action="Reading",
                        status="running",
                    )
                ],
            ),
            theme=get_theme_preset("pink"),
        )

        self.assertIsNotNone(renderable)


if __name__ == "__main__":
    unittest.main()
