from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from lucode.shell.input_adapter import ConsoleChoice, ConsoleFormField, ConsoleFormResult, RuntimeCommandSession
from lucode.shell.runtime_env import turn_timeout_seconds
from lucode.shell.turn_display import (
    format_turn_error,
    is_exit_command,
    is_new_command,
    is_stop_command,
)
from planning.planner import format_plan_preview, preview_plan
from runtime.commands.invocation import resolve_mcp_prompt_invocation
from runtime.config.cli import (
    apply_writable_config_command,
    parse_writable_config_command,
    render_diff_command,
    render_readonly_command,
    render_status_command,
)
from runtime.config.theme_config import load_theme_name, save_theme_name
from runtime.config.model_tuner import (
    apply_model_tuner_selection,
    build_model_tuner_state,
    model_tuner_command_items,
    model_tuner_help,
    render_model_tuner_snapshot,
    resolve_model_selection,
    resolve_role_selection,
)
from runtime.config.connect_wizard import (
    ConnectWizardCommandItem,
    apply_connect_wizard_connection,
    apply_connect_wizard_input,
    build_connect_wizard_state,
    connect_wizard_command_items,
    connected_provider_delete_items,
    render_connect_wizard_snapshot,
)
from runtime.context.semantic_compaction import compact_messages_tiered
from runtime.history import HistoryFacade, HistoryStore, render_history_panel, run_history_browser
from runtime.kernel.session import create_token_logger_hooks
from runtime.sessions import render_resume_preview, render_session_list
from runtime.ui.welcome import render_welcome_dashboard
from runtime.ui.theme import get_theme_preset, list_theme_presets, render_theme_list, render_theme_preview


@dataclass
class SlashCommandResult:
    handled: bool = False
    should_exit: bool = False
    reset_recent_turns: bool = False
    resumed_recent_turns: list[dict[str, str]] | None = None
    resumed_session_summary: str | None = None
    session_id: str | None = None
    expanded_user_input: str | None = None
    expanded_from_command: str | None = None


async def handle_slash_command(
    user_input: str,
    *,
    model_registry,
    runtime_settings,
    console,
    app_home: Path,
    project_root: Path,
    workspace_context,
    use_color: bool | None,
    show_logo: bool,
    started_mcp_ids: list[str],
    checkpoint_manager,
    session_store=None,
    current_session_id: str | None = None,
    last_run_context_summary: str = "",
) -> SlashCommandResult:
    if is_exit_command(user_input):
        print("已退出。")
        return SlashCommandResult(handled=True, should_exit=True)

    if is_stop_command(user_input):
        print("已停止当前输入，你可以重新输入新的问题。")
        return SlashCommandResult(handled=True)

    if is_new_command(user_input):
        print("已创建新对话，历史上下文已清空。")
        print(render_welcome_dashboard(workspace_context, runtime_settings, use_color=use_color, show_logo=show_logo))
        return SlashCommandResult(handled=True, reset_recent_turns=True, session_id="")

    if not user_input:
        return SlashCommandResult()

    lower_input = user_input.lower()
    if lower_input == "/resume" or lower_input.startswith("/resume "):
        return await _handle_resume_command(
            user_input,
            session_store=session_store,
            current_session_id=current_session_id,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
        )

    if lower_input == "/history" or lower_input.startswith("/history "):
        return await _handle_history_command(
            user_input,
            console=console,
            session_store=session_store,
            current_session_id=current_session_id,
            workspace_context=workspace_context,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
        )

    if lower_input == "/context":
        print(_render_last_run_context(last_run_context_summary))
        return SlashCommandResult(handled=True)

    if lower_input == "/theme" or lower_input.startswith("/theme "):
        print(
            _handle_theme_command(
                user_input,
                workspace_context=workspace_context,
                runtime_settings=runtime_settings,
                use_color=use_color,
                show_logo=show_logo,
            )
        )
        return SlashCommandResult(handled=True)

    if lower_input == "/models":
        if getattr(console, "interactive", False):
            await _handle_model_tuner_session(
                console=console,
                runtime_settings=runtime_settings,
                workspace_context=workspace_context,
                use_color=use_color,
                show_logo=show_logo,
            )
        else:
            print(render_readonly_command(user_input, runtime_settings, workspace_context))
        return SlashCommandResult(handled=True)

    if lower_input == "/connect":
        if getattr(console, "interactive", False):
            await _handle_connect_wizard_session(
                console=console,
                workspace_context=workspace_context,
                runtime_settings=runtime_settings,
            )
        else:
            print(render_readonly_command(user_input, runtime_settings, workspace_context))
        return SlashCommandResult(handled=True)

    parsed_config = parse_writable_config_command(user_input)
    if parsed_config is not None or user_input.lower().startswith(("/mode ", "/refiner ")):
        output, updated = apply_writable_config_command(
            user_input,
            app_home / ".env",
            runtime_settings,
            workspace_context=workspace_context,
        )
        print(output)
        if updated and parsed_config and parsed_config[0] == "mode":
            print(render_welcome_dashboard(workspace_context, runtime_settings, use_color=use_color, show_logo=show_logo))
        return SlashCommandResult(handled=True)

    if user_input.lower() == "/status":
        print(
            render_status_command(
                project_root,
                runtime_settings,
                started_mcp_ids=started_mcp_ids,
                rollback_status=checkpoint_manager.render_status(),
            )
        )
        return SlashCommandResult(handled=True)

    if user_input.lower().startswith("/diff"):
        print(render_diff_command(project_root))
        return SlashCommandResult(handled=True)

    if user_input.lower() == "/rollback":
        result = checkpoint_manager.rollback_last_turn()
        print(result.message)
        return SlashCommandResult(handled=True)

    mcp_prompt_invocation = resolve_mcp_prompt_invocation(user_input, workspace_context)
    if mcp_prompt_invocation is not None:
        print(f"已展开 MCP Prompt：{mcp_prompt_invocation.spec.command}")
        return SlashCommandResult(
            expanded_user_input=mcp_prompt_invocation.expanded_input,
            expanded_from_command=user_input,
        )

    config_output = render_readonly_command(user_input, runtime_settings, workspace_context)
    if config_output:
        print(config_output)
        return SlashCommandResult(handled=True)

    if user_input.startswith("/plan"):
        await _handle_plan_command(
            user_input,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
            console=console,
        )
        return SlashCommandResult(handled=True)

    return SlashCommandResult()


