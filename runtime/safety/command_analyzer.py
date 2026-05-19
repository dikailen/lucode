from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath


SEVERITY_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

DECISION_LABELS = {
    "allow": "允许",
    "allow_limited": "有限允许",
    "ask": "需要审批",
    "sandbox_preview": "沙箱预演",
    "deny": "拒绝",
}

HARD_DENY_COMMAND_PATTERNS = {
    "git push --force": "会强制改写远端历史",
    "git reset --hard": "会丢弃工作区修改",
    "git clean": "会删除未跟踪文件",
    "git push": "会把本地提交发布到远端",
    "git checkout --": "会覆盖工作区文件",
    "npm publish": "发布命令会把包或构建产物推送到外部仓库",
    "pnpm publish": "发布命令会把包或构建产物推送到外部仓库",
    "yarn publish": "发布命令会把包或构建产物推送到外部仓库",
    "bun publish": "发布命令会把包或构建产物推送到外部仓库",
    "twine upload": "发布命令会把包或构建产物推送到外部仓库",
}


@dataclass(frozen=True)
class CommandFinding:
    severity: str
    category: str
    message: str
    evidence: str = ""
    blocks_execution: bool = False


@dataclass(frozen=True)
class CommandAnalysis:
    command: str
    argv: tuple[str, ...]
    executable: str
    risk_level: str
    findings: tuple[CommandFinding, ...]
    decision: str = "ask"
    decision_reason: str = ""
    parse_error: str = ""

    @property
    def should_deny(self) -> bool:
        return self.decision == "deny" or bool(self.parse_error) or any(finding.blocks_execution for finding in self.findings)

    @property
    def blocking_summary(self) -> str:
        messages = [finding.message for finding in self.findings if finding.blocks_execution]
        if self.parse_error:
            messages.insert(0, self.parse_error)
        return "；".join(messages) or "命令风险超过当前策略"


def analyze_command(command: str) -> CommandAnalysis:
    text = str(command or "").strip()
    if not text:
        return CommandAnalysis(
            command=text,
            argv=(),
            executable="",
            risk_level="high",
            findings=(CommandFinding("high", "parse", "命令为空，无法确认执行意图", blocks_execution=True),),
            decision="deny",
            decision_reason="命令为空，不能安全执行",
            parse_error="命令为空",
        )

    try:
        argv = tuple(_strip_quotes(arg) for arg in shlex.split(text, posix=False) if _strip_quotes(arg))
    except ValueError as exc:
        return CommandAnalysis(
            command=text,
            argv=(),
            executable="",
            risk_level="high",
            findings=(CommandFinding("high", "parse", f"命令解析失败：{exc}", blocks_execution=True),),
            decision="deny",
            decision_reason="命令无法解析，不能确认执行边界",
            parse_error=str(exc),
        )

    executable = _basename(argv[0]) if argv else ""
    lowered = tuple(arg.lower() for arg in argv)
    findings: list[CommandFinding] = []

    findings.extend(_find_shell_operators(text, lowered))
    findings.extend(_find_nested_shell(executable, lowered))
    findings.extend(_find_destructive_commands(executable, lowered))
    findings.extend(_find_git_risks(lowered))
    findings.extend(_find_publish_risks(executable, lowered))
    findings.extend(_find_package_manager_risks(executable, lowered))
    findings.extend(_find_network_risks(executable, lowered))
    findings.extend(_find_interpreter_risks(executable, lowered))
    findings.extend(_find_path_traversal(argv))

    risk_level = _max_severity(findings)
    decision, decision_reason = _decide_command(argv, executable, risk_level, findings)
    return CommandAnalysis(
        command=text,
        argv=argv,
        executable=executable,
        risk_level=risk_level,
        findings=tuple(findings),
        decision=decision,
        decision_reason=decision_reason,
    )


def render_command_analysis(analysis: CommandAnalysis) -> list[str]:
    lines = [
        "命令风险分析",
        f"- 风险等级：{analysis.risk_level}",
        f"- 决策：{analysis.decision}（{DECISION_LABELS.get(analysis.decision, analysis.decision)}）",
    ]
    if analysis.decision_reason:
        lines.append(f"- 决策原因：{analysis.decision_reason}")
    if analysis.executable:
        lines.append(f"- 可执行程序：{analysis.executable}")
    if not analysis.findings:
        lines.append("- 结论：未发现明显高风险模式；仍需确认命令意图和工作目录。")
        return lines

    for finding in analysis.findings:
        prefix = "阻止" if finding.blocks_execution else "提示"
        evidence = f"（{finding.evidence}）" if finding.evidence else ""
        lines.append(f"- [{prefix}/{finding.severity}] {finding.message}{evidence}")
    return lines


