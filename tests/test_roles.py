"""Tests for the professional role taxonomy: loading, switching, compilation."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from alfred import cli, roles, runtime  # noqa: E402
from alfred.roles import RoleError  # noqa: E402

# The taxonomy contract from the specification. Every id must exist.
REQUIRED_BY_CATEGORY = {
    "Strategist": [
        "strategist-brand", "strategist-marketing", "strategist-content", "strategist-digital",
        "strategist-growth", "strategist-social-media", "strategist-corporate",
        "strategist-investment", "strategist-market-finance", "strategist-communications",
        "strategist-political",
    ],
    "Advisor": [
        "advisor-financial", "advisor-investment", "advisor-wealth", "advisor-tax",
        "advisor-risk", "advisor-transaction", "advisor-policy", "advisor-legal",
        "advisor-government-special", "advisor-academic", "advisor-career", "advisor-technical",
    ],
    "Consultant": [
        "consultant-management", "consultant-strategy", "consultant-it", "consultant-hr",
        "consultant-tax", "consultant-audit-risk", "consultant-technical",
        "consultant-implementation",
    ],
    "Specialist": [
        "specialist-marketing", "specialist-compliance", "specialist-procurement",
        "specialist-hr", "specialist-recruitment", "specialist-payroll", "specialist-security",
        "specialist-customer-success",
    ],
    "Associate": [
        "associate-investment-banking", "associate-consulting", "associate-legal",
        "associate-research", "associate-marketing",
    ],
    "Planner": [
        "planner-financial", "planner-supply-chain", "planner-urban", "planner-media",
        "planner-workforce",
    ],
    "Officer": [
        "officer-compliance", "officer-risk", "officer-credit", "officer-programme",
        "officer-procurement", "officer-human-resources",
    ],
    "Researcher": [
        "researcher-market", "researcher-policy", "researcher-ux", "researcher-data-scientist",
        "researcher-scientist",
    ],
    "Architect": [
        "architect-solutions", "architect-data", "architect-enterprise", "architect-security",
    ],
}
REQUIRED_AUDIT_CONTROLLERS = [
    "auditor-internal", "auditor-external", "auditor-it",
    "controller-financial", "controller-credit",
]

REQUIRED_FIELDS = ("**Role:**", "**Category:**", "**Lens:**", "**Methodology:**", "**Output format:**")


class RegistryTests(unittest.TestCase):
    """The markdown registry must parse completely and consistently."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)
        self.registry = roles.load_registry()

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_expected_role_count(self) -> None:
        self.assertEqual(len(self.registry), 69)

    def test_every_specified_role_exists(self) -> None:
        missing: list[str] = []
        for category, ids in REQUIRED_BY_CATEGORY.items():
            for role_id in ids:
                if role_id not in self.registry:
                    missing.append(f"{category}:{role_id}")
        for role_id in REQUIRED_AUDIT_CONTROLLERS:
            if role_id not in self.registry:
                missing.append(f"Auditors/Controllers:{role_id}")
        self.assertEqual(missing, [], f"missing roles: {missing}")

    def test_no_unexpected_roles(self) -> None:
        expected = {r for ids in REQUIRED_BY_CATEGORY.values() for r in ids} | set(
            REQUIRED_AUDIT_CONTROLLERS
        )
        self.assertEqual(set(self.registry), expected)

    def test_ids_are_normalised_and_unique(self) -> None:
        for role_id in self.registry:
            with self.subTest(role_id=role_id):
                self.assertEqual(role_id, role_id.lower())
                self.assertNotIn(" ", role_id)
                self.assertNotIn("_", role_id)
                self.assertIn("-", role_id)

    def test_every_block_has_all_required_fields(self) -> None:
        for role_id, block in self.registry.items():
            with self.subTest(role_id=role_id):
                for field in REQUIRED_FIELDS:
                    self.assertIn(field, block, f"{role_id} missing {field}")

    def test_every_block_opens_with_role_marker(self) -> None:
        for role_id, block in self.registry.items():
            with self.subTest(role_id=role_id):
                self.assertTrue(block.startswith(f"### role: {role_id}"))

    def test_no_block_leaks_into_another(self) -> None:
        for role_id, block in self.registry.items():
            with self.subTest(role_id=role_id):
                self.assertEqual(block.count("### role: "), 1)

    def test_role_ids_sorted_and_stable(self) -> None:
        self.assertEqual(roles.role_ids(), sorted(roles.role_ids()))
        self.assertEqual(roles.role_ids(), roles.role_ids())


