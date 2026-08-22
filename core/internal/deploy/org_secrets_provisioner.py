# GREP_SUMMARY: org-secrets provisioner gh-cli context-promote VPS_HOST VPS_SSH_KEY AGE TELEGRAM visibility auto-configure dotenv env-reader
# STRUCTURE: ▶ resolve_node(context) → ◇ resolve_values (node.yaml + local env/.env через env_reader.get_env_value + ssh/age-файлы) → ◇ gh secret set ×N (visibility plan) → ⊕ audit → ⎋ bool (best-effort)
# region MODULE_CONTRACT
## @purpose  Авто-провижининг org-секретов контекстной GitHub-организации при context-promote
##           (DevPlan 003, follow-up 2026-08-16). До этого секреты настраивались руками и
##           расходились: visibility PRIVATE без привязки к репо (CI видел пустые значения),
##           AGE_SECRET_KEY отсутствовал — mirror-org core-deploy падал за 9s с пустым хостом.
## @scope    Вызывается из context_promoter.promote_context после верификации mirror-HEAD.
##           Код платформы, локальная машина оператора (не VPS).
## @invariants
##   1. Best-effort: сбой gh/разрешения значения НЕ роняет promote (mirror уже запушен) —
##      IMP:10 WARN + audit FAIL-запись, но return True (promote продолжает).
##   2. Значения НЕ печатаются в логи (только имена секретов и источники).
##   3. Единый visibility-план: TELEGRAM_* → all (нужны project-репо org); VPS_HOST/
##      VPS_SSH_KEY/AGE_SECRET_KEY → selected с --repos ai-platform
##      (gh CLI: --visibility selected, не private).
##   4. Источники значений (порядок): env → локальный .env платформы (get_env_value —
##      канон env_reader, last-match) → node.yaml → ~/.ssh/ai-platform/{node}-ci →
##      node_detect.detect_age_key() (AGE).
##   5. DRY_RUN: план без gh-вызовов.
## @rationale «Настройка секретов при добавлении контекста» — требование оператора
##           (2026-08-16): context-promote должен приводить org в деплоябельное состояние
##           без ручных шагов в UI.
## @changes 2026-08-22 | T2.5 — dotenv-чтение конвергировано на shared/env_reader.get_env_value
##           (единый канон чтения .env для make/.env; last-match семантика). Локальный
##           парсер _env_file_lookup (first-match) удалён — фикс согласованности: для
##           фактического .env платформы (без дублей/export/quotes) поведение эквивалентно.
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.internal.shared.env_reader import get_env_value

logger = logging.getLogger(__name__)

# region CONSTANTS

# Секрет → (visibility, --repos | None)
_ORG_SECRET_PLAN: dict[str, tuple[str, list[str] | None]] = {
    "TELEGRAM_BOT_TOKEN": ("all", None),
    "TELEGRAM_CHAT_ID_CRITICAL": ("all", None),
    "TELEGRAM_CHAT_ID_WARNING": ("all", None),
    "TELEGRAM_ALLOWED_USERS": ("all", None),
    "TELEGRAM_CHAT_ID": ("all", None),
    "VPS_HOST": ("selected", ["ai-platform"]),
    "VPS_SSH_KEY": ("selected", ["ai-platform"]),
    "AGE_SECRET_KEY": ("selected", ["ai-platform"]),
}

# Источники значений (в порядке приоритета): env-имя → файловые кандидаты
_PLATFORM_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ENV_FILE = _PLATFORM_ROOT / ".env"


@dataclass(frozen=True)
class NodeInfo:
    """Резолв ноды по контексту: имя, хост, каталог node-configs."""

    name: str
    host: str
    dir: Path


# endregion CONSTANTS


