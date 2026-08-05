# GREP_SUMMARY: gate phantom-refs dangling-refs deploy-project.sh state_migration.py audit_logging.sh generate-dev-certs.sh strict-scan consumer-scan D3 make-literal-bans check-naming
# STRUCTURE: ▶ ┌_PHANTOM_NAMES (5) + _MAKE_LITERAL_BANS (3)┐ → ○ scan roots (core/ tests/ makefiles/ .github/ .kilo/ + root files) → ◇ re.escape(name) in line? → ⊕ violations[file:line] → ◇ _ALLOWLIST (пуст, D3) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Strict phantom-reference gate (DevPlan 116 B8 T8, D3): 0 упоминаний 5 удалённых
##           имён (deploy-project.sh, state_migration.py, audit_logging.sh, generate-dev-certs.sh,
##           platform-deploy.sh) в коде и CI — ВКЛЮЧАЯ docstring/TRAP-комментарии. Allowlist
##           гейта — пустая константа: история удаляется вместе с именами (решение пользователя
##           2026-08-01, D3).
##           + Make-literal баны (DevPlan 138 W1, расширение AC-5 DevPlan 120): 0 упоминаний
##           литералов «make preflight», «make compose-safe-up», «make sync-env-defaults»
##           во ВСЕХ сканируемых корнях — удаление make-таргетов завершается конструктивным
##           запретом возврата имени (DevPlan 138 §4.1).
## @scope    Read-only статический скан. Корни: core/, tests/ (кроме generated
##           tests/test_inventory.yaml и архивного tests/test_inventory_changes.yaml),
##           makefiles/, .github/, .kilo/, + AGENTS.md, .env.example, .pre-commit-config.yaml, Makefile.
##           ВНЕ скоупа (архив): reports/, .ai/plans/, worktrees. Исключения: .git, __pycache__,
##           node_modules, .venv.
##           Скан make-литералов (DevPlan 138 W1): те же корни, точные паттерны «make X».
## @invariants
##   - Детект: ЛЮБОЕ вхождение имени как подстроки (re.escape(name)), включая комментарии
##   - Gitignored файлы вне скана (эфемерные артефакты: tests/report*.xml — junit-отчёты
##     check_suite, перезаписываются каждым прогоном и могут содержать текст фейлов)
##   - Make-literal баны: точные литералы «make preflight»/«make compose-safe-up»/
##     «make sync-env-defaults» запрещены во всех корнях; полные имена НЕ добавляются в
##     _PHANTOM_NAMES (sync-env-defaults — self-name файла sync_env_defaults.py, вечный RED;
##     preflight/compose-safe-up легитимно живут в файлах/TRAP-записях, DevPlan 138 §4.1)
##   - _ALLOWLIST = frozenset() — строгий режим, никаких исключений
##   - Fail-сообщение: полный список файл:строка:имя
##   - Negative-тест обязателен (R5 anti-survivorship): фиктивный файл с именем → детект
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale U-42: удаление старых реализаций не сопровождалось удалением потребителей —
##            фантомные имена остались в 46 сайтах (код, тесты, CI, манифесты). Строгий
##            zero-allowlist гейт делает появление имени структурно невозможным.
## ⚠️ TRAP[DECISION] · 2026-08-01 · HI · D3: строгий гейт фантомов — 0 упоминаний 4 имён
## · Rejected: мягкий режим с allowlist (риск: allowlist-дрейф как в dead-code gate)
## · Reason: решение пользователя 2026-08-01 (D3) — история удаляется вместе с именами;
##   потеря исторических TRAP-аннотаций принята (тестовая история — в test_inventory_changes.yaml).
## · Rev: 2026-10-21 — пересмотр, если гейт начнёт блокировать легитимную историческую документацию.
## @changes 2026-08-01 | Created (DevPlan 116 B8 T8)
## @changes 2026-08-02 | DevPlan 120 AC-5: _PREFLIGHT_BAN («make preflight») — скан .kilo/ + AGENTS.md
## @changes 2026-08-05 | DevPlan 138 W1: _PREFLIGHT_BAN → _MAKE_LITERAL_BANS (3 литерала);
##            скан расширен на ВСЕ корни (фикс dot-dir механики — .kilo/.github реально сканируются)
# endregion MODULE_CONTRACT

