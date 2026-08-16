"""Static layer: env-chain detector tests (DevPlan 163 W-C C2).

# GREP_SUMMARY: test-static env-chain prometheus template-vars unresolved-placeholders duplicate U-48 R5
# STRUCTURE: ▶ tmpl с ${UNREGISTERED_VAR} (R5: неизвестная переменная) → RED | ▶ синтетический
#            дубль prometheus.yml (U-48) → RED | ▶ tmpl только с ${LITELLM_MASTER_KEY} → PASS → ⎋
"""
# region MODULE_CONTRACT
## @purpose  R5-пары детектора env_chain (DevPlan 163 W-C C2): негативный тест на класс
##           дефекта исходного гейта (неизвестная ${VAR} в prometheus.yml.tmpl — инвариант
##           test_prometheus_config_no_unexpanded_vars), позитивный тест на синтетический
##           дубль prometheus.yml (U-48), PASS-контроль (известная переменная не RED).
## @scope    Native imports; probe-файлы под tmp_path/core/modules/monitoring/config/
##           (фиксированный путь детектора).
## @invariants
##   - ${VAR} вне known = {LITELLM_MASTER_KEY} в prometheus.yml.tmpl → RED
##   - prometheus.yml (без .tmpl) существует → RED (U-48: .tmpl единственный источник)
##   - tmpl только с известными переменными → PASS
## @rationale R5 anti-survivorship (D5b + U-48): неразрешённые плейсхолдеры ломали
##            runtime-конфиг; md5-дубль prometheus.yml удалён (116 B3 T3) и запрещён.
## @changes 2026-08-13 | DevPlan 163 W-C C2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.static.env_chain import detect
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

_CONFIG_REL = "core/modules/monitoring/config"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · неизвестная ${VAR} в tmpl → RED (D5b-инвариант гейта)
# · Scenario: tmpl c `- bearer_token: ${UNREGISTERED_VAR}` — переменная вне known-множества
# ·   {LITELLM_MASTER_KEY}; исходный инвариант гейта: unresolved = template_vars − known = ∅
# · Last fail: prometheus.yml.tmpl содержал переменные вне .env.example/secrets.env (D5b)
# · Remove if: env-chain гейт отменяется
@ldd_trajectory
def test_env_chain_negative_unknown_var_detected(caplog, tmp_path) -> None:
    """R5 negative: неизвестная ${VAR} в prometheus.yml.tmpl детектируется (D5b)."""
    tmpl = tmp_path / _CONFIG_REL / "prometheus.yml.tmpl"
    tmpl.parent.mkdir(parents=True)
    tmpl.write_text(
        "scrape_configs:\n  - job_name: litellm\n    bearer_token: ${UNREGISTERED_VAR}\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "prometheus.yml.tmpl" in f.file]
    assert hits, "R5 FAIL: unknown template variable not detected"
    assert "UNREGISTERED_VAR" in hits[0].message
    logger.info("[IMP:9][test_env_chain] R5 unknown var RED: %s", hits[0])


# 🧪 TRAP[TEST] · POSITIVE · синтетический дубль prometheus.yml → RED (U-48)
# · Scenario: tmpl + prometheus.yml (без .tmpl) существуют одновременно — дубль-источник;
# ·   md5-дубль удалён в 116 B3 T3 (U-48), рендер генерирует runtime-конфиг из .tmpl
# · Last fail: N/A (синтетический вариант)
# · Remove if: prometheus.yml.tmpl single-source политика отменяется
@ldd_trajectory
def test_env_chain_duplicate_yml_forbidden(caplog, tmp_path) -> None:
    """Synthetic positive: дубль prometheus.yml рядом с .tmpl → RED (U-48)."""
    config = tmp_path / _CONFIG_REL
    config.mkdir(parents=True)
    (config / "prometheus.yml.tmpl").write_text("bearer_token: ${LITELLM_MASTER_KEY}\n", encoding="utf-8")
    (config / "prometheus.yml").write_text("bearer_token: placeholder\n", encoding="utf-8")
    findings = detect(tmp_path)
    hits = [f for f in findings if "prometheus.yml" in f.file]
    assert hits, "R5 FAIL: duplicate prometheus.yml not detected (U-48)"
    logger.info("[IMP:9][test_env_chain] duplicate prometheus.yml RED: %s", hits[0])


# 🧪 TRAP[TEST] · CONTROL · tmpl только с известной переменной → PASS
# · Scenario: tmpl c `${LITELLM_MASTER_KEY}` (known) и без дубля → 0 RED
# · Last fail: N/A (control — легитимный envsubst-плейсхолдер не должен быть RED)
# · Remove if: env-chain гейт отменяется
@ldd_trajectory
def test_env_chain_known_var_not_flagged(caplog, tmp_path) -> None:
    """PASS-контроль: ${LITELLM_MASTER_KEY} (известная переменная) не RED."""
    tmpl = tmp_path / _CONFIG_REL / "prometheus.yml.tmpl"
    tmpl.parent.mkdir(parents=True)
    tmpl.write_text(
        "scrape_configs:\n  - job_name: litellm\n    bearer_token: ${LITELLM_MASTER_KEY}\n",
        encoding="utf-8",
    )
    findings = detect(tmp_path)
    hits = [f for f in findings if "prometheus.yml.tmpl" in f.file]
    assert not hits, f"PASS-control FAIL: known template var flagged: {hits}"
    logger.info("[IMP:9][test_env_chain] known template var (LITELLM_MASTER_KEY) not flagged")