# region FUNC_resolve_node_for_context
## @purpose  Резолв ноды по контексту: node-configs/{node}/node.yaml, где contexts[].name == context.
## @io       ⇥ context: str, node_configs_dir: Path | None → ⎋ NodeInfo | None
## @complexity O(N) — N node-каталогов
def resolve_node_for_context(context: str, node_configs_dir: Path | None = None) -> NodeInfo | None:
    """Find node config whose contexts include <context>. Returns NodeInfo or None."""
    base = node_configs_dir or (_PLATFORM_ROOT / "node-configs")
    if not base.is_dir():
        return None
    for entry in sorted(base.iterdir()):
        yaml_path = entry / "node.yaml"
        if not entry.is_dir() or not yaml_path.is_file():
            continue
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            logger.info("[IMP:7][org-secrets] skip unparseable %s", yaml_path)
            continue
        if not isinstance(data, dict):
            continue
        contexts = data.get("contexts") or []
        if any(isinstance(c, dict) and str(c.get("name")) == context for c in contexts if isinstance(c, dict)):
            node = data.get("node") or {}
            if isinstance(node, dict):
                return NodeInfo(
                    name=str(node.get("name", entry.name)),
                    host=str(node.get("host", "")),
                    dir=entry,
                )
    return None


# endregion FUNC_resolve_node_for_context


# region FUNC__env_lookup
def _env_lookup(name: str, env: Mapping[str, str]) -> str | None:
    """Значение из env-словаря (пустая строка = отсутствие)."""
    value = env.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


# endregion FUNC__env_lookup


# region FUNC_resolve_secret_values
## @purpose  Разрешение значений орг-секретов из канонических локальных источников.
## @io       ⇥ context: str, env: Mapping | None, node: dict | None → ⎋ dict[str, str]
## @complexity O(S) — S секретов плана
## @invariants
##   - VPS_HOST ← node.yaml; пустой host → пропуск (WARN на выходе)
##   - VPS_SSH_KEY ← ~/.ssh/ai-platform/{node}-ci (приватный ключ; отсутствует → пропуск)
##   - AGE_SECRET_KEY ← env AGE_SECRET_KEY → node_detect.detect_age_key()
##   - TELEGRAM_* ← env → .env платформы
def resolve_secret_values(
    context: str,
    env: Mapping[str, str] | None = None,
    node: NodeInfo | None = None,
    env_file: Path | None = None,
) -> dict[str, str]:
    """Resolve org-secret values from local operator material. Missing sources are skipped."""
    env_map = os.environ if env is None else env
    env_path = env_file or _DEFAULT_ENV_FILE
    node_info = node if node is not None else resolve_node_for_context(context)
    values: dict[str, str] = {}

    if node_info is not None and node_info.host:
        values["VPS_HOST"] = node_info.host

    if node_info is not None:
        ssh_key = Path.home() / ".ssh" / "ai-platform" / f"{node_info.name}-ci"
        if ssh_key.is_file():
            import base64 as _b64

            # setup-ssh action (core-deploy.yml, key-encoding: base64) декодирует base64 —
            # raw PEM ломает шаг («base64: invalid input»); храним base64(PEM).
            values["VPS_SSH_KEY"] = _b64.b64encode(ssh_key.read_bytes()).decode("ascii")

    age_value = _env_lookup("AGE_SECRET_KEY", env_map)
    if age_value is None:
        try:
            from core.internal.shared.node_detect import detect_age_key  # лениво — leaf-модуль

            age_value = detect_age_key()
        # ruff: ignore[BLE001] — best-effort: AGE — необязательный секрет
        except Exception:  # noqa: EXC — best-effort: AGE — необязательный секрет
            age_value = None
    if age_value:
        values["AGE_SECRET_KEY"] = age_value

    for name in _ORG_SECRET_PLAN:
        if name in {"VPS_HOST", "VPS_SSH_KEY", "AGE_SECRET_KEY"}:
            continue
        # dotenv-чтение — канон env_reader.get_env_value (T2.5, last-match; пустое значение
        # при отсутствии файла/переменной → falsy → не настраивается, семантика сохранена)
        value = _env_lookup(name, env_map) or get_env_value(env_path, name)
        if value:
            values[name] = value

    return values


