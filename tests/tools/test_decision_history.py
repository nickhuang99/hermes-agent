"""Tests for the decision_history feature — decision_context parameter on skill_manage.

The decision_context parameter (task-001) records agent decision rationale to
DECISION_LOG.md in the skill directory. These tests verify creation, appending
on patch, format correctness, backward compatibility, and skill_view integration
(task-002, reading DECISION_LOG.md via linked_files + file_path).
"""

import json
from pathlib import Path

import pytest

from hermes_constants import get_hermes_home
from tools.skill_manager_tool import skill_manage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_skills_dir(monkeypatch):
    """Align skill_manager_tool.SKILLS_DIR and skills_tool.SKILLS_DIR with the
    test-session HERMES_HOME.

    ``SKILLS_DIR`` is a module-level constant resolved at import time, before
    the conftest monkeypatches ``HERMES_HOME``.  Without this fixture, create
    writes to the real ~/.hermes/skills/ while _find_skill (patch/edit/
    delete) looks in the temp dir.  Patching it here makes them agree.

    Also patches ``tools.skills_tool.SKILLS_DIR`` so skill_view can find
    skills created during the test (both modules have their own import-time
    SKILLS_DIR constant).
    """
    import tools.skill_manager_tool as smt
    import tools.skills_tool as st
    test_skills_dir = get_hermes_home() / "skills"
    monkeypatch.setattr(smt, "SKILLS_DIR", test_skills_dir)
    monkeypatch.setattr(st, "SKILLS_DIR", test_skills_dir)
    return test_skills_dir


# ── Helpers ──────────────────────────────────────────────────────────────


def _skill_dir(name: str) -> Path:
    """Return the filesystem path for a skill created during this test run."""
    return get_hermes_home() / "skills" / name


def _decision_log_path(name: str) -> Path:
    """Path to DECISION_LOG.md for a given skill name."""
    return _skill_dir(name) / "DECISION_LOG.md"


def _make_skill_content(name: str, description: str = "A test skill.") -> str:
    """Minimal valid SKILL.md content for create."""
    return (
        f"---\n"
        f"name: {name}\n"
        f"description: \"{description}\"\n"
        f"---\n\n"
        f"# {name}\n\n"
        f"Test skill body.\n"
    )


def _make_dc(diagnosis="test diagnosis", evidence="test evidence",
             outcome="accept"):
    """Minimal valid decision_context dict."""
    return {"diagnosis": diagnosis, "evidence": evidence, "outcome": outcome}


# ---------------------------------------------------------------------------
# Test 1: create with decision_context writes DECISION_LOG.md
# ---------------------------------------------------------------------------

class TestCreateWithDecisionContext:
    def test_create_writes_decision_log(self):
        name = "test-create-with-dc"
        result = json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(
                diagnosis="initial creation for testing",
                evidence="manual review",
                outcome="accept",
            ),
        ))
        assert result["success"], f"Create failed: {result}"

        # DECISION_LOG.md must exist
        log_path = _decision_log_path(name)
        assert log_path.exists(), f"DECISION_LOG.md not found at {log_path}"

        content = log_path.read_text(encoding="utf-8")
        assert "# Decision Log" in content
        assert f"Skill: `{name}`" in content
        assert "initial creation for testing" in content
        assert "manual review" in content
        assert "accept" in content

    def test_create_records_timestamp_and_action(self):
        name = "test-create-timestamp"
        result = json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(outcome="accept"),
        ))
        assert result["success"], f"Create failed: {result}"

        content = _decision_log_path(name).read_text(encoding="utf-8")
        # Check for timestamp + action pattern: "## YYYY-MM-DDTHH:MM:SSZ — create (accept)"
        assert " — create (accept)" in content, (
            f"No action header found in:\n{content}"
        )
        # Timestamp should start with a year and have the ISO format
        assert "## 20" in content, f"No timestamp found in:\n{content}"


# ---------------------------------------------------------------------------
# Test 2: patch appends a new entry (iteration)
# ---------------------------------------------------------------------------

