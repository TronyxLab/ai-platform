#!/usr/bin/env python3
# GREP_SUMMARY: fingerprint-salt, differs, toolchain-digest, env-vars, cache-invalidation, atomic-write-json, unique-tmp, REF-0107
# STRUCTURE: ▶ _fingerprint_salt(env/versions) → ◇ смена pytest-версии/env-var → ⟦fp ≠⟧ ·
#            ▶ save_cache → ◇ параллельные записи → ⎋ уникальный tmp, валидный JSON на месте
# region MODULE_CONTRACT
## @purpose  REF-0107 fingerprint-differs тесты: salt инвалидирует кэш при смене toolchain
##           (pip-upgrade) или семантических env-переменных; save_cache использует уникальный
##           tmp (два параллельных писателя не рвут файл фиксированным .json.tmp).
## @scope    core/internal/check_suite/fingerprint.py (_fingerprint_salt, save_cache).
## @invariants
##   - Та же версия+env → тот же salt (стабильность replay)
##   - Смена любой из {pytest, pytest-xdist, ruff}-версий / TEST_NO_XDIST /
##     REQUIRE_HONESTY_MODE / CHECK_XDIST_MAX_WORKERS → другой salt (replay = miss)
##   - compute_fingerprint включает salt в итоговый хеш (смена env → другой fp дерева)
## @rationale REF-0107 problem (5): «fingerprint cache не учитывает toolchain/env → replay
##            старых green'ов после pip-upgrade; fixed .json.tmp рвётся двумя make check».
## @changes 2026-08-25 | REF-0107 (DevPlan 11 Волна 3) — Created
# endregion MODULE_CONTRACT

import json
import logging
from pathlib import Path

from core.internal.check_suite import fingerprint as fp_mod
from core.internal.check_suite.fingerprint import _fingerprint_salt, save_cache

logger = logging.getLogger(__name__)


# region TEST_salt_stable_and_differs
def test_salt_stable_for_same_inputs() -> None:
    """Стабильность: те же версии и env → байт-идентичный salt (легитимный replay жив)."""
    versions = {"pytest": "9.1.1", "pytest-xdist": "3.6.1", "ruff": "0.16.2"}
    env = {"TEST_NO_XDIST": "", "REQUIRE_HONESTY_MODE": "fail", "CHECK_XDIST_MAX_WORKERS": ""}
    s1 = _fingerprint_salt(env=env, versions=versions)
    s2 = _fingerprint_salt(env=dict(env), versions=dict(versions))
    assert s1 == s2, "salt обязан быть детерминированным при тех же входах"
    logger.info("[IMP:9][fingerprint-salt] stable: %s…", s1[:12])


# endregion TEST_salt_stable_and_differs


def test_salt_differs_on_toolchain_upgrade() -> None:
    """REF-0107: pip-upgrade pytest меняет salt → старый зелёный отчёт не реплеится."""
    env: dict[str, str] = {}
    old = _fingerprint_salt(env=env, versions={"pytest": "9.1.0"})
    new = _fingerprint_salt(env=env, versions={"pytest": "9.1.1"})
    assert old != new, "смена версии pytest обязана менять fingerprint-salt"
    logger.info("[IMP:9][fingerprint-salt] toolchain-upgrade invalidates: %s… != %s…", old[:8], new[:8])


# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · QA R12/T2.G — basedpyright в составе salt
# · Scenario: bump basedpyright (его вердикты входят в static-фазу) без смены salt реплеил
#   старые зелёные отчёты от дерева, проверенного СТАРЫМ pyright'ом
# · Last fail: 2026-08-25 — _SALT_TOOLCHAIN_PKGS содержал только pytest/xdist/ruff
# · Remove if: static-фаза перестанет использовать basedpyright
def test_salt_differs_on_basedpyright_bump() -> None:
    """Bump basedpyright → salt меняется; отсутствие пакета → 'absent' без crash."""
    base_versions = {"pytest": "9.1.1", "pytest-xdist": "3.6.1", "ruff": "0.16.2", "basedpyright": "1.28.0"}
    old = _fingerprint_salt(versions=dict(base_versions))
    bumped = dict(base_versions, basedpyright="1.29.0")
    new = _fingerprint_salt(versions=bumped)
    assert old != new, "QA R12 FAIL: bump basedpyright обязан менять salt"

    # Отсутствующий пакет → маркер "absent" (детерминирован, не бросает)
    without = _fingerprint_salt(versions={"pytest": "9.1.1", "pytest-xdist": "3.6.1", "ruff": "0.16.2"})
    without_again = _fingerprint_salt(versions={"pytest": "9.1.1", "pytest-xdist": "3.6.1", "ruff": "0.16.2"})
    assert without == without_again, "absent-маркер обязан быть детерминированным"
    assert without != old, "absent vs установленная версия — разные salt"
    logger.info("[IMP:9][fingerprint-salt] basedpyright bump + absent-marker OK")


