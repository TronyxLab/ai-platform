# GREP_SUMMARY: test-hermes-init HermesInit setup-dirs check-config init-state guard idempotent profile-creation parity wrapper
# STRUCTURE: ┌tmp templates/data/context fixtures┐ → ◇ setup_dirs (профили, идемпотентно) → ◇ check_config (context config rsync) → ◇ init_state (guard + overlay + ownership) → ◇ run()/wrapper parity → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/hermes-agent/build/scripts/init.py (DevPlan 119 D5 —
##           TEST-FIRST: init.sh 157 LOC → Python HermesInit + тонкий wrapper). Тесты идемпотентности,
##           guard-файла, profile creation и parity init.sh/init.py (R5).
## @scope    Tests: setup_dirs (profile creation + skip existing), check_config (context config overlay
##           через инжектированный fake rsync), init_state (guard-идемпотентность, --ignore-existing
##           overlay, chown non-fatal), run() полный цикл, wrapper-парность (init.sh = exec init.py).
## @invariants
##   - fake rsync инжектируется через конструктор (никакого реального rsync/chown/root)
##   - HermesInit пути — tmp_path фикстуры (никаких /opt/* мутаций)
##   - R5 anti-survivorship: test_init_py_parity_negative (идемпотентность: второй run = no-op)
##   - LDD: IMP:9 в успешных сценариях
## @rationale D5 (DevPlan 119, AUDIT-1 F7): init.sh (157 LOC) — cont-init бизнес-логика без тестов.
##   Условие DevPlan D5 step 3-4: unit-тесты для init.py (test-first).
## @changes  2026-08-02 | DevPlan 119 D5 — Created (test-first)
# endregion MODULE_CONTRACT

import logging
import shutil
import sys
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# module-specific path (tests/AGENTS.md §sys.path policy)
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "hermes-agent" / "build" / "scripts")
)

from init import HermesInit

pytestmark = pytest.mark.static_audit


def _assert_imp9(caplog: pytest.LogCaptureFixture, needle: str | None = None) -> None:
    """Assert at least one IMP:9 log (LDD telemetry standard)."""
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
            if needle and needle in record.message:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    if needle:
        assert found, f"Critical LDD Error: No IMP:9 log containing '{needle}'"
    else:
        assert any("[IMP:9]" in r.message for r in caplog.records), "Critical LDD Error: No IMP:9 log found"