import logging
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()

# ── Фантомные имена (удалённые файлы, D3) ─────────────────────────────────────
# DevPlan 116 B1 T7: +platform-deploy.sh (legacy forced-command скрипт — 26 упоминаний
# очищены волной B1; канал заменён на orchestrator_cli dispatch / receive).
_PHANTOM_NAMES: tuple[str, ...] = (
    "deploy-project.sh",
    "state_migration.py",
    "audit_logging.sh",
    "generate-dev-certs.sh",
    "platform-deploy.sh",
)

# ── Корни скана (D3: core/, tests/, makefiles/, .github/, .kilo/ + файлы) ─────
_SCAN_ROOTS: tuple[str, ...] = ("core", "tests", "makefiles", ".github", ".kilo")
_SCAN_FILES: tuple[str, ...] = ("AGENTS.md", ".env.example", ".pre-commit-config.yaml", "Makefile")

# Generated/архивные файлы — вне скана (D3)
_EXCLUDE_FILES: frozenset[str] = frozenset(
    {
        "tests/test_inventory.yaml",  # generated (make test-inventory-sync)
        "tests/test_inventory_changes.yaml",  # архив истории инвентаря
        # Сам файл гейта — структурное место определения _PHANTOM_NAMES/_MAKE_LITERAL_BANS
        # (имена обязаны существовать как константы скана; self-reference неизбежен,
        # аналогично allowlist-константам других гейтов)
        "tests/gates/test_gate_phantom_refs.py",
    }
)

# Директории-исключения (части пути)
# worktrees: архивные снапшоты в .kilo/worktrees/* — исторические копии (как reports/),
# не являются живой документацией; иначе make-literal скан флагит 200+ архивных упоминаний.
_EXCLUDE_DIR_PARTS: frozenset[str] = frozenset({".git", "__pycache__", "node_modules", ".venv", "reports", "worktrees"})

# D3 2026-08-01: строгий режим — история удаляется вместе с именами. Allowlist пуст.
_ALLOWLIST: frozenset[str] = frozenset()

# ── Make-literal баны (DevPlan 138 W1, расширение DevPlan 120 AC-5) ────────────
# Удаление make-таргетов (compose-safe-up/preflight/sync-env-defaults) завершается
# конструктивным запретом: 0 упоминаний ТОЧНЫХ литералов «make X» во ВСЕХ сканируемых
# корнях (core/, tests/, makefiles/, .github/, .kilo/, root-файлы) — паритет механики
# _scan_paths (DevPlan 138 §4.1). Полные имена НЕ добавляются в _PHANTOM_NAMES:
# sync-env-defaults содержится в имени файла sync_env_defaults.py (self-name, вечный RED);
# preflight легитимно живёт в core/internal/deploy/preflight.py и TRAP-записях.
_MAKE_LITERAL_BANS: tuple[str, ...] = (
    "make preflight",
    "make compose-safe-up",
    "make sync-env-defaults",
)
_MAKE_LITERAL_SCAN_ROOTS: tuple[str, ...] = _SCAN_ROOTS
_MAKE_LITERAL_SCAN_FILES: tuple[str, ...] = _SCAN_FILES