def _handle_theme_command(
    user_input: str,
    *,
    workspace_context,
    runtime_settings,
    use_color: bool | None,
    show_logo: bool,
) -> str:
    parts = str(user_input or "").strip().split()
    current = load_theme_name(
        workspace_root=getattr(workspace_context, "workspace_root", None),
        user_home=getattr(workspace_context, "user_home", None),
    )
    if len(parts) == 1 or (len(parts) >= 2 and parts[1].lower() == "list"):
        return render_theme_list(current=current)
    if len(parts) >= 2 and parts[1].lower() == "preview":
        name = parts[2] if len(parts) >= 3 else current
        return render_theme_preview(name, workspace_root=getattr(workspace_context, "workspace_root", None))

    name = parts[1].lower()
    if get_theme_preset(name) is None:
        return f"未知主题：{name}\n可用主题：{', '.join(list_theme_presets())}"
    saved = save_theme_name(name, workspace_root=getattr(workspace_context, "workspace_root", None))
    return "\n".join(
        [
            f"已切换主题：{saved}",
            render_theme_preview(saved, workspace_root=getattr(workspace_context, "workspace_root", None)),
            render_welcome_dashboard(workspace_context, runtime_settings, use_color=use_color, show_logo=show_logo),
        ]
    )


def _render_last_run_context(summary: str) -> str:
    text = str(summary or "").strip()
    if not text:
        return "最近一轮共享上下文：暂无记录。先执行一个会读取文件或工具结果的任务，再输入 /context 查看。"
    return "最近一轮共享上下文\n" + text


def _history_facade_for_session_store(session_store) -> HistoryFacade:
    workspace_root = session_store.workspace_root
    if isinstance(session_store, HistoryStore):
        return HistoryFacade(workspace_root, history_store=session_store)
    return HistoryFacade(workspace_root, session_store=session_store)


def _uses_choice_menu(console) -> bool:
    return callable(getattr(console, "read_choice_line", None))


async def _handle_model_tuner_session(*, console, runtime_settings, workspace_context, use_color: bool | None, show_logo: bool) -> None:
    selected_role = "orchestrator"
    message = "已进入模型调音台。选择会立即写入当前项目，输入 q 退出。"
    while True:
        state = build_model_tuner_state(runtime_settings, workspace_context, selected_role=selected_role)
        if not _uses_choice_menu(console):
            print(render_model_tuner_snapshot(state, message=message))
        try:
            user_input = (await _read_model_tuner_line(console, state)).strip()
        except EOFError:
            print("已退出模型调音台。")
            return

        command = user_input.strip()
        lower = command.lower()
        if lower in {"q", "quit", "exit", "/exit", "back", "/back", "return"}:
            print("已退出模型调音台。")
            return
        if not command or lower in {"help", "?", "/help"}:
            message = model_tuner_help()
            continue
        if lower in {"list", "/list", "refresh", "鍒锋柊"}:
            message = "已刷新模型调音台。"
            continue
        if lower in {"s", "save", "淇濆瓨"}:
            message = "模型选择会即时保存；当前没有待保存修改。"
            continue

        try:
            if lower.startswith(("role ", "brain ")):
                selected_role = resolve_role_selection(command.split(maxsplit=1)[1])
                message = f"当前脑位：{_model_tuner_role_label(selected_role)}。"
                continue
            if command.isdigit() and 1 <= int(command) <= 4:
                selected_role = resolve_role_selection(command)
                message = f"当前脑位：{_model_tuner_role_label(selected_role)}。"
                continue
            if lower.startswith(("select ", "model ", "use ")):
                selector = command.split(maxsplit=1)[1]
                model_ref = resolve_model_selection(selector, state)
                result = apply_model_tuner_selection(
                    runtime_settings,
                    workspace_context,
                    role=selected_role,
                    refs=[model_ref],
                )
                message = result.message
                print(render_welcome_dashboard(workspace_context, runtime_settings, use_color=use_color, show_logo=show_logo))
                print(message)
                return
            if lower.startswith("/models brain "):
                output, updated = apply_writable_config_command(
                    command,
                    workspace_context.app_home / ".env",
                    runtime_settings,
                    workspace_context=workspace_context,
                )
                if updated:
                    print(render_welcome_dashboard(workspace_context, runtime_settings, use_color=use_color, show_logo=show_logo))
                message = output if updated else f"命令未生效：{output}"
                if updated:
                    print(message)
                    return
                continue
        except Exception as exc:
            message = f"操作失败：{exc}"
            continue

        message = "无法识别。用 role 1-4 切换脑位，select 1 应用模型，q 退出。"


async def _handle_connect_wizard_session(*, console, workspace_context, runtime_settings) -> None:
    state = build_connect_wizard_state(workspace_context)
    message = "已进入 Provider 连接向导。先选择 Provider，后面会进入完整表单。"
    while True:
        if not _uses_choice_menu(console):
            print(render_connect_wizard_snapshot(state, message=message))
        try:
            user_input = (await _read_connect_wizard_line(console, state)).strip()
        except EOFError:
            print("已退出 Provider 连接向导。")
            return

        command = user_input.strip()
        lower = command.lower()
        if lower in {"q", "quit", "exit", "/exit", "back", "/back", "return"}:
            print("已退出 Provider 连接向导。")
            return
        if lower in {"delete", "remove", "鍒犻櫎", "鍒犻櫎 provider", "delete provider"}:
            message = await _handle_connect_delete_session(
                console=console,
                state=state,
                workspace_context=workspace_context,
                runtime_settings=runtime_settings,
            )
            state = build_connect_wizard_state(workspace_context)
            if _uses_choice_menu(console):
                print(message)
            continue
        if lower.startswith(("delete ", "remove ")):
            provider_id = command.split(maxsplit=1)[1].strip()
            message = await _confirm_and_delete_provider(
                console=console,
                provider_id=provider_id,
                workspace_context=workspace_context,
                runtime_settings=runtime_settings,
            )
            state = build_connect_wizard_state(workspace_context)
            if _uses_choice_menu(console):
                print(message)
            continue
        if lower in {"connect", "save", "淇濆瓨"} and not state.selected_provider:
            message = "请先选择 Provider。"
            continue
        if lower in {"connect", "save", "淇濆瓨"}:
            try:
                result = apply_connect_wizard_connection(state)
                _clear_connect_wizard_caches()
                message = result.message
            except Exception as exc:
                message = f"连接失败：{exc}"
            continue

        try:
            state, message = apply_connect_wizard_input(state, command)
        except Exception as exc:
            message = f"操作失败：{exc}"
            continue

        if state.selected_provider:
            try:
                await _complete_connect_wizard_form(
                    console=console,
                    state=state,
                    workspace_context=workspace_context,
                    runtime_settings=runtime_settings,
                )
            except _ConnectWizardCancelled:
                print("已取消连接向导，未保存。")
                return
            except _ConnectWizardRestartProvider:
                state = build_connect_wizard_state(workspace_context)
                message = "已返回 Provider 选择。"
                if _uses_choice_menu(console):
                    print(message)
                continue
            return


