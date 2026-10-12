"""Tests for the professional role taxonomy: loading, resolution, compilation.

Run with the standard library alone, or under pytest - both work:
    python3 -m unittest discover -s tests
    pytest tests/
"""
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

EXPECTED_ROLE_COUNT = 172
EXPECTED_SENIORITY_COUNT = 17

# Every role named in the specification, grouped by family.
REQUIRED_ROLES = {
    # Strategists & Advisors (pre-existing)
    "strategist-brand", "strategist-marketing", "strategist-content", "strategist-digital",
    "strategist-growth", "strategist-social-media", "strategist-corporate",
    "strategist-investment", "strategist-market-finance", "strategist-communications",
    "strategist-political", "advisor-financial", "advisor-investment", "advisor-wealth",
    "advisor-tax", "advisor-risk", "advisor-transaction", "advisor-policy", "advisor-legal",
    "advisor-government-special", "advisor-academic", "advisor-career", "advisor-technical",
    # Consultants & Specialists
    "consultant-management", "consultant-strategy", "consultant-it", "consultant-hr",
    "consultant-tax", "consultant-audit-risk", "consultant-technical",
    "consultant-implementation", "specialist-marketing", "specialist-compliance",
    "specialist-procurement", "specialist-hr", "specialist-recruitment",
    "specialist-payroll", "specialist-security", "specialist-customer-success",
    # Coordinators, Managers & Executives
    "project-coordinator", "events-coordinator", "logistics-coordinator", "hr-coordinator",
    "product-manager", "project-manager", "account-manager", "operations-manager",
    "brand-manager", "account-executive", "sales-executive", "marketing-executive",
    # Representatives
    "sales-representative", "customer-service-representative", "insurance-agent",
    # Engineers & Developers
    "software-engineer", "data-engineer", "sales-engineer", "network-engineer",
    "reliability-engineer", "software-developer", "web-developer", "mobile-developer",
    "business-developer",
    # Designers, Producers & Writers
    "product-designer", "graphic-designer", "ux-designer", "brand-designer",
    "content-producer", "video-producer", "project-producer", "copywriter",
    "content-writer", "technical-writer",
    # Administrators, Assistants & Technicians
    "systems-administrator", "database-administrator", "office-administrator",
    "executive-assistant", "administrative-assistant", "research-assistant",
    "lab-technician", "it-support-technician", "field-technician",
    # Front office desks
    "equity-trader", "fx-trader", "rates-trader", "credit-trader", "commodities-trader",
    "derivatives-trader", "options-trader", "crypto-trader",
    # Market structure & buy-side trading
    "market-maker", "sales-trader", "institutional-sales", "portfolio-manager",
    "quant-trader", "quantitative-researcher", "algo-trader", "prop-trader",
    "prop-desk-head",
    # Middle & back office / control
    "trading-assistant", "trade-support-analyst", "market-risk-analyst",
    "trade-operations-analyst", "trading-compliance-officer",
    # Structuring & strategy
    "exotic-derivatives-structurer", "fx-strategist", "rates-strategist",
    "credit-strategist", "execution-trader",
    # Investment banking ladder
    "ib-analyst", "ib-associate", "ib-vice-president", "ib-director", "ib-managing-director",
    # Prop firm ladder
    "prop-junior-trader", "prop-senior-trader", "prop-head-trader",
    # Buy-side, VC
    "general-partner", "operating-partner", "venture-partner", "deal-lead",
    "corporate-development", "restructuring-advisor",
    # Legal & governance
    "general-counsel", "chief-legal-officer", "corporate-secretary",
    "data-protection-officer", "ai-governance-officer",
    # Supply chain
    "supply-chain-manager", "logistics-architect",
    # Frontier tech
    "ai-ml-engineer", "mlops-engineer", "devops-sre", "ethical-hacker", "cryptographer",
    # Modern hybrid
    "growth-hacker", "customer-success-manager", "chief-of-staff", "revops",
    "prompt-engineer", "product-owner", "scrum-master",
    # Associates, Planners & Officers (pre-existing)
    "associate-investment-banking", "associate-consulting", "associate-legal",
    "associate-research", "associate-marketing", "planner-financial", "planner-supply-chain",
    "planner-urban", "planner-media", "planner-workforce", "officer-compliance",
    "officer-risk", "officer-credit", "officer-programme", "officer-procurement",
    "officer-human-resources",
    # Researchers / Scientists / Architects / Auditors (pre-existing)
    "researcher-market", "researcher-policy", "researcher-ux", "researcher-data-scientist",
    "researcher-scientist", "architect-solutions", "architect-data", "architect-enterprise",
    "architect-security", "auditor-internal", "auditor-external", "auditor-it",
    "controller-financial", "controller-credit",
}