def _find_shell_operators(command: str, lowered: tuple[str, ...]) -> list[CommandFinding]:
    findings: list[CommandFinding] = []
    operator_patterns = {
        "&&": r"(^|\s)&&(\s|$)",
        "||": r"(^|\s)\|\|(\s|$)",
        "|": r"(^|\s)\|(\s|$)",
        ">>": r"(^|\s)>>(\s|$)",
        ">": r"(^|\s)>(\s|$)",
        "<": r"(^|\s)<(\s|$)",
        ";": r"(^|\s);(\s|$)",
    }
    for operator, pattern in operator_patterns.items():
        if operator in lowered or re.search(pattern, command):
            findings.append(
                CommandFinding(
                    "high",
                    "shell_operator",
                    "包含 shell 串联、管道或重定向操作，当前 command_runner 不允许 shell 展开",
                    evidence=operator,
                    blocks_execution=True,
                )
            )
    if "`" in command:
        findings.append(
            CommandFinding(
                "high",
                "shell_operator",
                "包含反引号，可能触发 shell 命令替换",
                evidence="`",
                blocks_execution=True,
            )
        )
    return findings


def _find_nested_shell(executable: str, lowered: tuple[str, ...]) -> list[CommandFinding]:
    shell_executables = {"powershell", "powershell.exe", "pwsh", "pwsh.exe", "cmd", "cmd.exe", "bash", "sh", "zsh"}
    shell_flags = {"-command", "-c", "/c", "-encodedcommand", "-enc"}
    if executable not in shell_executables:
        return []
    if any(flag in lowered for flag in shell_flags):
        return [
            CommandFinding(
                "high",
                "nested_shell",
                "通过 shell 解释器执行子命令，可能绕过 argv 级别安全约束",
                evidence=executable,
                blocks_execution=True,
            )
        ]
    return [
        CommandFinding(
            "medium",
            "nested_shell",
            "正在启动交互式 shell，请确认这不是为了绕过命令限制",
            evidence=executable,
        )
    ]


def _find_destructive_commands(executable: str, lowered: tuple[str, ...]) -> list[CommandFinding]:
    findings: list[CommandFinding] = []
    joined = " ".join(lowered)
    destructive = {"rm", "del", "erase", "rmdir", "remove-item", "format", "shutdown", "reboot", "reg", "schtasks"}
    if executable in destructive:
        findings.append(
            CommandFinding(
                "critical",
                "destructive",
                "命令可能删除文件、修改系统或影响机器状态",
                evidence=executable,
                blocks_execution=True,
            )
        )
    if "remove-item" in joined or re.search(r"(^|\s)rm(\s|$)", joined):
        if any(flag in joined for flag in {"-recurse", " -r", "-force", "-fo", "-rf", "-fr"}):
            findings.append(
                CommandFinding(
                    "critical",
                    "destructive",
                    "检测到递归或强制删除参数",
                    evidence=" ".join(arg for arg in lowered if arg.startswith("-")),
                    blocks_execution=True,
                )
            )
    return findings


def _find_git_risks(lowered: tuple[str, ...]) -> list[CommandFinding]:
    if not lowered or lowered[0] != "git":
        return []
    joined = " ".join(lowered)
    for pattern, message in HARD_DENY_COMMAND_PATTERNS.items():
        if not pattern.startswith("git "):
            continue
        if joined.startswith(pattern):
            return [
                CommandFinding(
                    "critical",
                    "git",
                    message,
                    evidence=pattern,
                    blocks_execution=True,
                )
            ]
    if lowered[:2] in {("git", "commit"), ("git", "merge"), ("git", "rebase")}:
        return [
            CommandFinding(
                "medium",
                "git",
                "Git 写操作会改变仓库历史或工作状态",
                evidence=" ".join(lowered[:2]),
            )
        ]
    return []


def _find_publish_risks(executable: str, lowered: tuple[str, ...]) -> list[CommandFinding]:
    joined = " ".join(lowered)
    publish_patterns = {
        pattern: message
        for pattern, message in HARD_DENY_COMMAND_PATTERNS.items()
        if "publish" in pattern or pattern == "twine upload"
    }
    matched = next((item for item in publish_patterns if joined == item or joined.startswith(item + " ")), "")
    if matched:
        return [
            CommandFinding(
                "critical",
                "publish",
                publish_patterns[matched],
                evidence=matched,
                blocks_execution=True,
            )
        ]
    return []