async def _read_connect_wizard_line(console, state) -> str:
    choices = connect_wizard_command_items(state)
    choice_reader = getattr(console, "read_choice_line", None)
    if callable(choice_reader):
        try:
            return await choice_reader(
                "\nProvider连接> ",
                choices,
                bottom_toolbar="↑↓ 选择，Enter 确认；滚轮可滚动终端历史；key <你的 key> 可直接粘贴；q 退出",
                reserve_space_for_menu=12,
            )
        except TypeError:
            pass
    print("\nProvider连接> ", end="", flush=True)
    reader = getattr(console, "read_runtime_line", None)
    if callable(reader):
        return await reader()
    return await console.read_line("")


async def _handle_connect_delete_session(*, console, state, workspace_context, runtime_settings) -> str:
    delete_items = connected_provider_delete_items(state)
    if not delete_items:
        return "暂无可删除 Provider。"
    choices = [
        ConnectWizardCommandItem("back", "返回连接向导", "不删除任何配置"),
        *delete_items,
    ]
    value = await _read_connect_delete_choice(
        console,
        "\n鍒犻櫎妯″瀷> ",
        choices,
        toolbar="↑↓ 选择要删除的模型/Provider，Enter 确认，q 返回",
    )
    lower = value.lower()
    if lower in {"q", "quit", "exit", "/exit", "back", "/back", "杩斿洖", "鍙栨秷"}:
        return "已取消删除。"
    if lower.startswith(("delete ", "remove ")):
        provider_id = value.split(maxsplit=1)[1].strip()
        return await _confirm_and_delete_provider(
            console=console,
            provider_id=provider_id,
            workspace_context=workspace_context,
            runtime_settings=runtime_settings,
        )
    return "请选择删除列表里的 Provider，或 q 返回。"


async def _confirm_and_delete_provider(*, console, provider_id: str, workspace_context, runtime_settings) -> str:
    provider_id = str(provider_id or "").strip()
    if not provider_id:
        return "请先选择要删除的 Provider。"
    choices = [
        ConnectWizardCommandItem("no", "取消", "不改动配置"),
        ConnectWizardCommandItem("yes", "确认删除", f"{provider_id}：删除项目配置、API key，并清理模型/脑位引用"),
    ]
    value = await _read_connect_delete_choice(
        console,
        "\n确认删除> ",
        choices,
        toolbar="↑↓ 选择，Enter 确认；默认取消，避免误删模型配置",
    )
    if value.lower() not in {"yes", "y", "confirm"}:
        return "已取消删除。"
    output, updated = apply_writable_config_command(
        f"/connect remove {provider_id}",
        workspace_context.app_home / ".env",
        runtime_settings,
        workspace_context=workspace_context,
    )
    if updated:
        _clear_connect_wizard_caches()
    return output


async def _read_connect_delete_choice(console, prompt: str, choices, *, toolbar: str) -> str:
    choice_reader = getattr(console, "read_choice_line", None)
    if callable(choice_reader):
        try:
            return str(
                await choice_reader(
                    prompt,
                    choices,
                    bottom_toolbar=toolbar,
                    reserve_space_for_menu=max(5, min(12, len(choices) + 2)),
                )
            ).strip()
        except TypeError:
            pass
    print(prompt, end="", flush=True)
    reader = getattr(console, "read_runtime_line", None)
    if callable(reader):
        return str(await reader()).strip()
    return str(await console.read_line("")).strip()


class _ConnectWizardCancelled(Exception):
    pass


class _ConnectWizardRestartProvider(Exception):
    pass


class _ConnectWizardFieldBack(Exception):
    pass


class _ConnectWizardFieldRetry(Exception):
    pass


async def _complete_connect_wizard_form(*, console, state, workspace_context, runtime_settings) -> None:
    message = "已进入完整连接表单。选择要编辑的字段，填完后选择保存。"
    while True:
        fullscreen_result = await _read_connect_fullscreen_form(console, state, message)
        if fullscreen_result is not None:
            try:
                state = _apply_connect_form_values(state, fullscreen_result.values)
                action = str(fullscreen_result.action or "").strip().lower()
                if action in {"q", "quit", "exit", "/exit", "cancel", "鍙栨秷"}:
                    raise _ConnectWizardCancelled()
                if action in {"change_provider", "provider", "back", "b", "return"}:
                    raise _ConnectWizardRestartProvider()
                if action in {"save_default", "default", "yes", "y", "set_default"}:
                    print(_save_connect_form(state, workspace_context, runtime_settings, set_default=True))
                    print("已退出 Provider 连接向导。")
                    return
                if action in {"save_only", "save", "connect", "only", "no", "n"}:
                    print(_save_connect_form(state, workspace_context, runtime_settings, set_default=False))
                    print("已退出 Provider 连接向导。")
                    return
                message = "全屏表单返回了未知动作，已回到轻量表单。"
            except _ConnectWizardFieldRetry as retry:
                message = f"{retry} 输入没有保存，请在表单里修正。"
                continue
            except ValueError as exc:
                message = str(exc)
                continue

        print(render_connect_wizard_snapshot(state, message=message))
        action = await _read_connect_form_action(console, state)
        lower = action.lower()
        if lower in {"q", "quit", "exit", "/exit", "cancel", "鍙栨秷"}:
            raise _ConnectWizardCancelled()
        if lower in {"change_provider", "provider", "back", "b", "return"}:
            raise _ConnectWizardRestartProvider()
        try:
            state, message, done = await _apply_connect_form_action(
                console=console,
                state=state,
                action=action,
                workspace_context=workspace_context,
                runtime_settings=runtime_settings,
            )
        except _ConnectWizardFieldBack:
            message = "已留在表单页。请选择要编辑的字段。"
            continue
        except _ConnectWizardFieldRetry as retry:
            message = f"{retry} 输入没有保存，已回到表单。"
            continue
        except ValueError as exc:
            message = str(exc)
            continue
        if done:
            print(message)
            print("已退出 Provider 连接向导。")
            return


