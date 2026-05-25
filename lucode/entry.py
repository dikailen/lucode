from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path


APP_HOME = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = "0.1.0"


def configure_stdio_encoding() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def fast_dispatch(argv: list[str]) -> int | None:
    if not argv or "--" in argv[:1]:
        return None
    if argv[0] in {"--version", "-v", "version"}:
        print(f"lucode {read_package_version()}")
        return 0
    return None


def read_package_version() -> str:
    package_path = APP_HOME / "package.json"
    if not package_path.exists():
        return DEFAULT_VERSION
    try:
        data = json.loads(package_path.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_VERSION
    return str(data.get("version") or DEFAULT_VERSION)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lucode",
        description="Lucode terminal coding agent.",
    )
    parser.add_argument("--workspace", help="指定工作区目录，默认使用当前目录向上查找 .lucode")
    parser.add_argument("--no-logo", action="store_true", help="启动交互界面时隐藏欢迎页 logo")
    parser.add_argument("--verbose", action="store_true", help="显示详细运行日志")
    parser.add_argument("--version", "-v", action="store_true", help="显示版本并退出")
    parser.add_argument("--startup-profile", action="store_true", help="显示启动阶段耗时")
    subparsers = parser.add_subparsers(dest="command")

    chat = subparsers.add_parser("chat", help="启动交互式终端代理")
    chat.set_defaults(command="chat")

    run = subparsers.add_parser("run", help="非交互执行一次任务")
    run.add_argument("prompt", nargs=argparse.REMAINDER, help="要执行的任务文本")
    run.set_defaults(command="run")

    init = subparsers.add_parser("init", help="在当前目录创建 .lucode 工作区")
    init.set_defaults(command="init")

    doctor = subparsers.add_parser("doctor", help="检查 Python、Git、模型配置、MCP 和权限状态")
    doctor.set_defaults(command="doctor")

    config = subparsers.add_parser("config", help="查看当前配置")
    config.set_defaults(command="config")

    model = subparsers.add_parser("model", help="查看模型优先级和可用模型")
    model.add_argument("--available", action="store_true", help="只显示当前可运行模型")
    model.set_defaults(command="model")

    mcp = subparsers.add_parser("mcp", help="查看 MCP 注册和信任状态")
    mcp.add_argument("--all", action="store_true", help="显示 core/user/workspace 全部 MCP")
    mcp.set_defaults(command="mcp")

    session = subparsers.add_parser("session", help="查看会话和回滚说明")
    session.set_defaults(command="session")

    connect = subparsers.add_parser("connect", help="连接 Provider，保存 API key 到用户级 auth.json")
    connect.add_argument("provider", nargs="?", help="Provider ID，例如 deepseek、openrouter 或 my_proxy")
    connect.add_argument("--api-key", help="Provider API key；本地 Provider 可省略")
    connect.add_argument("--homepage", help="官网或控制台地址，只用于展示")
    connect.add_argument("--base-url", help="真实模型请求地址")
    connect.add_argument("--display-name", help="显示名称")
    connect.add_argument("--model", action="append", default=[], help="添加一个模型名，可重复传入")
    connect.add_argument("--models", help="逗号分隔的模型名列表")
    connect.add_argument("--custom", action="store_true", help="按自定义 OpenAI-compatible 中转保存")
    connect.add_argument("--remove", "--delete", action="store_true", help="删除 Provider 配置、API key 和失效模型引用")
    connect.set_defaults(command="connect")

    models = subparsers.add_parser("models", help="查看或选择模型优先级")
    model_subparsers = models.add_subparsers(dest="models_action")
    model_subparsers.add_parser("list", help="按 Provider 分组查看模型源")
    model_subparsers.add_parser("available", help="只显示当前可运行模型")
    model_select = model_subparsers.add_parser("select", help="选择主模型和 fallback")
    model_select.add_argument("primary", help="主模型引用，例如 deepseek/deepseek-chat")
    model_select.add_argument("fallback", nargs="*", help="Fallback 模型引用")
    model_role = model_subparsers.add_parser("role", help="配置四脑角色模型优先级（兼容命令）")
    model_role.add_argument("role", help="query_refiner、orchestrator、executor 或 final_synthesizer")
    model_role.add_argument("refs", nargs="+", help="模型引用列表，例如 deepseek/deepseek-chat")
    model_subparsers.add_parser("roles", help="查看四脑角色模型配置")
    model_brain = model_subparsers.add_parser("brain", help="切换前置优化脑、主脑、执行脑或汇总脑模型")
    model_brain.add_argument("role", help="前置优化、主脑、执行、汇总，或 query_refiner/orchestrator/executor/final_synthesizer")
    model_brain.add_argument("refs", nargs="*", help="模型引用列表，例如 deepseek/deepseek-chat；role 为 reset 时可省略")
    models.set_defaults(command="models")

    auth = subparsers.add_parser("auth", help="管理用户级 Provider 凭据")
    auth_subparsers = auth.add_subparsers(dest="auth_action")
    auth_subparsers.add_parser("list", help="列出已保存凭据的 Provider")
    auth_login = auth_subparsers.add_parser("login", help="保存 Provider API key")
    auth_login.add_argument("provider", help="Provider ID")
    auth_login.add_argument("--api-key", required=True, help="Provider API key")
    auth_logout = auth_subparsers.add_parser("logout", help="删除 Provider API key")
    auth_logout.add_argument("provider", help="Provider ID")
    auth.set_defaults(command="auth")

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio_encoding()
    argv = list(sys.argv[1:] if argv is None else argv)
    fast_result = fast_dispatch(argv)
    if fast_result is not None:
        return fast_result
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"lucode {read_package_version()}")
        return 0
    if bool(getattr(args, "startup_profile", False)):
        os.environ["LUCODE_STARTUP_PROFILE"] = "1"
    from runtime.startup.profiler import StartupProfiler

    profiler = StartupProfiler()
    profiler.mark("parsed argv")
    if args.command in {None, "chat"}:
        context = _workspace_context(args)
        profiler.mark("resolved workspace")
        _export_context(context, args)
        from main import main as interactive_main

        profiler.mark("imported interactive runtime")
        profiler.print()
        asyncio.run(interactive_main())
        return 0
    context = _workspace_context(args)
    profiler.mark("resolved workspace")
    _export_context(context, args)

    if args.command == "run":
        profiler.mark("dispatch run")
        profiler.print()
        return asyncio.run(_handle_run(args, context))
    if args.command == "init":
        profiler.mark("dispatch init")
        profiler.print()
        return _handle_init(context)
    if args.command == "doctor":
        profiler.mark("dispatch doctor")
        profiler.print()
        return _handle_doctor(context)
    if args.command == "config":
        profiler.mark("dispatch config")
        profiler.print()
        return _handle_readonly("/config", context)
    if args.command == "model":
        profiler.mark("dispatch model")
        profiler.print()
        return _handle_readonly("/model available" if args.available else "/model", context)
    if args.command == "mcp":
        profiler.mark("dispatch mcp")
        profiler.print()
        return _handle_readonly("/mcp_all" if args.all else "/mcp", context)
    if args.command == "session":
        profiler.mark("dispatch session")
        profiler.print()
        return _handle_session(context)
    if args.command == "connect":
        profiler.mark("dispatch connect")
        profiler.print()
        return _handle_connect(args, context)
    if args.command == "models":
        profiler.mark("dispatch models")
        profiler.print()
        return _handle_models(args, context)
    if args.command == "auth":
        profiler.mark("dispatch auth")
        profiler.print()
        return _handle_auth(args, context)
    parser.print_help()
    return 2