def _find_package_manager_risks(executable: str, lowered: tuple[str, ...]) -> list[CommandFinding]:
    if len(lowered) < 2:
        return []
    package_managers = {"npm", "pnpm", "yarn", "bun", "pip", "pip3", "conda", "uv", "poetry"}
    mutating_subcommands = {
        "install",
        "add",
        "update",
        "upgrade",
        "remove",
        "uninstall",
        "sync",
        "env",
    }
    if executable not in package_managers:
        return []
    if any(arg in mutating_subcommands for arg in lowered[1:3]):
        return [
            CommandFinding(
                "medium",
                "package_manager",
                "包管理命令可能修改依赖、锁文件或当前环境",
                evidence=" ".join(lowered[:3]),
            )
        ]
    return []


def _find_network_risks(executable: str, lowered: tuple[str, ...]) -> list[CommandFinding]:
    network_commands = {"curl", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod", "ssh", "scp"}
    if executable not in network_commands:
        return []
    severity = "high" if executable in {"ssh", "scp"} else "medium"
    return [
        CommandFinding(
            severity,
            "network",
            "命令会访问网络或远端主机",
            evidence=executable,
        )
    ]


def _decide_command(
    argv: tuple[str, ...],
    executable: str,
    risk_level: str,
    findings: list[CommandFinding],
) -> tuple[str, str]:
    if any(finding.blocks_execution for finding in findings):
        return "deny", "命中硬阻断规则"
    lowered = tuple(arg.lower() for arg in argv)
    if not lowered:
        return "deny", "命令为空"
    if _is_readonly_command(lowered, executable):
        return "allow", "明确只读查询命令"
    if _is_limited_local_command(lowered, executable):
        return "allow_limited", "本地验证或版本查询，限制在当前工作区执行"
    if any(finding.category in {"package_manager", "network", "git", "inline_code", "nested_shell"} for finding in findings):
        return "ask", "会修改环境、访问网络或执行动态逻辑，需要用户审批"
    if risk_level in {"high", "critical"}:
        return "sandbox_preview", "风险较高，后续应先进入沙箱预演"
    return "ask", "未命中明确只读白名单，默认需要审批"


def _is_readonly_command(lowered: tuple[str, ...], executable: str) -> bool:
    if executable == "git" and lowered[:2] in {
        ("git", "status"),
        ("git", "diff"),
        ("git", "log"),
        ("git", "show"),
        ("git", "branch"),
    }:
        return True
    if executable in {"rg", "ripgrep", "findstr", "where", "dir", "ls", "pwd"}:
        return True
    if executable in {"python", "python.exe", "py"} and any(arg in {"--version", "-v"} for arg in lowered[1:]):
        return True
    if executable in {"node", "node.exe", "npm", "pnpm", "yarn", "bun"} and any(
        arg in {"--version", "-v", "version"} for arg in lowered[1:3]
    ):
        return True
    return False


def _is_limited_local_command(lowered: tuple[str, ...], executable: str) -> bool:
    if executable in {"python", "python.exe", "py"} and "-m" in lowered:
        module_index = lowered.index("-m")
        module = lowered[module_index + 1] if module_index + 1 < len(lowered) else ""
        return module in {"pytest", "unittest", "compileall"}
    if executable in {"pytest", "pytest.exe"}:
        return True
    if executable in {"npm", "pnpm", "yarn", "bun"} and len(lowered) >= 2:
        return lowered[1] in {"test", "run"} and "publish" not in lowered
    return False


def _find_interpreter_risks(executable: str, lowered: tuple[str, ...]) -> list[CommandFinding]:
    interpreters = {"python", "python.exe", "py", "node", "node.exe", "ruby", "perl"}
    inline_flags = {"-c", "-e"}
    if executable in interpreters and any(flag in lowered for flag in inline_flags):
        return [
            CommandFinding(
                "medium",
                "inline_code",
                "解释器内联代码可能执行任意本地逻辑",
                evidence=executable,
            )
        ]
    return []


def _find_path_traversal(argv: tuple[str, ...]) -> list[CommandFinding]:
    findings: list[CommandFinding] = []
    for arg in argv[1:]:
        if arg.startswith("-"):
            continue
        if ".." in PureWindowsPath(arg).parts or ".." in PurePosixPath(arg).parts:
            findings.append(
                CommandFinding(
                    "high",
                    "path",
                    "参数包含父目录跳转，可能越过工作区边界",
                    evidence=arg,
                    blocks_execution=True,
                )
            )
    return findings


def _max_severity(findings: list[CommandFinding]) -> str:
    if not findings:
        return "low"
    return max((finding.severity for finding in findings), key=lambda value: SEVERITY_RANK.get(value, 0))


def _strip_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _basename(value: str) -> str:
    text = _strip_quotes(value).strip()
    if not text:
        return ""
    return (PureWindowsPath(text).name or PurePosixPath(text).name or text).lower()
