# GREP_SUMMARY: ai-instructions sync integration protected-fail-fast never-overwrite determinism coder-rename project-filter manage-config lock-drift hermes-profile
# STRUCTURE: ┌synthetic canon + tmp consumer┐ → ○ cli_main(sync) → ◇ assert outputs/hashes/exit → ⎋ 8 сценариев T6.1
# region MODULE_CONTRACT
## @purpose  Платформенная обёртка-интеграция компилятора ai-instructions (DevPlan 001 T6.1):
##           protected-fail-fast, never-overwrite, детерминизм, kilo-ренейм роли Coder,
##           project-filter (@language/@stack), manage_config, lock-дрейф, hermes-эмиссия
##           в platform-профиль (profiles/platform/skills/)
## @scope    tests/unit; использует установленный пакет ai_instructions (dev extra, R12);
##           synthetic canon в tmp_path — без сети и без реального канона
## @invariants
##   - Никакого subprocess — прямой вызов cli.main() (native imports, тестовая политика)
##   - Никаких hardcoded путей вне tmp_path (Zero Hardcode Rule)
##   - Каждый тест: LDD-телеметрия IMP:7-10 + TRAP[TEST]
## @rationale  Интеграционный контур «компилятор ↔ платформа» тестируется на реальном
##   пакете с synthetic-каноном — изолированно, детерминированно, без Docker
# endregion MODULE_CONTRACT

import hashlib
import json
import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# Маркер ai_instructions (suite id ai-instructions в check-suite, DevPlan 001 T4.6)
pytestmark = [
    pytest.mark.ai_instructions,
    pytest.mark.static_audit,
]

STAMP_RE = "<!-- ai-instructions:"


def _print_ldd(caplog: pytest.LogCaptureFixture, needle: str | None = None) -> bool:
    """LDD-телеметрия: вывод IMP:7-10 траектории до asserts (Anti-Illusion Rule)."""
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp >= 7:
                print(record.message)
            if needle and needle in record.message:
                found = True
    print("--- END LDD TRAJECTORY ---")
    if needle:
        return found
    return any("[IMP:9]" in r.message for r in caplog.records)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_canon(root: Path) -> Path:
    """Synthetic canon: 2 правила (@protected + без директив), скилл, роли coder/architect."""
    rules = root / "rules"
    rules.mkdir(parents=True)
    (rules / "constitution.md").write_text(
        "# region MODULE_CONTRACT\n"
        "## @purpose  Core constitution\n"
        "## @scope    all\n"
        "## @invariants\n"
        "##   - @protected  true\n"
        "# endregion MODULE_CONTRACT\n\n"
        "**CORE DIRECTIVES**\n",
        encoding="utf-8",
    )
    (rules / "fail-fast.md").write_text(
        "# region MODULE_CONTRACT\n"
        "## @purpose  Fail fast\n"
        "## @scope    all\n"
        "## @invariants\n"
        "##   - @protected  true\n"
        "# endregion MODULE_CONTRACT\n\n"
        "**Fail-Fast Principle**\n",
        encoding="utf-8",
    )
    (rules / "python-rule.md").write_text(
        "# region MODULE_CONTRACT\n"
        "## @purpose  Python backend rule\n"
        "## @scope    backend\n"
        "## @invariants\n"
        "##   - @protected  true\n"
        "##   - @language python\n"
        "# endregion MODULE_CONTRACT\n\n"
        "# Python rule\n",
        encoding="utf-8",
    )
    (rules / "react-rule.md").write_text(
        "# region MODULE_CONTRACT\n"
        "## @purpose  React frontend rule\n"
        "## @scope    frontend\n"
        "## @invariants\n"
        "##   - @protected  true\n"
        "##   - @stack react\n"
        "##   - @language typescript\n"
        "# endregion MODULE_CONTRACT\n\n"
        "# React rule\n",
        encoding="utf-8",
    )
    (rules / "typescript-rule.md").write_text(
        "# region MODULE_CONTRACT\n"
        "## @purpose  TypeScript backend/bot rule (ai-project, без react)\n"
        "## @scope    ai-project\n"
        "## @invariants\n"
        "##   - @protected  true\n"
        "##   - @language typescript\n"
        "# endregion MODULE_CONTRACT\n\n"
        "# TypeScript rule\n",
        encoding="utf-8",
    )
    roles = root / "roles"
    (roles / "coder" / "role.md").parent.mkdir(parents=True)
    (roles / "coder" / "role.md").write_text(
        "---\nname: Coder\ncolor: '#00B894'\ndescription: Coder role\npermission:\n  read: allow\n"
        "---\n\n# §ROLE\n**Priorities: 1. Execution**\n",
        encoding="utf-8",
    )
    (roles / "architect" / "role.md").parent.mkdir(parents=True)
    (roles / "architect" / "role.md").write_text(
        "---\nname: Architect\ncolor: '#6C5CE7'\ndescription: Architect role\npermission:\n  read: allow\n"
        "---\n\n# §ROLE\n**Priorities: 1. Planning**\n",
        encoding="utf-8",
    )
    skills = root / "skills"
    (skills / "superposition" / "SKILL.md").parent.mkdir(parents=True)
    (skills / "superposition" / "SKILL.md").write_text(
        "---\nname: superposition\ndescription: Superposition protocol\n---\n\n"
        "# region MODULE_CONTRACT\n"
        "## @purpose  SKILL: Superposition\n"
        "## @scope    all\n"
        "## @invariants\n"
        "##   - @protected  true\n"
        "# endregion MODULE_CONTRACT\n\n"
        "## Superposition Protocol\n",
        encoding="utf-8",
    )
    (root / "VERSION").write_text("0.7.0\n", encoding="utf-8")
    return root