REQUIRED_SENIORITY = {
    "intern", "junior", "mid-level", "senior", "lead", "staff", "principal", "analyst",
    "associate", "vice-president", "director", "managing-director", "head", "partner",
    "executive", "president", "chief",
}

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
        self.assertEqual(len(self.registry), EXPECTED_ROLE_COUNT)

    def test_every_specified_role_exists(self) -> None:
        missing = sorted(REQUIRED_ROLES - set(self.registry))
        self.assertEqual(missing, [], f"missing roles: {missing}")

    def test_no_roles_outside_the_specification(self) -> None:
        extra = sorted(set(self.registry) - REQUIRED_ROLES)
        self.assertEqual(extra, [], f"unexpected roles: {extra}")

    def test_every_specified_seniority_exists(self) -> None:
        self.assertEqual(set(roles.seniority_ids()), REQUIRED_SENIORITY)

    def test_ids_are_normalised(self) -> None:
        for role_id in self.registry:
            with self.subTest(role_id=role_id):
                self.assertEqual(role_id, role_id.lower())
                self.assertNotIn(" ", role_id)
                self.assertNotIn("_", role_id)

    def test_no_duplicate_ids(self) -> None:
        self.assertEqual(len(set(self.registry)), len(self.registry))

    def test_every_block_has_all_required_fields(self) -> None:
        for role_id, block in self.registry.items():
            with self.subTest(role_id=role_id):
                for field in REQUIRED_FIELDS:
                    self.assertIn(field, block, f"{role_id} missing {field}")

    def test_every_block_opens_with_its_marker(self) -> None:
        for role_id, block in self.registry.items():
            with self.subTest(role_id=role_id):
                self.assertTrue(block.startswith(f"### role: {role_id}"))
                self.assertEqual(block.count("### role: "), 1)

    def test_every_seniority_block_has_fields(self) -> None:
        for seniority_id, block in roles.load_seniority().items():
            with self.subTest(seniority=seniority_id):
                for field in ("**Title prefix:**", "**Scope:**", "**Authority:**"):
                    self.assertIn(field, block)