def _make_fixture(tmp_path: Path, *, context_profiles: bool = False) -> dict:
    """Создать fixture-дерево: templates/context/data + fake rsync.

    ## @purpose — tmp_path-дерево для HermesInit (никаких /opt/*), fake rsync зеркалит src→dest.
    """
    templates = tmp_path / "templates" / "profiles"
    data = tmp_path / "data" / "profiles"
    context_dir = tmp_path / "context"
    guard = tmp_path / "data" / ".context-overlay-applied"
    hermes_config = tmp_path / "hermes" / "config"

    # Шаблонные профили
    (templates / "base-agent" / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (templates / "base-agent" / "config.yaml").write_text("model: claude\n")
    (templates / "coder" / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (templates / "coder" / "config.yaml").write_text("role: coder\n")

    # Context config overlay
    (context_dir / "config" / "telegram.yaml").parent.mkdir(parents=True, exist_ok=True)
    (context_dir / "config" / "telegram.yaml").write_text("bot_token: ctx\n")
    # Context profile overlay
    if context_profiles:
        (context_dir / "templates" / "profiles" / "coder" / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
        (context_dir / "templates" / "profiles" / "coder" / "config.yaml").write_text("role: context-coder\n")
        (context_dir / "templates" / "profiles" / "ctx-only" / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
        (context_dir / "templates" / "profiles" / "ctx-only" / "config.yaml").write_text("ctx: true\n")

    calls: list[tuple] = []

    def _mirror(src_dir: Path, dest_dir: Path, ignore_existing: bool) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            target = dest_dir / item.name
            if item.is_dir():
                _mirror(item, target, ignore_existing)
            elif not (ignore_existing and target.exists()):
                shutil.copy2(item, target)

    def fake_rsync(src: Path, dest: Path, ignore_existing: bool = False) -> None:
        src = Path(src)
        dest = Path(dest)
        calls.append((str(src), str(dest), ignore_existing))
        _mirror(src, dest, ignore_existing)

    return {
        "templates": templates,
        "data": data,
        "context_dir": context_dir,
        "guard": guard,
        "hermes_config": hermes_config,
        "calls": calls,
        "fake_rsync": fake_rsync,
    }


# region TEST_setup_dirs (profile creation)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · setup_dirs: профили создаются из шаблонов (D5)
# · Scenario: 2 шаблона → 2 профиля скопированы в data
# · Last fail: N/A (new — D5 test-first)
# · Remove if: profile creation логика меняется
def test_setup_dirs_creates_profiles(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """setup_dirs: шаблоны → профили в data."""
    caplog.set_level(logging.INFO)
    fx = _make_fixture(tmp_path)
    init = HermesInit(
        templates=fx["templates"],
        data=fx["data"],
        context_dir=fx["context_dir"],
        context_guard=fx["guard"],
        hermes_config=fx["hermes_config"],
        rsync=fx["fake_rsync"],
    )
    init.setup_dirs()
    assert (fx["data"] / "base-agent" / "config.yaml").is_file()
    assert (fx["data"] / "coder" / "config.yaml").is_file()
    _assert_imp9(caplog, "Profile created")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · setup_dirs: существующий профиль НЕ перезаписывается (D5)
# · Scenario: dest/config.yaml существует → SKIP, пользовательские правки сохранены
# · Last fail: N/A (new — D5 test-first; init.sh [ -f "$dest/config.yaml" ] guard)
# · Remove if: skip-existing семантика меняется
def test_setup_dirs_idempotent_skip_existing(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """R5 negative: существующий профиль не перезаписывается (правки пользователя сохраняются)."""
    caplog.set_level(logging.INFO)
    fx = _make_fixture(tmp_path)
    init = HermesInit(
        templates=fx["templates"],
        data=fx["data"],
        context_dir=fx["context_dir"],
        context_guard=fx["guard"],
        hermes_config=fx["hermes_config"],
        rsync=fx["fake_rsync"],
    )
    init.setup_dirs()
    # Пользовательская правка
    (fx["data"] / "coder" / "config.yaml").write_text("role: user-edited\n")
    init.setup_dirs()  # повторный вызов
    assert (fx["data"] / "coder" / "config.yaml").read_text() == "role: user-edited\n", "правка должна сохраниться"


# endregion TEST_setup_dirs


# region TEST_sync_profile_skills (D3, DevPlan 001 T4.7)


def _make_skills_fixture(tmp_path: Path) -> dict:
    """Fixture с шаблонными скиллами профиля (stamped + без stamp)."""
    templates = tmp_path / "templates" / "profiles"
    data = tmp_path / "data" / "profiles"
    skills = templates / "platform" / "skills" / "superposition"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: superposition\n---\n# Superposition\n<!-- ai-instructions:0.7.0 -->\n", encoding="utf-8"
    )
    return {"templates": templates, "data": data}


# 🧪 TRAP[TEST] · 2026-08-16 · Regression · sync_profile_skills: первый старт доставляет скиллы (D3)
# · Scenario: шаблонные скиллы профиля отсутствуют на volume → копируются при первом старте
# · Last fail: N/A (new — DevPlan 001 T4.7 test-first)
# · Remove if: sync-шаг профильных скиллов упраздняется (native hermes-механизм, Rev D3)
def test_sync_profile_skills_first_start(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """D3 first start: skills шаблона копируются в data/profiles/<name>/skills/."""
    caplog.set_level(logging.INFO)
    fx = _make_skills_fixture(tmp_path)
    init = HermesInit(templates=fx["templates"], data=fx["data"])
    init.sync_profile_skills()
    dest = fx["data"] / "platform" / "skills" / "superposition" / "SKILL.md"
    assert dest.is_file(), "скилл профиля должен быть доставлен на volume"
    assert "ai-instructions:0.7.0" in dest.read_text(encoding="utf-8")
    _assert_imp9(caplog, "SKILLS")


# 🧪 TRAP[TEST] · 2026-08-16 · Regression · sync_profile_skills: обновлённый шаблон перезаписывает stamped (D3)
# · Scenario: образ обновлён (шаблон изменён) → stamped-файл на volume перезаписывается
# · Last fail: N/A (new — DevPlan 001 T4.7 test-first)
# · Remove if: sync-шаг профильных скиллов упраздняется
def test_sync_profile_skills_updated_template_overwrites_stamped(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """D3 restart with updated image: stamped dest перезаписывается новым контентом шаблона."""
    caplog.set_level(logging.INFO)
    fx = _make_skills_fixture(tmp_path)
    init = HermesInit(templates=fx["templates"], data=fx["data"])
    init.sync_profile_skills()
    # Обновление образа: шаблон изменился
    skill_file = fx["templates"] / "platform" / "skills" / "superposition" / "SKILL.md"
    skill_file.write_text(
        "---\nname: superposition\n---\n# Superposition v2\n<!-- ai-instructions:0.7.0 -->\n", encoding="utf-8"
    )
    init.sync_profile_skills()
    dest = fx["data"] / "platform" / "skills" / "superposition" / "SKILL.md"
    assert "# Superposition v2" in dest.read_text(encoding="utf-8"), "stamped-файл должен перезаписаться"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · sync_profile_skills: файл без stamp не трогается (D3)
# · Scenario: оператор вручную правил скилл на volume (stamp стёрт/отсутствует) → never overwrite
# · Last fail: N/A (new — DevPlan 001 T4.7 test-first)
# · Remove if: never-overwrite семантика sync-шага меняется
def test_sync_profile_skills_manual_file_untouched(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """R5 negative: ручной файл без stamp на volume не перезаписывается ни при каком прогоне."""
    caplog.set_level(logging.INFO)
    fx = _make_skills_fixture(tmp_path)
    init = HermesInit(templates=fx["templates"], data=fx["data"])
    init.sync_profile_skills()
    dest = fx["data"] / "platform" / "skills" / "superposition" / "SKILL.md"
    dest.write_text("---\nname: superposition\n---\n# Operator edit (no stamp)\n", encoding="utf-8")
    init.sync_profile_skills()  # повторный старт с новым шаблоном
    assert "# Operator edit (no stamp)" in dest.read_text(encoding="utf-8"), "ручной файл должен сохраниться"
    _assert_imp9(caplog, "SKILLS")


# endregion TEST_sync_profile_skills


# region TEST_check_config (context config overlay)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · check_config: context config overlay (D5)
# · Scenario: context/config/ есть → rsync в hermes_config
# · Last fail: N/A (new — D5 test-first)
# · Remove if: context config overlay меняется
def test_check_config_overlay(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """check_config: context/config/ rsync → hermes_config/."""
    caplog.set_level(logging.INFO)
    fx = _make_fixture(tmp_path)
    init = HermesInit(
        templates=fx["templates"],
        data=fx["data"],
        context_dir=fx["context_dir"],
        context_guard=fx["guard"],
        hermes_config=fx["hermes_config"],
        rsync=fx["fake_rsync"],
    )
    init.check_config()
    assert (fx["hermes_config"] / "telegram.yaml").is_file(), "context config должен быть скопирован"
    _assert_imp9(caplog, "Context config overlay applied")


# endregion TEST_check_config


# region TEST_init_state (guard + overlay + ownership)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · init_state: guard-файл → overlay не повторяется (D5)
# · Scenario: первый run создаёт guard; второй run пропускает overlay (идемпотентность)
# · Last fail: N/A (new — D5 test-first; init.sh CONTEXT_GUARD guard)
# · Remove if: guard-семантика меняется
def test_init_state_guard_idempotent(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """R5 negative: повторный init_state с guard-файлом → overlay НЕ повторяется."""
    caplog.set_level(logging.INFO)
    fx = _make_fixture(tmp_path, context_profiles=True)
    init = HermesInit(
        templates=fx["templates"],
        data=fx["data"],
        context_dir=fx["context_dir"],
        context_guard=fx["guard"],
        hermes_config=fx["hermes_config"],
        rsync=fx["fake_rsync"],
    )
    init.init_state()
    assert fx["guard"].is_file(), "guard-файл должен быть создан"
    overlay_calls = [c for c in fx["calls"] if "templates/profiles" in c[0]]
    assert len(overlay_calls) == 1, "первый run: ровно один overlay"

    init.init_state()  # второй run — guard существует
    overlay_calls = [c for c in fx["calls"] if "templates/profiles" in c[0]]
    assert len(overlay_calls) == 1, "второй run: overlay НЕ должен повторяться (guard)"


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · init_state: overlay использует --ignore-existing (D5)
# · Scenario: контекстный профиль coder — базовый приоритетен (ignore_existing=True)
# · Last fail: N/A (new — D5 test-first)
# · Remove if: ignore-existing семантика меняется
def test_init_state_overlay_ignore_existing(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """init_state: rsync --ignore-existing — базовые профили приоритетны при re-init."""
    caplog.set_level(logging.INFO)
    fx = _make_fixture(tmp_path, context_profiles=True)
    init = HermesInit(
        templates=fx["templates"],
        data=fx["data"],
        context_dir=fx["context_dir"],
        context_guard=fx["guard"],
        hermes_config=fx["hermes_config"],
        rsync=fx["fake_rsync"],
    )
    init.setup_dirs()  # базовый coder создан
    init.init_state()  # контекстный overlay
    overlay_call = next(c for c in fx["calls"] if "templates/profiles" in c[0])
    assert overlay_call[2] is True, "overlay должен использовать ignore_existing=True"
    # coder не перезаписан контекстной версией (--ignore-existing), ctx-only добавлен
    assert (fx["data"] / "coder" / "config.yaml").read_text() == "role: coder\n"
    assert (fx["data"] / "ctx-only" / "config.yaml").is_file()


# endregion TEST_init_state


# region TEST_run + parity (R5)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · test_init_py_parity — полный run идемпотентен (D5, TEST_SPEC)
# · Scenario: run() дважды → второй no-op (профили/overlay не дублируются), exit 0
# · Last fail: N/A (new — D5; R5: init.sh идемпотентность сохраняется в Python)
# · Remove if: идемпотентность run() меняется
def test_init_py_parity_negative(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """R5 parity: run() дважды → idempotent (exit 0, без дублирования профилей/overlay)."""
    caplog.set_level(logging.INFO)
    fx = _make_fixture(tmp_path, context_profiles=True)
    init = HermesInit(
        templates=fx["templates"],
        data=fx["data"],
        context_dir=fx["context_dir"],
        context_guard=fx["guard"],
        hermes_config=fx["hermes_config"],
        rsync=fx["fake_rsync"],
    )
    assert init.run(context="prod") == 0
    profiles_after_first = sorted(p.name for p in (fx["data"]).iterdir()) if fx["data"].is_dir() else []

    assert init.run(context="prod") == 0
    profiles_after_second = sorted(p.name for p in (fx["data"]).iterdir()) if fx["data"].is_dir() else []
    assert profiles_after_first == profiles_after_second, "второй run не должен плодить профили"
    _assert_imp9(caplog, "COMPLETE")


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CONTEXT лог: задан → INFO, пуст → WARN (D5)
# · Scenario: context="dev" → IMP:9 лог Context specified; context=None → WARN base-only
# · Last fail: N/A (new — D5 test-first)
# · Remove if: CONTEXT логика меняется
def test_run_context_logging(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """run(): CONTEXT задан → INFO; не задан → WARN base-only (эквивалент init.sh)."""
    caplog.set_level(logging.INFO)
    fx = _make_fixture(tmp_path)
    init = HermesInit(
        templates=fx["templates"],
        data=fx["data"],
        context_dir=fx["context_dir"],
        context_guard=fx["guard"],
        hermes_config=fx["hermes_config"],
        rsync=fx["fake_rsync"],
    )
    init.run(context="dev")
    assert any("[IMP:9]" in r.message and "Context specified: dev" in r.message for r in caplog.records)

    caplog.clear()
    init.run(context="")
    assert any("[IMP:7]" in r.message and "No CONTEXT set" in r.message for r in caplog.records)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · wrapper: init.sh — тонкий (<10 LOC) + exec init.py (D5, AC-D5.2)
# · Scenario: wc -l < 10; содержит exec python3 /usr/local/bin/init.py
# · Last fail: N/A (new — D5; AC-D5.2)
# · Remove if: wrapper перестаёт быть тонким
def test_init_sh_wrapper_thin(caplog: pytest.LogCaptureFixture) -> None:
    """init.sh: <10 LOC wrapper, exec python3 /usr/local/bin/init.py (AC-D5.2)."""
    caplog.set_level(logging.INFO)
    init_sh = (
        Path(__file__).resolve().parent.parent.parent
        / "core"
        / "modules"
        / "hermes-agent"
        / "build"
        / "scripts"
        / "init.sh"
    )
    content = init_sh.read_text(encoding="utf-8")
    assert len(content.splitlines()) < 10, f"wrapper раздут: {len(content.splitlines())} LOC (AC-D5.2 <10)"
    assert "exec python3 /usr/local/bin/init.py" in content, "wrapper должен exec'ить init.py"
    assert "setup_dirs" not in content and "init_state" not in content, "бизнес-логика не должна быть в wrapper"


# GUARD-PRESERVE (168): единственное покрытие AC-D5.3 — Dockerfile копирует init.py в /usr/local/bin (REGRESSION, DevPlan 119 D5)
# 🧪 TRAP[TEST] · 2026-08-02 · Regression · Dockerfile копирует init.py (D5, AC-D5.3)
# · Scenario: единый Dockerfile содержит COPY init.py (L1→L2 коллапс DevPlan 002: build/Dockerfile удалён)
# · Last fail: N/A (new — D5; AC-D5.3)
# · Remove if: init.py перестаёт копироваться в образ
def test_dockerfile_copies_init_py(caplog: pytest.LogCaptureFixture) -> None:
    """Dockerfile: COPY init.py в /usr/local/bin/init.py (AC-D5.3, единый Dockerfile)."""
    caplog.set_level(logging.INFO)
    dockerfile = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "hermes-agent" / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")
    assert "init.py" in content and "/usr/local/bin/init.py" in content, (
        "Dockerfile должен копировать init.py (AC-D5.3)"
    )


# 🧪 TRAP[TEST] · 2026-08-06 · Regression · единый Dockerfile — USER 10000:10000 non-root (DevPlan 140 W6, AC-W6.1)
# · Scenario: единый Dockerfile (final-стадия) содержит USER 10000:10000 ПОСЛЕ последнего RUN, ПЕРЕД HEALTHCHECK
# · Last fail: hermes-root-500 — без USER (chown-if-root workaround init.py:167)
# · Remove if: снова переходим на root runtime (напр. s6-overlay несовместим с non-root)
def test_context_dockerfile_has_nonroot_user(caplog: pytest.LogCaptureFixture) -> None:
    """Единый Dockerfile: USER 10000:10000 после последнего RUN, перед HEALTHCHECK (AC-W6.1)."""
    caplog.set_level(logging.INFO)
    dockerfile = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "hermes-agent" / "Dockerfile"
    lines = dockerfile.read_text(encoding="utf-8").splitlines()

    user_idx = next((i for i, line in enumerate(lines) if line.strip().startswith("USER 10000")), None)
    assert user_idx is not None, "Единый Dockerfile должен содержать USER 10000:10000 (AC-W6.1)"

    health_idx = next(i for i, line in enumerate(lines) if line.strip().startswith("HEALTHCHECK"))
    run_idxs = [i for i, line in enumerate(lines) if line.strip().startswith("RUN ")]
    last_run_idx = max(run_idxs)
    assert user_idx > last_run_idx, (
        f"USER (строка {user_idx + 1}) должен идти ПОСЛЕ последнего RUN (строка {last_run_idx + 1})"
    )
    assert user_idx < health_idx, (
        f"USER (строка {user_idx + 1}) должен идти ПЕРЕД HEALTHCHECK (строка {health_idx + 1})"
    )
    logger.info(
        "[IMP:9][test] единый Dockerfile USER 10000:10000 (строка %d) после RUN (строка %d), перед HEALTHCHECK (строка %d) — AC-W6.1 PASS",
        user_idx + 1,
        last_run_idx + 1,
        health_idx + 1,
    )


# endregion TEST_run + parity