class ResolutionTests(unittest.TestCase):
    """Role lookup, normalisation, and failure paths."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_get_role_returns_block(self) -> None:
        block = roles.get_role("advisor-tax")
        self.assertIn("### role: advisor-tax", block)
        self.assertIn("Tax Advisor", block)

    def test_lookup_is_case_insensitive(self) -> None:
        self.assertEqual(roles.get_role("ADVISOR-TAX"), roles.get_role("advisor-tax"))

    def test_underscores_and_spaces_normalise(self) -> None:
        self.assertEqual(roles.get_role("advisor_tax"), roles.get_role("advisor-tax"))
        self.assertEqual(roles.get_role("  Advisor Tax "), roles.get_role("advisor-tax"))

    def test_unknown_role_raises_with_suggestion(self) -> None:
        with self.assertRaises(RoleError) as ctx:
            roles.get_role("advisor-tx")
        self.assertIn("advisor-tax", str(ctx.exception))

    def test_reversed_order_id_still_suggests(self) -> None:
        with self.assertRaises(RoleError) as ctx:
            roles.get_role("brand strategist")
        self.assertIn("strategist-brand", str(ctx.exception))

    def test_totally_unknown_role_raises(self) -> None:
        with self.assertRaises(RoleError):
            roles.get_role("zzzz-not-a-role")

    def test_title_and_category_read_from_fields(self) -> None:
        self.assertEqual(roles.role_title("architect-security"), "Security Architect")
        self.assertEqual(roles.role_category("architect-security"), "Architect")

    def test_categories_cover_every_role(self) -> None:
        grouped = roles.categories()
        flat = [r for ids in grouped.values() for r in ids]
        self.assertEqual(sorted(flat), roles.role_ids())
        self.assertEqual(sum(len(v) for v in grouped.values()), 69)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(RoleError):
            roles.load_registry("/nonexistent/07_roles.md")

    def test_empty_registry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.md"
            empty.write_text("# no roles here\n", encoding="utf-8")
            with self.assertRaises(RoleError):
                roles.load_registry(empty)

    def test_duplicate_id_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dup = Path(tmp) / "dup.md"
            dup.write_text(
                "### role: advisor-tax\n**Role:** A\n### role: advisor-tax\n**Role:** B\n",
                encoding="utf-8",
            )
            with self.assertRaises(RoleError):
                roles.load_registry(dup)


class CompilationTests:
    """Placeholder namespace kept for readability of the suite."""


class ApplyRoleTests(unittest.TestCase):
    """Prompt compilation and switching."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)
        self.base = runtime.load_system_prompt()

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_apply_role_appends_intro_and_activation(self) -> None:
        out = roles.apply_role(self.base, "auditor-internal")
        self.assertIn("ACTIVE PROFESSIONAL ROLE", out)
        self.assertIn("**Adopted role:** `auditor-internal`", out)
        self.assertTrue(out.index("ACTIVE PROFESSIONAL ROLE") > out.index("BLUF"))

    def test_base_content_is_preserved(self) -> None:
        out = roles.apply_role(self.base, "planner-workforce")
        self.assertIn("CORE IDENTITY", out)
        self.assertIn("EXECUTIVE COMMUNICATION PROTOCOL", out)
        self.assertIn("TOOLS & RESEARCH", out)
        self.assertTrue(out.startswith(self.base.rstrip()))

    def test_activation_note_appears_last(self) -> None:
        out = roles.apply_role(self.base, "architect-data")
        self.assertIn("**Adopted role:** `architect-data`", out)
        self.assertTrue(out.rstrip().endswith("section 2.7 above."))

    def test_activation_does_not_duplicate_existing_block(self) -> None:
        out = roles.apply_role(self.base, "advisor-tax")
        # The taxonomy is already compiled into the base prompt, so the role
        # definition must appear exactly once - no redundant second copy.
        self.assertEqual(out.count("### role: advisor-tax"), 1)
        self.assertIn("**Adopted role:** `advisor-tax`", out)

    def test_switching_updates_the_active_role(self) -> None:
        a = roles.apply_role(self.base, "advisor-tax")
        b = roles.apply_role(a, "planner-urban")
        self.assertEqual(roles.active_role(a), "advisor-tax")
        self.assertEqual(roles.active_role(b), "planner-urban")
        self.assertEqual(b.count("### role: planner-urban"), 1)
        self.assertEqual(b.count("### role: advisor-tax"), 1)

    def test_active_role_none_when_unactivated(self) -> None:
        self.assertIsNone(roles.active_role(self.base))

    def test_role_appended_in_full_when_block_absent(self) -> None:
        lean = roles.apply_role("A minimal system prompt.", "auditor-internal")
        self.assertIn("### role: auditor-internal", lean)
        self.assertEqual(roles.active_role(lean), "auditor-internal")

    def test_role_does_not_relax_bluf(self) -> None:
        out = roles.apply_role(self.base, "strategist-growth")
        for marker in ("BLUF", "Best-Case", "Zero Fluff", "Cite retrieved claims inline"):
            with self.subTest(marker=marker):
                self.assertIn(marker, out)

    def test_applying_same_role_twice_is_idempotent(self) -> None:
        once = roles.apply_role(self.base, "advisor-risk")
        twice = roles.apply_role(once, "advisor-risk")
        # Re-activating the same role must not duplicate its definition.
        self.assertEqual(twice.count("### role: advisor-risk"), 1)
        self.assertEqual(roles.active_role(twice), "advisor-risk")
        self.assertLessEqual(len(twice), len(once) + 400)

    def test_apply_role_to_empty_prompt_raises(self) -> None:
        with self.assertRaises(RoleError):
            roles.apply_role("   ", "advisor-tax")

    def test_compiled_prompt_size_is_bounded(self) -> None:
        out = roles.apply_role(self.base, "auditor-external")
        # A single role must add a small, bounded slice to the prompt.
        self.assertLess(len(out) - len(self.base), 3000)