def _workspace_context(args):
    from runtime.config.workspace import discover_workspace_context

    explicit_workspace = bool(getattr(args, "workspace", None))
    env_workspace = os.environ.get("LUCODE_WORKSPACE_ROOT")
    if explicit_workspace:
        cwd = Path(args.workspace).resolve()
    elif env_workspace:
        cwd = Path(env_workspace).expanduser().resolve()
        explicit_workspace = True
    else:
        cwd = Path.cwd()
    return discover_workspace_context(APP_HOME, cwd=cwd, explicit_workspace=explicit_workspace)


def _export_context(context, args=None) -> None:
    os.environ["LUCODE_APP_HOME"] = str(context.app_home)
    os.environ["LUCODE_USER_HOME"] = str(context.user_home)
    os.environ["LUCODE_WORKSPACE_ROOT"] = str(context.workspace_root)
    if bool(getattr(args, "no_logo", False)):
        os.environ["LUCODE_NO_LOGO"] = "1"
    if bool(getattr(args, "verbose", False)):
        os.environ["LUCODE_VERBOSE_RUNTIME"] = "1"


async def _handle_run(args, context) -> int:
    from runtime.kernel import KernelFacade

    prompt = " ".join(args.prompt or []).strip()
    if not prompt:
        print("请在 lucode run 后输入任务，例如：lucode run \"解释项目结构\"")
        return 2
    response = await KernelFacade(context).run_once(prompt, show_plan=True)
    if response.final_output and not getattr(response, "output_already_printed", False):
        print(response.final_output)
    response.print_summary()
    return 0