async def _read_connect_fullscreen_form(console, state, message: str) -> ConsoleFormResult | None:
    reader = getattr(console, "read_form", None)
    if not callable(reader):
        return None
    try:
        result = await reader(
            title="Lucode Provider 连接",
            fields=_connect_fullscreen_fields(state),
            actions=_connect_fullscreen_actions(state),
            message=_connect_fullscreen_message(state, message),
            footer="Tab/↑↓ 切换字段，鼠标可点选；Ctrl+S 保存并设默认，Ctrl+O 仅保存，Ctrl+P 重选 Provider，Esc 取消。",
        )
    except Exception:
        return None
    if result is None:
        return None
    return result


def _connect_fullscreen_fields(state) -> list[ConsoleFormField]:
    fields: list[ConsoleFormField] = []
    if state.custom:
        fields.extend(
            [
                ConsoleFormField(
                    name="homepage",
                    label="官网/控制台地址 *",
                    value=state.homepage,
                    required=True,
                    help="只用于展示和确认，例如 https://proxy.example.com",
                ),
                ConsoleFormField(
                    name="base_url",
                    label="真实请求地址 base_url *",
                    value=state.base_url,
                    required=True,
                    help="模型请求会走这个地址，例如 https://api.proxy.example.com/v1",
                ),
            ]
        )
    fields.append(
        ConsoleFormField(
            name="model",
            label="模型名 *",
            value=_selected_connect_model(state),
            required=True,
            help=_connect_model_hint(state),
        )
    )
    if _connect_form_needs_key(state):
        fields.append(
            ConsoleFormField(
                name="api_key",
                label="API key",
                value=state.api_key,
                required=True,
                secret=True,
                help="输入会隐藏，只保存到用户级 auth.json。",
            )
        )
    return fields


def _connect_fullscreen_actions(state) -> list[ConsoleChoice]:
    return [
        ConsoleChoice("save_default", "保存并设默认", _connect_state_primary_ref(state) or "填完字段后可保存"),
        ConsoleChoice("save_only", "仅保存", "稍后在 /models 选择"),
        ConsoleChoice("change_provider", "重选 Provider", state.selected_provider),
        ConsoleChoice("cancel", "取消", "不保存，返回聊天"),
    ]


def _connect_fullscreen_message(state, message: str) -> str:
    provider = state.display_name or state.selected_provider
    kind = "自定义中转" if state.custom else "内置预设"
    return f"{message}\n当前 Provider：{provider}（{kind}）。保存前会校验必填项。"


def _connect_model_hint(state) -> str:
    preset = state.provider_catalog.get(state.selected_provider) or {}
    models = [str(item).strip() for item in (preset.get("models") or []) if str(item).strip()]
    if models:
        return "推荐：" + ", ".join(models[:4])
    return "例如 qwen-max、gpt-5.2，或中转服务提供的模型名。"


def _apply_connect_form_values(state, values: dict[str, str]):
    if state.custom:
        homepage = str(values.get("homepage", state.homepage) or "").strip()
        base_url = str(values.get("base_url", state.base_url) or "").strip()
        if homepage:
            _validate_connect_url(homepage, "官网/控制台地址")
        if base_url:
            _validate_connect_url(base_url, "真实请求地址")
        state.homepage = homepage
        state.base_url = base_url
    model = str(values.get("model", state.model) or "").strip()
    state.model = _strip_field_alias(model, ("model", "models"))
    if "api_key" in values:
        state.api_key = _strip_field_alias(str(values.get("api_key") or "").strip(), ("key", "api-key", "apikey"))
    return state


async def _read_connect_form_action(console, state) -> str:
    choices = _connect_form_command_items(state)
    choice_reader = getattr(console, "read_choice_line", None)
    if callable(choice_reader):
        value = await choice_reader(
            "\n杩炴帴琛ㄥ崟> ",
            choices,
            bottom_toolbar="↑↓ 选择字段，Enter 编辑；滚轮可滚动终端历史；保存前不会写入；q 退出",
            reserve_space_for_menu=12,
        )
        return str(value or "").strip()
    print("\n杩炴帴琛ㄥ崟> ", end="", flush=True)
    reader = getattr(console, "read_runtime_line", None)
    if callable(reader):
        return str(await reader()).strip()
    return str(await console.read_line("")).strip()


def _connect_form_command_items(state):
    from runtime.config.connect_wizard import ConnectWizardCommandItem

    items = [ConnectWizardCommandItem("change_provider", "重新选择 Provider", state.selected_provider)]
    if state.custom:
        items.extend(
            [
                ConnectWizardCommandItem("edit_homepage", f"编辑 官网/控制台地址    {_field_value(state.homepage)}", "必填"),
                ConnectWizardCommandItem("edit_base_url", f"编辑 真实请求地址       {_field_value(state.base_url)}", "必填"),
                ConnectWizardCommandItem("edit_model", f"编辑 模型名            {_field_value(state.model)}", "必填"),
            ]
        )
    else:
        items.append(ConnectWizardCommandItem("edit_model", f"编辑 模型名            {_field_value(_selected_connect_model(state))}", "可用推荐模型"))
    if _connect_form_needs_key(state):
        key_state = "已填写" if state.api_key else "未填写"
        items.append(ConnectWizardCommandItem("edit_key", f"编辑 API key            {key_state}", "输入会隐藏"))
    items.extend(
        [
            ConnectWizardCommandItem("save_default", "保存并设为默认模型", _connect_state_primary_ref(state) or "填完字段后可保存"),
            ConnectWizardCommandItem("save_only", "仅保存 Provider", "稍后可在 /models 里选择"),
            ConnectWizardCommandItem("cancel", "取消，不保存", "返回聊天"),
        ]
    )
    return items


