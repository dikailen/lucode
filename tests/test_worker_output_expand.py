from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class WorkerOutputExpandTests(unittest.TestCase):
    def test_long_team_worker_output_is_saved_for_expand_without_hiding_report(self):
        from runtime.execution.multi_agent_runner import _record_worker_output_detail
        from runtime.history.expand_store import ExpandBlockStore

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            output = "\n".join(f"worker detail line {index}" for index in range(80))

            hint = _record_worker_output_detail(
                workspace,
                task_id="inspect_runtime",
                title="Inspect runtime",
                output=output,
                mode="full",
                route="team",
            )

            blocks = ExpandBlockStore(workspace).list_blocks()
            self.assertIn("/expand", hint)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0].kind, "worker")
            self.assertIn("inspect_runtime", blocks[0].block_id)
            self.assertIn("worker detail line 79", ExpandBlockStore(workspace).read(blocks[0].block_id) or "")

    def test_short_or_non_team_worker_output_does_not_create_expand_block(self):
        from runtime.execution.multi_agent_runner import _record_worker_output_detail
        from runtime.history.expand_store import ExpandBlockStore

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)

            short_hint = _record_worker_output_detail(
                workspace,
                task_id="short",
                title="Short",
                output="short output",
                mode="full",
                route="team",
            )
            single_hint = _record_worker_output_detail(
                workspace,
                task_id="single",
                title="Single",
                output="\n".join(f"single line {index}" for index in range(80)),
                mode="full",
                route="single_agent",
            )

            self.assertEqual(short_hint, "")
            self.assertEqual(single_hint, "")
            self.assertEqual(ExpandBlockStore(workspace).list_blocks(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