# 🧐 TRAP[DECISION] · 2026-08-05 · — · dot-dir механика _scan_paths: фикс мёртвого скана .kilo/.github
# · Rejected: оставить скан dot-корней мёртвым (startswith(".") на p.parts молча вырезал
#   .kilo/.github — прежний preflight-ban фактически сканировал только root AGENTS.md)
# · Reason: AC-2 DevPlan 138 требует «0 упоминаний во всех корнях (.github/.kilo)»;
#   фикс — dot-сегменты исключаются только НИЖЕ корня (verified: 0 фантомных имён в .github/.kilo,
#   phantom-гейт не ослаблен). Латентный баг обнаружен при W1-аудите скана.
# · Rev: если появится dot-корень, который должен быть ВНЕ скана (кроме .git/__pycache__/... в
#   _EXCLUDE_DIR_PARTS) — добавить его в _EXCLUDE_DIR_PARTS, а не в dot-фильтр.

# 🧐 TRAP[DECISION] · 2026-08-05 · — · gitignore-фильтр в _scan_paths: junit-отчёты вне скана
# · Rejected: ручное удаление tests/report*.xml перед каждым прогоном (хрупко — отчёты
#   перезаписываются каждым check/gate; забытый отчёт = вечный RED)
# · Reason: chicken-and-egg — junit-отчёты check_suite (gitignored) содержат текст фейлов
#   с литералами банов; упавший гейт пишет отчёт с литералами → следующий прогон вечно RED.
#   Батчевый git check-ignore исключает эфемерные артефакты, сохраняя сканирование
#   untracked-не-ignored файлов (новые файлы волны).
# · Rev: если появится gitignored-файл, который ДОЛЖЕН сканироваться (ignored, но репозиторный
#   контент) — заменить батчевый фильтр на явный exclude-список артефактов.


# region HELPER__scan_paths
def _batch_git_ignored(rels: list[str]) -> frozenset[str]:
    """Gitignored пути (батчевый git check-ignore) — эфемерные артефакты вне скана.

    ## @purpose — junit-отчёты check_suite (tests/report*.xml) и прочие gitignored
    ##            артефакты перезаписываются каждым прогоном и могут содержать текст
    ##            фейлов (включая литералы банов) — это НЕ репозиторный контент.
    ##            Гейт сканирует ТОЛЬКО не-ignored файлы (tracked + untracked
    ##            не-ignored — например, новые файлы волны, ещё не закоммиченные).
    ## @io — ⇥ rels: repo-относительные пути → ⎋ frozenset[str] gitignored релейтивов
    ## @complexity O(N) — один батчевый вызов git check-ignore
    ## @invariants
    ##   - tmp_path/абсолютные пути (negative-тесты) НЕ передаются в git (вне repo)
    ##   - git unavailable → пустое множество (сканируем всё — консервативно)
    ##   - exit 0/1 оба валидны (0 = хотя бы один ignored, 1 = ни одного)
    """
    repo_rels = [r for r in rels if not r.startswith("/") and "\x00" not in r]
    if not repo_rels:
        return frozenset()
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            input="\n".join(repo_rels),
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(ROOT),
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    if result.returncode not in (0, 1):
        return frozenset()
    return frozenset(p for p in result.stdout.splitlines() if p)