async def _apply_connect_form_action(*, console, state, action: str, workspace_context, runtime_settings):
    action = str(action or "").strip()
    lower = action.lower()
    if not action:
        return state, "请选择一个字段编辑，或选择保存。", False
    if lower in {"edit_homepage", "homepage", "home"}:
        state = await _collect_one_connect_field(console, state, "homepage")
        return state, "官网/控制台地址已更新。", False
    if lower in {"edit_base_url", "base-url", "base_url", "url"}:
        state = await _collect_one_connect_field(console, state, "base_url")
        return state, "真实请求地址已更新。", False
    if lower in {"edit_model", "model", "models"}:
        state = await _collect_one_connect_field(console, state, "model")
        return state, "模型名已更新。", False
    if lower in {"edit_key", "key", "api-key", "apikey"}:
        state = await _collect_one_connect_field(console, state, "key")
        return state, "API key 已填写，未在屏幕回显。", False
    if lower.startswith(("homepage ", "home ", "base-url ", "base_url ", "url ", "model ", "models ", "key ", "api-key ", "apikey ")):
        state = _apply_inline_connect_form_command(state, action)
        return state, "字段已更新。", False
    if lower in {"save_default", "default", "yes", "y", "set_default"}:
        return state, _save_connect_form(state, workspace_context, runtime_settings, set_default=True), True
    if lower in {"save_only", "save", "connect", "only", "no", "n"}:
        return state, _save_connect_form(state, workspace_context, runtime_settings, set_default=False), True
    if _is_provider_switch_command(action):
        raise _ConnectWizardRestartProvider()
    raise ValueError("无法识别。请用 ↑↓ 选择字段，或输入 edit_model、edit_key、save_default、save_only。")


def _apply_inline_connect_form_command(state, action: str):
    lower = str(action or "").strip().lower()
    if lower.startswith(("homepage ", "home ")):
        value = _strip_field_alias(action, ("homepage", "home"))
        _validate_connect_url(value, "官网/控制台地址")
    elif lower.startswith(("base-url ", "base_url ", "url ")):
        value = _strip_field_alias(action, ("base-url", "base_url", "url"))
        _validate_connect_url(value, "真实请求地址")
    state, _ = apply_connect_wizard_input(state, action)
    return state


def _save_connect_form(state, workspace_context, runtime_settings, *, set_default: bool) -> str:
    missing = _missing_connect_form_fields(state)
    if missing:
        raise ValueError("还缺：" + ", ".join(missing) + "。请先编辑这些字段。")
    result = apply_connect_wizard_connection(state)
    _clear_connect_wizard_caches()
    output = result.message
    if set_default:
        ref = _connect_request_primary_ref(result.request)
        if ref:
            switch_output, updated = apply_writable_config_command(
                f"/models select {ref}",
                workspace_context.app_home / ".env",
                runtime_settings,
                workspace_context=workspace_context,
            )
            output = f"{output}\n\n已设为默认模型：{ref}" if updated else f"{output}\n\n默认模型设置失败：{switch_output}"
        else:
            output = f"{output}\n\n这个 Provider 暂无模型名，未设置默认模型。"
    return output


def _missing_connect_form_fields(state) -> list[str]:
    missing = []
    if state.custom:
        if not str(state.homepage or "").strip():
            missing.append("官网/控制台地址")
        if not str(state.base_url or "").strip():
            missing.append("真实请求地址 base_url")
        if not str(state.model or "").strip():
            missing.append("模型名")
    elif not _selected_connect_model(state):
        missing.append("模型名")
    if _connect_form_needs_key(state) and not str(state.api_key or "").strip():
        missing.append("API key")
    return missing


def _field_value(value: str) -> str:
    text = str(value or "").strip()
    return text if text else "未填写"


def _selected_connect_model(state) -> str:
    if str(state.model or "").strip():
        return str(state.model).strip()
    preset = state.provider_catalog.get(state.selected_provider) or {}
    models = [str(item).strip() for item in (preset.get("models") or []) if str(item).strip()]
    return models[0] if models else ""


async def _collect_connect_form_fields(console, state):
    fields = _connect_form_fields(state)
    index = 0
    while index < len(fields):
        field = fields[index]
        try:
            state = await _collect_one_connect_field(console, state, field)
            fields = _connect_form_fields(state)
            index += 1
        except _ConnectWizardFieldBack:
            if index <= 0:
                raise _ConnectWizardRestartProvider()
            index -= 1
        except _ConnectWizardFieldRetry as retry:
            print(f"\n{retry}")
        except ValueError as exc:
            print(f"\n{exc}")
    return state


def _connect_form_fields(state) -> list[str]:
    fields: list[str] = []
    if state.custom:
        fields.extend(["homepage", "base_url", "model"])
    else:
        fields.append("model")
    if _connect_form_needs_key(state):
        fields.append("key")
    return fields


async def _collect_one_connect_field(console, state, field: str):
    if field == "homepage":
        value = await _read_connect_text_field(
            console,
            "官网/控制台地址",
            aliases=("homepage", "home"),
            help_text="示例：https://proxy.example.com；输入 back 返回上一步，provider deepseek 可重新选 Provider。",
        )
        _validate_connect_url(value, "官网/控制台地址")
        state, _ = apply_connect_wizard_input(state, f"homepage {value}")
        return state
    if field == "base_url":
        value = await _read_connect_text_field(
            console,
            "真实请求地址 base_url",
            aliases=("base-url", "base_url", "url"),
            help_text="示例：https://api.proxy.example.com/v1；模型请求会走这个地址。",
        )
        _validate_connect_url(value, "真实请求地址")
        state, _ = apply_connect_wizard_input(state, f"base-url {value}")
        return state
    if field == "model":
        model = await _read_connect_model_field(console, state)
        if model:
            state, _ = apply_connect_wizard_input(state, f"model {model}")
        return state
    if field == "key":
        key = await _read_connect_secret_field(console)
        state, _ = apply_connect_wizard_input(state, f"key {key}")
        return state
    return state


