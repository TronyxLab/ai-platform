# GREP_SUMMARY: test-postgres-restore, DR-runbook, F-031, F-032, SEC-0018, root-compose, COMPOSE_PROFILES, pre-restore-isolation, plan-012 T7
# STRUCTURE: ▶ Makefile text parse → ◇ test_restore_target_contract [root-compose ∋ env-file ∋ profile] → ◇ test_plaintext_sql_not_in_retry_scan [pre-restore dir ∉ scan · legacy skip · detector alive] → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Контрактные тесты DR restore (plan 012 T7 / F-031/F-032/SEC-0018):
##           (a) restore-таргет postgres использует ROOT-compose + secrets env-file +
##           COMPOSE_PROFILES (никаких «undefined volume»/«no service selected»);
##           (c) plaintext pre_restore_* не попадает в S3 retry-скан.
## @scope    Static Makefile parsing + tmp_path spool fixtures; 0 Docker, 0 subprocess.
## @invariants
##   - Makefile парсится текстово по образцу test_deploy_mk_chain/test_makefile_parser
##   - spool_retry импорт через sys.path core/modules/backup-cron/scripts
##     (контейнерный контракт: 0 imports из core/internal — тест повторяет канон)
##   - Негатив SEC-0018: детектор жив (обычный дамп сканируется), pre_restore — нет (R5-парность)
## @rationale F-031: restore «из коробки» падал (env/profiles/volumes); F-032/SEC-0018:
##            plaintext pre_restore снапшоты в скан-каталоге — риск S3-загрузки plaintext.
## @changes   CREATED 2026-08-26 | DevPlan 012 T7 — DR restore contract tests
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

import pytest
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

PLATFORM_ROOT: Path = Path(__file__).resolve().parent.parent.parent
POSTGRES_MAKEFILE: Path = PLATFORM_ROOT / "core" / "modules" / "postgres" / "Makefile"