def _scan_paths(roots: Sequence[Path], patterns: Sequence[str] = _PHANTOM_NAMES) -> list[str]:
    """Scan files under given roots for phantom name occurrences.

    ## @purpose — Core scanner: walks each root, skips excluded dirs/files, and reports
    ##            every line containing a phantom name (substring, re.escape).
    ##            patterns — имена/литералы для детекта (default: _PHANTOM_NAMES;
    ##            DevPlan 120 AC-5 / DevPlan 138 W1: «make X» литералы для make-literal скана).
    ## @io — ⇥ roots: sequence of Path, patterns: sequence of str → ⎋ list[str]
    ##           of "rel:line: name → snippet" violations
    ## @complexity O(F * L * N) where F = files, L = lines, N = pattern count
    ## @invariants
    ##   - Detection is substring-based (re.escape(name)) — includes comments/docstrings
    ##   - Binary files skipped via NUL-byte probe (first 2048 bytes)
    ##   - Relative paths are computed against ROOT for reporting
    ##   - Dot-dir корни (.kilo, .github) сканируются как таковые (DevPlan 138 W1):
    ##     исключаются только dot-сегменты НИЖЕ корня (фикс: ранее startswith(".")
    ##     на p.parts молча вырезал dot-корни — скан .kilo/.github был мёртвым)
    ##   - Gitignored файлы (junit-отчёты tests/report*.xml и т.п.) — вне скана:
    ##     эфемерные артефакты, перезаписываемые каждым прогоном (могут содержать
    ##     текст фейлов с литералами банов — chicken-and-egg)
    """
    # Pass 1: collect candidate files across all roots
    candidates: list[tuple[Path, str]] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            file_list = [root]
        else:
            file_list = [
                p
                for p in sorted(root.rglob("*"))
                if p.is_file()
                and not any(
                    seg in _EXCLUDE_DIR_PARTS or (i > 0 and seg.startswith("."))
                    for i, seg in enumerate(p.relative_to(root).parts)
                )
            ]
        for path in file_list:
            try:
                rel = str(path.relative_to(ROOT))
            except ValueError:
                # Negative-тест: путь вне ROOT (tmp_path) — используем absolute-фолбэк
                rel = str(path)
            if rel in _EXCLUDE_FILES:
                continue
            candidates.append((path, rel))

    # Pass 2: gitignored (эфемерные артефакты — junit-отчёты и т.п.) — вне скана
    ignored = _batch_git_ignored([rel for _path, rel in candidates])

    violations: list[str] = []
    for path, rel in candidates:
        if rel in ignored:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:2048]:
            continue  # бинарный файл
        text = raw.decode("utf-8", errors="replace")
        violations.extend(
            f"{rel}:{i}: {name} → {line.strip()[:100]}"
            for i, line in enumerate(text.splitlines(), 1)
            for name in patterns
            if re.search(re.escape(name), line)
        )

    return violations


# endregion HELPER__scan_paths


# ── Основной гейт-тест ─────────────────────────────────────────────────────────