class TestPatchAppendsIteration:
    def test_patch_appends_second_entry(self):
        name = "test-patch-append"
        # First create with decision_context
        r1 = json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(
                diagnosis="first creation",
                outcome="accept",
            ),
        ))
        assert r1["success"], f"Create failed: {r1}"

        # Then patch with new decision_context
        r2 = json.loads(skill_manage(
            action="patch",
            name=name,
            old_string="Test skill body.",
            new_string="Updated skill body.",
            decision_context=_make_dc(
                diagnosis="fixed a bug in the body text",
                evidence="spot check passed",
                outcome="accept",
            ),
        ))
        assert r2["success"], f"Patch failed: {r2}"

        content = _decision_log_path(name).read_text(encoding="utf-8")
        # Should have two entries (two "## " headers)
        assert content.count("## 20") >= 2, (
            f"Expected at least 2 entries, got {content.count('## 20')}:\n{content}"
        )
        assert "first creation" in content
        assert "fixed a bug in the body text" in content

    def test_patch_without_decision_context_leaves_log_unchanged(self):
        name = "test-patch-no-dc"
        r1 = json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(
                diagnosis="initial",
                outcome="accept",
            ),
        ))
        assert r1["success"], f"Create failed: {r1}"

        before = _decision_log_path(name).read_text(encoding="utf-8")

        # Patch without decision_context
        r2 = json.loads(skill_manage(
            action="patch",
            name=name,
            old_string="Test skill body.",
            new_string="Updated body, no dc.",
        ))
        assert r2["success"], f"Patch failed: {r2}"

        after = _decision_log_path(name).read_text(encoding="utf-8")
        assert before == after, (
            "DECISION_LOG.md should not change when patching without decision_context"
        )


# ---------------------------------------------------------------------------
# Test 3: create WITHOUT decision_context does NOT write DECISION_LOG.md
# ---------------------------------------------------------------------------

class TestCreateWithoutDecisionContext:
    def test_create_without_dc_does_not_create_log(self):
        name = "test-create-no-dc"
        result = json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            # no decision_context
        ))
        assert result["success"], f"Create failed: {result}"

        log_path = _decision_log_path(name)
        assert not log_path.exists(), (
            f"DECISION_LOG.md should NOT exist when decision_context is omitted, "
            f"but found at {log_path}"
        )


# ---------------------------------------------------------------------------
# Test 4: decision log format correctness
# ---------------------------------------------------------------------------

class TestDecisionLogFormat:
    def test_file_starts_with_decision_log_header(self):
        name = "test-format-header"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(),
        )
        content = _decision_log_path(name).read_text(encoding="utf-8")
        assert content.startswith("# Decision Log"), (
            f"File should start with '# Decision Log', got:\n{content[:200]}"
        )

    def test_contains_skill_reference(self):
        name = "test-format-skill-ref"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(),
        )
        content = _decision_log_path(name).read_text(encoding="utf-8")
        assert f"Skill: `{name}`" in content, (
            f"Missing skill reference in:\n{content}"
        )

    def test_contains_diagnosis_field(self):
        name = "test-format-diagnosis"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(
                diagnosis="rate limit errors in search script",
            ),
        )
        content = _decision_log_path(name).read_text(encoding="utf-8")
        assert "**Diagnosis:** rate limit errors in search script" in content

    def test_contains_evidence_field(self):
        name = "test-format-evidence"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(
                evidence="pass 3/5, fail 2/5 (timeout)",
            ),
        )
        content = _decision_log_path(name).read_text(encoding="utf-8")
        assert "**Evidence:** pass 3/5, fail 2/5 (timeout)" in content

    def test_contains_outcome_field(self):
        name = "test-format-outcome"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(outcome="revise"),
        )
        content = _decision_log_path(name).read_text(encoding="utf-8")
        assert "**Outcome:** revise" in content

    def test_empty_fields_still_recorded(self):
        """Even empty decision_context values should be written."""
        name = "test-format-empty"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context={"diagnosis": "", "evidence": "", "outcome": ""},
        )
        content = _decision_log_path(name).read_text(encoding="utf-8")
        assert "**Diagnosis:** " in content
        assert "**Evidence:** " in content
        assert "**Outcome:** " in content

    def test_outcome_is_defer(self):
        name = "test-format-defer"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(
                outcome="defer",
                diagnosis="need more data",
            ),
        )
        content = _decision_log_path(name).read_text(encoding="utf-8")
        assert " — create (defer)" in content
        assert "**Outcome:** defer" in content


