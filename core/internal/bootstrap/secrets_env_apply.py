# GREP_SUMMARY: secrets-env-apply, apply-secrets-env, allowlist-env, source-secrets, env-write, shared-parser, os-environ, cert-orchestrator, 170-W6-D3
# STRUCTURE: ▶ ┌env_path + allowlist + prefixes┐ → ⊕ parse(shared 086) → ◇ filter (allowlist ∪ prefixes) → ⊕ target.update(matched) → ⎋ dict[str,str]
# region MODULE_CONTRACT
## @purpose  Apply secrets.env into the process environment via a strict allowlist
##           (DevPlan 170 W6-D3): читает secrets.env через shared secrets_env_parser (086-канон),
##           записывает ТОЛЬКО allowlist-имена и префикс-имена в target (default os.environ).
##           Выделен из cert_orchestrator._source_secrets_env — изоляция мутации env
##           + прямая тестируемость allowlist-контракта (R5-negative: имя вне allowlist НЕ пишется).
## @scope    Потребитель: cert_orchestrator.py (обёртка _source_secrets_env вычисляет allowlist
##           из provider_registry и делегирует сюда). Канонический канал записи секретов в env
##           для любого модуля, которому нужен source-семантики без inline-парсинга.
## @invariants
##   1. Пишутся ТОЛЬКО ключи из allowlist ИЛИ с префиксом из prefixes (case-sensitive)
##   2. Файл читается каноническим shared secrets_env_parser.parse (DevPlan 086 — единый парсер)
##   3. FileNotFoundError пробрасывается — caller проверяет существование (facts.path_isfile) / ловит
##   4. target=None → os.environ (мутация процесса, source-семантика — контракт provider-тестов);
##      явный target (DI) не трогает os.environ
##   5. Возвращает dict записанных переменных (matched) — наблюдаемость без повторного парсинга env
##   6. Значения не логируются (086 TRAP: SECRET LEAK в логи) — только имена ключей
## @rationale  DevPlan 170 W6-D3 (research-A §5): _source_secrets_env (cert_orchestrator.py ~978-992)
##             писал в os.environ без изоляции и unit-тестов; allowlist-фильтр (154 W1 инвариант 4)
##             был неразрывно связан с реестром провайдеров. Вынос в отдельный модуль делает
##             allowlist-контракт тестируемым (R5-negative) и переиспользуемым, а cert_orchestrator
##             — тонкой обёрткой-делегатом (изоляция мутации env, DevPlan 170 W6-D3).
## @changes  2026-08-15 | DevPlan 170 W6-D3 — выделен из cert_orchestrator._source_secrets_env
# endregion MODULE_CONTRACT

import logging
import os
from collections.abc import Collection, MutableMapping

from core.internal.shared.secrets_env_parser import parse as parse_secrets_env

logger = logging.getLogger(__name__)


# region FUNC_apply_secrets_env
## @purpose  Прочитать secrets.env и записать allowlist-подмножество в target (default os.environ).
##            Возвращает dict записанных переменных — R5-negative наблюдаемость.
## @io — ⇥ env_path: str, allowlist: Collection[str], prefixes: tuple[str, ...],
##       target: MutableMapping[str, str] | None → ⎋ dict[str, str] (записанные пары)
## @complexity — O(N) — N = записей secrets.env
## @invariants
##   - key ∈ allowlist OR key.startswith(prefix) → записывается
##   - target=None → os.environ; явный target не модифицирует os.environ
##   - FileNotFoundError от parse пробрасывается (caller ловит; не-файл — не наша семантика)
##   - Значения никогда не логируются (086 TRAP[BUG]: SECRET LEAK) — только имена ключей
def apply_secrets_env(
    env_path: str,
    allowlist: Collection[str],
    prefixes: tuple[str, ...] = (),
    *,
    target: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Прочитать secrets.env и записать allowlist-имена в env (source-семантика).

    ▶ ┌env_path┐ → ⊕ parse (shared 086) → ◇ key ∈ allowlist ∪ prefixes? → ⊕ target.update → ⎋ matched dict
    """
    logger.info("[IMP:7][secrets_env_apply] Applying secrets.env: %s", env_path)
    parsed = parse_secrets_env(env_path)
    allow_set = set(allowlist)
    matched: dict[str, str] = {}
    for key, value in parsed.items():
        if key in allow_set or (prefixes and key.startswith(prefixes)):
            matched[key] = value
            logger.debug("[IMP:8][secrets_env_apply] Set env: %s", key)
    env_map: MutableMapping[str, str] = os.environ if target is None else target
    env_map.update(matched)
    logger.info(
        "[IMP:9][secrets_env_apply] Applied %d/%d allowlisted entries from %s",
        len(matched),
        len(parsed),
        env_path,
    )
    return matched


# endregion FUNC_apply_secrets_env