# region FUNC_test_no_phantom_names_in_code_and_ci
@pytest.mark.gate
@ldd_trajectory
def test_no_phantom_names_in_code_and_ci(caplog) -> None:
    """0 упоминаний 5 фантомных имён в коде и CI (D3, строгий режим).

    # ▶ scan roots → ◇ violations empty? → PASS · └→ FAIL: файл:строка:имя

    ## @purpose — DevPlan 116 B8 T8: удалённые файлы (deploy-project.sh, state_migration.py,
    ##            audit_logging.sh, generate-dev-certs.sh, platform-deploy.sh) не должны
    ##            упоминаться нигде в коде/тестах/CI — включая docstring/TRAP-комментарии (D3).
    ## @io — caplog → ⎋ None (pytest.fail с полным списком файл:строка)
    ## @complexity — O(F * L * N) — полный скан репозитория
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B8 T8 · D3-строгий скан 5 фантомных имён
    # · Regression: повторное появление имени удалённого файла в коде/CI (dangling ref)
    # · Scenario: скан core/, tests/, makefiles/, .github/, .kilo/, + root-файлов
    # · Last fail: 2026-08-01 — 46 сайтов упоминаний очищены волной B8
    # · Remove if: имена навсегда удалены из истории — скан не нужен (но negative-тест остаётся)
    caplog.set_level(logging.INFO)

    scan_roots: list[Path] = [ROOT / rel for rel in _SCAN_ROOTS] + [ROOT / f for f in _SCAN_FILES]
    violations = _scan_paths(scan_roots)

    logger.info("[IMP:8][phantom_gate] Scanned %d roots, %d violation(s)", len(scan_roots), len(violations))

    if violations:
        for v in violations:
            logger.error("[IMP:10][phantom_gate] %s", v)
        pytest.fail(
            f"[IMP:10][phantom_gate] {len(violations)} упоминание(й) удалённых файлов "
            f"({', '.join(_PHANTOM_NAMES)}) — D3 строгий режим, allowlist пуст:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    logger.info(
        "[IMP:9][phantom_gate] PASS: 0 упоминаний %s в коде и CI",
        ", ".join(_PHANTOM_NAMES),
    )


# endregion FUNC_test_no_phantom_names_in_code_and_ci


# ── Negative-тест (R5 anti-survivorship): сканер ДОЛЖЕН детектировать ──────────


# region FUNC_test_phantom_scan_detects_dummy_file
@pytest.mark.gate
@ldd_trajectory
def test_phantom_scan_detects_dummy_file(tmp_path, caplog) -> None:
    """Falsifiability: сканер обязан находить фантомное имя в фиктивном файле (R5).

    # ▶ tmp_path/dummy.sh с "deploy-project.sh" → ◇ violations non-empty? → PASS

    ## @purpose — Anti-survivorship: гейт, который не может упасть, — не гейт. Доказывает,
    ##            что _scan_paths() реально детектирует имена (включая комментарии), а не
    ##            молча проходит из-за ошибки сканирования.
    ## @io — ⇥ tmp_path → ⎋ None (assert детекта)
    ## @complexity — O(1) — один фиктивный файл
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B8 T8 · R5 anti-survivorship для phantom-гейта
    # · Regression: если скан сломается (пустые корни, бинарный-пропуск) — гейт станет
    #   вечнозелёным и перестанет ловить dangling refs
    # · Scenario: dummy.sh с фантомным именем в комментарии → скан обязан его найти
    # · Last fail: N/A (первый тест)
    # · Remove if: phantom-гейт удалён
    caplog.set_level(logging.INFO)

    dummy = tmp_path / "dummy.sh"
    dummy.write_text("#!/usr/bin/env bash\n# calls deploy-project.sh for delivery\n")

    violations = _scan_paths([tmp_path])
    logger.info("[IMP:8][phantom_gate][negative] violations: %s", violations)

    assert violations, "CRITICAL: сканер не детектировал 'deploy-project.sh' в фиктивном файле — гейт вечнозелёный!"
    assert any("deploy-project.sh" in v for v in violations), f"Сканер нашёл нарушения, но не то имя: {violations}"
    logger.info("[IMP:9][phantom_gate][negative] PASS: фиктивный файл с фантомным именем детектирован")


# endregion FUNC_test_phantom_scan_detects_dummy_file


# ── Make-literal баны (DevPlan 138 W1): удалённые таргеты конструктивно невозвратимы ──


