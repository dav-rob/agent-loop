import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import argparse
import tempfile

from agent_loop.config import Config
from agent_loop.cli import handle_start
from agent_loop.intake import (
    run_spec_intake,
    run_brainstorm_discussion,
    draft_compact_spec,
    run_ui_branch,
    run_spec_review
)

class TestIntake(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        state_dir = Path(self.tmpdir.name) / ".agent-loop"
        self.config = Config({
            "state_dir": str(state_dir),
            "logs_dir": str(state_dir / "logs"),
            "routes": {
                "planning": [{"provider": "mock", "model": "mock-model"}],
                "implementation": [],
            },
        })

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("agent_loop.cli.render_progress_md")
    @patch("agent_loop.cli.render_plan_md")
    @patch("agent_loop.cli.Orchestrator")
    @patch("agent_loop.cli.RunRepository")
    @patch("agent_loop.cli.get_db")
    def test_none_mode_non_interactive(self, mock_get_db, mock_run_repo_cls, mock_orch_cls, mock_render_plan, mock_render_progress):
        # 1. none mode creates a run with intake_mode="none" and goes to planner
        mock_repo = MagicMock()
        mock_repo.create.return_value = 1
        mock_repo.get.return_value = {"status": "awaiting_plan_approval"}
        mock_run_repo_cls.return_value = mock_repo
        
        args = argparse.Namespace(
            non_interactive=True,
            goal="Test goal",
            intake="none",
            unattended_policy="approve",
            ui=False,
            no_ui=False,
            no_spec_review=False
        )
        
        handle_start(args, self.config)
        
        mock_repo.create.assert_called_once()
        call_kwargs = mock_repo.create.call_args[1]
        self.assertEqual(call_kwargs["goal"], "Test goal")
        self.assertEqual(call_kwargs["intake_mode"], "none")
        
        # Check that planner was called immediately
        mock_orch = mock_orch_cls.return_value
        mock_orch.plan_run.assert_called_once()

    @patch("agent_loop.intake.run_spec_intake")
    @patch("agent_loop.cli.render_progress_md")
    @patch("agent_loop.cli.render_plan_md")
    @patch("agent_loop.cli.Orchestrator")
    @patch("agent_loop.cli.RunRepository")
    @patch("agent_loop.cli.get_db")
    @patch("builtins.input")
    def test_spec_mode_interactive(self, mock_input, mock_get_db, mock_run_repo_cls, mock_orch_cls, mock_render_plan, mock_render_progress, mock_run_spec_intake):
        # 2. spec mode calls brainstormer
        mock_repo = MagicMock()
        mock_repo.create.return_value = 1
        mock_repo.get.return_value = {"status": "awaiting_plan_approval"}
        mock_run_repo_cls.return_value = mock_repo
        
        mock_run_spec_intake.return_value = "# Compact Spec\n\nApproved spec."
        
        args = argparse.Namespace(
            non_interactive=False,
            goal="Test goal",
            intake="spec",
            ui=False,
            no_ui=False,
            no_spec_review=False
        )
        
        handle_start(args, self.config)
        
        mock_run_spec_intake.assert_called_once()
        mock_repo.create.assert_called_once()
        call_kwargs = mock_repo.create.call_args[1]
        self.assertEqual(call_kwargs["goal"], "# Compact Spec\n\nApproved spec.")
        self.assertEqual(call_kwargs["intake_mode"], "spec")

    @patch("agent_loop.intake.ModelRouter")
    def test_run_spec_review_needs_user_answer(self, mock_router_cls):
        # 4. If spec review returns needs-user-answer
        mock_adapter = MagicMock()
        mock_router_cls.return_value = mock_adapter
        
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.output = "Status: needs-user-answer\nReviewer Notes:\n* Missing detail\n# Revised Compact Spec\nDraft spec"
        mock_adapter.run.return_value = mock_res
        
        status, spec = run_spec_review("Original spec", self.config)
        self.assertEqual(status, "needs-user-answer")
        self.assertEqual(spec, "Draft spec")
        self.assertEqual(mock_adapter.run.call_args.kwargs["profile"], "spec_reviewer")

    @patch("agent_loop.cli.render_progress_md")
    @patch("agent_loop.cli.render_plan_md")
    @patch("agent_loop.cli.Orchestrator")
    @patch("agent_loop.cli.RunRepository")
    @patch("agent_loop.cli.get_db")
    def test_legacy_aliases(self, mock_get_db, mock_run_repo_cls, mock_orch_cls, mock_render_plan, mock_render_progress):
        # 5. Old autonomous maps to none, 6. Old brainstorm maps to spec
        mock_repo = MagicMock()
        mock_repo.create.return_value = 1
        mock_repo.get.return_value = {"status": "awaiting_plan_approval"}
        mock_run_repo_cls.return_value = mock_repo
        
        args1 = argparse.Namespace(non_interactive=True, goal="Goal", intake="autonomous", unattended_policy="approve", ui=False, no_ui=False, no_spec_review=False)
        handle_start(args1, self.config)
        self.assertEqual(mock_repo.create.call_args_list[0][1]["intake_mode"], "none")
        
        # Test brainstorm -> spec mapping in non_interactive
        with patch("agent_loop.intake.draft_compact_spec") as mock_draft:
            with patch("agent_loop.intake.run_spec_review") as mock_review:
                mock_draft.return_value = "Draft"
                mock_review.return_value = ("approved", "Draft")
                args2 = argparse.Namespace(non_interactive=True, goal="Goal", intake="brainstorm", unattended_policy="approve", ui=False, no_ui=False, no_spec_review=False)
                handle_start(args2, self.config)
                self.assertEqual(mock_repo.create.call_args_list[1][1]["intake_mode"], "spec")

    @patch("agent_loop.intake.ModelRouter")
    @patch("builtins.input")
    def test_brainstorm_requires_minimum_turns_and_summary_approval(self, mock_input, mock_router_cls):
        responses = [
            '{"status": "question", "question": "Who is this for?", "current_understanding": "The audience is still unclear."}',
            '{"status": "question", "question": "What is the first useful outcome?", "current_understanding": "The tool needs a focused first cut."}',
            '{"status": "question", "question": "How should it be verified?", "current_understanding": "We now know audience, outcome, and verification."}',
            '{"status": "ready", "draft_spec": "# Compact Spec\\n\\n## Outcome\\nBuild the useful thing"}',
            '{"draft_spec": "# Compact Spec\\n\\n## Outcome\\nBuild the useful thing"}',
        ]

        mock_router = MagicMock()
        mock_router.run.side_effect = [
            MagicMock(success=True, output=output, error="")
            for output in responses
        ]
        mock_router_cls.return_value = mock_router
        mock_input.side_effect = [
            "Operators",
            "A working CLI",
            "Run pytest",
            "skip",
            "plan",
        ]

        with patch("builtins.print") as mock_print:
            spec = run_brainstorm_discussion("Build a CLI", self.config)

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("Current understanding:", printed)
        self.assertIn("Brainstorming summary:", printed)
        self.assertEqual(mock_input.call_args_list[-1].args[0], "Continue brainstorming or create plan? (continue/plan/edit): ")
        self.assertEqual(mock_router.run.call_count, 4)
        self.assertIn("## Outcome", spec)

    @patch("agent_loop.intake.ModelRouter")
    def test_draft_spec_uses_central_router(self, mock_router_cls):
        config = Config({
            "state_dir": str(Path(self.tmpdir.name) / "fallback-state"),
            "logs_dir": str(Path(self.tmpdir.name) / "fallback-state" / "logs"),
            "routes": {
                "intake": [
                    {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
                    {"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"},
                ],
                "implementation": [],
            },
        })

        succeeded = MagicMock()
        succeeded.success = True
        succeeded.output = '{"draft_spec": "# Compact Spec\\n\\n## Outcome\\nDone"}'
        succeeded.error = ""
        mock_router = MagicMock()
        mock_router.run.return_value = succeeded
        mock_router_cls.return_value = mock_router

        spec = draft_compact_spec("Goal", "", config)

        self.assertIn("## Outcome", spec)
        mock_router.run.assert_called_once()
        self.assertEqual(mock_router.run.call_args.kwargs["profile"], "intake")

    @patch("agent_loop.intake.ModelRouter")
    def test_model_calls_print_progress_before_waiting(self, mock_router_cls):
        succeeded = MagicMock()
        succeeded.success = True
        succeeded.output = '{"draft_spec": "# Compact Spec\\n\\n## Outcome\\nDone"}'
        succeeded.error = ""
        mock_router = MagicMock()
        mock_router.run.return_value = succeeded
        mock_router_cls.return_value = mock_router

        with patch("builtins.print") as mock_print:
            draft_compact_spec("Goal", "", self.config)

        printed = [call.args[0] for call in mock_print.call_args_list if call.args]
        self.assertIn("Draft spec thinking...", printed)

    @patch("agent_loop.intake.run_ui_branch")
    @patch("agent_loop.intake.run_brainstorm_discussion")
    @patch("agent_loop.intake.run_spec_review")
    @patch("builtins.input")
    def test_spec_intake_defers_ui_phase(self, mock_input, mock_review, mock_brainstorm, mock_ui_branch):
        mock_brainstorm.return_value = "# Compact Spec\n\n## Outcome\nBuild a dashboard"
        mock_review.return_value = ("approved", "# Compact Spec\n\n## Outcome\nBuild a dashboard")
        mock_input.side_effect = ["yes", "yes"]

        spec = run_spec_intake("Build a dashboard", self.config)

        self.assertIn("Build a dashboard", spec)
        mock_ui_branch.assert_not_called()
        self.assertEqual(mock_input.call_args_list[0].args[0], "Approve this spec and start planning? (yes/no/edit): ")

    @patch("agent_loop.intake.ModelRouter")
    def test_model_failures_print_non_empty_diagnostic(self, mock_router_cls):
        config = Config({
            "state_dir": str(Path(self.tmpdir.name) / "diagnostic-state"),
            "logs_dir": str(Path(self.tmpdir.name) / "diagnostic-state" / "logs"),
            "routes": {
                "planning": [{"provider": "agy", "model": "Gemini 3.1 Pro (High)"}],
                "implementation": [],
            },
        })

        failed = MagicMock()
        failed.success = False
        failed.exit_code = 0
        failed.output = ""
        failed.error = ""
        failed.quota_exhausted = False
        failed.auth_required = True
        failed.transient_failure = False
        failed.unavailable = False

        mock_router = MagicMock()
        mock_router.run.return_value = failed
        mock_router_cls.return_value = mock_router

        with patch("builtins.print") as mock_print:
            draft_compact_spec("Goal", "", config)

        warnings = [
            call.args[0]
            for call in mock_print.call_args_list
            if call.args and "Draft spec model call failed" in call.args[0]
        ]
        self.assertTrue(warnings)
        self.assertFalse(warnings[0].rstrip().endswith("Error:"))
        self.assertIn("auth required", warnings[0])

    @patch("agent_loop.intake.ModelRouter")
    def test_adapter_construction_failure_is_reported_by_router(self, mock_router_cls):
        config = Config({
            "state_dir": str(Path(self.tmpdir.name) / "construction-fallback-state"),
            "logs_dir": str(Path(self.tmpdir.name) / "construction-fallback-state" / "logs"),
            "routes": {
                "planning": [
                    {"provider": "agy", "model": "Gemini 3.1 Pro (High)"},
                    {"provider": "codex", "model": "gpt-5.5"},
                ],
                "implementation": [],
            },
        })

        succeeded = MagicMock()
        succeeded.success = True
        succeeded.output = '{"draft_spec": "# Compact Spec\\n\\n## Outcome\\nDone"}'
        succeeded.error = ""

        mock_router = MagicMock()
        mock_router.run.return_value = succeeded
        mock_router_cls.return_value = mock_router

        spec = draft_compact_spec("Goal", "", config)

        self.assertIn("## Outcome", spec)
        mock_router.run.assert_called_once()

if __name__ == "__main__":
    unittest.main()