async def _read_connect_model_field(console, state) -> str:
    from runtime.config.connect_wizard import ConnectWizardCommandItem

    preset = state.provider_catalog.get(state.selected_provider) or {}
    models = [str(item).strip() for item in (preset.get("models") or []) if str(item).strip()]
    if not models:
        return await _read_connect_text_field(console, "模型名", aliases=("model", "models"))

    choices = [
        ConnectWizardCommandItem(f"model {model}", f"使用模型：{model}", state.selected_provider)
        for model in models[:8]
    ]
    choices.append(ConnectWizardCommandItem("custom_model", "手动输入模型名", "用于未列出的模型"))
    choice_reader = getattr(console, "read_choice_line", None)
    if callable(choice_reader):
        value = await choice_reader(
            "\n选择模型> ",
            choices,
            bottom_toolbar="↑↓ 选择模型，Enter 确认；选手动输入可填写未列出的模型",
            reserve_space_for_menu=10,
        )
        value = str(value or "").strip()
        if value.lower() in {"q", "quit", "exit", "cancel"}:
            raise _ConnectWizardCancelled()
        if value.lower() in {"back", "b", "return"}:
            raise _ConnectWizardFieldBack()
        if _is_provider_switch_command(value):
            raise _ConnectWizardFieldRetry("请先输入 back 返回 Provider 选择，再切换 Provider。")
        if value.lower() == "custom_model":
            return await _read_connect_text_field(
                console,
                "模型名",
                aliases=("model", "models"),
                help_text="示例：qwen-max 或 gpt-5.2；输入 back 返回上一步。",
            )
        return _strip_field_alias(value, ("model", "models")) or models[0]
    return await _read_connect_text_field(
        console,
        "模型名",
        default=models[0],
        aliases=("model", "models"),
        help_text="回车使用默认模型；输入 back 返回上一步。",
    )


async def _read_connect_save_decision(console, state) -> str:
    from runtime.config.connect_wizard import ConnectWizardCommandItem

    ref = _connect_state_primary_ref(state)
    choices = [
        ConnectWizardCommandItem("save_default", "保存并设为默认模型", ref or "适合立刻开始使用"),
        ConnectWizardCommandItem("save_only", "仅保存 Provider", "稍后可在 /models 调音台里选择"),
        ConnectWizardCommandItem("cancel", "取消保存", "不写入配置"),
    ]
    choice_reader = getattr(console, "read_choice_line", None)
    if callable(choice_reader):
        value = await choice_reader(
            "\n淇濆瓨鏂瑰紡> ",
            choices,
            bottom_toolbar="Up/Down choose save mode, Enter confirm; recommended: save and set default",
            reserve_space_for_menu=8,
        )
    else:
        value = await _read_connect_text_field(console, "淇濆瓨鏂瑰紡锛歞efault/save_only/cancel", default="save_default")
    value = str(value or "").strip().lower()
    if value in {"default", "yes", "y", "set_default"}:
        return "save_default"
    if value in {"save", "connect", "only", "no", "n"}:
        return "save_only"
    return value or "save_default"


async def _read_connect_text_field(
    console,
    label: str,
    *,
    default: str = "",
    aliases: tuple[str, ...] = (),
    help_text: str = "",
) -> str:
    prompt = f"\n{label}"
    if default:
        prompt += f"（回车使用 {default}）"
    prompt += "> "
    if help_text:
        print(f"{help_text}")
    reader = getattr(console, "read_runtime_line", None)
    if callable(reader):
        print(prompt, end="", flush=True)
        value = await reader()
    else:
        value = await console.read_line(prompt)
    value = str(value or "").strip()
    if value.lower() in {"q", "quit", "exit", "/exit", "鍙栨秷"}:
        raise _ConnectWizardCancelled()
    if value.lower() in {"back", "b", "return"}:
        raise _ConnectWizardFieldBack()
    if _is_provider_switch_command(value):
        raise _ConnectWizardRestartProvider()
    value = _strip_field_alias(value, aliases)
    if value.lower() in {"back", "b", "return"}:
        raise _ConnectWizardFieldBack()
    return value or default


async def _read_connect_secret_field(console) -> str:
    reader = getattr(console, "read_secret_line", None)
    if callable(reader):
        value = await reader("\nAPI key（输入会隐藏；只保存到用户级 auth.json）： ")
    else:
        value = await _read_connect_text_field(console, "API key", aliases=("key", "api-key", "apikey"))
    value = _strip_field_alias(str(value or "").strip(), ("key", "api-key", "apikey"))
    if value.lower() in {"q", "quit", "exit", "/exit", "鍙栨秷"}:
        raise _ConnectWizardCancelled()
    if value.lower() in {"back", "b", "return"}:
        raise _ConnectWizardFieldBack()
    if _is_provider_switch_command(value):
        raise _ConnectWizardRestartProvider()
    return value


def _strip_field_alias(value: str, aliases: tuple[str, ...]) -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    for alias in aliases:
        prefix = f"{alias.lower()} "
        if lowered.startswith(prefix):
            return raw[len(prefix):].strip()
    return raw


def _validate_connect_url(value: str, label: str) -> None:
    text = str(value or "").strip().lower()
    if not text.startswith(("https://", "http://")):
        raise _ConnectWizardFieldRetry(f"{label} 看起来不像 URL，请重新输入。示例：https://api.example.com/v1")