# endregion FUNC_resolve_secret_values


# region FUNC__set_one_secret
## @purpose  Один gh secret set: построение команды по visibility-плану, dry-run-вывод,
##           run_fn DI (тесты) или реальный subprocess. Возвращает bool.
## @io       ⇥ name, value, org, visibility, repos, run_fn, dry_run → ⎋ bool
## @complexity O(1)
def _run_gh(cmd: list[str], value: str, run_fn: Callable[[list[str], str], int] | None) -> tuple[int, str]:
    """Execute gh secret set (run_fn DI или реальный subprocess). Returns (rc, stderr)."""
    if run_fn is not None:
        return int(run_fn(cmd, value)), ""
    proc = subprocess.run(cmd, input=value, text=True, capture_output=True, check=False, timeout=60)
    return proc.returncode, proc.stderr.strip()


def _set_one_secret(
    name: str,
    value: str,
    org: str,
    visibility: str,
    repos: list[str] | None,
    run_fn: Callable[[list[str], str], int] | None,
    dry_run: bool,
) -> bool:
    """Configure one org secret via gh CLI (or run_fn DI). True on success."""
    if dry_run:
        logger.info("[IMP:8][org-secrets][dry-run] WOULD set %s (visibility=%s repos=%s)", name, visibility, repos)
        return True
    cmd = ["gh", "secret", "set", name, "-o", org, "--visibility", visibility]
    if repos:
        cmd += ["--repos", ",".join(repos)]
    try:
        rc, stderr = _run_gh(cmd, value, run_fn)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("[IMP:10][org-secrets] gh secret set %s error: %s", name, exc)
        return False
    if rc != 0:
        logger.info(
            "[IMP:10][org-secrets] gh secret set %s FAILED (rc=%d): %s",
            name,
            rc,
            stderr[-300:] if stderr else "(no stderr)",
        )
        return False
    logger.info("[IMP:9][org-secrets] secret %s configured for %s (visibility=%s)", name, org, visibility)
    return True


# endregion FUNC__set_one_secret


# region FUNC_ensure_context_secrets
## @purpose  Основной вход: gh secret set для каждого разрешённого секрета по visibility-плану.
## @io       ⇥ org: str, context: str, env: Mapping | None, run_fn: Callable | None,
##              dry_run: bool, node_configs_dir: Path | None → ⎋ bool
## @complexity O(S) — S секретов × 1 gh-вызов
## @invariants
##   - 0 значений → IMP:8 (нечего), True
##   - gh-сбой → IMP:10 WARN + return False (caller решает); значения НЕ логируются
##   - dry_run: план выводится, gh не вызывается, True
def ensure_context_secrets(
    org: str,
    context: str,
    *,
    env: Mapping[str, str] | None = None,
    run_fn: Callable[[list[str], str], int] | None = None,
    dry_run: bool = False,
    node_configs_dir: Path | None = None,
) -> bool:
    """Ensure context org secrets exist for mirror CI (best-effort, non-fatal)."""
    env_map = os.environ if env is None else env
    node = resolve_node_for_context(context, node_configs_dir=node_configs_dir)
    values = resolve_secret_values(context, env=env_map, node=node)

    if not values:
        logger.info("[IMP:8][org-secrets] no secret values resolved for %s — nothing to configure", org)
        return True

    missing = [n for n in _ORG_SECRET_PLAN if n not in values]
    if missing:
        logger.info(
            "[IMP:7][org-secrets] %s: not configuring missing sources: %s",
            org,
            ", ".join(missing),
        )

    ok = True
    for name in sorted(values):
        visibility, repos = _ORG_SECRET_PLAN[name]
        if not _set_one_secret(name, values[name], org, visibility, repos, run_fn, dry_run):
            ok = False

    return ok


# endregion FUNC_ensure_context_secrets