_SCRIPTS_DIR: str = str(PLATFORM_ROOT / "core" / "modules" / "backup-cron" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from spool_retry import find_pending  # pyright: ignore[reportImportCycles] — контейнерный модуль (path-inject выше)

pytestmark = pytest.mark.static_audit


def _read_makefile() -> str:
    assert POSTGRES_MAKEFILE.is_file(), f"[IMP:9][t7] FAIL: postgres Makefile not found: {POSTGRES_MAKEFILE}"
    return POSTGRES_MAKEFILE.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: restore target contract (root-compose + env-file + profiles)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_restore_target_contract(caplog: pytest.LogCaptureFixture) -> None:
    """Restore recipe uses root compose, secrets env-file and COMPOSE_PROFILES=postgres.

    ## @purpose — F-031 AC(a): restore работает штатно — без «undefined volume»
    ##            (-f base.yml), «no service selected» (без профиля) и голого env.
    ## @io — ⇥ Makefile text → ⎋ None (asserts contract lines)
    ## @complexity — O(N) line scan
    ## @scenario — AC T7(a): root-compose + source secrets.env + COMPOSE_PROFILES
    """
    # 🧪 TRAP[TEST] · 2026-08-26 · REGRESSION · F-031 restore out-of-the-box
    # · Scenario: RESTORE_COMPOSE определён через ROOT docker-compose.yml; stop/up в
    #             рецепте идут с COMPOSE_PROFILES=postgres; env-file подключён
    # · Last fail: F-031 — make -C postgres restore падал: secrets env, profiles,
    #   undefined volume postgres-data
    # · Remove if: restore переходит на выделенный python-модуль вместо Makefile-рецепта
    content = _read_makefile()

    # (1) Root-compose как источник стека для restore (volumes SoT) — target-specific
    # переопределение COMPOSE_FILE (текст рецепта остаётся каноническим $(COMPOSE_CMD), D70)
    root_compose_defined = "restore: COMPOSE_FILE = $(PLATFORM_ROOT)/docker-compose.yml" in content
    assert root_compose_defined, (
        "[IMP:9][t7] FAIL: RESTORE_COMPOSE must use -f $(PLATFORM_ROOT)/docker-compose.yml "
        "(module base.yml does not define shared volumes → 'undefined volume')"
    )

    # (2) Secrets env-file: postgres Makefile объявляет SECRETS_ENV, канон module.mk
    # подключает его к $(COMPOSE_CMD) через --env-file (F-031: голый env устранён)
    assert "SECRETS_ENV" in content, "[IMP:9][t7] FAIL: Makefile must declare SECRETS_ENV (secrets source)"
    module_mk = (PLATFORM_ROOT / "core" / "templates" / "module.mk").read_text(encoding="utf-8")
    assert "--env-file" in module_mk and "SECRETS_ENV" in module_mk, (
        "[IMP:9][t7] FAIL: COMPOSE_CMD canon (module.mk) must wire SECRETS_ENV via --env-file"
    )

    # (3) Профиль задан при stop/up в restore-фазах («no service selected» guard)
    profiled_phases = [
        line.strip()
        for line in content.splitlines()
        if "COMPOSE_PROFILES=postgres $(COMPOSE_CMD)" in line and ("stop" in line or "up -d" in line)
    ]
    assert len(profiled_phases) >= 2, (
        f"[IMP:9][t7] FAIL: restore PHASE 2/3 must run with COMPOSE_PROFILES=postgres, got {profiled_phases}"
    )
    logger.critical("[IMP:9][t7] Restore contract verified: root-compose + --env-file + COMPOSE_PROFILES")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: plaintext pre_restore not in S3 retry scan (SEC-0018 negative pairing)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_plaintext_sql_not_in_retry_scan(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """pre_restore_*.sql stays out of the S3 retry scan; regular dumps still detected.

    ## @purpose — SEC-0018/F-032 негатив-парность: (а) pre_restore в отдельном каталоге
    ##            ВНЕ скана не попадает в candidates; (б) legacy pre_restore_* внутри
    ##            скан-каталога исключается по префиксу с WARN; (в) детектор ЖИВ —
    ##            обычный pgdumpall_*.sql остаётся кандидатом (иначе гейт пустой).
    ## @io — ⇥ tmp_path spool fixture → ⎋ None (asserts scan results)
    ## @complexity — O(N) files
    ## @scenario — AC T7(c): plaintext .sql не попадает в S3-скан
    """
    # 🧪 TRAP[TEST] · 2026-08-26 · NEGATIVE+POSITIVE (R5 pair) · SEC-0018 spool isolation
    # · Scenario: spool{pre-restore/pre_restore_X.sql, postgres/pgdumpall_OK.sql,
    #             postgres/pre_restore_LEGACY.sql} → find_pending == [pgdumpall_OK]
    # · Last fail: F-032/SEC-0018 — pre_restore_$TS.sql писался ПРЯМО в spool/postgres/
    #   (скан-каталог) → plaintext попадал в retry-S3 пайплайн
    # · Remove if: pre-restore снапшоты переезжают из backup-spool вовсе (другое хранилище)
    spool = tmp_path / "backup-spool"
    pre_restore_dir = spool / "pre-restore"
    postgres_dir = spool / "postgres"
    pre_restore_dir.mkdir(parents=True)
    postgres_dir.mkdir(parents=True)

    # (a) Новый канон: pre_restore пишется ВНЕ скан-каталогов
    (pre_restore_dir / "pre_restore_20260826T000000Z.sql").write_text("-- plaintext snapshot", encoding="utf-8")
    # (b) Legacy: старый писатель оставил plaintext в скан-каталоге
    (postgres_dir / "pre_restore_20260101T000000Z.sql").write_text("-- legacy plaintext", encoding="utf-8")
    # (в) Детектор жив: регулярный дамп — кандидат
    (postgres_dir / "pgdumpall_20260825T030000Z.sql").write_text("-- real dump", encoding="utf-8")

    pending = find_pending(str(spool))
    keys = [s3_key for _, s3_key in pending]

    assert keys == ["postgres/pgdumpall_20260825T030000Z.sql"], (
        f"SEC-0018 FAIL: only the real dump may be scanned, got {keys}"
    )
    legacy_warns = [r for r in caplog.records if "SEC-0018" in r.getMessage() and "pre_restore" in r.getMessage()]
    assert legacy_warns, "Legacy in-scan pre_restore must be skipped LOUDLY (WARN)"
    logger.critical("[IMP:9][t7] Plaintext pre_restore isolated from S3 scan; detector alive for real dumps")