class ResolutionTests(unittest.TestCase):
    """Role lookup, seniority composition, and failure paths."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_exact_role_resolution(self) -> None:
        r = roles.resolve("advisor-tax")
        self.assertEqual(r.role_id, "advisor-tax")
        self.assertIsNone(r.seniority_id)
        self.assertFalse(r.composed)
        self.assertEqual(r.display, "Tax Advisor")

    def test_lookup_is_case_insensitive_and_separator_tolerant(self) -> None:
        for variant in ("ADVISOR-TAX", "advisor_tax", "  Advisor Tax "):
            with self.subTest(variant=variant):
                self.assertEqual(roles.resolve(variant).role_id, "advisor-tax")

    def test_compound_id_with_seniority_prefix(self) -> None:
        r = roles.resolve("managing_director_equity_trader")
        self.assertEqual(r.role_id, "equity-trader")
        self.assertEqual(r.seniority_id, "managing-director")
        self.assertEqual(r.display, "Managing Director Equity Trader")
        self.assertTrue(r.composed)

    def test_of_connector_is_accepted(self) -> None:
        r = roles.resolve("head_of_credit_trader")
        self.assertEqual(r.role_id, "credit-trader")
        self.assertEqual(r.seniority_id, "head")

    def test_seniority_with_prefixed_role_id(self) -> None:
        # "data-scientist" is carried inside "researcher-data-scientist".
        r = roles.resolve("principal_data_scientist")
        self.assertEqual(r.role_id, "researcher-data-scientist")
        self.assertEqual(r.seniority_id, "principal")

    def test_role_containing_a_seniority_word_resolves_exactly(self) -> None:
        r = roles.resolve("chief_of_staff")
        self.assertEqual(r.role_id, "chief-of-staff")
        self.assertIsNone(r.seniority_id)

    def test_seniority_then_role_order(self) -> None:
        r = roles.resolve("junior_software_engineer")
        self.assertEqual(r.role_id, "software-engineer")
        self.assertEqual(r.seniority_id, "junior")

    def test_bare_seniority_is_rejected_with_guidance(self) -> None:
        for bare in ("managing_director", "junior", "chief", "head"):
            with self.subTest(bare=bare):
                with self.assertRaises(RoleError) as ctx:
                    roles.resolve(bare)
                self.assertIn("seniority modifier, not a role", str(ctx.exception))

    def test_unknown_role_raises(self) -> None:
        with self.assertRaises(RoleError):
            roles.resolve("zzzz-not-a-role")

    def test_all_compositions_resolve_for_every_role(self) -> None:
        # Every role must be reachable both bare and under a seniority.
        for role_id in roles.role_ids():
            with self.subTest(role_id=role_id):
                self.assertEqual(roles.resolve(role_id).role_id, role_id)
                composed = roles.resolve(f"senior_{role_id}")
                self.assertEqual(composed.role_id, role_id)
                self.assertEqual(composed.seniority_id, "senior")

    def test_categories_cover_every_role(self) -> None:
        grouped = roles.categories()
        flat = [r for ids in grouped.values() for r in ids]
        self.assertEqual(sorted(flat), roles.role_ids())
        self.assertEqual(len(flat), EXPECTED_ROLE_COUNT)
        self.assertGreaterEqual(len(grouped), 10)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(RoleError):
            roles.load_registry("/nonexistent/07_roles.md")

    def test_empty_registry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.md"
            empty.write_text("# no roles\n", encoding="utf-8")
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


class ApplyRoleTests(unittest.TestCase):
    """Prompt compilation and switching."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)
        self.base = runtime.load_system_prompt()

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_apply_inserts_activation_note(self) -> None:
        out = roles.apply_role(self.base, "auditor-internal")
        self.assertIn("ACTIVE PROFESSIONAL ROLE", out)
        self.assertIn("**Adopted role:** `auditor-internal`", out)
        self.assertTrue(out.index("ACTIVE PROFESSIONAL ROLE") > out.index("BLUF"))

    def test_activation_does_not_duplicate_existing_block(self) -> None:
        out = roles.apply_role(self.base, "advisor-tax")
        self.assertEqual(out.count("### role: advisor-tax"), 1)

    def test_composed_role_records_seniority(self) -> None:
        out = roles.apply_role(self.base, "managing_director_equity_trader")
        self.assertIn("**Adopted role:** `equity-trader`", out)
        self.assertIn("**Seniority:** `managing-director`", out)
        self.assertEqual(roles.active_role(out), "equity-trader")
        self.assertEqual(roles.active_seniority(out), "managing-director")

    def test_switching_updates_the_active_role(self) -> None:
        a = roles.apply_role(self.base, "advisor-tax")
        b = roles.apply_role(a, "planner-urban")
        self.assertEqual(roles.active_role(a), "advisor-tax")
        self.assertEqual(roles.active_role(b), "planner-urban")
        self.assertEqual(b.count("### role: planner-urban"), 1)

    def test_switching_seniority_updates_it(self) -> None:
        a = roles.apply_role(self.base, "junior_equity_trader")
        b = roles.apply_role(a, "managing_director_equity_trader")
        self.assertEqual(roles.active_seniority(a), "junior")
        self.assertEqual(roles.active_seniority(b), "managing-director")

    def test_reactivating_same_role_is_idempotent(self) -> None:
        once = roles.apply_role(self.base, "advisor-risk")
        twice = roles.apply_role(once, "advisor-risk")
        self.assertEqual(twice, once)

    def test_active_role_none_when_unactivated(self) -> None:
        self.assertIsNone(roles.active_role(self.base))
        self.assertIsNone(roles.active_seniority(self.base))

    def test_base_content_is_preserved(self) -> None:
        out = roles.apply_role(self.base, "planner-workforce")
        self.assertIn("CORE IDENTITY", out)
        self.assertIn("EXECUTIVE COMMUNICATION PROTOCOL", out)
        self.assertTrue(out.startswith(self.base.rstrip()))

    def test_role_does_not_relax_core_constraints(self) -> None:
        out = roles.apply_role(self.base, "strategist-growth")
        for marker in ("BLUF", "Best-Case", "Zero Fluff", "Cite retrieved claims inline"):
            with self.subTest(marker=marker):
                self.assertIn(marker, out)

    def test_role_does_not_relax_privacy_framework(self) -> None:
        out = roles.apply_role(self.base, "portfolio-manager")
        for marker in ("Zero-Leakage", "zero-leakage", "air-gapped", "defensive"):
            with self.subTest(marker=marker):
                self.assertIn(marker.lower(), out.lower())

    def test_activation_note_appears_last(self) -> None:
        out = roles.apply_role(self.base, "architect-data")
        self.assertIn("**Adopted role:** `architect-data`", out)
        self.assertTrue(out.rstrip().endswith("definition appears in section 2.7."))

    def test_apply_role_to_empty_prompt_raises(self) -> None:
        with self.assertRaises(RoleError):
            roles.apply_role("   ", "advisor-tax")

    def test_single_role_adds_a_bounded_slice(self) -> None:
        out = roles.apply_role(self.base, "auditor-external")
        self.assertLess(len(out) - len(self.base), 3000)