def _is_provider_switch_command(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith("provider ") or text.startswith("custom ")


def _connect_form_needs_key(state) -> bool:
    preset = state.provider_catalog.get(state.selected_provider) or {}
    return not bool(preset.get("local"))


def _connect_state_primary_ref(state) -> str:
    model = str(state.model or "").strip()
    if not model:
        preset = state.provider_catalog.get(state.selected_provider) or {}
        model = str((preset.get("models") or [""])[0]).strip()
    return f"{state.selected_provider}/{model}" if state.selected_provider and model else ""


def _connect_request_primary_ref(request) -> str:
    model = request.models[0] if request.models else ""
    return f"{request.normalized_provider}/{model}" if model else ""


def _clear_connect_wizard_caches() -> None:
    try:
        from catalog_system.model_catalog import clear_model_catalog_cache
        from runtime.commands.completion import clear_completion_caches

        clear_model_catalog_cache()
        clear_completion_caches()
    except Exception:
        pass


async def _read_model_tuner_line(console, state) -> str:
    choices = model_tuner_command_items(state)
    choice_reader = getattr(console, "read_choice_line", None)
    if callable(choice_reader):
        try:
            return await choice_reader(
                "\n模型调音台> ",
                choices,
                bottom_toolbar="↑↓ 选择，Enter 确认，q 退出",
                reserve_space_for_menu=12,
            )
        except TypeError:
            pass
    print("\n模型调音台> ", end="", flush=True)
    reader = getattr(console, "read_runtime_line", None)
    if callable(reader):
        return await reader()
    return await console.read_line("")


def _model_tuner_role_label(role: str) -> str:
    from runtime.config.model_config import model_role_label

    return model_role_label(role)


async def _handle_resume_command(
    user_input: str,
    *,
    session_store,
    current_session_id: str | None,
    model_registry,
    runtime_settings,
) -> SlashCommandResult:
    if session_store is None:
        print("当前运行环境没有启用会话存储。")
        return SlashCommandResult(handled=True)

    facade = _history_facade_for_session_store(session_store)
    session_view = facade.as_session_store()
    selector = user_input.split(maxsplit=1)[1].strip() if len(user_input.split(maxsplit=1)) > 1 else ""
    include_context_summary = False
    if selector.lower().startswith(("with-context", "context")):
        include_context_summary = True
        selector = selector.split(maxsplit=1)[1].strip() if len(selector.split(maxsplit=1)) > 1 else "last"
    if not selector or selector.lower() == "list":
        print(render_session_list(session_view))
        return SlashCommandResult(handled=True)

    try:
        session_id = facade.resolve(selector)
    except ValueError as exc:
        print(f"恢复失败：{exc}")
        print(render_session_list(session_view))
        return SlashCommandResult(handled=True)

    if not session_id:
        print(f"没有找到会话：{selector}")
        print(render_session_list(session_view))
        return SlashCommandResult(handled=True)

    return await _resume_session_result(
        session_id,
        session_store=session_view,
        current_session_id=current_session_id,
        model_registry=model_registry,
        runtime_settings=runtime_settings,
        include_context_summary=include_context_summary,
    )


async def _handle_history_command(
    user_input: str,
    *,
    console,
    session_store,
    current_session_id: str | None,
    workspace_context,
    model_registry,
    runtime_settings,
) -> SlashCommandResult:
    if session_store is None:
        print("当前运行环境没有启用会话存储。")
        return SlashCommandResult(handled=True)

    facade = _history_facade_for_session_store(session_store)
    session_view = facade.as_session_store()
    selector = user_input.split(maxsplit=1)[1].strip() if len(user_input.split(maxsplit=1)) > 1 else ""
    if selector.lower().startswith("search"):
        query = selector.split(maxsplit=1)[1].strip() if len(selector.split(maxsplit=1)) > 1 else ""
        return _handle_history_search_command(
            query,
            facade=facade,
            workspace_context=workspace_context,
            current_session_id=current_session_id,
        )
    if selector.lower().startswith("export"):
        target = selector.split(maxsplit=1)[1].strip() if len(selector.split(maxsplit=1)) > 1 else ""
        return await _handle_history_export_command(
            target,
            console=console,
            facade=facade,
            workspace_context=workspace_context,
            current_session_id=current_session_id,
        )
    if selector.lower().startswith(("remove", "delete", "rm")):
        target = selector.split(maxsplit=1)[1].strip() if len(selector.split(maxsplit=1)) > 1 else ""
        return await _handle_history_remove_command(
            target,
            console=console,
            facade=facade,
            workspace_context=workspace_context,
            current_session_id=current_session_id,
        )
    include_context_summary = False
    if selector.lower().startswith(("with-context", "context")):
        include_context_summary = True
        selector = selector.split(maxsplit=1)[1].strip() if len(selector.split(maxsplit=1)) > 1 else "last"
    if selector:
        try:
            session_id = facade.resolve(selector)
        except ValueError as exc:
            print(f"历史恢复失败：{exc}")
            print(render_history_panel(workspace_root=workspace_context.workspace_root, items=facade.list_items(limit=12)))
            return SlashCommandResult(handled=True)
        if not session_id:
            print(f"没有找到历史会话：{selector}")
            print(render_history_panel(workspace_root=workspace_context.workspace_root, items=facade.list_items(limit=12)))
            return SlashCommandResult(handled=True)
        return await _resume_session_result(
            session_id,
            session_store=session_view,
            current_session_id=current_session_id,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
            include_context_summary=include_context_summary,
        )

    items = facade.list_items(limit=20)
    if getattr(console, "interactive", False):
        selection = await run_history_browser(
            console=console,
            facade=facade,
            workspace_root=workspace_context.workspace_root,
        )
        if selection.action != "resume" or not selection.history_id:
            print("已退出历史面板。")
            return SlashCommandResult(handled=True, session_id=current_session_id)
        return await _resume_session_result(
            selection.history_id,
            session_store=session_view,
            current_session_id=current_session_id,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
        )

    preview = facade.preview(items[0].history_id) if items else None
    print(
        render_history_panel(
            workspace_root=workspace_context.workspace_root,
            items=items,
            selected_index=0,
            preview=preview,
        )
    )
    return SlashCommandResult(handled=True, session_id=current_session_id)


def _handle_history_search_command(
    query: str,
    *,
    facade: HistoryFacade,
    workspace_context,
    current_session_id: str | None,
) -> SlashCommandResult:
    if not query:
        print(render_history_panel(workspace_root=workspace_context.workspace_root, items=facade.list_items(limit=12), message="请输入 /history search <关键词>。"))
        return SlashCommandResult(handled=True, session_id=current_session_id)
    items = facade.search(query, limit=20)
    preview = facade.preview(items[0].history_id) if items else None
    message = f"搜索：{query}，命中 {len(items)} 个会话。" if items else f"搜索：{query}，没有命中历史会话。"
    print(
        render_history_panel(
            workspace_root=workspace_context.workspace_root,
            items=items,
            selected_index=0,
            preview=preview,
            message=message,
            highlight_terms=query.split(),
            footer="输入 /history <会话ID前缀> 恢复，/history export <会话ID前缀> 导出",
        )
    )
    return SlashCommandResult(handled=True, session_id=current_session_id)


async def _handle_history_export_command(
    selector: str,
    *,
    console,
    facade: HistoryFacade,
    workspace_context,
    current_session_id: str | None,
) -> SlashCommandResult:
    if not selector and getattr(console, "interactive", False):
        selection = await run_history_browser(
            console=console,
            facade=facade,
            workspace_root=workspace_context.workspace_root,
            purpose="export",
        )
        if selection.action != "resume" or not selection.history_id:
            print("已取消导出历史会话。")
            return SlashCommandResult(handled=True, session_id=current_session_id)
        selector = selection.history_id
    selector = selector or "last"
    try:
        export_path = facade.export(selector)
    except ValueError as exc:
        print(f"导出失败：{exc}")
        return SlashCommandResult(handled=True, session_id=current_session_id)
    print(f"已导出历史会话：{export_path}")
    return SlashCommandResult(handled=True, session_id=current_session_id)


async def _handle_history_remove_command(
    selector: str,
    *,
    console,
    facade: HistoryFacade,
    workspace_context,
    current_session_id: str | None,
) -> SlashCommandResult:
    items = facade.list_items(limit=20)
    if not items:
        print(render_history_panel(workspace_root=workspace_context.workspace_root, items=[], message="暂无可删除历史。"))
        return SlashCommandResult(handled=True, session_id=current_session_id)

    session_id = None
    if selector:
        try:
            session_id = facade.resolve(selector)
        except ValueError as exc:
            print(f"删除失败：{exc}")
            print(render_history_panel(workspace_root=workspace_context.workspace_root, items=items))
            return SlashCommandResult(handled=True, session_id=current_session_id)
    elif getattr(console, "interactive", False):
        selection = await run_history_browser(
            console=console,
            facade=facade,
            workspace_root=workspace_context.workspace_root,
            purpose="remove",
        )
        if selection.action == "resume" and selection.history_id:
            session_id = selection.history_id
    else:
        print(render_history_panel(workspace_root=workspace_context.workspace_root, items=items, message="请使用 /history remove <会话ID前缀> 删除。"))
        return SlashCommandResult(handled=True, session_id=current_session_id)

    if not session_id:
        print("已取消删除历史会话。")
        return SlashCommandResult(handled=True, session_id=current_session_id)

    preview = facade.preview(session_id)
    if not await _confirm_history_delete(console, preview):
        print("已取消删除历史会话。")
        return SlashCommandResult(handled=True, session_id=current_session_id)

    try:
        deleted = facade.delete(session_id)
    except ValueError as exc:
        print(f"删除失败：{exc}")
        return SlashCommandResult(handled=True, session_id=current_session_id)
    label = deleted.title or preview.last_user or session_id
    if deleted.deleted:
        print(f"已删除历史会话：{label} ({session_id})")
    else:
        print(f"历史会话文件已不存在：{label} ({session_id})")
    next_session_id = None if current_session_id == session_id else current_session_id
    return SlashCommandResult(handled=True, session_id=next_session_id or "")


async def _confirm_history_delete(console, preview) -> bool:
    title = preview.last_user or preview.first_user or preview.session_id
    choices = [
        ConsoleChoice("no", "取消", f"保留：{title}"),
        ConsoleChoice("yes", "确认删除", f"删除：{title}"),
    ]
    choice_reader = getattr(console, "read_choice_line", None)
    if callable(choice_reader):
        try:
            value = await choice_reader(
                "\n确认删除历史> ",
                choices,
                bottom_toolbar="↑↓ 选择，Enter 确认；默认取消，删除后会移除本地 JSONL 会话文件",
                reserve_space_for_menu=6,
            )
        except TypeError:
            value = await choice_reader("\n确认删除历史> ", choices)
    else:
        print(f"确认删除历史会话：{title}")
        reader = getattr(console, "read_line", None)
        value = await reader("输入 yes 确认删除，其它输入取消> ") if callable(reader) else "no"
    return str(value or "").strip().lower() in {"yes", "y", "确认", "确认删除"}


async def _resume_session_result(
    session_id: str,
    *,
    session_store,
    current_session_id: str | None,
    model_registry,
    runtime_settings,
    include_context_summary: bool = False,
) -> SlashCommandResult:
    compacted = await compact_messages_tiered(
        session_store.load_messages(session_id),
        tail_messages=6,
        model_registry=model_registry,
        runtime_settings=runtime_settings,
    )
    context_summary_attached = False
    if include_context_summary:
        context_loader = getattr(session_store, "load_context_summary", None)
        context_summary = context_loader(session_id) if callable(context_loader) else ""
        context_summary = str(context_summary or "").strip()
        if context_summary:
            context_summary_attached = True
            summary = "\n\n".join(
                part
                for part in [
                    compacted.summary,
                    "已附加历史 Context 摘要。它是背景，不是本轮新任务。",
                    context_summary,
                ]
                if str(part or "").strip()
            )
            compacted = replace(compacted, summary=summary)
    turns = compacted.recent_turns
    if not turns:
        print(f"会话没有可恢复消息：{session_id}")
        return SlashCommandResult(handled=True, session_id=current_session_id)

    print(render_resume_preview(session_store, session_id, compacted_context=compacted))
    if context_summary_attached:
        print("已附加历史 Context 摘要。")
    return SlashCommandResult(
        handled=True,
        resumed_recent_turns=turns,
        resumed_session_summary=compacted.summary,
        session_id=session_id,
    )


async def _handle_plan_command(user_input: str, *, model_registry, runtime_settings, console) -> None:
    plan_input = user_input.removeprefix("/plan").strip()
    if not plan_input:
        print("请在 /plan 后面输入要规划的问题。")
        return

    print("\n正在生成规划预览，不会执行任务...")
    hooks = create_token_logger_hooks()
    try:
        refiner_model_id = (
            runtime_settings.select_model_id(model_registry, "query_refiner")
            if runtime_settings.query_refiner_enabled
            else None
        )
        planner_model_id = runtime_settings.select_model_id(model_registry, "orchestrator")
        session = RuntimeCommandSession(console, timeout_seconds=turn_timeout_seconds())

        async def _preview_work():
            refined, plan = await preview_plan(
                plan_input,
                refiner_model=model_registry.get_model(refiner_model_id) if refiner_model_id else None,
                planner_model=model_registry.get_model(planner_model_id),
                hooks=hooks,
                refiner_enabled=runtime_settings.query_refiner_enabled,
            )
            return format_plan_preview(refined, plan)

        turn_result = await session.run(_preview_work)
        print(turn_result.final_output)
    except Exception as exc:
        print(format_turn_error(exc))
    finally:
        hooks.print_summary()
