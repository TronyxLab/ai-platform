"""Env-chain detector — unresolved template placeholders (DevPlan 163 W-C).

# GREP_SUMMARY: static env-chain prometheus envsubst template-vars unexpanded-placeholders single-source U-48
# STRUCTURE: ▶ read prometheus.yml.tmpl → ○ find ${...} placeholders → ◇ each ∈ known env vars?
#            → ⊕ unresolved Finding → ◇ prometheus.yml дубль существует? → ⊕ Finding → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор целостности env-цепочки (DevPlan 163 W-C C1; порт
##           tests/gates/test_gate_env_chain.py, DevPlan 011 T7 + 116 B3 T3 U-48):
##           (1) prometheus.yml.tmpl содержит только известные ${VAR} паттерны
##           (неразрешённые envsubst-плейсхолдеры → RED); (2) prometheus.yml (без .tmpl)
##           НЕ должен существовать — .tmpl единственный источник (U-48).
##           Находки — rule="env-chain" (blocking).
## @scope    Конфиг-файлы core/modules/monitoring/config/. Не требует envsubst-бинарника
##           (чистая статическая проверка набора переменных).
## @invariants
##   - template_vars = все ${VAR} паттерны (без $$-эскейпов) в prometheus.yml.tmpl
##   - known_vars = {"LITELLM_MASTER_KEY"} — документированные в .env.example/secrets.env
##   - unresolved = template_vars - known_vars → RED
##   - prometheus.yml (без .tmpl) существует → RED (рендер генерирует runtime-конфиг из .tmpl)
##   - `changed`: при --changed прогон только если tmpl/yml в changed
## @rationale D5b: LITELLM_MASTER_KEY резолвится через envsubst (init container);
##            финальный конфиг не должен иметь неразрешённых плейсхолдеров. U-48:
##            дубль prometheus.yml — дрейф двух источников.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created (порт env-chain гейта)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

_TMPL_REL = "core/modules/monitoring/config/prometheus.yml.tmpl"
_YML_REL = "core/modules/monitoring/config/prometheus.yml"

# Известные переменные, резолвимые envsubst при деплое
_KNOWN_VARS: frozenset[str] = frozenset(("LITELLM_MASTER_KEY",))

# ${VAR} без $$-эскейпа
_RE_TEMPLATE_VAR: re.Pattern[str] = re.compile(r"(?<!\$)\$\{(\w+)\}")


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти неразрешённые плейсхолдеры/дубли в prometheus-конфиге.

    # ▶ ┌prometheus.yml.tmpl┐ → ○ ${VAR} set → ◇ unresolved? → ⊕ Finding
    #   → ◇ prometheus.yml существует? → ⊕ Finding → ⎋

    ## @purpose  Главный вход детектора (registry): правила D5b (неразрешённые
    ##           переменные) + U-48 (дубль-файл запрещён).
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(N) — размер tmpl
    ## @invariants  tmpl отсутствует → 0 находок (skip, не fail); findings.file —
    ##              repo-relative путь проблемного файла
    """
    if changed is not None and not ({_TMPL_REL, _YML_REL} & changed):
        logger.info("[IMP:8][env_chain][changed] No changed env-chain source — skipping")
        return []

    tmpl = root / _TMPL_REL
    if not tmpl.is_file():
        logger.warning("[IMP:7][env_chain] Template not found: %s", tmpl)
        return []

    findings: list[Finding] = []
    try:
        tmpl_content = tmpl.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    template_vars = set(_RE_TEMPLATE_VAR.findall(tmpl_content))
    unresolved = template_vars - _KNOWN_VARS
    if unresolved:
        findings.append(
            Finding(
                rule="env-chain",
                file=_TMPL_REL,
                line=1,
                message="unknown template variables in prometheus.yml.tmpl: " + ", ".join(sorted(unresolved)),
            )
        )
        logger.warning("[IMP:9][env_chain][RED] unknown template vars: %s", sorted(unresolved))

    yml = root / _YML_REL
    if yml.is_file():
        findings.append(
            Finding(
                rule="env-chain",
                file=_YML_REL,
                line=1,
                message="prometheus.yml duplicate must NOT exist — prometheus.yml.tmpl is the single source (U-48)",
            )
        )
        logger.warning("[IMP:9][env_chain][RED] prometheus.yml duplicate exists (U-48)")

    logger.info("[IMP:9][env_chain] template vars=%d, findings=%d", len(template_vars), len(findings))
    if not findings:
        logger.info("[IMP:9][env_chain] PASS: all template variables known, no duplicate")
    return findings


# endregion FUNC_detect