class PersonaFrameworkTests(unittest.TestCase):
    """The persona module must carry the sovereign/privacy/defensive framework."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)
        self.persona = (REPO_ROOT / "prompts" / "persona.md").read_text(encoding="utf-8")

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_sovereign_allegiance_present(self) -> None:
        self.assertIn("## Sovereign Allegiance", self.persona)
        self.assertIn("exclusively to the Principal", self.persona)

    def test_zero_leakage_framework_present(self) -> None:
        self.assertIn("## Zero-Leakage & Air-Gapped Privacy", self.persona)
        for marker in ("Local-first execution", "Zero external transmission of secrets",
                       "Breach protocol", "Redact before you externalise"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.persona)

    def test_defensive_risk_lens_present(self) -> None:
        self.assertIn("## Defensive Risk & Capital Preservation", self.persona)
        for marker in ("Survival before optimisation", "Size the downside",
                       "Preserve optionality"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.persona)

    def test_framework_does_not_override_honesty(self) -> None:
        # Loyalty must not be framed as licence to mislead.
        for marker in ("Flattery is a betrayal", "whole truth", "candor"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.persona)


class VoicePersonaTests(unittest.TestCase):
    """The persona must carry the Pennyworth voice without diluting rigor."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)
        self.persona = (REPO_ROOT / "prompts" / "persona.md").read_text(encoding="utf-8")

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_voice_section_present_with_required_traits(self) -> None:
        self.assertIn("## Voice, Tone & Demeanor", self.persona)
        for marker in ("Unflappable under pressure", "Absolute precision",
                       "British-inspired cadence", "Dry, understated wit",
                       "Protective familiarity", "A steady hand"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.persona)

    def test_principal_address_is_defined(self) -> None:
        self.assertIn("### Addressing the Principal", self.persona)
        self.assertIn("Master Fontaine", self.persona)

    def test_voice_is_declared_constant_across_modes(self) -> None:
        self.assertIn("### Voice Integration Across Every Mode", self.persona)
        for marker in ("trade order", "auditing a codebase", "policy brief"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.persona)

    def test_voice_has_explicit_bounds(self) -> None:
        bounds_start = self.persona.index("Bounds the voice must never cross:")
        bounds = self.persona[bounds_start:bounds_start + 1200]
        for marker in ("never obscures", "never appears inside precision-critical output",
                       "never substitutes for evidence", "never delays the BLUF",
                       "never claims experiences"):
            with self.subTest(marker=marker):
                self.assertIn(marker, bounds)

    def test_wit_must_not_cover_risk_or_bad_findings(self) -> None:
        self.assertIn("softens, or decorates a material risk, loss, or bad finding", self.persona)

    def test_voice_does_not_relax_candor(self) -> None:
        # Deference in manner, truth in content.
        self.assertIn("never dilutes candor in content", self.persona)
        self.assertIn("says \"no\"", self.persona)
        self.assertIn("Politeness is the manner; the truth is the message", self.persona)

    def test_voice_does_not_relax_privacy_or_defensive_lens(self) -> None:
        self.assertIn("Zero-Leakage & Air-Gapped Privacy", self.persona)
        self.assertIn("Defensive Risk & Capital Preservation", self.persona)

    def test_persona_models_the_restrained_voice(self) -> None:
        # The persona specifies no exclamation marks; the file should obey itself.
        self.assertNotIn("!", self.persona)


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
        self.assertIn(f"{EXPECTED_ROLE_COUNT} roles", out)
        self.assertIn("advisor-tax", out)
        self.assertIn("equity-trader", out)

    def test_roles_json_shape(self) -> None:
        code, out, _ = self._run(["roles", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["total"], EXPECTED_ROLE_COUNT)
        self.assertEqual(sum(len(v) for v in data["categories"].values()), EXPECTED_ROLE_COUNT)

    def test_roles_category_filter(self) -> None:
        code, out, _ = self._run(["roles", "--category", "Trader"])
        self.assertEqual(code, 0)
        self.assertIn("equity-trader", out)
        self.assertNotIn("advisor-tax", out)

    def test_roles_show_prints_block(self) -> None:
        code, out, _ = self._run(["roles", "--show", "architect-data"])
        self.assertEqual(code, 0)
        self.assertIn("### role: architect-data", out)

    def test_roles_show_unknown_exits_one(self) -> None:
        code, _, err = self._run(["roles", "--show", "nope-nope"])
        self.assertEqual(code, 1)
        self.assertIn("unknown role", err)

    def test_unknown_category_exits_one(self) -> None:
        code, _, err = self._run(["roles", "--category", "Nope"])
        self.assertEqual(code, 1)
        self.assertIn("unknown category", err)

    def test_role_flag_accepts_compound_id(self) -> None:
        captured: dict[str, str] = {}

        def fake_chat(user_text, system_prompt, settings):
            captured["system"] = system_prompt
            return "ok"

        with mock.patch.object(runtime, "chat", side_effect=fake_chat), \
             mock.patch.dict(os.environ, {"ALFRED_API_KEY": "sk-x"}, clear=False):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["ask", "q", "--role", "managing_director_equity_trader"])

        self.assertEqual(code, 0)
        self.assertEqual(roles.active_role(captured["system"]), "equity-trader")
        self.assertEqual(roles.active_seniority(captured["system"]), "managing-director")

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

    def test_bare_seniority_flag_exits_one(self) -> None:
        code, _, err = self._run(["ask", "q", "--role", "managing_director"],
                                 env={"ALFRED_API_KEY": "sk-x"})
        self.assertEqual(code, 1)
        self.assertIn("seniority modifier, not a role", err)

    def test_parsers_expose_role(self) -> None:
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["chat", "--role", "chief_of_staff"]).role, "chief_of_staff")
        self.assertEqual(
            parser.parse_args(["research", "q", "--role", "junior_quant_trader"]).role,
            "junior_quant_trader")


