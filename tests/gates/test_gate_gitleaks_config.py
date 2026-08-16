# GREP_SUMMARY: gate gitleaks-config age-secret-key custom-rule useDefault extend tomllib R5 probe regex security C3
# STRUCTURE: ▶ tomllib load .gitleaks.toml → ◇ find [[rules]] age-secret-key → ◇ re.search probe (R5-negative: probe матчится, короткие/тестовые фикстуры — нет) → ◇ [extend] useDefault guard → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 170 W2-A1, Research C3): .gitleaks.toml содержит кастомное правило
##           age-secret-key (AGE-ключи не покрыты дефолтными правилами gitleaks), а regex правила
##           РАБОТОСПОСОБЕН: матчит реальный формат AGE-ключа и НЕ матчит короткие/тестовые фикстуры.
## @scope    Статический анализ .gitleaks.toml (tomllib) — без реального gitleaks-бинарника.
##           (a) правило присутствует; (b) R5-negative: probe-строка матчится, негативы не матчатся.
## @invariants
##   - [[rules]] id=age-secret-key присутствует, regex == AGE-SECRET-KEY-1[A-Z0-9]{57,}
##   - [extend] useDefault == true (TRAP[BUG] 2026-07-12: top-level useDefault молча игнорируется)
##   - R5-negative: probe 'AGE-SECRET-KEY-1' + 57+ [A-Z0-9] МАТЧИТСЯ; короткая (<57) и
##     тестовые фикстуры (lowercase / 52 символа / '...') НЕ матчатся — gitleaks не блокирует
##     коммиты тестов с синтетическими AGE-ключами
##   - probe-строка строится КОНКАТЕНАЦИЕЙ — цельная строка не присутствует в исходнике
##     (иначе сам тест-файл был бы детектирован gitleaks pre-commit как секрет)
## @rationale Research C3 (DevPlan 170): дефолтные правила gitleaks НЕ покрывают AGE-ключи
##            (bech32 X25519 AGE-SECRET-KEY-…). Кастомное правило + работающий regex —
##            единственная защита от утечки AGE_SECRET_KEY (мастер-ключ шифрования ноды).
##            R5-пара (probe матчится / фикстуры не матчатся) доказывает детекцию, а не
##            наличие «мёртвой» конфигурации.
## @changes 2026-08-14 | DevPlan 170 W2-A1 — Created (C3)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import tomllib

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GITLEAKS_TOML = PROJECT_ROOT / ".gitleaks.toml"

# Канонический regex правила (совпадает со значением в .gitleaks.toml) —
# дубль допускается: тест-гейт валидирует конфиг, а не порождает его.
AGE_SECRET_KEY_RE = re.compile(r"AGE-SECRET-KEY-1[A-Z0-9]{57,}")

# Тестовая фикстура в стиле tests/unit/test_age_key_backup.py::_FAKE_KEY
# (52 символа [A-Z0-9] после «1» — ровно «почти ключ», не настоящий).
_FAKE_KEY_52 = "AGE-SECRET-KEY-1QXX0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0"


def _load_gitleaks_config() -> dict:
    """Load .gitleaks.toml via tomllib.

    ## @purpose — Единая точка чтения конфига для всех тестов гейта.
    ## @io — ⇥ None → ⎋ dict (parsed TOML) ⚡ AssertionError (файл отсутствует/не TOML)
    ## @complexity — O(F) где F = размер конфига
    ## @invariants
    ##   - Файл обязан существовать (pre-commit + 4 CI workflow читают его)
    ##   - Коррапт TOML → явный fail (не тихий skip)
    """
    assert GITLEAKS_TOML.is_file(), f"[IMP:10][gitleaks-config] {GITLEAKS_TOML} not found"
    with GITLEAKS_TOML.open("rb") as f:
        data = tomllib.load(f)
    logger.info("[IMP:8][gitleaks-config] Parsed %s (%d rules)", GITLEAKS_TOML.name, len(data.get("rules", [])))
    return data