def _handle_init(context) -> int:
    config_dir = context.workspace_root / ".lucode"
    created = []
    for path in [
        config_dir,
        config_dir / "skills",
        config_dir / "mcp",
        config_dir / "memory",
        config_dir / "sessions",
    ]:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    config_path = config_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text('mode = "solo"\nprivacy = "local_first"\n', encoding="utf-8")
        created.append(config_path)
    permissions_path = config_dir / "permissions.toml"
    if not permissions_path.exists():
        permissions_path.write_text(
            "\n".join(
                [
                    "[read]",
                    'default = "allow"',
                    'deny = [".env", "**/*.pem", "**/*secret*", "**/*token*"]',
                    "",
                    "[write]",
                    'default = "ask"',
                    'deny = [".git/**", ".agent_quarantine/**", ".lucode/auth.json"]',
                    "",
                    "[shell]",
                    'default = "ask"',
                    'deny = ["git reset --hard", "git clean", "rm -rf", "npm publish"]',
                    "",
                    "[mcp.workspace]",
                    'default = "ask"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        created.append(permissions_path)
    print(f"Lucode 工作区已初始化：{config_dir}")
    if created:
        print("创建：")
        for path in created:
            print(f"- {path}")
    else:
        print("没有新建文件，工作区已存在。")
    return 0


def _handle_doctor(context) -> int:
    from catalog_system.model_catalog import load_model_catalog
    from runtime.config.model_config import auth_path, load_provider_catalog, project_config_path
    from runtime.providers.registry import ProviderRegistry
    from runtime.tools.registry import build_tool_registry

    catalog = load_model_catalog()
    configured = [item for item in catalog.get("models", []) if item.get("configured")]
    provider_catalog = load_provider_catalog()
    provider_registry = ProviderRegistry()
    registry = build_tool_registry(workspace_context=context)
    lines = [
        "Lucode doctor",
        f"APP_HOME：{context.app_home}",
        f"USER_HOME：{context.user_home}",
        f"WORKSPACE_ROOT：{context.workspace_root}",
        f".lucode：{'已发现' if context.has_project_config else '未初始化'}",
        f"Python：{sys.executable}",
        f"Python 来源：{_python_source_label()}",
        f"Python 环境：{_python_environment_label()}",
        f"OpenAI Agents SDK：{_agents_sdk_status()}",
        f"Git：{shutil.which('git') or '未找到'}",
        f"ripgrep：{shutil.which('rg') or '未找到，可继续使用 PowerShell 搜索'}",
        f"Provider 预设：{len(provider_catalog)} 个",
        f"Provider Registry：OpenAI-compatible 已启用，SDK cache {provider_registry.cache_size()} 个",
        "Message Transformer：已启用空内容过滤、tool id 清洗、tool result 顺序检查",
        f"已配置模型：{len(configured)} 个",
        f"auth.json：{auth_path(context.user_home)}",
        f"项目配置：{project_config_path(context.workspace_root)}",
        f"工具注册表：{len(registry.servers)} 个 MCP 记录",
    ]
    print("\n".join(lines))
    return 0


def _python_source_label() -> str:
    configured = os.environ.get("LUCODE_PYTHON")
    if configured:
        try:
            if Path(configured).resolve() == Path(sys.executable).resolve():
                return "LUCODE_PYTHON"
        except Exception:
            pass
        return f"当前进程（LUCODE_PYTHON={configured}）"
    if os.environ.get("PYTHON"):
        return "PYTHON"
    return "当前进程"


def _python_environment_label() -> str:
    prefix = Path(sys.prefix).resolve()
    parts = list(prefix.parts)
    lowered = [part.lower() for part in parts]
    if "envs" in lowered:
        index = lowered.index("envs")
        if index + 1 < len(parts):
            return f"conda: {parts[index + 1]}"
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    if conda_env:
        return f"conda: {conda_env}"
    if Path(getattr(sys, "base_prefix", sys.prefix)).resolve() != prefix:
        return f"venv: {prefix.name}"
    return "system/base"


def _agents_sdk_status() -> str:
    try:
        spec = importlib.util.find_spec("agents")
    except Exception:
        spec = None
    if spec is None:
        return f"缺失（请运行：\"{sys.executable}\" -m pip install openai-agents）"
    try:
        version = importlib.metadata.version("openai-agents")
    except importlib.metadata.PackageNotFoundError:
        version = "版本未知"
    return f"已安装（openai-agents {version}）"


def _handle_readonly(command: str, context) -> int:
    from runtime.config.cli import render_readonly_command
    from runtime.config.settings import RuntimeSettings

    print(render_readonly_command(command, RuntimeSettings.from_env(), context))
    return 0


def _handle_session(context) -> int:
    from runtime.history import HistoryFacade
    from runtime.sessions import render_session_list

    print(render_session_list(HistoryFacade(context.workspace_root).as_session_store(), limit=10))
    return 0


def _handle_connect(args, context) -> int:
    from runtime.config.cli import render_readonly_command
    from runtime.config.connect_command import (
        apply_provider_connect_request,
        provider_requires_api_key,
        redact_connect_secret,
        render_provider_connect_success,
        request_from_cli_args,
    )
    from runtime.config.settings import RuntimeSettings

    if not args.provider:
        print(render_readonly_command("/connect", RuntimeSettings.from_env(), context))
        return 0

    if bool(getattr(args, "remove", False)):
        from runtime.config.model_config import remove_provider_config

        try:
            result = remove_provider_config(args.provider, workspace_root=context.workspace_root, user_home=context.user_home)
            try:
                from catalog_system.model_catalog import clear_model_catalog_cache
                from runtime.commands.completion import clear_completion_caches

                clear_model_catalog_cache()
                clear_completion_caches()
            except Exception:
                pass
        except Exception as exc:
            print(f"删除 Provider 失败：{exc}")
            return 1
        if not result["provider_removed"] and not result["auth_removed"]:
            print(f"没有找到 Provider：{result['provider_id']}，未改动配置。")
            return 1
        print(f"已删除 Provider：{result['provider_id']}")
        print(f"项目配置：{'已删除' if result['provider_removed'] else '未找到'}")
        print(f"API key：{'已删除' if result['auth_removed'] else '未找到'}")
        print(f"已清理失效模型引用：{result.get('removed_model_refs', 0)} 个")
        print("失效脑位会在下次启动或重新加载时回到默认可用模型顺序。")
        return 0

    try:
        request = request_from_cli_args(args)
    except Exception as exc:
        print(f"连接失败：{exc}")
        return 1
    try:
        missing_builtin_key = not args.custom and provider_requires_api_key(request) and not request.api_key
    except Exception as exc:
        print(f"连接失败：{redact_connect_secret(str(exc), request)}")
        return 1
    if missing_builtin_key:
        print(render_readonly_command(f"/connect {args.provider}", RuntimeSettings.from_env(), context))
        print("")
        print("还缺 API key，未写入项目配置。")
        print(f"保存命令：lucode connect {request.normalized_provider} --api-key <key>")
        return 0
    try:
        result = apply_provider_connect_request(
            request,
            workspace_root=context.workspace_root,
            user_home=context.user_home,
        )
    except Exception as exc:
        print(f"连接失败：{redact_connect_secret(str(exc), request)}")
        return 1
    print(render_provider_connect_success(result, api_key_provided=bool(request.api_key)))
    return 0


def _handle_models(args, context) -> int:
    from runtime.config.cli import render_readonly_command
    from runtime.config.model_config import reset_role_model_priorities, select_model_priority, select_role_model_priority
    from runtime.config.settings import RuntimeSettings

    if getattr(args, "models_action", None) == "list":
        print(render_readonly_command("/models list", RuntimeSettings.from_env(), context))
        return 0
    if getattr(args, "models_action", None) == "available":
        print(render_readonly_command("/models available", RuntimeSettings.from_env(), context))
        return 0
    if getattr(args, "models_action", None) == "roles":
        print(render_readonly_command("/models roles", RuntimeSettings.from_env(), context))
        return 0
    if getattr(args, "models_action", None) == "select":
        try:
            select_model_priority(
                workspace_root=context.workspace_root,
                primary_ref=args.primary,
                fallback_refs=args.fallback,
            )
        except Exception as exc:
            print(f"模型选择失败：{exc}")
            return 1
        print(f"已选择主模型：{args.primary}")
        print(f"Fallback：{', '.join(args.fallback) if args.fallback else '无'}")
        return 0
    if getattr(args, "models_action", None) in {"role", "brain"}:
        if getattr(args, "models_action", None) == "brain" and str(args.role).strip().lower() == "reset":
            reset_role_model_priorities(workspace_root=context.workspace_root)
            print("已重置多脑模型覆盖配置。")
            print("已删除项目配置中的 [roles]。")
            return 0
        if not args.refs:
            print("模型角色配置失败：请提供至少一个模型引用，或使用 lucode models brain reset。")
            return 1
        try:
            select_role_model_priority(
                workspace_root=context.workspace_root,
                role=args.role,
                refs=args.refs,
            )
        except Exception as exc:
            print(f"模型角色配置失败：{exc}")
            return 1
        print(f"已配置角色模型：{args.role}")
        print(f"模型顺序：{', '.join(args.refs)}")
        return 0

    print(render_readonly_command("/models", RuntimeSettings.from_env(), context))
    return 0


def _handle_auth(args, context) -> int:
    from runtime.config.model_config import connect_provider, load_auth, remove_provider_auth

    action = getattr(args, "auth_action", None) or "list"
    if action == "list":
        auth = load_auth(user_home=context.user_home)
        providers = sorted((auth.get("providers") or {}).keys())
        print("已保存凭据的 Provider")
        if not providers:
            print("- 无")
        for provider_id in providers:
            print(f"- {provider_id}：已保存 key")
        return 0
    if action == "login":
        try:
            connect_provider(args.provider, api_key=args.api_key, workspace_root=context.workspace_root, user_home=context.user_home)
        except Exception as exc:
            print(f"保存失败：{exc}")
            return 1
        print(f"已保存 Provider 凭据：{args.provider}")
        return 0
    if action == "logout":
        removed = remove_provider_auth(args.provider, user_home=context.user_home)
        print(f"{'已删除' if removed else '未找到'} Provider 凭据：{args.provider}")
        return 0
    print("未知 auth 命令。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