class SecretProtectionTests(unittest.TestCase):
    """Roles and seniority must never surface configured secrets."""

    def test_settings_repr_hides_both_secrets(self) -> None:
        s = runtime.resolve_settings(
            api_key="sk-llm-secret", environ={"TAVILY_API_KEY": "tvly-search-secret"})
        text = repr(s)
        self.assertNotIn("sk-llm-secret", text)
        self.assertNotIn("tvly-search-secret", text)

    def test_role_resolution_never_touches_env(self) -> None:
        with mock.patch.dict(os.environ, {"ALFRED_API_KEY": "sk-env-secret"}, clear=False):
            resolved = roles.resolve("managing_director_equity_trader")
            self.assertNotIn("sk-env-secret", repr(resolved))


class BuildIntegrationTests(unittest.TestCase):
    """The registry must be wired into the assembled prompt."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_registry_module_is_registered(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("bp", REPO_ROOT / "scripts" / "build_prompt.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertIn("domains/07_roles.md", builder.MODULES)
        self.assertIn("PROFESSIONAL ROLE TAXONOMY", builder.REQUIRED_MARKERS)

    def test_assembled_prompt_contains_taxonomy_and_persona(self) -> None:
        prompt = runtime.load_system_prompt()
        self.assertIn("PROFESSIONAL ROLE TAXONOMY", prompt)
        self.assertIn("### role: strategist-brand", prompt)
        self.assertIn("### role: equity-trader", prompt)
        self.assertIn("### seniority: managing-director", prompt)
        self.assertIn("Zero-Leakage & Air-Gapped Privacy", prompt)

    def test_artifact_is_in_sync(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("bp2", REPO_ROOT / "scripts" / "build_prompt.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        current = (REPO_ROOT / "ALFRED.system.md").read_text(encoding="utf-8")
        self.assertEqual(current, builder.assemble())


if __name__ == "__main__":
    unittest.main()