def test_salt_differs_on_semantic_env_var() -> None:
    """REF-0107: каждая семантическая env-переменная меняет salt."""
    base_env = {"TEST_NO_XDIST": "", "REQUIRE_HONESTY_MODE": "", "CHECK_XDIST_MAX_WORKERS": ""}
    base = _fingerprint_salt(env=base_env)
    for var in ("TEST_NO_XDIST", "REQUIRE_HONESTY_MODE", "CHECK_XDIST_MAX_WORKERS"):
        changed = dict(base_env)
        changed[var] = "1" if var == "TEST_NO_XDIST" else ("fail" if var == "REQUIRE_HONESTY_MODE" else "4")
        assert _fingerprint_salt(env=changed) != base, f"смена {var} обязана менять salt"
        logger.info("[IMP:9][fingerprint-salt] env-var %s invalidates cache", var)


# region TEST_compute_fingerprint_includes_salt
def test_compute_fingerprint_differs_across_env(monkeypatch, tmp_path) -> None:
    """compute_fingerprint одного дерева даёт разные fp при разном honesty-mode (salt внутри)."""
    import subprocess as sp

    git_init = ["git", "init", "-q", str(tmp_path)]
    sp.run(git_init, check=True, capture_output=True)
    (tmp_path / "f.txt").write_text("stable content\n")
    sp.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)

    tree_files_real = fp_mod.tree_files  # сохраняем реализацию до патча пакетной атрибуции

    def _fake_tree_files(_root):  # monkeypatch-контракт пакета (check_suite.tree_files)
        return tree_files_real(tmp_path)

    monkeypatch.setattr(fp_mod.cs, "tree_files", _fake_tree_files)

    monkeypatch.setenv("REQUIRE_HONESTY_MODE", "")
    fp_a = fp_mod.compute_fingerprint(tmp_path)
    monkeypatch.setenv("REQUIRE_HONESTY_MODE", "fail")
    fp_b = fp_mod.compute_fingerprint(tmp_path)

    assert fp_a is not None and fp_b is not None
    assert fp_a != fp_b, "одно дерево + другая honesty-mode = другой fingerprint (cache miss)"
    logger.info("[IMP:9][fingerprint-salt] tree-fp differs across env: %s… vs %s…", fp_a[:8], fp_b[:8])


# endregion TEST_compute_fingerprint_includes_salt


# region TEST_save_cache_unique_tmp
def test_save_cache_atomic_and_loadable(tmp_path, caplog) -> None:
    """save_cache пишет валидный JSON атомарно; tmp-мусор (fixed .json.tmp) не остаётся."""
    caplog.set_level(logging.INFO)
    target = tmp_path / "sub" / "check-cache.json"
    payload = {"fingerprint": "abc123", "status": "green", "checks": []}
    save_cache(target, payload)

    assert target.is_file(), "кэш-файл обязан существовать после save_cache"
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["fingerprint"] == "abc123"
    leftovers = [p.name for p in target.parent.iterdir() if p.name != target.name]
    assert not leftovers, f"атомарный writer не должен оставлять tmp-артефактов: {leftovers}"
    logger.info("[IMP:9][fingerprint-salt] save_cache atomic, loadable, no tmp litter")


def test_save_cache_none_path_noop(tmp_path: Path) -> None:
    """path=None (git недоступен) → no-op без исключения (прежний контракт)."""
    # R1: явный ассерт — no-op не создаёт артефактов и возвращает None
    result = save_cache(None, {"fingerprint": "x"})
    assert result is None
    assert not list(tmp_path.iterdir()), "no-op обязан не писать файлов"
    logger.info("[IMP:9][fingerprint-salt] None-path noop OK")


# endregion TEST_save_cache_unique_tmp
