import json


PERMISSION_POLICY = {
    "version": 1,
    "description": "Unified local permission policy for dynamic Agents and MCP tools.",
    "permissions": {
        "read": {
            "default": "allow",
            "scope": "project_root_only",
            "notes": "Read-only filesystem MCPs may inspect files under the configured project roots.",
        },
        "edit": {
            "default": "ask",
            "scope": "project_root_only",
            "notes": "File creation, overwrite, exact replacement, and patch application require user approval.",
        },
        "delete": {
            "default": "ask",
            "scope": "project_root_only",
            "notes": "Deletion requires a zip backup first, then user approval through the MCP interruption flow.",
        },
        "bash": {
            "default": "ask",
            "scope": "project_root_cwd",
            "notes": "Commands run without a shell, with dangerous commands rejected before approval.",
        },
        "git": {
            "default": "read_allow_write_ask",
            "scope": "project_root_repo",
            "notes": "git status/diff/log are allowed; git commit requires approval; push/reset/clean are denied.",
        },
        "web": {
            "default": "allow",
            "scope": "external_network",
            "notes": "Web search/fetch is allowed only when the planner explicitly grants web_search MCP.",
        },
    },
    "hard_denies": [
        "Access outside project_root",
        "Modifying .git, .agent_quarantine, or .agent_cache",
        "Modifying .env through automated edit tools",
        "Shell pipelines, redirection, and command chaining in command_runner",
        "git push, git reset --hard, and git clean",
    ],
}


def load_permission_policy() -> dict:
    return PERMISSION_POLICY


def compact_permission_policy_for_prompt() -> str:
    lines = ["权限策略："]
    for name, item in PERMISSION_POLICY["permissions"].items():
        lines.append(
            f"- {name}: 默认={item['default']} | 范围={item['scope']} | 说明={item['notes']}"
        )
    lines.append("硬性拒绝：" + "；".join(PERMISSION_POLICY["hard_denies"]))
    return "\n".join(lines)


def permission_policy_json() -> str:
    return json.dumps(PERMISSION_POLICY, ensure_ascii=False, indent=2) + "\n"