@pytest.mark.gate
class TestGateGitleaksConfig:
    """Gate: .gitleaks.toml covers AGE keys via a WORKING custom rule (DevPlan 170 C3)."""

    # 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · age-secret-key правило отсутствовало (Research C3)
    # · Scenario: .gitleaks.toml имел только [extend] useDefault — AGE-ключи (AGE-SECRET-KEY-…)
    # ·   не детектировались ни pre-commit, ни CI gitleaks (4 workflow, pin v8.30.1)
    # · Last fail: N/A (новое правило; C3-аудит зафиксировал пробел)
    # · Remove if: AGE-ключи перестанут использовать префикс AGE-SECRET-KEY-1[A-Z0-9]{57,}
    def test_age_secret_key_rule_present(self, caplog) -> None:
        """[[rules]] age-secret-key присутствует с каноническим regex (C3)."""
        caplog.set_level(logging.DEBUG)
        config = _load_gitleaks_config()

        rules = config.get("rules", [])
        assert isinstance(rules, list) and rules, (
            "[IMP:10][gitleaks-config] [[rules]] секция отсутствует — custom rule не зарегистрирован"
        )
        age_rule = next((r for r in rules if r.get("id") == "age-secret-key"), None)
        assert age_rule is not None, "[IMP:10][gitleaks-config] [[rules]] id=age-secret-key не найден (C3)"
        assert age_rule.get("regex") == AGE_SECRET_KEY_RE.pattern, (
            f"[IMP:10][gitleaks-config] regex не совпадает с каноном: "
            f"{age_rule.get('regex')!r} != {AGE_SECRET_KEY_RE.pattern!r}"
        )
        assert "AGE-SECRET-KEY-" in age_rule.get("keywords", []), (
            "[IMP:10][gitleaks-config] keywords должны содержать 'AGE-SECRET-KEY-' (оптимизация gitleaks)"
        )

        logger.info(
            "[IMP:9][gitleaks-config] PASS: [[rules]] age-secret-key = %s",
            age_rule.get("regex"),
        )
        assert "[IMP:9]" in caplog.text, "[IMP:9][gitleaks-config] LDD: бизнес-лог отсутствует"

    # 🧪 TRAP[TEST] · 2026-08-14 · NEGATIVE (R5) · regex МАТЧИТ реальный формат AGE-ключа
    # · Scenario: probe-строка 'AGE-SECRET-KEY-1' + 60×[A-Z0-9] (конкатенация — цельная
    # ·   строка не светится в исходнике) → re.search обязан найти совпадение
    # · Last fail: N/A (новый probe; доказывает работоспособность, а не наличие конфига)
    # · Remove if: формат AGE-ключа изменится (не bech32 X25519 с AGE-SECRET-KEY-1)
    def test_regex_matches_real_age_key_probe(self, caplog) -> None:
        """R5-negative: probe реального формата ключа МАТЧИТСЯ (детекция работает)."""
        caplog.set_level(logging.DEBUG)
        config = _load_gitleaks_config()
        rule_regex = next(r["regex"] for r in config["rules"] if r.get("id") == "age-secret-key")
        rule = re.compile(rule_regex)

        # Конкатенация: цельная секретная строка отсутствует в исходнике →
        # gitleaks pre-commit не детектирует сам тест-файл.
        probe = "AGE-SECRET-" + "KEY-1" + "QQ" * 30  # 60 [A-Z0-9] после префикса «1»
        assert len(probe) >= 15 + 57, f"[IMP:10] probe слишком короткий: {len(probe)}"
        assert rule.search(probe), "[IMP:10][gitleaks-config] R5 FAIL: probe НЕ матчится — regex мёртвый"

        logger.info("[IMP:9][gitleaks-config] PASS: probe (%d chars) матчится regex-ом", len(probe))
        assert "[IMP:9]" in caplog.text, "[IMP:9][gitleaks-config] LDD: бизнес-лог отсутствует"

    # 🧪 TRAP[TEST] · 2026-08-14 · NEGATIVE (R5) · короткие/тестовые фикстуры НЕ матчатся
    # · Scenario: короткая строка (<57), фикстура-стиль test_age_key_backup (52 символа),
    # ·   lowercase-фикстуры (test_bootstrap_auto / test_contract_deploy_ssh),
    # ·   '1ABC...TEST' с '...' — ни одна НЕ должна матчиться (иначе gitleaks блокирует
    # ·   коммиты существующих тестов с синтетическими ключами)
    # · Last fail: N/A (новый негатив; защита от ложных срабатываний)
    # · Remove if: тестовые фикстуры перестанут использовать AGE-SECRET-KEY- префикс
    def test_regex_rejects_short_and_test_fixtures(self, caplog) -> None:
        """R5-negative: короткие/тестовые AGE-строки НЕ матчатся (0 false positives)."""
        caplog.set_level(logging.DEBUG)
        config = _load_gitleaks_config()
        rule_regex = next(r["regex"] for r in config["rules"] if r.get("id") == "age-secret-key")
        rule = re.compile(rule_regex)

        negatives = [
            "AGE-SECRET-KEY-1QQ",  # короткая — <57 символов после «1»
            _FAKE_KEY_52,  # 52 символа — фикстура-стиль tests/unit/test_age_key_backup.py
            "AGE-SECRET-KEY-test-value-12345",  # lowercase — test_bootstrap_auto.py
            "AGE-SECRET-KEY-1ABC...TEST",  # '...' не входит в [A-Z0-9] — test_bootstrap_dry_run.py
        ]
        for neg in negatives:
            assert not rule.search(neg), f"[IMP:10][gitleaks-config] R5 FAIL: ложное срабатывание на {neg!r}"

        logger.info("[IMP:9][gitleaks-config] PASS: %d негативных фикстур НЕ матчатся", len(negatives))
        assert "[IMP:9]" in caplog.text, "[IMP:9][gitleaks-config] LDD: бизнес-лог отсутствует"

    # 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · useDefault под [extend] (TRAP[BUG] 2026-07-12)
    # · Scenario: top-level useDefault=true молча игнорируется gitleaks v8.30.1 (0 правил детекции);
    # ·   перенос useDefault из [extend] → сканер работает с ZERO правил
    # · Last fail: 2026-07-12 — useDefault был top-level, gitleaks сканировал без правил (gitleaks#1985)
    # · Remove if: gitleaks исправит поведение top-level useDefault (новая мажорная версия)
    def test_extend_use_default_guard(self, caplog) -> None:
        """[extend] useDefault == true — регрессионный guard TRAP[BUG] 2026-07-12."""
        caplog.set_level(logging.DEBUG)
        config = _load_gitleaks_config()

        extend = config.get("extend", {})
        assert extend.get("useDefault") is True, (
            "[IMP:10][gitleaks-config] useDefault должен быть под [extend] "
            "(top-level молча игнорируется gitleaks v8.30.1 — TRAP[BUG] 2026-07-12)"
        )

        logger.info("[IMP:9][gitleaks-config] PASS: [extend] useDefault=true (дефолтные правила активны)")
        assert "[IMP:9]" in caplog.text, "[IMP:9][gitleaks-config] LDD: бизнес-лог отсутствует"