# region FUNC_test_no_make_literal_bans_in_scan_roots
@pytest.mark.gate
@ldd_trajectory
def test_no_make_literal_bans_in_scan_roots(caplog) -> None:
    """0 упоминаний make-литералов удалённых таргетов во ВСЕХ сканируемых корнях (DevPlan 138 W1).

    # ▶ скан core/ tests/ makefiles/ .github/ .kilo/ + root-файлов → ◇ литерал «make X»? → RED · └→ PASS

    ## @purpose — DevPlan 138 W1 (§4.1): удаление make-таргетов (compose-safe-up, preflight,
    ##            sync-env-defaults) завершается конструктивным literal-баном — 0 упоминаний
    ##            точных паттернов «make preflight», «make compose-safe-up»,
    ##            «make sync-env-defaults» во всех корнях (паритет механики _scan_paths).
    ##            Полные имена НЕ в _PHANTOM_NAMES: self-name файла / легитимные упоминания.
    ## @io — caplog → ⎋ None (pytest.fail с файл:строка)
    ## @complexity O(F * L)
    """
    # 🧪 TRAP[TEST] · DevPlan 138 W1 · literal-бан make-таргетов
    # · Regression: возврат «make preflight»/«make compose-safe-up»/«make sync-env-defaults»
    #   в код/доки/CI (кодер снова гоняет удалённый таргет)
    # · Scenario: скан всех корней на 3 точных литерала (regex-подстрока re.escape)
    # · Last fail: N/A (первый прогон расширенного скана; preflight-ban ввёл DevPlan 120)
    # · Remove if: literal-баны удалены (пересмотр DevPlan 138 §3.1/§3.2 — Rev: —)
    caplog.set_level(logging.INFO)

    scan_roots: list[Path] = [ROOT / rel for rel in _MAKE_LITERAL_SCAN_ROOTS] + [
        ROOT / f for f in _MAKE_LITERAL_SCAN_FILES
    ]
    violations = _scan_paths(scan_roots, patterns=_MAKE_LITERAL_BANS)

    logger.info(
        "[IMP:8][phantom_gate][make-literal-ban] Scanned %d roots, %d violation(s)",
        len(scan_roots),
        len(violations),
    )

    if violations:
        for v in violations:
            logger.error("[IMP:10][phantom_gate][make-literal-ban] %s", v)
        pytest.fail(
            f"[IMP:10][phantom_gate][make-literal-ban] {len(violations)} упоминание(й) "
            f"{', '.join(_MAKE_LITERAL_BANS)} во всех сканируемых корнях — "
            "DevPlan 138 W1 literal-бан нарушен:\n" + "\n".join(f"  - {v}" for v in violations)
        )

    logger.critical(
        "[IMP:9][phantom_gate][make-literal-ban] PASS: 0 упоминаний %s во всех сканируемых корнях",
        ", ".join(_MAKE_LITERAL_BANS),
    )


# endregion FUNC_test_no_make_literal_bans_in_scan_roots


# region FUNC_test_make_literal_ban_scan_detects_dummy
@pytest.mark.gate
@ldd_trajectory
def test_make_literal_ban_scan_detects_dummy(tmp_path, caplog) -> None:
    """Falsifiability: сканер обязан находить ВСЕ 3 make-литерала в фиктивном файле (R5).

    # ▶ tmp_path/dummy.md с «make compose-safe-up» → ◇ violations non-empty? → PASS

    ## @purpose — Anti-survivorship: make-literal скан, который не может упасть, — не гейт.
    ##            Доказывает, что _scan_paths(patterns=_MAKE_LITERAL_BANS) реально детектирует
    ##            каждый из 3 литералов (а не молча проходит из-за ошибки сканирования).
    ## @io — ⇥ tmp_path → ⎋ None (assert детекта)
    ## @complexity O(1) — один фиктивный файл
    """
    # 🧪 TRAP[TEST] · DevPlan 138 W1 · R5 anti-survivorship для make-literal скана
    # · Regression: сломанный скан (пустые корни, dot-dir фильтр, бинарный-пропуск) → вечнозелёный гейт
    # · Scenario: dummy.md со всеми 3 литералами в тексте → скан обязан найти каждый
    # · Last fail: N/A (первый тест)
    # · Remove if: make-literal скан удалён
    caplog.set_level(logging.INFO)

    dummy = tmp_path / "dummy.md"
    dummy.write_text(
        "# Инструкция\n\n"
        "Запустите make preflight для диагностики.\n"
        "Старый алиас make compose-safe-up больше не существует.\n"
        "Регенерация: make sync-env-defaults.\n"
    )

    violations = _scan_paths([tmp_path], patterns=_MAKE_LITERAL_BANS)
    logger.info("[IMP:8][phantom_gate][make-literal-ban][negative] violations: %s", violations)

    assert violations, "CRITICAL: make-literal скан не детектировал «make X» — гейт вечнозелёный!"
    for literal in _MAKE_LITERAL_BANS:
        assert any(literal in v for v in violations), f"Сканер нашёл нарушения, но не литерал {literal!r}: {violations}"
    logger.info("[IMP:9][phantom_gate][make-literal-ban][negative] PASS: все 3 make-литерала детектированы")


# endregion FUNC_test_make_literal_ban_scan_detects_dummy