# ---------------------------------------------------------------------------
# Test 5: backward compatibility — existing skill_manage tests still work
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_create_without_decision_context_still_works(self):
        """Ensure the new optional parameter doesn't break existing create flow."""
        name = "test-bw-create"
        result = json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
        ))
        assert result["success"], f"Create failed: {result}"
        # Skill directory must exist (standard create behavior)
        assert _skill_dir(name).is_dir()
        assert (_skill_dir(name) / "SKILL.md").exists()

    def test_patch_without_decision_context_still_works(self):
        """Ensure the new optional parameter doesn't break existing patch flow."""
        name = "test-bw-patch"
        json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
        ))
        result = json.loads(skill_manage(
            action="patch",
            name=name,
            old_string="Test skill body.",
            new_string="Patched skill body.",
        ))
        assert result["success"], f"Patch failed: {result}"
        content = (_skill_dir(name) / "SKILL.md").read_text(encoding="utf-8")
        assert "Patched skill body." in content
        assert "Test skill body." not in content

    def test_edit_without_decision_context_still_works(self):
        """Ensure the new optional parameter doesn't break existing edit flow."""
        name = "test-bw-edit"
        json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
        ))
        result = json.loads(skill_manage(
            action="edit",
            name=name,
            content=_make_skill_content(name, "edited description"),
        ))
        assert result["success"], f"Edit failed: {result}"
        content = (_skill_dir(name) / "SKILL.md").read_text(encoding="utf-8")
        assert "edited description" in content

    def test_delete_without_decision_context_still_works(self):
        """Delete should work regardless of decision_context (and not write log)."""
        name = "test-bw-delete"
        json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
        ))
        result = json.loads(skill_manage(
            action="delete",
            name=name,
        ))
        assert result["success"], f"Delete failed: {result}"
        # After delete, skill dir should not exist
        assert not _skill_dir(name).exists()

    def test_create_with_none_decision_context_behaves_same_as_omitted(self):
        """Explicit decision_context=None should be identical to omitting it."""
        name = "test-bw-none-dc"
        result = json.loads(skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=None,
        ))
        assert result["success"], f"Create failed: {result}"
        assert not _decision_log_path(name).exists(), (
            "DECISION_LOG.md should not exist when decision_context is None"
        )


# ---------------------------------------------------------------------------
# Test 6: skill_view exposes DECISION_LOG.md in linked_files
# ---------------------------------------------------------------------------

class TestSkillViewExposesDecisionLog:
    def test_linked_files_contains_decision_log(self):
        """After creating with decision_context, skill_view linked_files includes it."""
        name = "test-sv-linked"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(),
        )
        from tools.skills_tool import skill_view
        result = json.loads(skill_view(name))
        assert result["success"], f"skill_view failed: {result}"
        linked = result.get("linked_files", {})
        assert "DECISION_LOG.md" in str(linked), (
            f"linked_files should contain DECISION_LOG.md:\n"
            f"{json.dumps(linked, indent=2)}"
        )

    def test_skill_view_reads_decision_log_content(self):
        """skill_view(file_path='DECISION_LOG.md') returns the log content."""
        name = "test-sv-read"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            decision_context=_make_dc(),
        )
        from tools.skills_tool import skill_view
        result = json.loads(skill_view(name, file_path="DECISION_LOG.md"))
        assert result["success"], f"skill_view failed: {result}"
        assert "Decision Log" in result["content"]


# ---------------------------------------------------------------------------
# Test 7: skill without decision log has no linked_file reference
# ---------------------------------------------------------------------------

class TestSkillWithoutLogNoLinkedFile:
    def test_no_decision_log_in_linked_files(self):
        """Skill created without decision_context should not list DECISION_LOG.md."""
        name = "test-no-log-linked"
        skill_manage(
            action="create",
            name=name,
            content=_make_skill_content(name),
            # no decision_context
        )
        from tools.skills_tool import skill_view
        result = json.loads(skill_view(name))
        assert result["success"], f"skill_view failed: {result}"
        linked = result.get("linked_files", {})
        assert "DECISION_LOG.md" not in str(linked), (
            f"DECISION_LOG.md should NOT appear in linked_files:\n"
            f"{json.dumps(linked, indent=2)}"
        )