class CliRoleTests(unittest.TestCase):
    """CLI surface for listing and adopting roles."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def _run(self, argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        clean = {k: v for k, v in os.environ.items() if k != "ALFRED_API_KEY"}
        if env:
            clean.update(env)
        with mock.patch.dict(os.environ, clean, clear=True):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_roles_lists_all(self) -> None:
        code, out, _ = self._run(["roles"])
        self.assertEqual(code, 0)
        self.assertIn("69 roles", out)
        self.assertIn("advisor-tax", out)

    def test_roles_json_shape(self) -> None:
        code, out, _ = self._run(["roles", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["total"], 69)
        self.assertEqual(sum(len(v) for v in data["categories"].values()), 69)

    def test_roles_category_filter(self) -> None:
        code, out, _ = self._run(["roles", "--category", "Architect"])
        self.assertEqual(code, 0)
        self.assertIn("architect-solutions", out)
        self.assertNotIn("advisor-tax", out)

    def test_roles_category_filter_is_case_insensitive(self) -> None:
        code, out, _ = self._run(["roles", "--category", "architect"])
        self.assertEqual(code, 0)
        self.assertIn("architect-data", out)

    def test_roles_unknown_category_exits_one(self) -> None:
        code, _, err = self._run(["roles", "--category", "Nope"])
        self.assertEqual(code, 1)
        self.assertIn("unknown category", err)

    def test_roles_show_prints_block(self) -> None:
        code, out, _ = self._run(["roles", "--show", "architect-data"])
        self.assertEqual(code, 0)
        self.assertIn("### role: architect-data", out)

    def test_roles_show_unknown_exits_one(self) -> None:
        code, _, err = self._run(["roles", "--show", "nope-nope"])
        self.assertEqual(code, 1)
        self.assertIn("unknown role", err)

    def test_role_flag_flows_into_ask(self) -> None:
        captured: dict[str, str] = {}

        def fake_chat(user_text, system_prompt, settings):
            captured["system"] = system_prompt
            return "ok"

        with mock.patch.object(runtime, "chat", side_effect=fake_chat), \
             mock.patch.dict(os.environ, {"ALFRED_API_KEY": "sk-x"}, clear=False):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["ask", "q", "--role", "auditor-internal"])

        self.assertEqual(code, 0)
        self.assertIn("### role: auditor-internal", captured["system"])
        self.assertIn("ACTIVE PROFESSIONAL ROLE", captured["system"])

    def test_ask_without_role_has_no_role_block(self) -> None:
        captured: dict[str, str] = {}

        def fake_chat(user_text, system_prompt, settings):
            captured["system"] = system_prompt
            return "ok"

        with mock.patch.object(runtime, "chat", side_effect=fake_chat), \
             mock.patch.dict(os.environ, {"ALFRED_API_KEY": "sk-x"}, clear=False):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["ask", "q"])

        self.assertEqual(code, 0)
        self.assertNotIn("ACTIVE PROFESSIONAL ROLE", captured["system"])

    def test_ask_with_unknown_role_exits_one(self) -> None:
        code, _, err = self._run(["ask", "q", "--role", "totally-bogus"],
                                 env={"ALFRED_API_KEY": "sk-x"})
        self.assertEqual(code, 1)
        self.assertIn("unknown role", err)

    def test_chat_parser_exposes_role(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["chat", "--role", "advisor-tax"])
        self.assertEqual(args.role, "advisor-tax")

    def test_research_parser_exposes_role(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["research", "q", "--role", "planner-urban"])
        self.assertEqual(args.role, "planner-urban")

    def test_research_ask_applies_role(self) -> None:
        captured: dict[str, object] = {}

        def fake_chat_messages(messages, settings):
            captured["messages"] = messages
            return "done"

        with mock.patch("alfred.tools.search.search") as fake_search, \
             mock.patch.object(runtime, "chat_messages", side_effect=fake_chat_messages), \
             mock.patch.dict(os.environ, {"ALFRED_API_KEY": "sk-x"}, clear=False):
            fake_search.return_value = [
                roles_mod_result()
            ]
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["research", "q", "--ask", "--role", "advisor-policy"])

        self.assertEqual(code, 0)
        system = captured["messages"][0]["content"]
        self.assertIn("### role: advisor-policy", system)


def roles_mod_result():
    from alfred.tools.search import SearchResult
    return SearchResult(title="T", url="https://example.com", snippet="S", provider="urllib")


class BuildIntegrationTests(unittest.TestCase):
    """The registry module must be wired into the assembled prompt."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_registry_module_is_registered(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bp", REPO_ROOT / "scripts" / "build_prompt.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertIn("domains/07_roles.md", builder.MODULES)
        self.assertIn("PROFESSIONAL ROLE TAXONOMY", builder.REQUIRED_MARKERS)

    def test_assembled_prompt_contains_taxonomy(self) -> None:
        prompt = runtime.load_system_prompt()
        self.assertIn("PROFESSIONAL ROLE TAXONOMY", prompt)
        self.assertIn("### role: strategist-brand", prompt)

    def test_artifact_is_in_sync(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bp2", REPO_ROOT / "scripts" / "build_prompt.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        current = (REPO_ROOT / "ALFRED.system.md").read_text(encoding="utf-8")
        self.assertEqual(current, builder.assemble())


if __name__ == "__main__":
    unittest.main()