def _make_pins(tmp_path: Path) -> Path:
    pins = tmp_path / "ai-instructions-pins.yaml"
    pins.write_text(
        "canon:\n"
        "  tag: v0.7.0\n"
        "  remote: https://example.invalid/repo.git\n"
        "hermes:\n"
        "  enabled: true\n"
        "  roles_as_skills: true\n"
        "  profile: platform\n"
        "  emit_dir: core/modules/hermes-agent/build/templates/profiles\n"
        "templates:\n"
        "  requires_instructions_version: 0.7.0\n",
        encoding="utf-8",
    )
    return pins


# region TEST_protected_fail_fast
def test_protected_collision_fail_fast(tmp_path, monkeypatch, caplog):
    # 🧪 TRAP[TEST] · Regression: none · Scenario: consumer перекрывает @protected правило → exit 1
    # · Last fail: n/a · Remove if: protected-семантика резолвера меняется
    caplog.set_level(logging.INFO, logger="ai_instructions")
    from ai_instructions.runtime.cli import main as cli_main

    canon = _write_canon(tmp_path / "canon")
    consumer = tmp_path / "consumer"
    (consumer / ".ai" / "rules").mkdir(parents=True)
    (consumer / ".ai" / "rules" / "constitution.md").write_text(
        "# region MODULE_CONTRACT\n"
        "## @purpose  Project override attempt\n"
        "## @invariants\n"
        "# endregion MODULE_CONTRACT\n\n"
        "# Override\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(consumer)
    rc = cli_main(["sync", "--canon-path", str(canon), "--config", str(_make_pins(tmp_path))])
    _print_ldd(caplog, "SYNC")
    assert rc == 1
    assert not (consumer / ".kilo" / "rules" / "constitution.md").exists()


# endregion TEST_protected_fail_fast


# region TEST_never_overwrite
def test_never_overwrite_manual_file(tmp_path, monkeypatch, caplog):
    # 🧪 TRAP[TEST] · Regression: none · Scenario: ручной файл .kilo (без stamp) не тронут
    # · Last fail: n/a · Remove if: never-overwrite семантика меняется
    caplog.set_level(logging.INFO, logger="ai_instructions")
    from ai_instructions.runtime.cli import main as cli_main

    canon = _write_canon(tmp_path / "canon")
    consumer = tmp_path / "consumer"
    (consumer / ".kilo" / "rules").mkdir(parents=True)
    manual = consumer / ".kilo" / "rules" / "constitution.md"
    manual.write_text("# Manual edit\n", encoding="utf-8")
    monkeypatch.chdir(consumer)
    rc = cli_main(["sync", "--canon-path", str(canon), "--config", str(_make_pins(tmp_path))])
    found = _print_ldd(caplog)
    assert rc == 0
    assert found
    assert manual.read_text(encoding="utf-8") == "# Manual edit\n", "ручной файл не должен перезаписываться"


# endregion TEST_never_overwrite


# region TEST_determinism
def test_sync_deterministic_noop(tmp_path, monkeypatch, caplog):
    # 🧪 TRAP[TEST] · Regression: none · Scenario: двойной sync → байт-в-байт идентичные выходы
    # · Last fail: n/a · Remove if: детерминизм эмиссии нарушается
    caplog.set_level(logging.INFO, logger="ai_instructions")
    from ai_instructions.runtime.cli import main as cli_main

    canon = _write_canon(tmp_path / "canon")
    consumer = tmp_path / "consumer"
    consumer.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(consumer)
    pins = _make_pins(tmp_path)
    assert cli_main(["sync", "--canon-path", str(canon), "--config", str(pins)]) == 0
    first = {p.relative_to(consumer): _sha256(p) for p in sorted((consumer / ".kilo").rglob("*.md"))}
    assert cli_main(["sync", "--canon-path", str(canon), "--config", str(pins)]) == 0
    second = {p.relative_to(consumer): _sha256(p) for p in sorted((consumer / ".kilo").rglob("*.md"))}
    found = _print_ldd(caplog, "SYNC")
    assert found
    assert first == second, "повторный sync должен быть no-op (хэши совпадают)"


# endregion TEST_determinism


# region TEST_coder_rename
def test_coder_rename_and_orphan_cleanup(tmp_path, monkeypatch, caplog):
    # 🧪 TRAP[TEST] · Regression: DevPlan 001 D1 — единое имя роли Coder (code.md → coder.md)
    # · Scenario: canon role coder → .kilo/agents/coder.md (name: Coder); старый stamped code.md удалён
    # · Last fail: n/a · Remove if: D1-ренейм отменяется
    caplog.set_level(logging.INFO, logger="ai_instructions")
    from ai_instructions.runtime.cli import main as cli_main

    canon = _write_canon(tmp_path / "canon")
    consumer = tmp_path / "consumer"
    (consumer / ".kilo" / "agents").mkdir(parents=True)
    (consumer / ".kilo" / "agents" / "code.md").write_text(
        "---\nname: Code\n---\n# Old role\n<!-- ai-instructions:0.6.3 -->\n", encoding="utf-8"
    )
    monkeypatch.chdir(consumer)
    rc = cli_main(["sync", "--canon-path", str(canon), "--config", str(_make_pins(tmp_path))])
    found = _print_ldd(caplog, "SYNC")
    assert rc == 0
    assert found
    coder_md = consumer / ".kilo" / "agents" / "coder.md"
    assert coder_md.is_file(), "coder.md должен эмититься"
    assert "name: Coder" in coder_md.read_text(encoding="utf-8")
    assert not (consumer / ".kilo" / "agents" / "code.md").exists(), "code.md — stamped-сирота, удаляется"
    assert STAMP_RE in coder_md.read_text(encoding="utf-8")


# endregion TEST_coder_rename


# region TEST_project_filter
def test_project_filter_language_stack(tmp_path, monkeypatch, caplog):
    # 🧪 TRAP[TEST] · Regression: none · Scenario: backend-фильтр по @language/@stack
    # · Last fail: n/a · Remove if: project-filter семантика меняется
    caplog.set_level(logging.INFO, logger="ai_instructions")
    from ai_instructions.runtime.cli import main as cli_main

    canon = _write_canon(tmp_path / "canon")
    project = tmp_path / "project"
    monkeypatch.chdir(tmp_path)
    pins = _make_pins(tmp_path)
    rc = cli_main([
        "sync",
        "--canon-path",
        str(canon),
        "--config",
        str(pins),
        "--project-dir",
        str(project),
        "--template",
        "backend",
    ])
    found = _print_ldd(caplog, "SYNC")
    assert rc == 0
    assert found
    rules = {p.name for p in (project / ".kilo" / "rules").glob("*.md")}
    assert "python-rule.md" in rules, "backend: @language python включён"
    assert "react-rule.md" not in rules, "backend: @stack react исключён"
    assert "constitution.md" in rules, "без директив — включён всегда"
    assert "fail-fast.md" in rules
    # скиллы без language/stack директив включены во все уровни (superposition — canon skill)
    assert (project / ".kilo" / "skills" / "superposition" / "SKILL.md").is_file()


# endregion TEST_project_filter


# region TEST_project_filter_ai_project
def test_project_filter_ai_project(tmp_path, monkeypatch, caplog):
    # 🧪 TRAP[TEST] · Regression: Отклонение №6 (019) — ai-project фильтр · Scenario:
    # --template ai-project включает @language typescript (вкл. react+typescript) и без-директив
    # правила, исключает @language python · Last fail: N/A — choices {all, backend, frontend}
    # физически отвергал ai-project («invalid choice») · Remove if: ai-project-фильтр семантика
    # меняется (TRAP[DECISION] в vendor emitter._filter_entries)
    caplog.set_level(logging.INFO, logger="ai_instructions")
    from ai_instructions.runtime.cli import main as cli_main

    canon = _write_canon(tmp_path / "canon")
    project = tmp_path / "project"
    monkeypatch.chdir(tmp_path)
    pins = _make_pins(tmp_path)
    rc = cli_main([
        "sync",
        "--canon-path",
        str(canon),
        "--config",
        str(pins),
        "--project-dir",
        str(project),
        "--template",
        "ai-project",
    ])
    found = _print_ldd(caplog, "SYNC")
    assert rc == 0
    assert found
    rules = {p.name for p in (project / ".kilo" / "rules").glob("*.md")}
    assert "typescript-rule.md" in rules, "ai-project: @language typescript включён"
    assert "react-rule.md" in rules, "ai-project: react+typescript (@language typescript) включён"
    assert "python-rule.md" not in rules, "ai-project: @language python исключён"
    assert "constitution.md" in rules, "без директив — включён всегда"
    assert "fail-fast.md" in rules
    assert (project / ".kilo" / "skills" / "superposition" / "SKILL.md").is_file()


# endregion TEST_project_filter_ai_project


# region TEST_manage_config
def test_manage_config_preserves_user_keys(tmp_path, monkeypatch, caplog):
    # 🧪 TRAP[TEST] · Regression: none · Scenario: kilo.json — пользовательские ключи сохраняются
    # · Last fail: n/a · Remove if: manage_config поведение меняется
    caplog.set_level(logging.INFO, logger="ai_instructions")
    from ai_instructions.runtime.cli import main as cli_main

    canon = _write_canon(tmp_path / "canon")
    project = tmp_path / "project"
    (project / ".kilo" / "rules").mkdir(parents=True)
    cfg = project / "kilo.json"
    cfg.write_text(
        json.dumps({"instructions": [".kilo/rules/custom.md"], "agent": {"architect": {"disable": True}}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    pins = _make_pins(tmp_path)
    rc = cli_main([
        "sync",
        "--canon-path",
        str(canon),
        "--config",
        str(pins),
        "--project-dir",
        str(project),
        "--template",
        "all",
    ])
    found = _print_ldd(caplog, "CONFIG")
    assert rc == 0
    assert found
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["agent"]["architect"]["disable"] is True, "пользовательские ключи сохраняются"
    assert ".kilo/rules/*.md" in data["instructions"]
    assert ".kilo/rules/custom.md" in data["instructions"]


# endregion TEST_manage_config


# region TEST_lock_drift
def test_lock_drift_detected(tmp_path, monkeypatch, caplog):
    # 🧪 TRAP[TEST] · Regression: none · Scenario: ручная правка generated → check exit 1
    # · Last fail: n/a · Remove if: lock-дрейф семантика меняется
    caplog.set_level(logging.INFO, logger="ai_instructions")
    from ai_instructions.runtime.cli import main as cli_main

    canon = _write_canon(tmp_path / "canon")
    consumer = tmp_path / "consumer"
    consumer.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(consumer)
    pins = _make_pins(tmp_path)
    assert cli_main(["sync", "--canon-path", str(canon), "--config", str(pins)]) == 0
    assert cli_main(["check"]) == 0
    emitted = consumer / ".kilo" / "rules" / "constitution.md"
    emitted.write_text(emitted.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    rc = cli_main(["check"])
    found = _print_ldd(caplog, "CHECK")
    assert found
    assert rc == 1, "ручное изменение generated-файла → drift exit 1"


# endregion TEST_lock_drift


# region TEST_hermes_profile_emission
def test_hermes_profile_skills_emission(tmp_path, monkeypatch, caplog):
    # 🧪 TRAP[TEST] · Regression: DevPlan 001 D2 — скиллы/роли в профиль platform hermes
    # · Scenario: skills + role-<id> эмитятся в <emit_dir>/platform/skills/
    # · Last fail: n/a · Remove if: hermes-эмиссия в platform-профиль меняется
    caplog.set_level(logging.INFO, logger="ai_instructions")
    from ai_instructions.runtime.cli import main as cli_main

    canon = _write_canon(tmp_path / "canon")
    consumer = tmp_path / "consumer"
    consumer.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(consumer)
    pins = _make_pins(tmp_path)
    rc = cli_main(["sync", "--canon-path", str(canon), "--config", str(pins)])
    found = _print_ldd(caplog, "SYNC")
    assert rc == 0
    assert found
    hermes_skills = (
        consumer / "core" / "modules" / "hermes-agent" / "build" / "templates" / "profiles" / "platform" / "skills"
    )
    assert (hermes_skills / "superposition" / "SKILL.md").is_file(), "скилл канона в профиль platform"
    role_coder = hermes_skills / "role-coder" / "SKILL.md"
    assert role_coder.is_file(), "роль канона → role-coder/SKILL.md (roles_as_skills)"
    assert "name: role-coder" in role_coder.read_text(encoding="utf-8")
    assert "role-architect" in {p.name for p in hermes_skills.iterdir()}


# endregion TEST_hermes_profile_emission
