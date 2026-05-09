import json
import hashlib
import os
import shutil
import subprocess
import sys
import unittest
import uuid
import stat
import asyncio
import contextlib
import io
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
TEMP_ROOT = PROJECT_ROOT / ".agent_test_tmp"


def _safe_rmtree(path: Path) -> None:
    if not path.exists():
        return

    def _onerror(func, value, exc_info):
        try:
            os.chmod(value, stat.S_IWRITE)
        except Exception:
            pass
        try:
            func(value)
        except Exception:
            pass

    shutil.rmtree(path, onerror=_onerror)


class BudgetedFilesystemTests(unittest.TestCase):
    def setUp(self):
        os.environ["BUDGETED_FS_ROOT"] = str(PROJECT_ROOT)
        os.environ["BUDGETED_FS_LABEL"] = "test_project"
        os.environ["BUDGETED_FS_MAX_READ_CALLS"] = "1"
        os.environ["BUDGETED_FS_MAX_CHARS_PER_FILE"] = "200"
        os.environ["BUDGETED_FS_MAX_TOTAL_CHARS"] = "500"
        from mcp_servers.readonly import budgeted_filesystem_mcp as fs

        fs.READ_CALLS = 0
        fs.TOTAL_CHARS = 0
        self.fs = fs

    def test_read_budget_and_env_protection(self):
        payload = json.loads(self.fs.read_file("main.py", max_chars=100))
        self.assertEqual(payload["path"], "main.py")
        with self.assertRaises(ValueError):
            self.fs.read_file(".env", max_chars=20)
        with self.assertRaises(RuntimeError):
            self.fs.read_file("main.py", max_chars=20)

    def test_read_file_includes_sha256_for_strict_edits(self):
        payload = json.loads(self.fs.read_file("main.py", max_chars=100))

        self.assertRegex(payload["sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(payload["sha256"], hashlib.sha256((PROJECT_ROOT / "main.py").read_bytes()).hexdigest())


class CodeLocatorTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = PROJECT_ROOT / ".agent_cache" / f"test_code_locator_{uuid.uuid4().hex}"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["CODE_LOCATOR_PROJECT_ROOT"] = str(PROJECT_ROOT)
        os.environ["CODE_LOCATOR_CACHE_DIR"] = str(self.cache_dir)
        os.environ["CODE_LOCATOR_MAX_FILES"] = "700"
        os.environ["CODE_LOCATOR_MAX_FILE_BYTES"] = "300000"

    def tearDown(self):
        if self.cache_dir.exists():
            _safe_rmtree(self.cache_dir)

    def test_locator_cache_and_results(self):
        from mcp_servers.readonly import code_locator_mcp as locator

        first = json.loads(locator.locate_code("MCPServerManager startup"))
        second = json.loads(locator.locate_code("MCPServerManager startup"))
        self.assertIn(first["cache"], {"hit", "rebuilt"})
        self.assertEqual(second["cache"], "hit")
        self.assertTrue(any(item["path"] == "mcp_servers/__init__.py" for item in second["results"]))

    def test_outline_uses_cache(self):
        from mcp_servers.readonly import code_locator_mcp as locator

        outline = json.loads(locator.get_file_outline("mcp_servers/__init__.py"))
        self.assertEqual(outline["path"], "mcp_servers/__init__.py")
        self.assertTrue(any(item["name"] == "MCPServerManager" for item in outline["symbols"]))

    def test_cache_dir_is_isolated_per_test_run(self):
        from mcp_servers.readonly import code_locator_mcp as locator

        json.loads(locator.locate_code("MCPServerManager startup"))

        self.assertTrue((self.cache_dir / "code_locator_index.json").exists())
        self.assertEqual(locator._cache_dir(), self.cache_dir.resolve())

    def test_index_cache_write_is_atomic(self):
        from mcp_servers.readonly import code_locator_mcp as locator

        cache_path = self.cache_dir / "atomic_index.json"

        locator._atomic_write_text(cache_path, '{"ok": true}')

        self.assertEqual(cache_path.read_text(encoding="utf-8"), '{"ok": true}')
        self.assertEqual(list(self.cache_dir.glob(".atomic_index.json.*.tmp")), [])


class CodeLocatorGraphTests(unittest.TestCase):
    def setUp(self):
        self.project_root = TEMP_ROOT / "locator_graph_project"
        self.cache_dir = self.project_root / ".agent_cache"
        self.project_root.mkdir(parents=True, exist_ok=True)
        os.environ["CODE_LOCATOR_PROJECT_ROOT"] = str(self.project_root)
        os.environ["CODE_LOCATOR_CACHE_DIR"] = str(self.cache_dir)
        os.environ["CODE_LOCATOR_MAX_FILES"] = "50"
        os.environ["CODE_LOCATOR_MAX_FILE_BYTES"] = "100000"

        (self.project_root / "api.py").write_text(
            "\n".join(
                [
                    "from security import verify_password",
                    "",
                    "def login(username, password):",
                    "    if verify_password(username, password):",
                    "        return create_session(username)",
                    "    return None",
                    "",
                    "def create_session(username):",
                    "    return {'user': username}",
                ]
            ),
            encoding="utf-8",
        )
        (self.project_root / "security.py").write_text(
            "\n".join(
                [
                    "def verify_password(username, password):",
                    "    hashed = load_hash(username)",
                    "    return hashed == password",
                    "",
                    "def load_hash(username):",
                    "    return 'secret'",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def test_bm25_ast_graph_returns_called_symbol_context(self):
        from mcp_servers.readonly import code_locator_mcp as locator

        payload = json.loads(locator.locate_code("login password bug", max_results=5))
        result_paths = {item["path"] for item in payload["results"]}
        all_related_paths = {
            related["path"]
            for item in payload["results"]
            for related in item.get("related_symbols", [])
        }

        self.assertEqual(payload["method"], "bm25_ast_graph")
        self.assertTrue((self.cache_dir / "code_graph.db").exists())
        self.assertIn("api.py", result_paths)
        self.assertIn("security.py", result_paths | all_related_paths)
        self.assertTrue(
            any(
                related.get("name") == "verify_password"
                for item in payload["results"]
                for related in item.get("related_symbols", [])
            )
        )

    def test_graph_cache_hit_and_ast_outline_ranges(self):
        from mcp_servers.readonly import code_locator_mcp as locator

        first = json.loads(locator.locate_code("login password bug", max_results=5))
        second = json.loads(locator.locate_code("login password bug", max_results=5))
        outline = json.loads(locator.get_file_outline("api.py"))
        login = next(item for item in outline["symbols"] if item["name"] == "login")

        self.assertIn(first["graph"]["cache"], {"hit", "rebuilt"})
        self.assertEqual(second["graph"]["cache"], "hit")
        self.assertEqual(second["cache"], "hit")
        self.assertEqual(login["type"], "function")
        self.assertEqual(login["start_line"], 3)
        self.assertGreaterEqual(login["end_line"], login["start_line"])

    def test_graph_connection_uses_busy_timeout(self):
        from mcp_servers.readonly import code_locator_mcp as locator

        connection = locator._connect_graph_database(self.cache_dir / "code_graph.db")
        try:
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            connection.close()

        self.assertGreaterEqual(busy_timeout, 5000)


class GitToolsTests(unittest.TestCase):
    def setUp(self):
        os.environ["GIT_TOOLS_PROJECT_ROOT"] = str(PROJECT_ROOT)
        os.environ["GIT_TOOLS_QUARANTINE_DIR"] = str(PROJECT_ROOT / ".agent_quarantine")

    def test_git_status_returns_structured_changes(self):
        from mcp_servers.execution.git_mcp import git_status

        payload = json.loads(git_status(short=True))
        self.assertIn("returncode", payload)
        self.assertIn("changed_files", payload)
        self.assertIsInstance(payload["changed_files"], list)

    def test_git_diff_returns_json(self):
        from mcp_servers.execution.git_mcp import git_diff

        payload = json.loads(git_diff(max_chars=1000))
        self.assertIn("returncode", payload)
        self.assertIn("stdout", payload)
        self.assertIn("stderr", payload)


class WorkspaceEditTests(unittest.TestCase):
    def setUp(self):
        os.environ["WORKSPACE_EDIT_PROJECT_ROOT"] = str(PROJECT_ROOT)
        os.environ["WORKSPACE_EDIT_QUARANTINE_DIR"] = str(PROJECT_ROOT / ".agent_quarantine")
        os.environ["WORKSPACE_EDIT_STRICT_SHA256"] = "1"
        TEMP_ROOT.mkdir(exist_ok=True)
        self.relative_path = f".agent_test_tmp/{uuid.uuid4().hex}.txt"

    def tearDown(self):
        for key in [
            "WORKSPACE_EDIT_STRICT_SHA256",
            "WORKSPACE_EDIT_MAX_BACKUP_BYTES",
            "WORKSPACE_EDIT_MAX_BACKUP_FILES",
        ]:
            os.environ.pop(key, None)
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def test_create_replace_delete_with_backup(self):
        from mcp_servers.mutation.workspace_edit_mcp import create_file, delete_file, replace_in_file

        create_file(self.relative_path, "alpha", "regression test create")
        target = PROJECT_ROOT / self.relative_path
        alpha_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        replace_result = replace_in_file(
            self.relative_path,
            "alpha",
            "beta",
            "regression test replace",
            expected_replacements=1,
            expected_sha256=alpha_hash,
        )
        self.assertIn("备份", replace_result)
        self.assertEqual(target.read_text(encoding="utf-8"), "beta")

        beta_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        delete_result = delete_file(self.relative_path, "regression test delete", expected_sha256=beta_hash)
        self.assertIn("压缩备份", delete_result)
        self.assertFalse(target.exists())

    def test_protected_cache_refused(self):
        from mcp_servers.mutation.workspace_edit_mcp import write_file

        with self.assertRaises(ValueError):
            write_file(".agent_cache/should_not_write.txt", "x", "protected path test")

    def test_write_file_rejects_stale_expected_sha256(self):
        from mcp_servers.mutation.workspace_edit_mcp import write_file

        target = PROJECT_ROOT / self.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("alpha", encoding="utf-8")
        old_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        target.write_text("beta", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "expected_sha256"):
            write_file(self.relative_path, "gamma", "stale OCC write", expected_sha256=old_hash)

        self.assertEqual(target.read_text(encoding="utf-8"), "beta")

    def test_write_existing_file_requires_expected_sha256_in_strict_mode(self):
        from mcp_servers.mutation.workspace_edit_mcp import write_file

        target = PROJECT_ROOT / self.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("alpha", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "expected_sha256"):
            write_file(self.relative_path, "beta", "strict write without baseline")

        self.assertEqual(target.read_text(encoding="utf-8"), "alpha")

    def test_replace_existing_file_requires_expected_sha256_in_strict_mode(self):
        from mcp_servers.mutation.workspace_edit_mcp import replace_in_file

        target = PROJECT_ROOT / self.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("alpha", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "expected_sha256"):
            replace_in_file(self.relative_path, "alpha", "beta", "strict replace without baseline")

        self.assertEqual(target.read_text(encoding="utf-8"), "alpha")

    def test_replace_in_file_accepts_current_expected_sha256(self):
        from mcp_servers.mutation.workspace_edit_mcp import replace_in_file

        target = PROJECT_ROOT / self.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("alpha", encoding="utf-8")
        current_hash = hashlib.sha256(target.read_bytes()).hexdigest()

        replace_in_file(
            self.relative_path,
            "alpha",
            "beta",
            "strict OCC replace",
            expected_replacements=1,
            expected_sha256=current_hash,
        )

        self.assertEqual(target.read_text(encoding="utf-8"), "beta")

    def test_apply_unified_patch_requires_sha256_for_existing_file_in_strict_mode(self):
        from mcp_servers.mutation.workspace_edit_mcp import apply_unified_patch

        target = PROJECT_ROOT / self.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("alpha\n", encoding="utf-8")

        patch_text = (
            f"--- a/{self.relative_path}\n"
            f"+++ b/{self.relative_path}\n"
            "@@ -1 +1 @@\n"
            "-alpha\n"
            "+patched\n"
        )

        with self.assertRaisesRegex(ValueError, "expected_sha256"):
            apply_unified_patch(patch_text, "strict patch without baseline")

        self.assertEqual(target.read_text(encoding="utf-8"), "alpha\n")

    def test_apply_unified_patch_rejects_stale_expected_sha256_map(self):
        from mcp_servers.mutation.workspace_edit_mcp import apply_unified_patch

        target = PROJECT_ROOT / self.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("alpha\n", encoding="utf-8")
        old_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        target.write_text("changed\n", encoding="utf-8")

        patch_text = (
            f"--- a/{self.relative_path}\n"
            f"+++ b/{self.relative_path}\n"
            "@@ -1 +1 @@\n"
            "-changed\n"
            "+patched\n"
        )
        with self.assertRaisesRegex(ValueError, "expected_sha256"):
            apply_unified_patch(
                patch_text,
                "stale OCC patch",
                expected_sha256_by_path={self.relative_path: old_hash},
            )

        self.assertEqual(target.read_text(encoding="utf-8"), "changed\n")

    def test_backup_size_limit_blocks_large_existing_file_before_write(self):
        from mcp_servers.mutation.workspace_edit_mcp import write_file

        os.environ["WORKSPACE_EDIT_MAX_BACKUP_BYTES"] = "4"
        target = PROJECT_ROOT / self.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("too-large", encoding="utf-8")
        current_hash = hashlib.sha256(target.read_bytes()).hexdigest()

        with self.assertRaisesRegex(ValueError, "backup size"):
            write_file(self.relative_path, "new content", "backup size guard", expected_sha256=current_hash)

        self.assertEqual(target.read_text(encoding="utf-8"), "too-large")


class CommandRunnerTests(unittest.TestCase):
    def setUp(self):
        os.environ["COMMAND_RUNNER_PROJECT_ROOT"] = str(PROJECT_ROOT)
        self.quarantine_dir = TEMP_ROOT / "command_quarantine"
        os.environ["COMMAND_RUNNER_QUARANTINE_DIR"] = str(self.quarantine_dir)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def test_run_command_success_and_missing_executable(self):
        from mcp_servers.execution.command_mcp import run_command

        ok = json.loads(run_command(f'"{sys.executable}" --version', "regression version check"))
        self.assertEqual(ok["returncode"], 0)

        missing = json.loads(run_command("definitely_missing_agents_demo_command", "regression missing command"))
        self.assertEqual(missing["returncode"], 127)

    def test_denied_command_raises_before_execution(self):
        from mcp_servers.execution.command_mcp import run_command

        with self.assertRaises(ValueError):
            run_command("git push", "regression denied command")


class OperationLogTests(unittest.TestCase):
    def setUp(self):
        self.quarantine_dir = TEMP_ROOT / "operation_log_quarantine"
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        os.environ["WORKSPACE_EDIT_PROJECT_ROOT"] = str(PROJECT_ROOT)
        os.environ["WORKSPACE_EDIT_QUARANTINE_DIR"] = str(self.quarantine_dir)
        os.environ["COMMAND_RUNNER_PROJECT_ROOT"] = str(PROJECT_ROOT)
        os.environ["COMMAND_RUNNER_QUARANTINE_DIR"] = str(self.quarantine_dir)
        self.relative_path = f".agent_test_tmp/{uuid.uuid4().hex}.txt"

    def tearDown(self):
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def _records(self):
        path = self.quarantine_dir / "operations.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_workspace_edit_logs_unified_success_record(self):
        from mcp_servers.mutation.workspace_edit_mcp import create_file, delete_file

        create_file(self.relative_path, "alpha", "operation log create")
        target = PROJECT_ROOT / self.relative_path
        current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        delete_file(self.relative_path, "operation log delete", expected_sha256=current_hash)

        records = self._records()
        self.assertEqual(records[-2]["tool"], "workspace_edit.create_file")
        self.assertEqual(records[-2]["status"], "success")
        self.assertTrue(records[-2]["approval"]["required"])
        self.assertEqual(records[-2]["params_summary"]["target_path"], self.relative_path)
        self.assertEqual(records[-1]["tool"], "workspace_edit.delete_file")
        self.assertTrue(records[-1]["backup"]["created"])
        self.assertIn("backup_path", records[-1]["backup"])

    def test_denied_command_logs_failed_record_without_running(self):
        from mcp_servers.execution.command_mcp import run_command

        with self.assertRaises(ValueError):
            run_command("git push", "operation log denied command")

        record = self._records()[-1]
        self.assertEqual(record["tool"], "command_runner.run_command")
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["approval"]["required"])
        self.assertIn("denied", record["error"].lower())
        self.assertEqual(record["params_summary"]["command"], "git push")

    def test_runtime_fast_path_git_status_logs_unified_record(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _run_git_status_fast_path

        os.environ["AGENTS_OPERATION_LOG_PATH"] = str(self.quarantine_dir / "operations.jsonl")
        task = PlannedTask(
            id="git_status",
            title="查看 git status",
            instruction="查看当前工作区改动。",
            skill_id="project_explorer",
            model="local_model",
            mcp=["git_tools"],
        )

        try:
            _run_git_status_fast_path(PROJECT_ROOT, task)
        finally:
            os.environ.pop("AGENTS_OPERATION_LOG_PATH", None)

        record = self._records()[-1]
        self.assertEqual(record["tool"], "runtime_fast_path.git_status")
        self.assertEqual(record["status"], "success")
        self.assertFalse(record["approval"]["required"])
        self.assertEqual(record["params_summary"]["task_id"], "git_status")

    def test_runtime_fast_path_web_search_logs_unified_record(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution import dynamic

        os.environ["AGENTS_OPERATION_LOG_PATH"] = str(self.quarantine_dir / "operations.jsonl")
        task = PlannedTask(
            id="url_search",
            title="只返回 OpenAI Agents SDK MCP 官方链接",
            instruction="联网搜索并只返回 URL。",
            skill_id="project_explorer",
            model="local_model",
            mcp=["web_search"],
        )

        with patch.object(
            dynamic,
            "web_search",
            return_value=json.dumps({"results": [{"url": "https://openai.github.io/openai-agents-python/"}]}),
        ):
            try:
                output = dynamic._run_url_search_fast_path("OpenAI Agents SDK MCP docs URL", task)
            finally:
                os.environ.pop("AGENTS_OPERATION_LOG_PATH", None)

        self.assertIn("https://openai.github.io/openai-agents-python/", output)
        record = self._records()[-1]
        self.assertEqual(record["tool"], "runtime_fast_path.web_search")
        self.assertEqual(record["status"], "success")
        self.assertFalse(record["approval"]["required"])
        self.assertIn("OpenAI Agents SDK", record["params_summary"]["query"])


class SafeDeleteTests(unittest.TestCase):
    def setUp(self):
        os.environ["SAFE_DELETE_PROJECT_ROOT"] = str(PROJECT_ROOT)
        self.quarantine_dir = TEMP_ROOT / "safe_delete_quarantine"
        os.environ["SAFE_DELETE_QUARANTINE_DIR"] = str(self.quarantine_dir)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        TEMP_ROOT.mkdir(exist_ok=True)

    def tearDown(self):
        for key in ["SAFE_DELETE_MAX_BACKUP_BYTES", "SAFE_DELETE_MAX_BACKUP_FILES"]:
            os.environ.pop(key, None)
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def test_env_and_cache_are_protected(self):
        from mcp_servers.mutation.safe_delete_mcp import safe_delete_file

        with self.assertRaises(ValueError):
            safe_delete_file(".env", "regression protected env")
        with self.assertRaises(ValueError):
            safe_delete_file(".agent_cache", "regression protected cache")

    def test_backup_action_is_logged(self):
        from mcp_servers.mutation.safe_delete_mcp import safe_delete_file

        relative_path = f".agent_test_tmp/{uuid.uuid4().hex}.txt"
        target = PROJECT_ROOT / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("temporary delete candidate", encoding="utf-8")

        result = safe_delete_file(relative_path, "regression backup log")
        self.assertIn("原文件未移动、未删除", result)
        self.assertTrue(target.exists())

        records = [
            json.loads(line)
            for line in (self.quarantine_dir / "operations.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(records[-1]["tool"], "safe_delete.safe_delete_file")
        self.assertEqual(records[-1]["status"], "success")
        self.assertTrue(records[-1]["backup"]["created"])

    def test_backup_size_limit_blocks_large_candidate_without_deleting(self):
        from mcp_servers.mutation.safe_delete_mcp import safe_delete_file

        os.environ["SAFE_DELETE_MAX_BACKUP_BYTES"] = "4"
        relative_path = f".agent_test_tmp/{uuid.uuid4().hex}.txt"
        target = PROJECT_ROOT / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("too-large", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "backup size"):
            safe_delete_file(relative_path, "backup size guard")

        self.assertTrue(target.exists())
        self.assertFalse((self.quarantine_dir / "operations.jsonl").exists())


class WebSearchRankingTests(unittest.TestCase):
    def tearDown(self):
        for key in [
            "WEB_SEARCH_PROVIDER",
            "WEB_SEARCH_API_KEY",
            "BRAVE_SEARCH_API_KEY",
            "BING_SEARCH_API_KEY",
            "SERPAPI_API_KEY",
        ]:
            os.environ.pop(key, None)

    def test_source_tiers(self):
        from mcp_servers.network.web_search_mcp import _source_tier, _tier_score

        docs = _source_tier("OpenAI Agents SDK docs", "https://openai.github.io/openai-agents-python/", "Docs")
        github = _source_tier("OpenAI Agents SDK", "https://github.com/openai/openai-agents-python", "GitHub")
        community = _source_tier("OpenAI Agents SDK", "https://stackoverflow.com/questions/1", "Question")
        self.assertEqual(github, "official_github")
        self.assertGreater(_tier_score(docs), _tier_score(community))
        self.assertGreater(_tier_score(github), _tier_score(community))

    def test_web_search_failure_returns_json_error(self):
        from mcp_servers.network import web_search_mcp

        with patch.object(web_search_mcp.requests, "get", side_effect=web_search_mcp.requests.RequestException("boom")):
            payload = json.loads(web_search_mcp.web_search("OpenAI Agents SDK"))
        self.assertEqual(payload["results"], [])
        self.assertIn("error", payload)

    def test_web_search_provider_payload_has_source_metadata_and_official_first(self):
        from mcp_servers.network import web_search_mcp

        class FakeProvider:
            provider_id = "fake_provider"
            provider_label = "Fake Provider"

            def search(self, query, max_results, timeout):
                return web_search_mcp.SearchProviderResponse(
                    provider_id=self.provider_id,
                    provider_label=self.provider_label,
                    results=[
                        {
                            "title": "Community answer",
                            "url": "https://stackoverflow.com/questions/1",
                        },
                        {
                            "title": "OpenAI Agents SDK Docs",
                            "url": "https://openai.github.io/openai-agents-python/",
                        },
                    ],
                )

        with patch.object(web_search_mcp, "_get_search_provider", return_value=FakeProvider()):
            payload = json.loads(web_search_mcp.web_search("OpenAI Agents SDK docs", max_results=2))

        self.assertEqual(payload["source_provider"], "fake_provider")
        self.assertEqual(payload["source_provider_label"], "Fake Provider")
        self.assertIn("retrieved_at", payload)
        self.assertEqual(payload["results"][0]["source_tier"], "official_docs")
        self.assertEqual(payload["results"][0]["source_provider"], "fake_provider")
        self.assertIn("retrieved_at", payload["results"][0])
        self.assertIn("source_priority", payload)

    def test_api_search_provider_without_key_returns_structured_error(self):
        from mcp_servers.network import web_search_mcp

        os.environ["WEB_SEARCH_PROVIDER"] = "brave"
        os.environ.pop("WEB_SEARCH_API_KEY", None)
        os.environ.pop("BRAVE_SEARCH_API_KEY", None)

        payload = json.loads(web_search_mcp.web_search("OpenAI Agents SDK"))

        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["source_provider"], "brave")
        self.assertIn("API Key", payload["error"])


class RuntimeHelpersTests(unittest.TestCase):
    def test_runtime_modules_are_grouped_by_runtime_responsibility(self):
        expected = [
            PROJECT_ROOT / "runtime" / "agents" / "factory.py",
            PROJECT_ROOT / "runtime" / "config" / "cli.py",
            PROJECT_ROOT / "runtime" / "config" / "settings.py",
            PROJECT_ROOT / "runtime" / "config" / "execution_mode.py",
            PROJECT_ROOT / "runtime" / "modes" / "solo.py",
            PROJECT_ROOT / "runtime" / "modes" / "serial.py",
            PROJECT_ROOT / "runtime" / "modes" / "full.py",
            PROJECT_ROOT / "runtime" / "common" / "conversation.py",
            PROJECT_ROOT / "runtime" / "execution" / "dynamic.py",
            PROJECT_ROOT / "runtime" / "execution" / "pipeline.py",
            PROJECT_ROOT / "runtime" / "safety" / "auditor.py",
            PROJECT_ROOT / "runtime" / "safety" / "checkpoint.py",
            PROJECT_ROOT / "runtime" / "memory" / "flywheel.py",
            PROJECT_ROOT / "runtime" / "workspace" / "run_workspace.py",
        ]

        for path in expected:
            self.assertTrue(path.exists(), f"missing runtime module: {path}")

    def test_git_status_parser(self):
        from runtime.execution.dynamic import _parse_git_status_short

        parsed = _parse_git_status_short(" M main.py\n?? tests/run_regression.py\n")
        self.assertEqual(parsed[0]["path"], "main.py")
        self.assertEqual(parsed[0]["status"], "已修改")
        self.assertEqual(parsed[1]["status"], "未跟踪")

    def test_turn_error_format(self):
        from main import _format_turn_error

        message = _format_turn_error(RuntimeError("boom"))
        self.assertIn("程序没有退出", message)
        self.assertIn("RuntimeError", message)

    def test_privacy_model_error_format(self):
        from main import _format_turn_error

        message = _format_turn_error(ValueError("No configured models allowed by privacy mode: offline"))
        self.assertIn("隐私模式 offline", message)
        self.assertIn("没有可用的本地模型", message)
        self.assertIn("AGENTS_PRIVACY_MODE", message)

    def test_missing_tool_model_behavior_error_is_explained(self):
        from main import _format_turn_error

        class ModelBehaviorError(Exception):
            pass

        message = _format_turn_error(ModelBehaviorError("Tool command_runner not found in agent dynamic_analyze"))

        self.assertIn("模型尝试调用未分配的工具", message)
        self.assertIn("command_runner", message)
        self.assertIn("重新规划", message)

    def test_only_slash_exit_is_exit_command(self):
        from main import _is_exit_command, _is_new_command, _is_stop_command

        self.assertTrue(_is_exit_command("/exit"))
        self.assertTrue(_is_exit_command(" /EXIT "))
        self.assertFalse(_is_exit_command("exit"))
        self.assertFalse(_is_exit_command("quit"))
        self.assertFalse(_is_exit_command("退出"))
        self.assertTrue(_is_stop_command("/stop"))
        self.assertTrue(_is_stop_command(" /STOP "))
        self.assertFalse(_is_stop_command("stop"))
        self.assertTrue(_is_new_command("/new"))
        self.assertTrue(_is_new_command(" /NEW "))
        self.assertFalse(_is_new_command("new"))

    def test_recent_context_is_stored_and_rendered_with_size_budget(self):
        from runtime.common.conversation import append_recent_turn, compose_recent_context

        turns = []
        append_recent_turn(turns, "user", "u" * 1200, max_chars=60)
        append_recent_turn(turns, "assistant", "a" * 1200, max_chars=80)

        self.assertLessEqual(len(turns[0]["content"]), 90)
        self.assertLessEqual(len(turns[1]["content"]), 110)
        self.assertIn("[truncated", turns[0]["content"])

        prompt = compose_recent_context(turns, "current question", max_chars=50)
        self.assertIn("current question", prompt)
        self.assertLess(len(prompt), 500)

    def test_main_reconfigures_stdin_to_utf8_for_piped_chinese_input(self):
        source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('sys.stdin.reconfigure(encoding="utf-8")', source)

    def test_main_startup_copy_uses_three_mode_language(self):
        source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("终端工程代理已启动", source)
        self.assertIn("solo 为默认单模型工具 Agent", source)
        self.assertNotIn("普通提问会先显示本轮规划再执行", source)

    def test_status_and_diff_commands_are_rendered_without_model_calls(self):
        from runtime.config.cli import render_status_command, render_diff_command
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings(execution_mode="solo", privacy_mode="local_first")
        status = render_status_command(PROJECT_ROOT, settings, started_mcp_ids=["git_tools"])
        diff = render_diff_command(PROJECT_ROOT, max_chars=2000)

        self.assertIn("运行状态", status)
        self.assertIn("当前模式：单模型工具 Agent", status)
        self.assertIn("前置优化副脑：关闭", status)
        self.assertIn("已启动 MCP：git_tools", status)
        self.assertIn("Git 工作区", status)
        self.assertIn("Diff 摘要", diff)
        self.assertNotIn("sk-", status + diff)

    def test_refiner_command_detector_accepts_only_on_off(self):
        from runtime.config.cli import parse_writable_config_command

        self.assertEqual(parse_writable_config_command("/refiner on"), ("refiner", "on"))
        self.assertEqual(parse_writable_config_command("/refiner off"), ("refiner", "off"))
        self.assertEqual(parse_writable_config_command("/mode serial"), ("mode", "serial"))
        self.assertIsNone(parse_writable_config_command("/refiner maybe"))
        self.assertIsNone(parse_writable_config_command("/mode auto"))

    def test_stream_delta_extracts_visible_text_only(self):
        from main import _stream_delta_text

        class FakeEvent:
            def __init__(self, event_type, delta=""):
                self.type = event_type
                self.delta = delta

        class Wrapper:
            def __init__(self, data):
                self.type = "raw_response_event"
                self.data = data

        self.assertEqual(_stream_delta_text(Wrapper(FakeEvent("response.output_text.delta", "你好"))), "你好")
        self.assertEqual(_stream_delta_text(Wrapper(FakeEvent("response.reasoning_text.delta", "hidden"))), "")

    def test_mutation_tool_preview_summarizes_write_before_approval(self):
        from main import _format_tool_preview

        preview = _format_tool_preview(
            "workspace_edit_mcp.write_file",
            json.dumps(
                {
                    "path": "runtime/config/cli.py",
                    "content": "hello" * 100,
                    "expected_sha256": "a" * 64,
                }
            ),
        )

        self.assertIn("写入预览", preview)
        self.assertIn("runtime/config/cli.py", preview)
        self.assertIn("内容长度", preview)
        self.assertNotIn("hellohellohellohellohello", preview)


class ExecutionModeRoutingTests(unittest.TestCase):
    def test_normalize_execution_mode(self):
        from runtime.config.execution_mode import normalize_execution_mode

        self.assertEqual(normalize_execution_mode("SOLO"), "solo")
        self.assertEqual(normalize_execution_mode("serial"), "serial")
        self.assertEqual(normalize_execution_mode("full"), "full")
        self.assertEqual(normalize_execution_mode("auto"), "solo")
        self.assertEqual(normalize_execution_mode("unknown"), "solo")

    def test_only_explicit_solo_uses_solo_mode(self):
        from runtime.config.execution_mode import should_use_solo_mode

        self.assertTrue(should_use_solo_mode("你好你有什么技能", "solo"))
        self.assertTrue(should_use_solo_mode("分析当前项目结构", "solo"))
        self.assertTrue(should_use_solo_mode("hello, what can you do?", "solo"))

    def test_serial_and_full_never_use_solo_mode(self):
        from runtime.config.execution_mode import should_use_solo_mode

        self.assertFalse(should_use_solo_mode("你好你有什么技能", "serial"))
        self.assertFalse(should_use_solo_mode("你好你有什么技能", "full"))
        self.assertFalse(should_use_solo_mode("hello, what can you do?", "serial"))

    def test_legacy_auto_is_treated_as_solo_without_keyword_routing(self):
        from runtime.config.execution_mode import should_use_solo_mode

        self.assertTrue(should_use_solo_mode("分析当前项目结构", "auto"))
        self.assertTrue(should_use_solo_mode("请联网查一下 OpenAI Agents SDK 官方文档链接", "auto"))

    def test_runtime_route_is_mode_based_not_keyword_based(self):
        from runtime.config.execution_mode import runtime_route_for_input

        self.assertEqual(runtime_route_for_input("分析当前项目结构", "solo"), "solo")
        self.assertEqual(runtime_route_for_input("你好", "serial"), "serial")
        self.assertEqual(runtime_route_for_input("你好", "full"), "serial")
        self.assertEqual(runtime_route_for_input("分析当前项目结构", "auto"), "solo")


class RuntimeInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_running_turn(self):
        from main import RuntimeCommandSession

        cancelled = asyncio.Event()

        class FakeConsole:
            interactive = True

            async def read_runtime_line(self):
                return "/stop"

            def defer(self, line):
                raise AssertionError(f"unexpected deferred line: {line}")

        async def long_turn():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return "finished"

        result = await RuntimeCommandSession(FakeConsole()).run(long_turn())

        self.assertTrue(result.stopped)
        self.assertIn("/stop", result.final_output)
        self.assertTrue(cancelled.is_set())

    async def test_runtime_non_command_input_is_deferred_to_next_turn(self):
        from main import RuntimeCommandSession

        class FakeConsole:
            interactive = True

            def __init__(self):
                self.lines = asyncio.Queue()
                self.deferred = []
                self.lines.put_nowait("下一条真实问题")

            async def read_runtime_line(self):
                return await self.lines.get()

            def defer(self, line):
                self.deferred.append(line)

        async def short_turn():
            await asyncio.sleep(0.01)
            return "finished"

        console = FakeConsole()
        result = await RuntimeCommandSession(console).run(short_turn())

        self.assertEqual(result.final_output, "finished")
        self.assertEqual(console.deferred, ["下一条真实问题"])

    async def test_runtime_session_routes_approval_input(self):
        from main import RuntimeCommandSession

        class FakeConsole:
            interactive = True

            async def read_runtime_line(self):
                return "yes"

            def defer(self, line):
                raise AssertionError(f"unexpected deferred line: {line}")

        session = RuntimeCommandSession(FakeConsole())

        async def approval_turn():
            answer = await session.request_approval("是否批准？")
            return f"answer={answer}"

        result = await session.run(approval_turn())

        self.assertEqual(result.final_output, "answer=yes")

    async def test_runtime_session_times_out_and_cancels_turn(self):
        from main import RuntimeCommandSession

        cancelled = asyncio.Event()

        class FakeConsole:
            interactive = False

            async def read_runtime_line(self):
                return ""

        async def slow_turn():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return "finished"

        result = await RuntimeCommandSession(FakeConsole(), timeout_seconds=0.05).run(slow_turn())

        self.assertTrue(result.stopped)
        self.assertIn("超时时间", result.final_output)
        self.assertTrue(cancelled.is_set())


class PipelineTests(unittest.TestCase):
    def test_gate_adds_code_pipeline_tools(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import apply_pipeline_gate

        plan = PlannerResult(
            route_type="single_agent",
            reason="fix code",
            refined_request="fix MCP startup bug",
            tasks=[
                PlannedTask(
                    id="fix_bug",
                    title="Fix MCP startup bug",
                    instruction="Fix the MCP startup bug.",
                    skill_id="jpc_now_skill",
                    model="mimo_model",
                    mcp=[],
                )
            ],
        )
        decision = apply_pipeline_gate(plan, plan.refined_request)
        task = plan.tasks[0]
        self.assertTrue(decision.needs_code_pipeline)
        self.assertIn("code_locator", task.mcp)
        self.assertIn("project_filesystem_readonly", task.mcp)
        self.assertIn("workspace_edit", task.mcp)

    def test_verifier_report_for_code_task(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.pipeline import build_verification_report

        task = PlannedTask(
            id="fix_bug",
            title="Fix bug",
            instruction="Fix code.",
            skill_id="jpc_now_skill",
            model="mimo_model",
            mcp=["workspace_edit"],
        )
        report = build_verification_report(PROJECT_ROOT, task)
        self.assertIn("Verifier 校验摘要", report)
        self.assertIn("git status", report)

    def test_verifier_skips_readonly_no_modify_analysis(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.pipeline import build_verification_report

        task = PlannedTask(
            id="analyze_tests",
            title="分析 tests 目录",
            instruction="只读分析 tests 目录覆盖能力，不修改任何文件，不运行测试。",
            skill_id="jpc_now_skill",
            model="mimo_model",
            mcp=["code_locator", "project_filesystem_readonly"],
        )

        report = build_verification_report(PROJECT_ROOT, task)

        self.assertEqual(report, "")

    def test_verifier_runs_configured_command_for_edit_task(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.pipeline import build_verification_report

        os.environ["AGENTS_VERIFY_COMMANDS"] = f'"{sys.executable}" -c "print(\'verify-ok\')"'
        try:
            task = PlannedTask(
                id="fix_bug",
                title="Fix bug",
                instruction="Fix code.",
                skill_id="jpc_now_skill",
                model="mimo_model",
                mcp=["workspace_edit"],
            )
            report = build_verification_report(PROJECT_ROOT, task)
        finally:
            os.environ.pop("AGENTS_VERIFY_COMMANDS", None)

        self.assertIn("Configured verification commands", report)
        self.assertIn("verify-ok", report)
        self.assertIn("returncode=0", report)

    def test_pipeline_state_records_gate_task_and_verifier(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import GateDecision, PipelineRunState

        plan = PlannerResult(
            route_type="single_agent",
            reason="test",
            refined_request="fix code",
            tasks=[
                PlannedTask(
                    id="fix_bug",
                    title="Fix bug",
                    instruction="Fix bug.",
                    skill_id="jpc_now_skill",
                    model="mimo_model",
                    mcp=["workspace_edit"],
                )
            ],
        )
        state = PipelineRunState.create("fix code", plan)
        decision = GateDecision(
            needs_code_pipeline=True,
            edit_intent=True,
            test_intent=False,
            should_verify=True,
            risk_level="medium",
            reason="test gate",
            applied_tasks=["fix_bug"],
        )
        state.record_gate(decision)
        state.record_task_result(plan.tasks[0], "done")
        state.record_verification("fix_bug", "verified")
        payload = state.to_dict()
        self.assertEqual(payload["route_type"], "single_agent")
        self.assertEqual(payload["gate"]["risk_level"], "medium")
        self.assertEqual(payload["tasks"][0]["status"], "completed")
        self.assertEqual(payload["tasks"][0]["verification"], "verified")

    def test_tasks_are_ordered_by_dependencies_before_parallel_group(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.dynamic import _ordered_tasks_for_execution

        plan = PlannerResult(
            route_type="multi_agent",
            reason="dependency order",
            refined_request="先分析再修改",
            tasks=[
                PlannedTask(
                    id="fix",
                    title="修复",
                    instruction="修复。",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    parallel_group=1,
                    depends_on=["inspect"],
                ),
                PlannedTask(
                    id="inspect",
                    title="分析",
                    instruction="分析。",
                    skill_id="project_explorer",
                    model="local_model",
                    parallel_group=2,
                ),
            ],
        )

        ordered = _ordered_tasks_for_execution(plan)

        self.assertEqual([task.id for task in ordered], ["inspect", "fix"])

    def test_file_aware_scheduler_batches_disjoint_write_intents(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _execution_batches_for_group

        tasks = [
            PlannedTask(
                id="frontend",
                title="前端修改",
                instruction="修改前端文件。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["ui/app.py"],
            ),
            PlannedTask(
                id="backend",
                title="后端修改",
                instruction="修改后端文件。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["api/server.py"],
            ),
        ]

        batches = _execution_batches_for_group(tasks)

        self.assertEqual([[task.id for task in batch] for batch in batches], [["frontend", "backend"]])

    def test_serial_runtime_batches_every_task_individually(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _execution_batches_for_mode

        tasks = [
            PlannedTask(
                id="frontend",
                title="前端修改",
                instruction="修改前端文件。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["ui/app.py"],
            ),
            PlannedTask(
                id="backend",
                title="后端修改",
                instruction="修改后端文件。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["api/server.py"],
            ),
        ]

        solo_batches = _execution_batches_for_mode(tasks, "solo")
        serial_batches = _execution_batches_for_mode(tasks, "serial")

        self.assertEqual([[task.id for task in batch] for batch in solo_batches], [["frontend"], ["backend"]])
        self.assertEqual([[task.id for task in batch] for batch in serial_batches], [["frontend"], ["backend"]])

    def test_full_runtime_serializes_parent_child_path_conflicts(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _execution_batches_for_mode

        tasks = [
            PlannedTask(
                id="dir_edit",
                title="目录级调整",
                instruction="修改 src/ 下结构。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["src/"],
            ),
            PlannedTask(
                id="file_edit",
                title="文件级调整",
                instruction="修改 src/app.py。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["src/app.py"],
            ),
        ]

        batches = _execution_batches_for_mode(tasks, "full")

        self.assertEqual([[task.id for task in batch] for batch in batches], [["dir_edit"], ["file_edit"]])

    def test_parallel_batch_audit_reports_safe_parallel_reason(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _format_parallel_batch_audit

        batch = [
            PlannedTask(
                id="frontend",
                title="前端修改",
                instruction="修改 ui/app.py。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["ui/app.py"],
            ),
            PlannedTask(
                id="backend",
                title="后端修改",
                instruction="修改 api/server.py。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["api/server.py"],
            ),
        ]

        message = _format_parallel_batch_audit(2, batch)

        self.assertIn("并行组 2", message)
        self.assertIn("安全并行", message)
        self.assertIn("ui/app.py", message)
        self.assertIn("api/server.py", message)

    def test_full_runtime_prints_parallel_batch_audit_when_running_safe_batch(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.dynamic import _run_multi_agent

        class FakeFactory:
            async def create_task_agent(self, task):
                return f"agent:{task.id}"

            def create_synthesizer_agent(self, model_id, run_workspace_server):
                return "synthesizer"

        class FakeResult:
            def __init__(self, final_output):
                self.final_output = final_output

        async def fake_run_agent(agent, prompt, hooks, max_turns=20):
            return FakeResult(f"done:{agent}")

        plan = PlannerResult(
            route_type="multi_agent",
            reason="safe parallel",
            refined_request="parallel test",
            tasks=[
                PlannedTask(
                    id="frontend",
                    title="前端修改",
                    instruction="修改 ui/app.py。",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    mcp=["workspace_edit"],
                    write_intent=["ui/app.py"],
                ),
                PlannedTask(
                    id="backend",
                    title="后端修改",
                    instruction="修改 api/server.py。",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    mcp=["workspace_edit"],
                    write_intent=["api/server.py"],
                ),
            ],
            needs_synthesis=True,
            synthesis_instruction="汇总结果。",
        )

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            asyncio.run(
                _run_multi_agent(
                    "parallel test",
                    plan,
                    PROJECT_ROOT,
                    "local_model",
                    FakeFactory(),
                    hooks=None,
                    run_agent=fake_run_agent,
                    execution_mode="full",
                )
            )

        output = buffer.getvalue()
        self.assertIn("安全并行启动 2 个临时 Agent", output)
        self.assertIn("ui/app.py", output)
        self.assertIn("api/server.py", output)

    def test_full_runtime_keeps_safe_parallel_batches_but_serializes_conflicts(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _execution_batches_for_mode

        disjoint_tasks = [
            PlannedTask(
                id="frontend",
                title="前端修改",
                instruction="修改前端文件。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["ui/app.py"],
            ),
            PlannedTask(
                id="backend",
                title="后端修改",
                instruction="修改后端文件。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["api/server.py"],
            ),
        ]
        conflicting_tasks = [
            PlannedTask(
                id="first",
                title="修改配置 A",
                instruction="修改同一个配置。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["runtime/settings.py"],
            ),
            PlannedTask(
                id="second",
                title="修改配置 B",
                instruction="也修改同一个配置。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["runtime/settings.py"],
            ),
        ]

        safe_batches = _execution_batches_for_mode(disjoint_tasks, "full")
        conflict_batches = _execution_batches_for_mode(conflicting_tasks, "full")

        self.assertEqual([[task.id for task in batch] for batch in safe_batches], [["frontend", "backend"]])
        self.assertEqual([[task.id for task in batch] for batch in conflict_batches], [["first"], ["second"]])

    def test_file_aware_scheduler_serializes_conflicting_or_unknown_writes(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _execution_batches_for_group

        tasks = [
            PlannedTask(
                id="first",
                title="修改配置 A",
                instruction="修改同一个配置。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["runtime/settings.py"],
            ),
            PlannedTask(
                id="second",
                title="修改配置 B",
                instruction="也修改同一个配置。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=["runtime/settings.py"],
            ),
            PlannedTask(
                id="unknown",
                title="未知写入",
                instruction="需要修改但没有声明文件。",
                skill_id="jpc_now_skill",
                model="local_model",
                mcp=["workspace_edit"],
                write_intent=[],
            ),
        ]

        batches = _execution_batches_for_group(tasks)

        self.assertEqual([[task.id for task in batch] for batch in batches], [["first"], ["second"], ["unknown"]])

    def test_file_aware_scheduler_serializes_declared_dependencies_even_in_same_group(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _execution_batches_for_group

        tasks = [
            PlannedTask(
                id="analyze",
                title="分析",
                instruction="先分析。",
                skill_id="project_explorer",
                model="local_model",
                mcp=["project_filesystem_readonly"],
                parallel_group=1,
            ),
            PlannedTask(
                id="rewrite",
                title="改写",
                instruction="基于分析结果改写。",
                skill_id="humanizer_zh",
                model="local_model",
                mcp=[],
                parallel_group=1,
                depends_on=["analyze"],
            ),
        ]

        batches = _execution_batches_for_group(tasks)

        self.assertEqual([[task.id for task in batch] for batch in batches], [["analyze"], ["rewrite"]])

    def test_agent_contract_marks_workspace_edit_as_strict_when_write_intent_exists(self):
        from planning.planner_schema import PlannedTask
        from runtime.agents.factory import AgentFactory

        task = PlannedTask(
            id="fix_config",
            title="修复配置",
            instruction="修改配置展示。",
            skill_id="jpc_now_skill",
            model="local_model",
            mcp=["workspace_edit", "project_filesystem_readonly"],
            write_intent=["runtime/cli_config.py"],
        )

        contract = AgentFactory(None, None)._execution_contract(task)

        self.assertIn("strict", contract)
        self.assertIn("expected_sha256", contract)
        self.assertIn("sha256", contract)

    def test_gate_ignores_non_code_analysis_and_chinese_rewrite_workflow(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import apply_pipeline_gate

        plan = PlannerResult(
            route_type="multi_agent",
            reason="先项目分析再中文改写",
            refined_request="分析 tests 目录覆盖能力，然后改写成自然中文，不修改文件。",
            tasks=[
                PlannedTask(
                    id="analyze_tests",
                    title="分析 tests 目录",
                    instruction="只读分析 tests 目录覆盖的能力。",
                    skill_id="project_explorer",
                    model="deepseek_v4_flash_model",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                ),
                PlannedTask(
                    id="rewrite_summary",
                    title="改写中文结论",
                    instruction="把分析结论改写成自然中文，不修改文件。",
                    skill_id="humanizer_zh",
                    model="deepseek_v4_flash_model",
                    mcp=[],
                    parallel_group=2,
                    depends_on=["analyze_tests"],
                ),
            ],
            needs_synthesis=True,
            synthesis_instruction="汇总两步结果。",
        )

        decision = apply_pipeline_gate(plan, plan.refined_request)

        self.assertFalse(decision.needs_code_pipeline)
        self.assertFalse(decision.edit_intent)
        self.assertEqual(decision.applied_tasks, [])

    def test_gate_respects_readonly_tests_analysis_even_when_planner_mentions_tests(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import apply_pipeline_gate

        plan = PlannerResult(
            route_type="multi_agent",
            reason="先分析 tests 目录，再改写中文，不修改文件。",
            refined_request="分析 tests 目录覆盖能力，再改写为自然中文；不要修改任何文件，不要运行测试。",
            tasks=[
                PlannedTask(
                    id="test_coverage_analysis",
                    title="分析 tests 目录覆盖的测试能力",
                    instruction=(
                        "只通过读取测试文件分析 tests 目录覆盖了哪些能力。不要修改任何文件，不要运行测试。"
                    ),
                    skill_id="project_explorer",
                    model="deepseek_v4_flash_model",
                    mcp=["project_filesystem_readonly", "code_locator"],
                    parallel_group=1,
                ),
                PlannedTask(
                    id="humanize",
                    title="改写为自然中文",
                    instruction="基于前一步结论改写，不添加新事实。",
                    skill_id="humanizer_zh",
                    model="deepseek_v4_flash_model",
                    mcp=[],
                    parallel_group=2,
                    depends_on=["test_coverage_analysis"],
                ),
            ],
            needs_synthesis=True,
            synthesis_instruction="汇总两步结果。",
        )

        decision = apply_pipeline_gate(plan, plan.refined_request)

        self.assertFalse(decision.needs_code_pipeline)
        self.assertNotIn("workspace_edit", plan.tasks[0].mcp)
        self.assertNotIn("command_runner", plan.tasks[0].mcp)

    def test_dependency_context_is_added_to_later_task_prompt(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _dependency_context_for_task, _task_prompt

        task = PlannedTask(
            id="rewrite_summary",
            title="改写中文结论",
            instruction="把分析结论改写成自然中文。",
            skill_id="humanizer_zh",
            model="deepseek_v4_flash_model",
            depends_on=["analyze_tests"],
        )

        context = _dependency_context_for_task(task, {"analyze_tests": "tests 覆盖了 MCP、模型配置和流水线。"})
        prompt = _task_prompt("分析 tests 后改写", task.instruction, context)

        self.assertIn("前序任务输出", prompt)
        self.assertIn("analyze_tests", prompt)
        self.assertIn("MCP、模型配置和流水线", prompt)

    def test_latest_workspace_context_reports_changed_files(self):
        from planning.planner_schema import PlannedTask
        from runtime.execution.dynamic import _latest_workspace_context

        project_root = TEMP_ROOT / "workspace_context_project"
        _safe_rmtree(project_root)
        project_root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=project_root, check=True, capture_output=True, text=True)
        (project_root / "sample.py").write_text("print('hello')\n", encoding="utf-8")
        task = PlannedTask(
            id="inspect",
            title="分析最新状态",
            instruction="查看项目状态。",
            skill_id="project_explorer",
            model="local_model",
        )

        context = _latest_workspace_context(project_root, task)

        self.assertIn("最新项目状态", context)
        self.assertIn("sample.py", context)

    def test_task_prompt_includes_latest_workspace_context(self):
        from runtime.execution.dynamic import _task_prompt

        prompt = _task_prompt(
            "分析项目后修改",
            "继续执行下一步。",
            dependency_context="[inspect]\n已经分析 main.py。",
            workspace_context="最新项目状态：\n- main.py：已修改",
        )

        self.assertIn("前序任务输出", prompt)
        self.assertIn("最新项目状态", prompt)
        self.assertIn("main.py", prompt)


class PatchProposalLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger_dir = TEMP_ROOT / "patch_ledger"
        self.project_root = TEMP_ROOT / "patch_ledger_project"
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        (self.project_root / "target.py").write_text("print('alpha')\n", encoding="utf-8")

    def tearDown(self):
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def test_patch_proposal_ledger_records_expected_hashes(self):
        from planning.planner_schema import PlannedTask
        from runtime.workspace.patch_ledger import PatchProposalLedger

        task = PlannedTask(
            id="fix_target",
            title="修复目标文件",
            instruction="修改 target.py。",
            skill_id="jpc_now_skill",
            model="local_model",
            mcp=["workspace_edit"],
            write_intent=["target.py"],
        )
        ledger = PatchProposalLedger(self.project_root, self.ledger_dir)

        entry = ledger.record_proposal(task, "准备修改 target.py")

        self.assertEqual(entry["task_id"], "fix_target")
        self.assertEqual(entry["status"], "proposed")
        self.assertRegex(entry["expected_sha256"]["target.py"], r"^[a-f0-9]{64}$")
        lines = (self.ledger_dir / "patch_proposals.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["task_id"], "fix_target")

    def test_patch_proposal_ledger_records_completion_without_content_dump(self):
        from planning.planner_schema import PlannedTask
        from runtime.workspace.patch_ledger import PatchProposalLedger

        task = PlannedTask(
            id="fix_target",
            title="修复目标文件",
            instruction="修改 target.py。",
            skill_id="jpc_now_skill",
            model="local_model",
            mcp=["workspace_edit"],
            write_intent=["target.py"],
        )
        ledger = PatchProposalLedger(self.project_root, self.ledger_dir)

        ledger.record_proposal(task, "准备修改 target.py")
        entry = ledger.record_task_status(task.id, "completed", "完成修改。\n" + "x" * 3000)

        self.assertEqual(entry["status"], "completed")
        self.assertLessEqual(len(entry["output_preview"]), 900)
        self.assertNotIn("x" * 1000, entry["output_preview"])


class AgentFactorySoloTests(unittest.TestCase):
    def test_create_solo_agent_uses_distinct_name_and_tool_capable_instruction(self):
        from runtime.agents.factory import AgentFactory

        class FakeRegistry:
            def get_model(self, model_id):
                return f"fake-model:{model_id}"

            def get_model_info(self, model_id):
                return {"supports_tools": True, "model_name": "tool-capable"}

        agent = AgentFactory(FakeRegistry(), mcp_manager=None).create_solo_agent("model_a", mcp_servers=["fake-mcp"])

        self.assertEqual(agent.name, "solo_agent")
        self.assertIn("单模型工具 Agent", agent.instructions)
        self.assertIn("可以读写文件", agent.instructions)
        self.assertIn("不能创建多个 Agent", agent.instructions)
        self.assertIn("不要自动升级", agent.instructions)
        self.assertIn("不要主动提 serial/full", agent.instructions)
        self.assertIn("不要使用 emoji", agent.instructions)
        self.assertEqual(agent.mcp_servers, ["fake-mcp"])

    def test_create_solo_agent_refuses_toolless_model_when_tools_are_attached(self):
        from runtime.agents.factory import AgentFactory

        class FakeRegistry:
            def get_model(self, model_id):
                return f"fake-model:{model_id}"

            def get_model_info(self, model_id):
                return {"supports_tools": False, "model_name": "no-tools"}

        with self.assertRaises(ValueError):
            AgentFactory(FakeRegistry(), mcp_manager=None).create_solo_agent("model_a", mcp_servers=["fake-mcp"])

    def test_task_agent_only_mentions_tools_assigned_to_task(self):
        from planning.planner_schema import PlannedTask
        from runtime.agents.factory import AgentFactory

        task = PlannedTask(
            id="analyze_tests",
            title="分析测试覆盖",
            instruction="只读分析 tests/run_regression.py。",
            skill_id="project_explorer",
            model="local_model",
            mcp=["project_filesystem_readonly"],
        )

        instructions = AgentFactory(None, None)._task_instructions(task)

        self.assertIn("project_filesystem_readonly", instructions)
        self.assertIn("read_file", instructions)
        self.assertNotIn("command_runner", instructions)
        self.assertNotIn("workspace_edit", instructions)
        self.assertNotIn("web_search", instructions)

    def test_task_agent_readonly_budget_mentions_large_file_segmentation(self):
        from planning.planner_schema import PlannedTask
        from runtime.agents.factory import AgentFactory

        task = PlannedTask(
            id="analyze_large_file",
            title="分析大文件",
            instruction="只读分析 tests/run_regression.py 的能力覆盖，不修改文件。",
            skill_id="project_explorer",
            model="local_model",
            mcp=["code_locator", "project_filesystem_readonly"],
            read_set=["tests/run_regression.py"],
        )

        instructions = AgentFactory(None, None)._task_instructions(task)

        self.assertIn("如果目标文件过大", instructions)
        self.assertIn("先获取文件信息", instructions)
        self.assertIn("分段", instructions)


class SoloModeTests(unittest.TestCase):
    def test_solo_readonly_budget_is_harder_than_default_project_budget(self):
        from mcp_servers import create_readonly_filesystem_server
        from runtime.modes.solo import SOLO_READONLY_BUDGET_PROFILE

        default_server = create_readonly_filesystem_server(PROJECT_ROOT, "project_filesystem_readonly")
        solo_server = create_readonly_filesystem_server(
            PROJECT_ROOT,
            "project_filesystem_readonly",
            budget_profile=SOLO_READONLY_BUDGET_PROFILE,
        )

        default_env = default_server.params.env
        solo_env = solo_server.params.env

        self.assertLess(int(solo_env["BUDGETED_FS_MAX_READ_CALLS"]), int(default_env["BUDGETED_FS_MAX_READ_CALLS"]))
        self.assertLess(int(solo_env["BUDGETED_FS_MAX_TOTAL_CHARS"]), int(default_env["BUDGETED_FS_MAX_TOTAL_CHARS"]))
        self.assertLessEqual(
            int(solo_env["BUDGETED_FS_MAX_CHARS_PER_FILE"]),
            int(default_env["BUDGETED_FS_MAX_CHARS_PER_FILE"]),
        )

    def test_solo_chat_does_not_attach_mcp_tools(self):
        from runtime.modes.solo import _solo_mcp_ids_for_input
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings(privacy_mode="cloud_allowed")

        self.assertEqual(_solo_mcp_ids_for_input("你好，介绍一下你能做什么", settings), [])

    def test_solo_project_analysis_uses_readonly_and_locator_only(self):
        from runtime.modes.solo import _solo_mcp_ids_for_input
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings(privacy_mode="cloud_allowed")
        mcp_ids = _solo_mcp_ids_for_input("请分析 runtime/agent_factory.py 的职责，不要修改文件", settings)

        self.assertIn("project_filesystem_readonly", mcp_ids)
        self.assertIn("code_locator", mcp_ids)
        self.assertIn("git_tools", mcp_ids)
        self.assertNotIn("workspace_edit", mcp_ids)
        self.assertNotIn("command_runner", mcp_ids)

    def test_solo_readonly_request_with_tests_path_does_not_trigger_command_runner(self):
        from runtime.modes.solo import _solo_mcp_ids_for_input
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings(privacy_mode="cloud_allowed")
        mcp_ids = _solo_mcp_ids_for_input(
            "请阅读 tests/run_regression.py 里 RuntimeSettingsTests 覆盖了什么，不要运行测试也不要修改文件",
            settings,
        )

        self.assertIn("project_filesystem_readonly", mcp_ids)
        self.assertIn("code_locator", mcp_ids)
        self.assertIn("git_tools", mcp_ids)
        self.assertNotIn("workspace_edit", mcp_ids)
        self.assertNotIn("command_runner", mcp_ids)

    def test_solo_edit_and_test_request_gets_write_and_command_tools(self):
        from runtime.modes.solo import _solo_mcp_ids_for_input
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings(privacy_mode="cloud_allowed")
        mcp_ids = _solo_mcp_ids_for_input("帮我修改 main.py 并运行测试验证", settings)

        self.assertIn("workspace_edit", mcp_ids)
        self.assertIn("command_runner", mcp_ids)
        self.assertIn("git_tools", mcp_ids)

    def test_solo_offline_filters_web_search_tool(self):
        from runtime.modes.solo import _solo_mcp_ids_for_input
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings(privacy_mode="offline")
        mcp_ids = _solo_mcp_ids_for_input("联网搜索最新官方文档", settings)

        self.assertNotIn("web_search", mcp_ids)


class PlannerSchemaN3Tests(unittest.TestCase):
    def test_parse_planner_result_supports_task_execution_contract_fields(self):
        from planning.planner_schema import parse_planner_result

        payload = {
            "route_type": "multi_agent",
            "reason": "需要先分析再修改",
            "refined_request": "修复项目中的配置展示问题",
            "tasks": [
                {
                    "id": "inspect",
                    "title": "定位配置展示逻辑",
                    "instruction": "找出配置展示相关代码。",
                    "skill_id": "project_explorer",
                    "model": "local_model",
                    "mcp": ["code_locator"],
                    "parallel_group": 1,
                    "acceptance_criteria": ["指出相关文件和函数"],
                    "expected_outputs": ["配置展示逻辑位置说明"],
                    "read_set": ["runtime/cli_config.py"],
                    "write_intent": [],
                },
                {
                    "id": "fix",
                    "title": "修复配置展示逻辑",
                    "instruction": "基于 inspect 的结论修改代码。",
                    "skill_id": "jpc_now_skill",
                    "model": "local_model",
                    "mcp": ["workspace_edit"],
                    "parallel_group": 2,
                    "depends_on": ["inspect"],
                    "acceptance_criteria": ["新增模型配置能显示中文卡片"],
                    "expected_outputs": ["runtime/cli_config.py 的小范围修改"],
                    "read_set": ["runtime/cli_config.py"],
                    "write_intent": ["runtime/cli_config.py"],
                },
            ],
            "needs_synthesis": True,
            "synthesis_instruction": "汇总分析和修改结果。",
        }

        plan = parse_planner_result(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(plan.tasks[0].acceptance_criteria, ["指出相关文件和函数"])
        self.assertEqual(plan.tasks[1].depends_on, ["inspect"])
        self.assertEqual(plan.tasks[1].expected_outputs, ["runtime/cli_config.py 的小范围修改"])
        self.assertEqual(plan.tasks[1].read_set, ["runtime/cli_config.py"])
        self.assertEqual(plan.tasks[1].write_intent, ["runtime/cli_config.py"])

    def test_multi_agent_missing_synthesis_is_normalized(self):
        from planning.planner_schema import parse_planner_result

        payload = {
            "route_type": "multi_agent",
            "reason": "先分析再改写",
            "refined_request": "分析 tests 目录，再改写成自然中文。",
            "tasks": [
                {
                    "id": "analyze_tests",
                    "title": "分析 tests",
                    "instruction": "分析 tests。",
                    "skill_id": "project_explorer",
                    "model": "deepseek_v4_flash_model",
                    "mcp": ["project_filesystem_readonly"],
                    "parallel_group": 1,
                },
                {
                    "id": "rewrite",
                    "title": "改写结论",
                    "instruction": "改写上一步结论。",
                    "skill_id": "humanizer_zh",
                    "model": "deepseek_v4_flash_model",
                    "mcp": [],
                    "parallel_group": 2,
                    "depends_on": ["analyze_tests"],
                },
            ],
            "needs_synthesis": False,
            "synthesis_instruction": "",
        }

        plan = parse_planner_result(json.dumps(payload, ensure_ascii=False))

        self.assertTrue(plan.needs_synthesis)
        self.assertIn("最终", plan.synthesis_instruction)

    def test_legacy_model_names_are_normalized_to_dynamic_catalog_ids(self):
        from planning.planner_schema import parse_planner_result

        payload = {
            "route_type": "single_agent",
            "reason": "代码任务",
            "refined_request": "检查测试目录",
            "tasks": [
                {
                    "id": "inspect_tests",
                    "title": "检查 tests",
                    "instruction": "分析 tests 目录。",
                    "skill_id": "jpc_now_skill",
                    "model": "mimo_model",
                    "mcp": ["project_filesystem_readonly", "code_locator"],
                }
            ],
            "needs_synthesis": False,
        }

        fake_catalog = {
            "models": [
                {
                    "id": "mimo_v25_model",
                    "configured": True,
                    "is_local": False,
                    "supports_tools": True,
                    "best_for_skills": ["jpc_now_skill"],
                    "reasoning_level": "medium",
                    "cost_level": "medium",
                    "model_tier": "medium",
                },
                {
                    "id": "mimo_v25_pro_model",
                    "configured": True,
                    "is_local": False,
                    "supports_tools": True,
                    "best_for_skills": ["jpc_now_skill"],
                    "reasoning_level": "medium",
                    "cost_level": "medium",
                    "model_tier": "medium",
                },
            ]
        }
        with patch("catalog_system.model_catalog.load_model_catalog", return_value=fake_catalog):
            plan = parse_planner_result(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(plan.tasks[0].model, "mimo_v25_pro_model")

    def test_task_contract_fields_default_to_empty_lists(self):
        from planning.planner_schema import PlannedTask

        task = PlannedTask(
            id="simple",
            title="Simple",
            instruction="Explain.",
            skill_id="project_explorer",
            model="local_model",
        )

        self.assertEqual(task.depends_on, [])
        self.assertEqual(task.acceptance_criteria, [])
        self.assertEqual(task.expected_outputs, [])
        self.assertEqual(task.read_set, [])
        self.assertEqual(task.write_intent, [])

    def test_direct_answer_with_explicit_web_search_is_normalized_to_web_search_task(self):
        from planning.planner_schema import parse_planner_result

        payload = {
            "route_type": "direct_answer",
            "reason": "模型误判为可直接回答",
            "refined_request": "请联网找一下 OpenAI Agents SDK MCP 官方链接，只返回链接。",
            "direct_answer_instruction": "直接解释 MCP。",
            "tasks": [],
            "needs_synthesis": False,
        }

        plan = parse_planner_result(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(plan.route_type, "single_agent")
        self.assertEqual(plan.tasks[0].skill_id, "project_explorer")
        self.assertEqual(plan.tasks[0].mcp, ["web_search"])
        self.assertIn("只返回链接", plan.tasks[0].instruction)

    def test_simple_capability_question_does_not_trigger_web_search_fallback(self):
        from planning.planner_schema import parse_planner_result

        payload = {
            "route_type": "direct_answer",
            "reason": "用户只是询问系统能力，可以直接回答。",
            "refined_request": "请介绍你所具备的功能和技能。",
            "direct_answer_instruction": "用简洁中文介绍当前系统能力，可以提到项目分析、代码修改、运行测试和联网搜索能力。",
            "tasks": [],
            "needs_synthesis": False,
        }

        plan = parse_planner_result(
            json.dumps(payload, ensure_ascii=False),
            fallback_user_input="\n".join(
                [
                    "原始用户输入：你好你有什么技能",
                    "refiner_raw_user_input：你好你有什么技能",
                    "refined_request：请介绍你所具备的功能和技能。",
                    "explicit_constraints：[]",
                ]
            ),
        )

        self.assertEqual(plan.route_type, "direct_answer")
        self.assertEqual(plan.tasks, [])
        self.assertNotIn("web_search", plan.reason)

    def test_english_capability_question_does_not_trigger_web_search_fallback(self):
        from planning.planner_schema import parse_planner_result

        payload = {
            "route_type": "direct_answer",
            "reason": "Simple capability question.",
            "refined_request": "hello, what can you do?",
            "direct_answer_instruction": "Briefly explain capabilities, including code help and web search when explicitly requested.",
            "tasks": [],
            "needs_synthesis": False,
        }

        plan = parse_planner_result(
            json.dumps(payload, ensure_ascii=False),
            fallback_user_input="Original user input: hello, what can you do?",
        )

        self.assertEqual(plan.route_type, "direct_answer")
        self.assertEqual(plan.tasks, [])

    def test_web_search_intent_survives_refiner_losing_original_constraints(self):
        from planning.planner_schema import parse_planner_result

        payload = {
            "route_type": "direct_answer",
            "reason": "优化后像概念解释题",
            "refined_request": "如何使用 OpenAI Agents SDK 中的 MCP 功能？",
            "direct_answer_instruction": "解释 MCP 使用方法。",
            "tasks": [],
            "needs_synthesis": False,
        }

        plan = parse_planner_result(
            json.dumps(payload, ensure_ascii=False),
            fallback_user_input="请联网找一下 OpenAI Agents SDK MCP 文档的官方链接，只返回链接，不改文件",
        )

        self.assertEqual(plan.route_type, "single_agent")
        self.assertEqual(plan.tasks[0].mcp, ["web_search"])
        self.assertIn("官方链接", plan.tasks[0].instruction)

    def test_web_search_task_preserves_only_links_and_no_edit_constraints(self):
        from planning.planner_schema import parse_planner_result

        payload = {
            "route_type": "single_agent",
            "reason": "需要联网搜索",
            "refined_request": "解释 OpenAI Agents SDK MCP。",
            "tasks": [
                {
                    "id": "search",
                    "title": "搜索并解释",
                    "instruction": "搜索官方资料并总结。",
                    "skill_id": "project_explorer",
                    "model": "deepseek_v4_flash_model",
                    "mcp": ["web_search", "workspace_edit"],
                    "write_intent": ["notes.md"],
                }
            ],
            "needs_synthesis": False,
        }

        plan = parse_planner_result(
            json.dumps(payload, ensure_ascii=False),
            fallback_user_input="请联网找一下 OpenAI Agents SDK MCP 文档的官方链接，只返回链接，不改文件",
        )

        self.assertEqual(plan.tasks[0].mcp, ["web_search"])
        self.assertEqual(plan.tasks[0].write_intent, [])
        self.assertIn("只返回链接", plan.tasks[0].instruction)
        self.assertIn("只返回链接", plan.tasks[0].acceptance_criteria)
        self.assertIn("纯链接文本", plan.tasks[0].expected_outputs)

    def test_validation_rejects_write_intent_without_workspace_edit(self):
        from planning.plan_validator import validate_plan
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.safety.privacy import PrivacyPolicy

        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434"
        os.environ["MODEL_LOCAL_MODEL"] = "qwen3:8b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"

        plan = PlannerResult(
            route_type="single_agent",
            reason="非法写入计划",
            refined_request="修改配置展示",
            tasks=[
                PlannedTask(
                    id="fix",
                    title="修复配置",
                    instruction="修改 runtime/cli_config.py。",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    mcp=["project_filesystem_readonly"],
                    write_intent=["runtime/cli_config.py"],
                    acceptance_criteria=["配置展示正常"],
                )
            ],
        )

        validation = validate_plan(plan, privacy_policy=PrivacyPolicy.from_env())
        self.assertFalse(validation.valid)
        self.assertTrue(any("workspace_edit" in error for error in validation.errors))

    def test_execution_plan_format_includes_dependency_and_acceptance(self):
        from planning.plan_validator import PlanValidation
        from planning.planner import format_execution_plan
        from planning.planner_schema import PlannedTask, PlannerResult, RefinedRequest

        refined = RefinedRequest(raw_user_input="修复配置", refined_request="修复配置展示")
        plan = PlannerResult(
            route_type="single_agent",
            reason="单任务修复",
            refined_request="修复配置展示",
            tasks=[
                PlannedTask(
                    id="fix",
                    title="修复配置",
                    instruction="修改 runtime/cli_config.py。",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    mcp=["workspace_edit"],
                    depends_on=["inspect"],
                    acceptance_criteria=["配置展示正常"],
                    write_intent=["runtime/cli_config.py"],
                )
            ],
        )

        text = format_execution_plan(refined, plan, PlanValidation(valid=True))
        self.assertIn("依赖：inspect", text)
        self.assertIn("验收：配置展示正常", text)
        self.assertIn("写入意图：runtime/cli_config.py", text)


class PlanReviewerTests(unittest.TestCase):
    def test_reviewer_warns_and_serializes_conflicting_parallel_write_intents(self):
        from planning.plan_reviewer import review_plan
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.dynamic import _execution_batches_for_group

        plan = PlannerResult(
            route_type="multi_agent",
            reason="两个任务都要修改同一文件",
            refined_request="并行修复同一个文件",
            tasks=[
                PlannedTask(
                    id="front",
                    title="修改展示",
                    instruction="修改 runtime/cli_config.py。",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    mcp=["workspace_edit"],
                    parallel_group=1,
                    acceptance_criteria=["展示正常"],
                    write_intent=["runtime/cli_config.py"],
                ),
                PlannedTask(
                    id="backend",
                    title="修改配置",
                    instruction="也修改 runtime/cli_config.py。",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    mcp=["workspace_edit"],
                    parallel_group=1,
                    acceptance_criteria=["配置正常"],
                    write_intent=["runtime/cli_config.py"],
                ),
            ],
            needs_synthesis=True,
            synthesis_instruction="汇总结果。",
        )

        review = review_plan(plan)
        batches = _execution_batches_for_group(plan.tasks)

        self.assertTrue(review.approved, review.issues)
        self.assertTrue(any("同一并行组" in warning for warning in review.warnings))
        self.assertTrue(any("runtime/cli_config.py" in warning for warning in review.warnings))
        self.assertEqual([[task.id for task in batch] for batch in batches], [["front"], ["backend"]])

    def test_reviewer_rejects_unknown_dependency_and_cycle(self):
        from planning.plan_reviewer import review_plan
        from planning.planner_schema import PlannedTask, PlannerResult

        plan = PlannerResult(
            route_type="multi_agent",
            reason="依赖异常",
            refined_request="修复项目",
            tasks=[
                PlannedTask(
                    id="a",
                    title="A",
                    instruction="A",
                    skill_id="project_explorer",
                    model="local_model",
                    depends_on=["missing", "b"],
                    acceptance_criteria=["A 完成"],
                ),
                PlannedTask(
                    id="b",
                    title="B",
                    instruction="B",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    depends_on=["a"],
                    acceptance_criteria=["B 完成"],
                ),
            ],
            needs_synthesis=True,
            synthesis_instruction="汇总结果。",
        )

        review = review_plan(plan)

        self.assertFalse(review.approved)
        self.assertTrue(any("未知依赖" in issue for issue in review.issues))
        self.assertTrue(any("循环依赖" in issue for issue in review.issues))

    def test_reviewer_approves_ordered_plan_and_warns_missing_contract(self):
        from planning.plan_reviewer import review_plan
        from planning.planner_schema import PlannedTask, PlannerResult

        plan = PlannerResult(
            route_type="single_agent",
            reason="单任务",
            refined_request="分析项目结构",
            tasks=[
                PlannedTask(
                    id="inspect",
                    title="分析项目",
                    instruction="读取项目结构并说明。",
                    skill_id="project_explorer",
                    model="local_model",
                    mcp=["project_filesystem_readonly"],
                )
            ],
        )

        review = review_plan(plan)

        self.assertTrue(review.approved, review.issues)
        self.assertTrue(any("acceptance_criteria" in warning for warning in review.warnings))

    def test_reviewer_failure_can_be_converted_to_replan_audit(self):
        from planning.plan_reviewer import review_plan
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.safety.auditor import audit_plan_review_failure

        plan = PlannerResult(
            route_type="multi_agent",
            reason="冲突计划",
            refined_request="并行修改同一文件",
            tasks=[
                PlannedTask(
                    id="a",
                    title="A",
                    instruction="A",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    parallel_group=1,
                    depends_on=["missing"],
                ),
                PlannedTask(
                    id="b",
                    title="B",
                    instruction="B",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    parallel_group=1,
                ),
            ],
            needs_synthesis=True,
            synthesis_instruction="汇总。",
        )

        audit = audit_plan_review_failure(review_plan(plan))

        self.assertFalse(audit.passed)
        self.assertTrue(audit.needs_replan)
        self.assertTrue(any("未知依赖" in issue for issue in audit.remaining_issues))


class AuditorLoopTests(unittest.TestCase):
    def test_final_auditor_passes_completed_plan_and_formats_report(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.safety.auditor import audit_execution, format_final_report
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="single_agent",
            reason="修复代码",
            refined_request="修复配置展示",
            tasks=[
                PlannedTask(
                    id="fix",
                    title="修复配置展示",
                    instruction="修复展示。",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    mcp=["workspace_edit"],
                    acceptance_criteria=["配置展示正常"],
                    expected_outputs=["runtime/cli_config.py 更新"],
                )
            ],
        )
        state = PipelineRunState.create("修复配置展示", plan)
        state.record_task_result(plan.tasks[0], "已修改 runtime/cli_config.py")
        state.record_verification("fix", "Verifier 校验摘要：git diff 已检查")

        audit = audit_execution(plan, state, "已完成。")
        report = format_final_report("已完成。", audit)

        self.assertTrue(audit.passed)
        self.assertEqual(audit.remaining_issues, [])
        self.assertIn("最终审核：通过", report)
        self.assertIn("修改内容", report)
        self.assertIn("验证情况", report)

    def test_final_auditor_returns_remaining_issues_for_failed_tasks(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.safety.auditor import audit_execution
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="single_agent",
            reason="修复代码",
            refined_request="修复配置展示",
            tasks=[
                PlannedTask(
                    id="fix",
                    title="修复配置展示",
                    instruction="修复展示。",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    mcp=["workspace_edit"],
                    acceptance_criteria=["配置展示正常"],
                )
            ],
        )
        state = PipelineRunState.create("修复配置展示", plan)
        state.record_task_error(plan.tasks[0], "工具调用失败")

        audit = audit_execution(plan, state, "失败。")

        self.assertFalse(audit.passed)
        self.assertTrue(any("fix" in issue for issue in audit.remaining_issues))
        self.assertTrue(audit.needs_replan)

    def test_repair_loop_caps_attempts_and_builds_replan_context(self):
        from runtime.safety.auditor import AuditResult
        from runtime.safety.repair_loop import build_repair_request, repair_strategy_for_audit, should_retry

        audit = AuditResult(
            passed=False,
            summary="还有问题",
            remaining_issues=["测试未通过", "缺少验证"],
            needs_replan=True,
        )

        self.assertTrue(should_retry(attempt=1, max_attempts=3, audit=audit))
        self.assertTrue(should_retry(attempt=2, max_attempts=3, audit=audit))
        self.assertFalse(should_retry(attempt=3, max_attempts=3, audit=audit))

        prompt = build_repair_request("修复配置展示", audit, attempt=2)
        self.assertIn("第 2 轮", prompt)
        self.assertIn("测试未通过", prompt)
        self.assertIn("避免重复完全相同的方法", prompt)


    def test_repair_strategy_for_verification_failure(self):
        from runtime.safety.auditor import AuditResult
        from runtime.safety.repair_loop import repair_strategy_for_audit

        audit = AuditResult(
            passed=False,
            summary="still failing",
            remaining_issues=["verification command failed", "missing verification"],
            needs_replan=True,
        )

        strategy = repair_strategy_for_audit(audit)

        self.assertEqual(strategy["type"], "verification_failed")
        self.assertIn("run verification", strategy["instruction"].lower())

    def test_auditor_checks_expected_outputs_and_strict_acceptance_markers(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.safety.auditor import audit_execution
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="single_agent",
            reason="fix code",
            refined_request="fix output",
            tasks=[
                PlannedTask(
                    id="fix",
                    title="Fix output",
                    instruction="Fix output.",
                    skill_id="jpc_now_skill",
                    model="local_model",
                    mcp=["workspace_edit"],
                    acceptance_criteria=["must_contain: OK_DONE"],
                    expected_outputs=["must_contain: runtime/cli_config.py"],
                )
            ],
        )
        state = PipelineRunState.create("fix output", plan)
        state.record_task_result(plan.tasks[0], "changed another file")
        state.record_verification("fix", "returncode=0")

        audit = audit_execution(plan, state, "finished without marker")

        self.assertFalse(audit.passed)
        self.assertTrue(any("预期输出未出现" in issue for issue in audit.remaining_issues))
        self.assertTrue(any("验收标记未出现" in issue for issue in audit.remaining_issues))

    def test_auditor_semantic_acceptance_passes_when_core_concepts_are_covered(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.safety.auditor import audit_execution
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="single_agent",
            reason="analyze runtime",
            refined_request="说明 runtime 目录结构",
            tasks=[
                PlannedTask(
                    id="inspect_runtime",
                    title="说明 runtime 目录结构",
                    instruction="分析 runtime 目录结构。",
                    skill_id="project_explorer",
                    model="local_model",
                    mcp=["project_filesystem_readonly"],
                    acceptance_criteria=["说明 runtime 目录结构，覆盖 agents、modes、safety 三类职责"],
                    expected_outputs=["自然中文总结 runtime 目录中 agents、modes、safety 的用途"],
                )
            ],
        )
        state = PipelineRunState.create(plan.refined_request, plan)
        state.record_task_result(
            plan.tasks[0],
            "runtime 目录按职责拆分：agents 负责 Agent 创建，modes 负责 solo/serial/full 模式入口，"
            "safety 负责隐私、审计、checkpoint 和修复循环。",
        )

        audit = audit_execution(plan, state, "已经用自然中文总结 runtime 目录结构。")

        self.assertTrue(audit.passed, audit.remaining_issues)

    def test_auditor_semantic_acceptance_fails_when_required_concept_is_missing(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.safety.auditor import audit_execution
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="single_agent",
            reason="analyze runtime",
            refined_request="说明 runtime 目录结构",
            tasks=[
                PlannedTask(
                    id="inspect_runtime",
                    title="说明 runtime 目录结构",
                    instruction="分析 runtime 目录结构。",
                    skill_id="project_explorer",
                    model="local_model",
                    mcp=["project_filesystem_readonly"],
                    acceptance_criteria=["说明 runtime 目录结构，覆盖 agents、modes、safety 三类职责"],
                )
            ],
        )
        state = PipelineRunState.create(plan.refined_request, plan)
        state.record_task_result(plan.tasks[0], "runtime 目录里有一些 Python 文件。")

        audit = audit_execution(plan, state, "只做了非常笼统的说明。")

        self.assertFalse(audit.passed)
        self.assertTrue(any("语义验收未满足" in issue for issue in audit.remaining_issues))


class CheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.repo = TEMP_ROOT / f"checkpoint_repo_{uuid.uuid4().hex[:8]}"
        self.repo.mkdir(parents=True, exist_ok=True)
        self._git(["init"])
        (self.repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
        self._git(["add", "app.py"])
        self._git(["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"])

    def tearDown(self):
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def _git(self, args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )

    def test_clean_git_checkpoint_rolls_back_agent_changes(self):
        from runtime.safety.checkpoint import create_checkpoint, rollback_checkpoint

        checkpoint = create_checkpoint(self.repo)
        self.assertTrue(checkpoint.can_rollback)

        (self.repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
        (self.repo / "new_file.py").write_text("temporary\n", encoding="utf-8")

        result = rollback_checkpoint(checkpoint)

        self.assertTrue(result.rolled_back)
        self.assertEqual((self.repo / "app.py").read_text(encoding="utf-8"), "print('ok')\n")
        self.assertFalse((self.repo / "new_file.py").exists())

    def test_dirty_git_checkpoint_refuses_rollback_to_protect_user_changes(self):
        from runtime.safety.checkpoint import create_checkpoint, rollback_checkpoint

        (self.repo / "app.py").write_text("print('user dirty change')\n", encoding="utf-8")
        checkpoint = create_checkpoint(self.repo)
        result = rollback_checkpoint(checkpoint)

        self.assertFalse(checkpoint.can_rollback)
        self.assertFalse(result.rolled_back)
        self.assertIn("已有未提交改动", result.message)
        self.assertEqual((self.repo / "app.py").read_text(encoding="utf-8"), "print('user dirty change')\n")

    def test_scoped_checkpoint_rolls_back_only_agent_touched_files_in_dirty_workspace(self):
        from runtime.safety.checkpoint import create_checkpoint, rollback_checkpoint

        (self.repo / "user_notes.md").write_text("user draft\n", encoding="utf-8")
        checkpoint = create_checkpoint(self.repo, scoped_paths=["app.py", "agent_new.py"])

        (self.repo / "app.py").write_text("print('agent changed')\n", encoding="utf-8")
        (self.repo / "agent_new.py").write_text("temporary\n", encoding="utf-8")

        result = rollback_checkpoint(checkpoint)

        self.assertTrue(result.rolled_back, result.message)
        self.assertEqual((self.repo / "app.py").read_text(encoding="utf-8"), "print('ok')\n")
        self.assertFalse((self.repo / "agent_new.py").exists())
        self.assertEqual((self.repo / "user_notes.md").read_text(encoding="utf-8"), "user draft\n")

    def test_scoped_checkpoint_refuses_same_file_dirty_user_change(self):
        from runtime.safety.checkpoint import create_checkpoint, rollback_checkpoint

        (self.repo / "app.py").write_text("print('user dirty change')\n", encoding="utf-8")
        checkpoint = create_checkpoint(self.repo, scoped_paths=["app.py"])
        result = rollback_checkpoint(checkpoint)

        self.assertFalse(result.rolled_back)
        self.assertIn("同一文件", result.message)
        self.assertEqual((self.repo / "app.py").read_text(encoding="utf-8"), "print('user dirty change')\n")

    def test_session_checkpoint_rolls_back_last_turn_once(self):
        from runtime.safety.session_checkpoint import SessionCheckpointManager

        manager = SessionCheckpointManager(self.repo)
        manager.begin_turn()
        (self.repo / "app.py").write_text("print('agent changed')\n", encoding="utf-8")
        manager.complete_turn()

        status_before = manager.render_status()
        result = manager.rollback_last_turn()
        status_after = manager.render_status()

        self.assertIn("可回滚", status_before)
        self.assertTrue(result.rolled_back, result.message)
        self.assertIn("没有可回滚", status_after)
        self.assertEqual((self.repo / "app.py").read_text(encoding="utf-8"), "print('ok')\n")

    def test_session_checkpoint_refuses_when_no_turn_checkpoint_exists(self):
        from runtime.safety.session_checkpoint import SessionCheckpointManager

        manager = SessionCheckpointManager(self.repo)
        result = manager.rollback_last_turn()

        self.assertFalse(result.rolled_back)
        self.assertIn("没有可回滚", result.message)


class FailureMemoryTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = TEMP_ROOT / f"failure_memory_{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def test_failure_case_is_recorded_with_redacted_metadata(self):
        from runtime.memory.flywheel import FlywheelStore

        store = FlywheelStore(PROJECT_ROOT, cache_dir=self.cache_dir)
        entry = store.record_failure_case(
            user_request="修复配置展示",
            attempt_count=3,
            models_used=["local_model"],
            files_touched=["runtime/cli_config.py"],
            failure_reasons=["API key sk-secret1234567890 泄漏风险"],
            rollback_status="rolled_back",
            lesson="本地弱模型需要更明确的验收标准。",
        )

        self.assertEqual(entry["kind"], "failure_case")
        self.assertIn("failure", entry["tags"])
        self.assertNotIn("sk-secret1234567890", json.dumps(entry, ensure_ascii=False))
        self.assertEqual(entry["metadata"]["attempt_count"], 3)


class CatalogRefreshTests(unittest.TestCase):
    def test_skill_catalog_discovers_added_skill_folder(self):
        from catalog_system.refresher import build_skill_catalog

        project_root = TEMP_ROOT / f"skill_catalog_{uuid.uuid4().hex}"
        skill_dir = project_root / "skills" / "demo-extra-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo-extra-skill\ndescription: 临时动态 skill。\n---\n\n# Demo\n",
            encoding="utf-8",
        )

        catalog = build_skill_catalog(project_root)
        ids = {item["id"] for item in catalog["skills"]}

        self.assertIn("demo_extra_skill", ids)

    def test_mcp_catalog_discovers_unknown_mcp_file_as_pending(self):
        from catalog_system.refresher import build_mcp_catalog

        project_root = TEMP_ROOT / f"mcp_catalog_{uuid.uuid4().hex}"
        mcp_dir = project_root / "mcp_servers"
        mcp_dir.mkdir(parents=True)
        (mcp_dir / "demo_extra_mcp.py").write_text("def ping():\n    return 'pong'\n", encoding="utf-8")

        catalog = build_mcp_catalog(project_root)
        by_id = {item["id"]: item for item in catalog["mcp_servers"]}

        self.assertIn("demo_extra", by_id)
        self.assertFalse(by_id["demo_extra"]["implemented"])
        self.assertIn("待登记", by_id["demo_extra"]["summary_zh"])

    def test_mcp_catalog_discovers_nested_unknown_mcp_file_without_duplicate_known_entries(self):
        from catalog_system.refresher import build_mcp_catalog

        project_root = TEMP_ROOT / f"nested_mcp_catalog_{uuid.uuid4().hex}"
        mcp_root = project_root / "mcp_servers"
        readonly_dir = mcp_root / "readonly"
        execution_dir = mcp_root / "execution"
        mutation_dir = mcp_root / "mutation"
        readonly_dir.mkdir(parents=True)
        execution_dir.mkdir(parents=True)
        mutation_dir.mkdir(parents=True)
        (readonly_dir / "demo_nested_mcp.py").write_text("def ping():\n    return 'pong'\n", encoding="utf-8")
        (readonly_dir / "budgeted_filesystem_mcp.py").write_text("def read_file():\n    return ''\n", encoding="utf-8")
        (execution_dir / "command_mcp.py").write_text("def run_command():\n    return ''\n", encoding="utf-8")
        (execution_dir / "git_mcp.py").write_text("def git_status():\n    return ''\n", encoding="utf-8")
        (mutation_dir / "safe_delete_mcp.py").write_text("def safe_delete_file():\n    return ''\n", encoding="utf-8")

        catalog = build_mcp_catalog(project_root)
        pending_ids = {
            item["id"]
            for item in catalog["mcp_servers"]
            if not item.get("implemented")
        }

        self.assertIn("demo_nested", pending_ids)
        self.assertNotIn("budgeted_filesystem", pending_ids)
        self.assertNotIn("command", pending_ids)
        self.assertNotIn("git", pending_ids)
        self.assertNotIn("safe_delete", pending_ids)


class ModelCapabilityTests(unittest.TestCase):
    def test_model_tier_is_detected_from_common_local_model_names(self):
        from runtime.agents.model_capability import detect_model_tier, strategy_for_model_name

        self.assertEqual(detect_model_tier("qwen3:8b").value, "small")
        self.assertEqual(detect_model_tier("qwen3:14b").value, "medium")
        self.assertEqual(detect_model_tier("qwen3:72b").value, "large")
        self.assertTrue(strategy_for_model_name("qwen3:8b").force_plan_before_edit)
        self.assertGreater(
            strategy_for_model_name("qwen3:72b").max_files_per_task,
            strategy_for_model_name("qwen3:8b").max_files_per_task,
        )

    def test_catalog_and_gate_apply_model_capability_strategy(self):
        from catalog_system.model_catalog import load_model_catalog
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import apply_pipeline_gate

        os.environ["MODEL_SMALL_LOCAL_BASE_URL"] = "http://localhost:11434"
        os.environ["MODEL_SMALL_LOCAL_MODEL"] = "qwen3:8b"
        os.environ["MODEL_SMALL_LOCAL_BACKEND"] = "ollama"
        try:
            catalog = load_model_catalog()
            local = next(item for item in catalog["models"] if item["id"] == "small_local_model")
            self.assertEqual(local["model_tier"], "small")
            self.assertEqual(local["execution_strategy"]["max_files_per_task"], 2)

            plan = PlannerResult(
                route_type="single_agent",
                reason="fix code",
                refined_request="fix parser bug",
                tasks=[
                    PlannedTask(
                        id="fix_parser",
                        title="Fix parser bug",
                        instruction="Fix the parser bug.",
                        skill_id="jpc_now_skill",
                        model="small_local_model",
                        mcp=[],
                    )
                ],
            )
            apply_pipeline_gate(plan, plan.refined_request)
            task = plan.tasks[0]
            self.assertIn("最多读取 2 个核心文件", task.instruction)
            self.assertIn("小模型策略", task.risk_notes)
        finally:
            for key in [
                "MODEL_SMALL_LOCAL_BASE_URL",
                "MODEL_SMALL_LOCAL_MODEL",
                "MODEL_SMALL_LOCAL_BACKEND",
            ]:
                os.environ.pop(key, None)


class FlywheelTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = TEMP_ROOT / "flywheel_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def test_flywheel_store_redacts_and_searches_entries(self):
        from runtime.memory.flywheel import FlywheelStore

        store = FlywheelStore(PROJECT_ROOT, cache_dir=self.cache_dir)
        store.append_entry(
            kind="lesson",
            summary="Git MCP failure was fixed. secret sk-test1234567890 should not leak.",
            tags=["git", "mcp"],
            source="regression",
        )

        matches = store.search("git status MCP failure", limit=3)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["kind"], "lesson")
        self.assertIn("[REDACTED_SECRET]", matches[0]["summary"])
        self.assertNotIn("sk-test", matches[0]["summary"])

    def test_flywheel_records_pipeline_state_summary(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.memory.flywheel import FlywheelStore
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="single_agent",
            reason="fix code",
            refined_request="修复 git MCP 报错",
            tasks=[
                PlannedTask(
                    id="fix_git",
                    title="Fix git MCP",
                    instruction="Fix git status failure.",
                    skill_id="jpc_now_skill",
                    model="mimo_model",
                    mcp=["git_tools"],
                )
            ],
        )
        state = PipelineRunState.create("修复 git MCP 报错", plan)
        state.record_task_result(plan.tasks[0], "git status now works")

        store = FlywheelStore(PROJECT_ROOT, cache_dir=self.cache_dir)
        entry = store.record_pipeline_state(state)

        self.assertEqual(entry["kind"], "pipeline_summary")
        self.assertIn("single_agent", entry["summary"])
        self.assertIn("jpc_now_skill", entry["tags"])


class RuntimeSettingsTests(unittest.TestCase):
    def tearDown(self):
        for key in [
            "AGENTS_QUERY_REFINER_ENABLED",
            "AGENTS_QUERY_REFINER_MODEL_PRIORITY",
            "AGENTS_ORCHESTRATOR_MODEL_PRIORITY",
            "AGENTS_FINAL_SYNTHESIZER_MODEL_PRIORITY",
            "AGENTS_PRIVACY_MODE",
            "AGENTS_OFFLINE_NETWORK_MCP_POLICY",
            "AGENTS_EXECUTION_MODE",
        ]:
            os.environ.pop(key, None)

    def test_default_role_priorities_are_derived_from_registered_models(self):
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "configured": True,
                    "is_local": True,
                    "supports_tools": False,
                    "reasoning_level": "medium",
                    "cost_level": "low",
                    "best_for_skills": [],
                    "model_tier": "small",
                },
                {
                    "id": "deepseek_v4_flash_model",
                    "configured": True,
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "medium",
                    "cost_level": "low",
                    "best_for_skills": ["project_explorer", "humanizer_zh"],
                    "model_tier": "medium",
                },
                {
                    "id": "deepseek_v4_pro_model",
                    "configured": True,
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "high",
                    "cost_level": "high",
                    "best_for_skills": ["orchestrator_planner", "final_synthesizer"],
                    "model_tier": "large",
                },
                {
                    "id": "mimo_v25_pro_model",
                    "configured": True,
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "medium",
                    "cost_level": "medium",
                    "best_for_skills": ["jpc_now_skill"],
                    "model_tier": "medium",
                },
                {
                    "id": "legacy_unconfigured_model",
                    "configured": False,
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "high",
                    "cost_level": "high",
                    "best_for_skills": ["orchestrator_planner"],
                    "model_tier": "large",
                },
            ]
        }

        with patch("runtime.config.settings.load_model_catalog", return_value=fake_catalog):
            settings = RuntimeSettings.from_env()

        all_priority_ids = (
            settings.query_refiner_model_priority
            + settings.orchestrator_model_priority
            + settings.final_synthesizer_model_priority
        )
        self.assertNotIn("deepseek_V4_flash_model", all_priority_ids)
        self.assertNotIn("deepseek_V4_pro_model", all_priority_ids)
        self.assertNotIn("mimo_model", all_priority_ids)
        self.assertNotIn("legacy_unconfigured_model", all_priority_ids)
        self.assertIn("deepseek_v4_flash_model", settings.query_refiner_model_priority)
        self.assertEqual(settings.orchestrator_model_priority[0], "deepseek_v4_pro_model")
        self.assertIn("mimo_v25_pro_model", settings.final_synthesizer_model_priority)

    def test_default_role_priorities_fall_back_to_registered_local_model_only(self):
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "configured": True,
                    "is_local": True,
                    "supports_tools": False,
                    "reasoning_level": "medium",
                    "cost_level": "low",
                    "best_for_skills": [],
                    "model_tier": "small",
                    "probe": {
                        "status": "ok",
                        "supports_basic_chat": True,
                        "supports_tools": True,
                        "planner_suitable": True,
                        "execution_suitable": True,
                    },
                }
            ]
        }

        with patch("runtime.config.settings.load_model_catalog", return_value=fake_catalog):
            settings = RuntimeSettings.from_env()

        self.assertEqual(settings.query_refiner_model_priority, ["local_model"])
        self.assertEqual(settings.orchestrator_model_priority, ["local_model"])
        self.assertEqual(settings.final_synthesizer_model_priority, ["local_model"])

    def test_unprobed_local_model_is_excluded_from_default_priorities(self):
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "configured": True,
                    "is_local": True,
                    "supports_tools": True,
                    "reasoning_level": "medium",
                    "cost_level": "low",
                    "best_for_skills": [],
                    "model_tier": "small",
                    "probe": {},
                },
                {
                    "id": "deepseek_v4_pro_model",
                    "configured": True,
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "high",
                    "cost_level": "high",
                    "best_for_skills": ["orchestrator_planner", "final_synthesizer"],
                    "model_tier": "large",
                    "probe": {},
                },
            ]
        }

        with patch("runtime.config.settings.load_model_catalog", return_value=fake_catalog):
            settings = RuntimeSettings.from_env()

        all_priority_ids = (
            settings.query_refiner_model_priority
            + settings.orchestrator_model_priority
            + settings.final_synthesizer_model_priority
        )
        self.assertNotIn("local_model", all_priority_ids)
        self.assertIn("deepseek_v4_pro_model", all_priority_ids)

    def test_probe_failed_model_is_excluded_from_default_priorities(self):
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "configured": True,
                    "is_local": True,
                    "supports_tools": True,
                    "reasoning_level": "medium",
                    "cost_level": "low",
                    "best_for_skills": [],
                    "model_tier": "small",
                    "probe": {"status": "probe_failed"},
                },
                {
                    "id": "mimo_v25_pro_model",
                    "configured": True,
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "medium",
                    "cost_level": "medium",
                    "best_for_skills": ["jpc_now_skill"],
                    "model_tier": "medium",
                    "probe": {},
                },
            ]
        }

        with patch("runtime.config.settings.load_model_catalog", return_value=fake_catalog):
            settings = RuntimeSettings.from_env()

        all_priority_ids = (
            settings.query_refiner_model_priority
            + settings.orchestrator_model_priority
            + settings.final_synthesizer_model_priority
        )
        self.assertNotIn("local_model", all_priority_ids)
        self.assertIn("mimo_v25_pro_model", all_priority_ids)

    def test_role_model_priorities_are_read_from_env(self):
        from runtime.config.settings import RuntimeSettings

        os.environ["AGENTS_QUERY_REFINER_MODEL_PRIORITY"] = "mimo_model, deepseek_V4_flash_model"
        os.environ["AGENTS_ORCHESTRATOR_MODEL_PRIORITY"] = "deepseek_V4_flash_model"
        os.environ["AGENTS_FINAL_SYNTHESIZER_MODEL_PRIORITY"] = "mimo_model"
        settings = RuntimeSettings.from_env()
        self.assertEqual(settings.query_refiner_model_priority[0], "mimo_model")
        self.assertEqual(settings.orchestrator_model_priority, ["deepseek_V4_flash_model"])
        self.assertEqual(settings.final_synthesizer_model_priority, ["mimo_model"])

    def test_query_refiner_is_disabled_by_default(self):
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings.from_env()

        self.assertFalse(settings.query_refiner_enabled)
        self.assertIn("前置优化=关闭", settings.summary_zh())

    def test_query_refiner_can_be_enabled_from_env(self):
        from runtime.config.settings import RuntimeSettings

        os.environ["AGENTS_QUERY_REFINER_ENABLED"] = "true"
        settings = RuntimeSettings.from_env()

        self.assertTrue(settings.query_refiner_enabled)

    def test_query_refiner_can_be_disabled(self):
        from runtime.config.settings import RuntimeSettings

        os.environ["AGENTS_QUERY_REFINER_ENABLED"] = "false"
        settings = RuntimeSettings.from_env()
        self.assertFalse(settings.query_refiner_enabled)

    def test_select_role_model_uses_registry_first_configured(self):
        from runtime.config.settings import RuntimeSettings

        class FakeRegistry:
            def __init__(self):
                self.received = None

            def first_configured(self, preferred):
                self.received = list(preferred)
                return preferred[1]

        os.environ["AGENTS_ORCHESTRATOR_MODEL_PRIORITY"] = "missing_model, deepseek_V4_pro_model"
        settings = RuntimeSettings.from_env()
        registry = FakeRegistry()
        selected = settings.select_model_id(registry, "orchestrator")
        self.assertEqual(selected, "deepseek_V4_pro_model")
        self.assertEqual(registry.received, ["missing_model", "deepseek_V4_pro_model"])

    def test_privacy_mode_is_loaded_from_env(self):
        from runtime.config.settings import RuntimeSettings

        os.environ["AGENTS_PRIVACY_MODE"] = "offline"
        settings = RuntimeSettings.from_env()
        self.assertEqual(settings.privacy_mode, "offline")
        self.assertIn("隐私模式=offline", settings.summary_zh())

    def test_execution_mode_defaults_to_solo_and_loads_from_env(self):
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings.from_env()
        self.assertEqual(settings.execution_mode, "solo")
        self.assertIn("执行模式=solo", settings.summary_zh())

        os.environ["AGENTS_EXECUTION_MODE"] = "serial"
        settings = RuntimeSettings.from_env()
        self.assertEqual(settings.execution_mode, "serial")

    def test_invalid_execution_mode_falls_back_to_solo(self):
        from runtime.config.settings import RuntimeSettings

        os.environ["AGENTS_EXECUTION_MODE"] = "parallel_everything"
        settings = RuntimeSettings.from_env()

        self.assertEqual(settings.execution_mode, "solo")


class ModelBackendPrivacyTests(unittest.TestCase):
    def tearDown(self):
        for key in [
            "AGENTS_PRIVACY_MODE",
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_MODEL",
            "DEEPSEEK_pro_API_KEY",
            "DEEPSEEK_BASE_pro_URL",
            "DEEPSEEK_pro_MODEL",
            "MODEL_DEEPSEEK_API_KEY",
            "MODEL_DEEPSEEK_BASE_URL",
            "MODEL_DEEPSEEK_MODELS",
            "MODEL_DEEPSEEK_BACKEND",
            "MODEL_LOCAL_API_KEY",
            "MODEL_LOCAL_BASE_URL",
            "MODEL_LOCAL_MODEL",
            "MODEL_LOCAL_BACKEND",
            "MODEL_LOCAL_DISPLAY_NAME",
            "MODEL_LOCAL_PROVIDER",
            "MODEL_CLOUD_API_KEY",
            "MODEL_CLOUD_BASE_URL",
            "MODEL_CLOUD_MODEL",
            "MODEL_CLOUD_BACKEND",
            "MODEL_CLOUD_PROVIDER",
            "MODEL_CLOUD_DISPLAY_NAME",
            "MODEL_SILICONFLOW_API_KEY",
            "MODEL_SILICONFLOW_BASE_URL",
            "MODEL_SILICONFLOW_MODELS",
            "MODEL_SILICONFLOW_BACKEND",
            "MODEL_SILICONFLOW_PROVIDER",
            "MODEL_SILICONFLOW_DISPLAY_PREFIX",
            "MODEL_SILICONFLOW_STRENGTHS",
            "MODEL_SILICONFLOW_BEST_FOR_SKILLS",
            "MODEL_SILICONFLOW_COST_LEVEL",
            "MODEL_SILICONFLOW_REASONING_LEVEL",
            "MODEL_SILICONFLOW_SUPPORTS_TOOLS",
            "MIMO_API_MODELS",
            "MIMO_API_DISPLAY_PREFIX",
            "AGENTS_OFFLINE_NETWORK_MCP_POLICY",
        ]:
            os.environ.pop(key, None)

    def test_ollama_model_is_configured_without_api_key_and_has_local_privacy(self):
        from catalog_system.model_catalog import load_model_catalog

        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434"
        os.environ["MODEL_LOCAL_MODEL"] = "qwen3:8b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"
        catalog = load_model_catalog()
        local = next(item for item in catalog["models"] if item["id"] == "local_model")
        self.assertTrue(local["configured"])
        self.assertEqual(local["backend_type"], "ollama")
        self.assertTrue(local["is_local"])
        self.assertEqual(local["privacy_level"], "local")

    def test_ollama_deepseek_r1_is_marked_without_tool_support(self):
        from catalog_system.model_catalog import load_model_catalog

        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["MODEL_LOCAL_MODEL"] = "deepseek-r1:7b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"
        catalog = load_model_catalog()
        local = next(item for item in catalog["models"] if item["id"] == "local_model")
        self.assertFalse(local["supports_tools"])

    def test_deepseek_shared_provider_only_registers_models_listed_in_env(self):
        from catalog_system.model_catalog import load_model_catalog

        os.environ["MODEL_DEEPSEEK_API_KEY"] = "shared-deepseek-key"
        os.environ["MODEL_DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
        os.environ["MODEL_DEEPSEEK_MODELS"] = "deepseek_v4_pro:deepseek-v4-pro"

        catalog = load_model_catalog()
        models = {item["id"]: item for item in catalog["models"]}

        self.assertIn("deepseek_v4_pro_model", models)
        self.assertNotIn("deepseek_V4_flash_model", models)
        self.assertNotIn("deepseek_V4_pro_model", models)
        self.assertTrue(models["deepseek_v4_pro_model"]["configured"])
        self.assertEqual(models["deepseek_v4_pro_model"]["model_name"], "deepseek-v4-pro")
        self.assertEqual(models["deepseek_v4_pro_model"]["base_url"], "https://api.deepseek.com")

    def test_mimo_shared_provider_models_expand_versions(self):
        from catalog_system.model_catalog import load_model_catalog

        os.environ["MIMO_API_KEY"] = "mimo-key"
        os.environ["MIMO_API_BASE_URL"] = "https://api.xiaomimimo.com/v1"
        os.environ["MIMO_API_MODELS"] = "mimo_v25:mimo-v2.5,mimo_v25_pro:mimo-v2.5-pro"

        catalog = load_model_catalog()
        models = {item["id"]: item for item in catalog["models"]}

        self.assertIn("mimo_v25_model", models)
        self.assertIn("mimo_v25_pro_model", models)
        self.assertNotIn("mimo_model", models)
        self.assertEqual(models["mimo_v25_model"]["model_name"], "mimo-v2.5")
        self.assertEqual(models["mimo_v25_pro_model"]["model_name"], "mimo-v2.5-pro")

    def test_shared_provider_models_expand_into_multiple_registered_models(self):
        from catalog_system.model_catalog import ModelRegistry, load_model_catalog

        os.environ["MODEL_SILICONFLOW_API_KEY"] = "sf-key"
        os.environ["MODEL_SILICONFLOW_BASE_URL"] = "https://api.siliconflow.cn/v1"
        os.environ["MODEL_SILICONFLOW_MODELS"] = "qwen_plus:Qwen/Qwen3-8B, deepseek_r1:deepseek-ai/DeepSeek-R1"
        os.environ["MODEL_SILICONFLOW_BACKEND"] = "openai_compatible"
        os.environ["MODEL_SILICONFLOW_PROVIDER"] = "siliconflow"
        os.environ["MODEL_SILICONFLOW_DISPLAY_PREFIX"] = "硅基流动"

        catalog = load_model_catalog()
        models = {item["id"]: item for item in catalog["models"]}

        self.assertIn("siliconflow_qwen_plus_model", models)
        self.assertIn("siliconflow_deepseek_r1_model", models)
        self.assertTrue(models["siliconflow_qwen_plus_model"]["configured"])
        self.assertEqual(models["siliconflow_qwen_plus_model"]["api_key_env"], "MODEL_SILICONFLOW_API_KEY")
        self.assertEqual(models["siliconflow_qwen_plus_model"]["base_url_env"], "MODEL_SILICONFLOW_BASE_URL")
        self.assertEqual(models["siliconflow_qwen_plus_model"]["model_name"], "Qwen/Qwen3-8B")
        self.assertEqual(models["siliconflow_qwen_plus_model"]["display_name_zh"], "硅基流动 qwen_plus")

        registry = ModelRegistry()
        self.assertIn("siliconflow_deepseek_r1_model", registry.definitions)
        self.assertEqual(
            registry.definitions["siliconflow_deepseek_r1_model"]["model_name_value"],
            "deepseek-ai/DeepSeek-R1",
        )

    def test_shared_provider_model_pairs_accept_plain_model_names(self):
        from catalog_system.model_catalog import load_model_catalog

        os.environ["MODEL_SILICONFLOW_API_KEY"] = "sf-key"
        os.environ["MODEL_SILICONFLOW_BASE_URL"] = "https://api.siliconflow.cn/v1"
        os.environ["MODEL_SILICONFLOW_MODELS"] = "Qwen/Qwen3-8B, deepseek-ai/DeepSeek-V3"
        os.environ["MODEL_SILICONFLOW_BACKEND"] = "openai_compatible"

        catalog = load_model_catalog()
        models = {item["id"]: item for item in catalog["models"]}

        self.assertIn("siliconflow_qwen_qwen3_8b_model", models)
        self.assertIn("siliconflow_deepseek_ai_deepseek_v3_model", models)
        self.assertEqual(models["siliconflow_deepseek_ai_deepseek_v3_model"]["model_name"], "deepseek-ai/DeepSeek-V3")

    def test_missing_api_key_marks_shared_provider_models_unconfigured(self):
        from catalog_system.model_catalog import load_model_catalog

        os.environ.pop("MODEL_DEEPSEEK_API_KEY", None)
        os.environ["MODEL_DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
        os.environ["MODEL_DEEPSEEK_MODELS"] = "deepseek_v4_flash:deepseek-v4-flash"

        catalog = load_model_catalog()
        models = {item["id"]: item for item in catalog["models"]}

        self.assertIn("deepseek_v4_flash_model", models)
        self.assertFalse(models["deepseek_v4_flash_model"]["configured"])

    def test_probe_cache_overrides_tool_support_guess(self):
        from catalog_system.model_catalog import load_model_catalog
        from catalog_system.model_probe import save_probe_cache

        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["MODEL_LOCAL_MODEL"] = "deepseek-r1:7b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"
        cache = {
            "version": 1,
            "results": {
                "local_model": {
                    "model_id": "local_model",
                    "model_name": "deepseek-r1:7b",
                    "supports_basic_chat": True,
                    "supports_json_output": True,
                    "supports_tools": True,
                    "planner_suitable": True,
                    "execution_suitable": True,
                    "fingerprint": "",
                    "probed_at": 0,
                }
            },
        }
        catalog = load_model_catalog()
        local = next(item for item in catalog["models"] if item["id"] == "local_model")
        cache["results"]["local_model"]["fingerprint"] = local["probe_fingerprint"]
        save_probe_cache(PROJECT_ROOT, cache)
        try:
            catalog = load_model_catalog()
            local = next(item for item in catalog["models"] if item["id"] == "local_model")
            self.assertTrue(local["supports_tools"])
            self.assertEqual(local["probe"]["supports_tools"], True)
        finally:
            probe_path = PROJECT_ROOT / ".agent_cache" / "model_capabilities.json"
            if probe_path.exists():
                probe_path.unlink()

    def test_offline_privacy_filters_cloud_models_and_web_search(self):
        from catalog_system.model_catalog import ModelRegistry
        from planning.planner_schema import PlannedTask, PlannerResult
        from planning.plan_validator import validate_plan
        from runtime.safety.privacy import PrivacyPolicy

        os.environ["AGENTS_PRIVACY_MODE"] = "offline"
        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434"
        os.environ["MODEL_LOCAL_MODEL"] = "qwen3:8b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"
        os.environ["MODEL_CLOUD_API_KEY"] = "cloud-key"
        os.environ["MODEL_CLOUD_BASE_URL"] = "https://api.deepseek.com"
        os.environ["MODEL_CLOUD_MODEL"] = "deepseek-chat"
        os.environ["MODEL_CLOUD_BACKEND"] = "openai_compatible"

        registry = ModelRegistry()
        self.assertEqual(registry.first_configured(["cloud_model", "local_model"]), "local_model")

        plan = PlannerResult(
            route_type="single_agent",
            reason="needs current info",
            refined_request="search web",
            tasks=[
                PlannedTask(
                    id="search",
                    title="Search web",
                    instruction="Search current docs.",
                    skill_id="project_explorer",
                    model="cloud_model",
                    mcp=["web_search"],
                )
            ],
        )
        validation = validate_plan(plan, privacy_policy=PrivacyPolicy.from_env())
        self.assertFalse(validation.valid)
        self.assertTrue(any("隐私模式 offline" in error for error in validation.errors))
        self.assertTrue(any("web_search" in warning for warning in validation.warnings))

    def test_offline_web_search_warns_without_blocking_local_model_plan(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from planning.plan_validator import validate_plan
        from runtime.safety.privacy import PrivacyPolicy

        os.environ["AGENTS_PRIVACY_MODE"] = "offline"
        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["MODEL_LOCAL_MODEL"] = "deepseek-r1:7b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"

        plan = PlannerResult(
            route_type="single_agent",
            reason="user explicitly asked for web search",
            refined_request="联网查资料",
            tasks=[
                PlannedTask(
                    id="search",
                    title="Search with warning",
                    instruction="Search external docs.",
                    skill_id="project_explorer",
                    model="local_model",
                    mcp=["web_search"],
                )
            ],
        )
        validation = validate_plan(plan, privacy_policy=PrivacyPolicy.from_env())
        self.assertTrue(validation.valid, validation.errors)
        self.assertTrue(any("offline" in warning and "web_search" in warning for warning in validation.warnings))

    def test_offline_web_search_can_be_blocked_by_policy(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from planning.plan_validator import validate_plan
        from runtime.safety.privacy import PrivacyPolicy

        os.environ["AGENTS_PRIVACY_MODE"] = "offline"
        os.environ["AGENTS_OFFLINE_NETWORK_MCP_POLICY"] = "block"
        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["MODEL_LOCAL_MODEL"] = "qwen3:8b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"

        plan = PlannerResult(
            route_type="single_agent",
            reason="user asked for web search",
            refined_request="search web",
            tasks=[
                PlannedTask(
                    id="search",
                    title="Search with block policy",
                    instruction="Search external docs.",
                    skill_id="project_explorer",
                    model="local_model",
                    mcp=["web_search"],
                )
            ],
        )
        validation = validate_plan(plan, privacy_policy=PrivacyPolicy.from_env())
        self.assertFalse(validation.valid)
        self.assertTrue(any("web_search" in error for error in validation.errors))

    def test_model_catalog_reuses_cache_until_env_changes(self):
        from catalog_system import model_catalog

        os.environ["MODEL_CACHE_A_BASE_URL"] = "https://example.invalid/v1"
        os.environ["MODEL_CACHE_A_MODEL"] = "cache-a"
        os.environ["MODEL_CACHE_A_API_KEY"] = "key-a"
        try:
            model_catalog.clear_model_catalog_cache()
            first = model_catalog.load_model_catalog(force_reload=True)
            second = model_catalog.load_model_catalog()
            self.assertIs(first, second)

            os.environ["MODEL_CACHE_A_MODEL"] = "cache-b"
            third = model_catalog.load_model_catalog()
            self.assertIsNot(second, third)
            models = {item["id"]: item for item in third["models"]}
            self.assertEqual(models["cache_a_model"]["model_name"], "cache-b")
        finally:
            for key in [
                "MODEL_CACHE_A_BASE_URL",
                "MODEL_CACHE_A_MODEL",
                "MODEL_CACHE_A_API_KEY",
            ]:
                os.environ.pop(key, None)
            model_catalog.clear_model_catalog_cache()

    def test_local_first_prefers_local_then_cloud(self):
        from catalog_system.model_catalog import ModelRegistry

        os.environ["AGENTS_PRIVACY_MODE"] = "local_first"
        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434"
        os.environ["MODEL_LOCAL_MODEL"] = "qwen3:8b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"
        os.environ["MODEL_CLOUD_API_KEY"] = "cloud-key"
        os.environ["MODEL_CLOUD_BASE_URL"] = "https://api.deepseek.com"
        os.environ["MODEL_CLOUD_MODEL"] = "deepseek-chat"
        os.environ["MODEL_CLOUD_BACKEND"] = "openai_compatible"

        registry = ModelRegistry()
        selected = registry.first_configured(["cloud_model", "local_model"])
        self.assertEqual(selected, "local_model")


class ModelProbeTests(unittest.TestCase):
    def tearDown(self):
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)

    def test_probe_model_capabilities_detects_tools_unsupported_error(self):
        from catalog_system import model_probe

        class FakeResponse:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.text = json.dumps(payload)

            def json(self):
                return self._payload

        responses = [
            FakeResponse(
                200,
                {"choices": [{"message": {"content": '{"ok": true}'}}]},
            ),
            FakeResponse(
                400,
                {"error": {"message": "registry.ollama.ai/library/deepseek-r1:7b does not support tools"}},
            ),
        ]

        with patch.object(model_probe, "_post_json", side_effect=responses):
            result = model_probe.probe_model_capabilities(
                {
                    "model_name": "deepseek-r1:7b",
                    "backend_type": "ollama",
                    "base_url": "http://localhost:11434/v1",
                },
                timeout=0.1,
            )

        self.assertTrue(result["supports_basic_chat"])
        self.assertFalse(result["supports_tools"])
        self.assertEqual(result["status"], "tools_unsupported")

    def test_refresh_model_probe_cache_records_failures_without_raising(self):
        from catalog_system import model_probe

        project_root = TEMP_ROOT / "probe_project"
        project_root.mkdir(parents=True, exist_ok=True)
        catalog = {
            "models": [
                {
                    "id": "local_model",
                    "configured": True,
                    "is_local": True,
                    "model_name": "qwen3:8b",
                    "backend_type": "ollama",
                    "base_url": "http://localhost:11434/v1",
                }
            ]
        }

        with patch.object(
            model_probe,
            "probe_model_service",
            return_value={
                "service_status": "unknown",
                "service_available": None,
                "service_error": "",
                "model_present": None,
            },
        ), patch.object(model_probe, "probe_model_capabilities", side_effect=RuntimeError("probe boom")):
            cache = model_probe.refresh_model_probe_cache(project_root, catalog, force=True)

        result = cache["results"]["local_model"]
        self.assertEqual(result["status"], "probe_failed")
        self.assertIn("probe boom", result["error"])
        self.assertTrue((project_root / ".agent_cache" / "model_capabilities.json").exists())

    def test_probe_ollama_service_marks_online_when_tags_endpoint_works(self):
        from catalog_system import model_probe

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"models": [{"name": "deepseek-r1:7b"}]}

        class FakeSession:
            trust_env = False

            def get(self, endpoint, timeout):
                self.endpoint = endpoint
                self.timeout = timeout
                return FakeResponse()

            def close(self):
                return None

        with patch.object(model_probe.requests, "Session", return_value=FakeSession()):
            result = model_probe.probe_ollama_service(
                {
                    "model_name": "deepseek-r1:7b",
                    "backend_type": "ollama",
                    "base_url": "http://localhost:11434/v1",
                },
                timeout=0.2,
            )

        self.assertTrue(result["service_available"])
        self.assertEqual(result["service_status"], "online")
        self.assertTrue(result["model_present"])
        self.assertTrue(result["service_endpoint"].endswith("/api/tags"))

    def test_refresh_model_probe_cache_marks_capability_probe_failed_when_service_online(self):
        from catalog_system import model_probe

        project_root = TEMP_ROOT / "probe_service_online"
        project_root.mkdir(parents=True, exist_ok=True)
        catalog = {
            "models": [
                {
                    "id": "local_model",
                    "configured": True,
                    "is_local": True,
                    "model_name": "deepseek-r1:7b",
                    "backend_type": "ollama",
                    "base_url": "http://localhost:11434/v1",
                }
            ]
        }

        with patch.object(
            model_probe,
            "probe_model_service",
            return_value={
                "service_status": "online",
                "service_available": True,
                "service_error": "",
                "model_present": True,
            },
        ), patch.object(model_probe, "probe_model_capabilities", side_effect=RuntimeError("generation timeout")):
            cache = model_probe.refresh_model_probe_cache(project_root, catalog, force=True)

        result = cache["results"]["local_model"]
        self.assertEqual(result["status"], "capability_probe_failed")
        self.assertTrue(result["service_available"])
        self.assertTrue(result["model_present"])
        self.assertIn("generation timeout", result["error"])


class ReadonlyCliConfigTests(unittest.TestCase):
    def tearDown(self):
        for key in [
            "AGENTS_PRIVACY_MODE",
            "MODEL_LOCAL_API_KEY",
            "MODEL_LOCAL_BASE_URL",
            "MODEL_LOCAL_MODEL",
            "MODEL_LOCAL_BACKEND",
            "MODEL_LOCAL_DISPLAY_NAME",
            "MODEL_LOCAL_PROVIDER",
            "MODEL_CLOUD_API_KEY",
            "MODEL_CLOUD_BASE_URL",
            "MODEL_CLOUD_MODEL",
            "MODEL_CLOUD_BACKEND",
            "MODEL_CLOUD_PROVIDER",
            "MODEL_CLOUD_DISPLAY_NAME",
        ]:
            os.environ.pop(key, None)

    def setUp(self):
        os.environ["AGENTS_PRIVACY_MODE"] = "local_first"
        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434"
        os.environ["MODEL_LOCAL_MODEL"] = "qwen3:8b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"
        os.environ["MODEL_LOCAL_DISPLAY_NAME"] = "本地 Qwen3"
        os.environ["MODEL_CLOUD_API_KEY"] = "sk-test-secret-should-not-leak"
        os.environ["MODEL_CLOUD_BASE_URL"] = "https://api.deepseek.com"
        os.environ["MODEL_CLOUD_MODEL"] = "deepseek-chat"
        os.environ["MODEL_CLOUD_BACKEND"] = "openai_compatible"
        os.environ["MODEL_CLOUD_DISPLAY_NAME"] = "DeepSeek Cloud"

    def test_config_command_shows_privacy_and_separates_local_cloud_models(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        output = render_readonly_command("/config", RuntimeSettings.from_env())
        self.assertIn("当前隐私模式：本地优先", output)
        self.assertIn("本地模型", output)
        self.assertIn("云端模型", output)
        self.assertIn("local_model", output)
        self.assertIn("cloud_model", output)
        self.assertNotIn("sk-test-secret", output)

    def test_config_command_shows_model_capability_dashboard_in_chinese(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "display_name_zh": "本地 DeepSeek R1",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": False,
                    "planner_suitable": False,
                    "execution_suitable": False,
                    "probe": {"status": "tools_unsupported"},
                },
                {
                    "id": "cloud_model",
                    "display_name_zh": "DeepSeek Cloud",
                    "backend_type": "openai_compatible",
                    "configured": True,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "supports_tools": True,
                    "planner_suitable": True,
                    "execution_suitable": True,
                    "probe": {"status": "ok"},
                },
            ]
        }
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/config", RuntimeSettings.from_env())

        self.assertIn("能力：工具 否 | 主脑 否 | 执行 否", output)
        self.assertIn("能力：工具 是 | 主脑 是 | 执行 是", output)
        self.assertIn("探测：不支持工具调用", output)
        self.assertIn("探测：正常", output)

    def test_config_command_marks_unprobed_models_as_conservative_guess(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "display_name_zh": "本地 Qwen3",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": True,
                    "probe": {},
                }
            ]
        }
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/config", RuntimeSettings.from_env())

        self.assertIn("能力：工具 是（保守判断） | 主脑 可尝试（未探测） | 执行 是（保守判断）", output)
        self.assertIn("探测：未探测（使用保守判断）", output)

    def test_readonly_commands_use_human_readable_chinese_values(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings.from_env()
        config = render_readonly_command("/config", settings)
        api_show = render_readonly_command("/api show", settings)
        privacy = render_readonly_command("/privacy", settings)
        model = render_readonly_command("/model", settings)

        combined = "\n".join([config, api_show, privacy, model])
        self.assertIn("Ollama 本地服务", combined)
        self.assertIn("OpenAI 兼容接口", combined)
        self.assertIn("状态：配置完整 |", combined)
        self.assertIn("本地", combined)
        self.assertIn("云端", combined)
        self.assertIn("本地优先", privacy)
        self.assertIn("离线模式", privacy)
        self.assertIn("允许云端", privacy)
        self.assertIn("主脑模型优先级", model)
        self.assertNotIn("backend=", combined)
        self.assertNotIn("configured=True", combined)
        self.assertNotIn("privacy=cloud", combined)
        self.assertNotIn("privacy=local", combined)

    def test_config_command_uses_compact_multiline_model_cards(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "display_name_zh": "本地 DeepSeek R1",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": False,
                    "planner_suitable": False,
                    "execution_suitable": False,
                    "probe": {"status": "tools_unsupported"},
                }
            ]
        }
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/config", RuntimeSettings.from_env())

        self.assertIn("- 本地 DeepSeek R1（local_model）", output)
        self.assertIn("  基础：Ollama 本地服务 | 配置完整 | 可聊天，不支持工具 | 本地", output)
        self.assertIn("  能力：工具 否 | 主脑 否 | 执行 否", output)
        self.assertIn("  探测：不支持工具调用", output)
        self.assertNotIn("local_model | 本地 DeepSeek R1 | 接口类型", output)

    def test_api_and_model_commands_use_compact_multiline_blocks(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings.from_env()
        api_show = render_readonly_command("/api show", settings)
        model = render_readonly_command("/model", settings)

        self.assertIn("- 本地 Qwen3（local_model）", api_show)
        self.assertIn("  地址：http://localhost:11434", api_show)
        self.assertIn("  状态：配置完整 | 未确认可用 | 本地", api_show)
        self.assertIn("前置优化副脑", model)
        self.assertIn("当前隐私模式：本地优先", model)
        self.assertIn("本地 Qwen3（local_model）", model)
        self.assertNotIn("当前优先级里的模型都没有在 .env 注册", model)
        self.assertNotIn("前置优化副脑：", model)
        self.assertNotIn(" → ", model)

    def test_model_available_command_shows_only_runtime_available_models(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "display_name_zh": "本地 DeepSeek R1",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": False,
                    "probe": {"status": "probe_failed"},
                },
                {
                    "id": "deepseek_v4_pro_model",
                    "display_name_zh": "DeepSeek Pro",
                    "backend_type": "openai_compatible",
                    "configured": True,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "supports_tools": True,
                    "planner_suitable": True,
                    "execution_suitable": True,
                    "probe": {},
                },
            ]
        }
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/model available", RuntimeSettings.from_env())

        self.assertIn("可用模型（紧凑视图）", output)
        self.assertIn("DeepSeek Pro（deepseek_v4_pro_model）", output)
        self.assertNotIn("本地 DeepSeek R1", output)
        self.assertNotIn("暂不可用", output)

    def test_model_available_command_explains_when_no_available_models(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "display_name_zh": "本地 DeepSeek R1",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": False,
                    "probe": {"status": "probe_failed"},
                }
            ]
        }
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/model available", RuntimeSettings.from_env())

        self.assertIn("当前没有确认可用的模型", output)
        self.assertIn("/config", output)

    def test_local_config_without_probe_is_not_presented_as_available(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "display_name_zh": "本地 DeepSeek R1",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": False,
                    "probe": {},
                }
            ]
        }
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/model", RuntimeSettings.from_env())
            config = render_readonly_command("/config", RuntimeSettings.from_env())

        self.assertIn("配置完整 | 未确认可用", output)
        self.assertIn("配置完整 | 未确认可用", config)
        self.assertNotIn("已配置 | 本地", output)
        self.assertNotIn("可加入优先级的候选模型", output)
        self.assertIn("暂不可用模型", output)
        self.assertIn("本地 DeepSeek R1（local_model）", output)

    def test_model_command_with_dynamic_priorities_does_not_show_missing_priority_warning(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "deepseek_v4_flash_model",
                    "display_name_zh": "DeepSeek deepseek_v4_flash",
                    "backend_type": "openai_compatible",
                    "configured": True,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "medium",
                    "cost_level": "low",
                    "model_tier": "medium",
                    "best_for_skills": ["project_explorer", "humanizer_zh"],
                },
                {
                    "id": "mimo_v25_pro_model",
                    "display_name_zh": "MiMo mimo_v25_pro",
                    "backend_type": "openai_compatible",
                    "configured": True,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "medium",
                    "cost_level": "medium",
                    "model_tier": "medium",
                    "best_for_skills": ["jpc_now_skill"],
                },
            ]
        }

        with patch("runtime.config.settings.load_model_catalog", return_value=fake_catalog):
            settings = RuntimeSettings.from_env()
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/model", settings)

        self.assertIn("DeepSeek deepseek_v4_flash（deepseek_v4_flash_model）", output)
        self.assertIn("MiMo mimo_v25_pro（mimo_v25_pro_model）", output)
        self.assertNotIn("当前优先级里的模型都没有在 .env 注册", output)
        self.assertNotIn("deepseek_V4_flash_model", output)
        self.assertNotIn("mimo_model", output)

    def test_model_command_shows_candidate_suggestions_for_configured_models_not_in_priority(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "mimo_model",
                    "display_name_zh": "MiMo v2.5 Pro",
                    "backend_type": "openai_compatible",
                    "configured": True,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "medium",
                    "cost_level": "medium",
                    "model_tier": "medium",
                    "best_for_skills": ["jpc_now_skill"],
                },
                {
                    "id": "qwen_coder_model",
                    "display_name_zh": "Qwen Coder",
                    "backend_type": "openai_compatible",
                    "configured": True,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "high",
                    "cost_level": "medium",
                    "model_tier": "large",
                    "best_for_skills": ["project_explorer", "jpc_now_skill"],
                },
            ]
        }
        settings = RuntimeSettings(
            query_refiner_enabled=True,
            query_refiner_model_priority=["mimo_model"],
            orchestrator_model_priority=["mimo_model"],
            final_synthesizer_model_priority=["mimo_model"],
            privacy_mode="cloud_allowed",
        )
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/model", settings)

        self.assertIn("可加入优先级的候选模型", output)
        self.assertIn("Qwen Coder（qwen_coder_model）", output)
        self.assertIn("建议角色：前置优化副脑, 主脑模型优先级, 汇总副脑", output)

    def test_probe_failed_model_is_not_shown_as_priority_candidate(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "deepseek_v4_pro_model",
                    "display_name_zh": "DeepSeek Pro",
                    "backend_type": "openai_compatible",
                    "configured": True,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "supports_tools": True,
                    "reasoning_level": "high",
                    "cost_level": "high",
                    "model_tier": "large",
                    "best_for_skills": ["orchestrator_planner"],
                    "probe": {},
                },
                {
                    "id": "local_model",
                    "display_name_zh": "本地 DeepSeek R1",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": False,
                    "reasoning_level": "medium",
                    "cost_level": "low",
                    "model_tier": "small",
                    "best_for_skills": [],
                    "probe": {"status": "probe_failed"},
                },
            ]
        }
        settings = RuntimeSettings(
            query_refiner_enabled=True,
            query_refiner_model_priority=["deepseek_v4_pro_model"],
            orchestrator_model_priority=["deepseek_v4_pro_model"],
            final_synthesizer_model_priority=["deepseek_v4_pro_model"],
            privacy_mode="cloud_allowed",
        )
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/model", settings)

        self.assertNotIn("可加入优先级的候选模型", output)
        self.assertIn("暂不可用模型", output)
        self.assertIn("本地 DeepSeek R1（local_model）", output)
        self.assertIn("连接不可用", output)

    def test_local_model_service_online_but_probe_failed_has_specific_message(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "display_name_zh": "本地 DeepSeek R1",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": False,
                    "probe": {
                        "status": "capability_probe_failed",
                        "service_available": True,
                        "model_present": True,
                    },
                }
            ]
        }
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/model", RuntimeSettings.from_env())
            config = render_readonly_command("/config", RuntimeSettings.from_env())

        self.assertIn("服务在线，能力探测失败", output)
        self.assertIn("处理建议：Ollama 服务在线，但模型能力探测失败", output)
        self.assertIn("探测：服务在线，能力探测失败", config)

    def test_local_model_service_unavailable_has_specific_message(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "display_name_zh": "本地 DeepSeek R1",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": False,
                    "probe": {
                        "status": "service_unavailable",
                        "service_available": False,
                    },
                }
            ]
        }
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/model", RuntimeSettings.from_env())

        self.assertIn("本地服务未连通", output)
        self.assertIn("处理建议：Ollama 服务未连通", output)

    def test_model_command_hides_unconfigured_models_from_priority_view(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "deepseek_v4_flash_model",
                    "display_name_zh": "DeepSeek Flash",
                    "backend_type": "openai_compatible",
                    "configured": False,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "supports_tools": True,
                },
                {
                    "id": "mimo_v25_pro_model",
                    "display_name_zh": "MiMo Pro",
                    "backend_type": "openai_compatible",
                    "configured": True,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "supports_tools": True,
                },
            ]
        }
        settings = RuntimeSettings(
            query_refiner_enabled=True,
            query_refiner_model_priority=["mimo_v25_pro_model"],
            orchestrator_model_priority=["mimo_v25_pro_model"],
            final_synthesizer_model_priority=["mimo_v25_pro_model"],
            privacy_mode="cloud_allowed",
        )
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            model_output = render_readonly_command("/model", settings)
            config_output = render_readonly_command("/config", settings)

        self.assertNotIn("DeepSeek Flash（deepseek_v4_flash_model）", model_output)
        self.assertIn("MiMo Pro（mimo_v25_pro_model）", model_output)
        self.assertIn("DeepSeek Flash（deepseek_v4_flash_model）", config_output)
        self.assertIn("配置不完整", config_output)

    def test_model_command_does_not_show_unregistered_default_models(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "local_model",
                    "display_name_zh": "Local",
                    "backend_type": "ollama",
                    "configured": True,
                    "privacy_level": "local",
                    "is_local": True,
                    "supports_tools": False,
                    "reasoning_level": "medium",
                    "cost_level": "medium",
                    "model_tier": "small",
                    "best_for_skills": [],
                }
            ]
        }
        settings = RuntimeSettings(
            query_refiner_enabled=True,
            query_refiner_model_priority=["deepseek_V4_flash_model", "mimo_model"],
            orchestrator_model_priority=["deepseek_V4_pro_model"],
            final_synthesizer_model_priority=["mimo_model"],
            privacy_mode="local_first",
        )
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/model", settings)

        self.assertNotIn("deepseek_V4_flash_model", output)
        self.assertNotIn("deepseek_V4_pro_model", output)
        self.assertNotIn("mimo_model", output)
        self.assertNotIn("可加入优先级的候选模型", output)
        self.assertIn("暂不可用模型", output)
        self.assertIn("Local（local_model）", output)

    def test_api_show_command_does_not_expose_keys(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        output = render_readonly_command("/api show", RuntimeSettings.from_env())
        self.assertIn("API 配置", output)
        self.assertIn("https://api.deepseek.com", output)
        self.assertIn("http://localhost:11434", output)
        self.assertNotIn("sk-test-secret", output)

    def test_api_show_uses_resolved_catalog_base_url_for_shared_model_config(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        fake_catalog = {
            "models": [
                {
                    "id": "deepseek_V4_pro_model",
                    "display_name_zh": "DeepSeek V4 Pro",
                    "backend_type": "openai_compatible",
                    "configured": True,
                    "privacy_level": "cloud",
                    "is_local": False,
                    "base_url": "https://api.deepseek.com",
                }
            ]
        }
        with patch("runtime.config.cli.load_model_catalog", return_value=fake_catalog):
            output = render_readonly_command("/api show", RuntimeSettings.from_env())

        self.assertIn("地址：https://api.deepseek.com", output)
        self.assertNotIn("地址：未配置", output)

    def test_privacy_and_model_commands_are_readonly(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings.from_env()
        privacy = render_readonly_command("/privacy", settings)
        model = render_readonly_command("/model", settings)
        switch_hint = render_readonly_command("/privacy offline", settings)

        self.assertIn("只读查看", privacy)
        self.assertIn("主脑模型优先级", model)
        self.assertIn("当前版本不会直接改写 .env", switch_hint)

    def test_mode_command_shows_execution_mode_readonly(self):
        from runtime.config.cli import render_readonly_command
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings(execution_mode="solo")
        output = render_readonly_command("/mode", settings)
        switch_hint = render_readonly_command("/mode solo", settings)

        self.assertIn("执行模式状态", output)
        self.assertIn("当前模式：单模型工具 Agent", output)
        self.assertIn("solo / serial / full", output)
        self.assertNotIn("auto：", output)
        self.assertIn("可以读写文件、联网、跑命令和测试", output)
        self.assertIn("支持直接切换", switch_hint)


class WritableCliConfigTests(unittest.TestCase):
    def setUp(self):
        self.env_path = TEMP_ROOT / f"cli_env_{uuid.uuid4().hex}.env"
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        self.env_path.write_text(
            "MODEL_TEST_API_KEY=sk-do-not-print\n"
            "AGENTS_EXECUTION_MODE=solo\n"
            "AGENTS_QUERY_REFINER_ENABLED=false\n",
            encoding="utf-8",
        )

    def tearDown(self):
        if TEMP_ROOT.exists():
            _safe_rmtree(TEMP_ROOT)
        for key in ["AGENTS_EXECUTION_MODE", "AGENTS_QUERY_REFINER_ENABLED"]:
            os.environ.pop(key, None)

    def test_mode_write_updates_env_file_and_runtime_settings(self):
        from runtime.config.cli import apply_writable_config_command
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings(execution_mode="solo")
        output, updated = apply_writable_config_command("/mode serial", self.env_path, settings)

        text = self.env_path.read_text(encoding="utf-8")
        self.assertTrue(updated)
        self.assertIn("已切换执行模式：多 Agent 串行", output)
        self.assertIn("AGENTS_EXECUTION_MODE=serial", text)
        self.assertIn("MODEL_TEST_API_KEY=sk-do-not-print", text)
        self.assertEqual(settings.execution_mode, "serial")

    def test_refiner_write_updates_env_file_and_runtime_settings(self):
        from runtime.config.cli import apply_writable_config_command
        from runtime.config.settings import RuntimeSettings

        settings = RuntimeSettings(query_refiner_enabled=False)
        output, updated = apply_writable_config_command("/refiner on", self.env_path, settings)

        text = self.env_path.read_text(encoding="utf-8")
        self.assertTrue(updated)
        self.assertIn("前置优化副脑已开启", output)
        self.assertIn("AGENTS_QUERY_REFINER_ENABLED=true", text)
        self.assertTrue(settings.query_refiner_enabled)

    def test_invalid_writable_command_does_not_touch_env_file(self):
        from runtime.config.cli import apply_writable_config_command
        from runtime.config.settings import RuntimeSettings

        before = self.env_path.read_text(encoding="utf-8")
        output, updated = apply_writable_config_command("/mode auto", self.env_path, RuntimeSettings())

        self.assertFalse(updated)
        self.assertIn("无法识别", output)
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), before)


class PlannerRefinerToggleTests(unittest.TestCase):
    def test_build_refined_request_without_refiner(self):
        from planning.planner import build_refined_request_without_refiner

        refined = build_refined_request_without_refiner("请检查当前项目")
        self.assertEqual(refined.refined_request, "请检查当前项目")
        self.assertEqual(refined.likely_intent, "mixed")
        self.assertTrue(any("已关闭" in item for item in refined.possible_ambiguities))


class LocalModelPlannerCompatibilityTests(unittest.TestCase):
    def tearDown(self):
        for key in [
            "AGENTS_PRIVACY_MODE",
            "MODEL_LOCAL_API_KEY",
            "MODEL_LOCAL_BASE_URL",
            "MODEL_LOCAL_MODEL",
            "MODEL_LOCAL_BACKEND",
            "MODEL_LOCAL_DISPLAY_NAME",
            "MODEL_LOCAL_PROVIDER",
        ]:
            os.environ.pop(key, None)

    def test_parse_json_after_deepseek_r1_think_block(self):
        from planning.planner_schema import parse_planner_result

        text = """
<think>
我应该先判断这是闲聊，然后直接回答。
</think>
好的，下面是 JSON：
```json
{
  "route_type": "direct_answer",
  "reason": "用户只是简单问候",
  "refined_request": "你好，简单介绍一下你现在能帮我做什么",
  "direct_answer_instruction": "用简洁中文介绍当前系统能力。",
  "tasks": [],
  "needs_synthesis": false
}
```
"""
        plan = parse_planner_result(text, fallback_user_input="你好，简单介绍一下你现在能帮我做什么")
        self.assertEqual(plan.route_type, "direct_answer")
        self.assertIn("简单问候", plan.reason)

    def test_plain_chatty_planner_output_falls_back_to_direct_answer(self):
        from planning.planner_schema import parse_planner_result

        text = "你好！我可以帮你分析项目、定位代码、运行测试，也可以回答普通问题。"
        plan = parse_planner_result(text, fallback_user_input="你好，简单介绍一下你现在能帮我做什么")
        self.assertEqual(plan.route_type, "direct_answer")
        self.assertIn("本地模型未返回合法 JSON", plan.reason)
        self.assertIn("简单介绍", plan.direct_answer_instruction)

    def test_plain_code_locator_output_falls_back_to_single_agent(self):
        from planning.planner_schema import parse_planner_result

        text = "我会先定位 MCPServerManager，然后查看相关启动代码。"
        plan = parse_planner_result(text, fallback_user_input="帮我定位 MCPServerManager 是在哪里启动 MCP 的，先不要修改")
        self.assertEqual(plan.route_type, "single_agent")
        self.assertEqual(len(plan.tasks), 1)
        self.assertEqual(plan.tasks[0].skill_id, "project_explorer")
        self.assertIn("code_locator", plan.tasks[0].mcp)

    def test_fallback_uses_current_turn_not_recent_project_context(self):
        from planning.planner_schema import parse_planner_result

        composed_input = "\n".join(
            [
                "以下是最近几轮对话，供理解上下文。不要把历史内容当成本轮新任务，除非用户明确要求继续。",
                "用户：帮我定位 MCPServerManager 是在哪里启动 MCP 的，先不要修改",
                "助手：已定位到 mcp_servers/__init__.py。",
                "",
                "本轮用户问题：你好，简单介绍一下你现在能帮他做些什么？",
            ]
        )
        plan = parse_planner_result("我可以帮你分析项目和回答问题。", fallback_user_input=composed_input)
        self.assertEqual(plan.route_type, "direct_answer")
        self.assertIn("你好", plan.refined_request)

    def test_offline_fallback_single_agent_uses_tool_capable_local_model(self):
        from planning.plan_validator import validate_plan
        from planning.planner_schema import parse_planner_result
        from runtime.safety.privacy import PrivacyPolicy

        os.environ["AGENTS_PRIVACY_MODE"] = "offline"
        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["MODEL_LOCAL_MODEL"] = "qwen3:8b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"

        plan = parse_planner_result(
            "我会先定位 MCPServerManager，然后查看相关启动代码。",
            fallback_user_input="帮我定位 MCPServerManager 是在哪里启动 MCP 的，先不要修改",
        )
        self.assertEqual(plan.route_type, "single_agent")
        self.assertEqual(plan.tasks[0].model, "local_model")
        validation = validate_plan(plan, privacy_policy=PrivacyPolicy.from_env())
        self.assertTrue(validation.valid, validation.errors)

    def test_tool_task_avoids_local_model_without_tool_support_when_cloud_allowed(self):
        from planning.planner_schema import parse_planner_result

        os.environ["AGENTS_PRIVACY_MODE"] = "cloud_allowed"
        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["MODEL_LOCAL_MODEL"] = "deepseek-r1:7b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"
        os.environ["MODEL_CLOUD_API_KEY"] = "cloud-key"
        os.environ["MODEL_CLOUD_BASE_URL"] = "https://api.deepseek.com"
        os.environ["MODEL_CLOUD_MODEL"] = "deepseek-chat"
        os.environ["MODEL_CLOUD_BACKEND"] = "openai_compatible"

        plan = parse_planner_result(
            "我会先定位 MCPServerManager，然后查看相关启动代码。",
            fallback_user_input="帮我定位 MCPServerManager 是在哪里启动 MCP 的，先不要修改",
        )
        self.assertEqual(plan.route_type, "single_agent")
        self.assertEqual(plan.tasks[0].model, "cloud_model")
        self.assertIn("code_locator", plan.tasks[0].mcp)

    def test_offline_tool_task_without_tool_capable_local_model_becomes_direct_answer(self):
        from planning.plan_validator import validate_plan
        from planning.planner_schema import parse_planner_result
        from runtime.safety.privacy import PrivacyPolicy

        os.environ["AGENTS_PRIVACY_MODE"] = "offline"
        os.environ["MODEL_LOCAL_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["MODEL_LOCAL_MODEL"] = "deepseek-r1:7b"
        os.environ["MODEL_LOCAL_BACKEND"] = "ollama"

        plan = parse_planner_result(
            "我会先定位 MCPServerManager，然后查看相关启动代码。",
            fallback_user_input="帮我定位 MCPServerManager 是在哪里启动 MCP 的，先不要修改",
        )
        self.assertEqual(plan.route_type, "direct_answer")
        self.assertIn("不支持工具调用", plan.direct_answer_instruction)
        validation = validate_plan(plan, privacy_policy=PrivacyPolicy.from_env())
        self.assertTrue(validation.valid, validation.errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)

