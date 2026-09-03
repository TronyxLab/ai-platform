#!/usr/bin/env python3
# GREP_SUMMARY: phases-final-verify, final-verify, end-state, exit-10, deploy-integrity, devplan-029, T5, honest-exit0, postcondition, cert-on-disk, secrets-env, vhost-rendered, ghcr-token
# STRUCTURE: ▶ phase_final_verify (φ-final-verify, init-only, после φ8.5 converge) → ◇ 4 end-state assertions ┌certs on disk┐ ┌secrets.env full┐ ┌exposed vhost rendered┐ ┌GHCR token≠skip┐ → ◇ fail → ⚡PlatformFatalError (exit 10) → ⎋ True
# region MODULE_CONTRACT
## @purpose  φ-final-verify (DevPlan 029 T5) — финальная фаза INIT-бутстрапа ПОСЛЕ φ8.5
##           converge_services: 4 end-state assertion'а дают ЧЕСТНЫЙ exit 0 (постмортем-класс
##           «успех = заявление, а не проверка»): серты exposed-доменов на диске, secrets.env
##           полный (re-run verify_required_sops_secrets), exposed-проекты с отрендеренным
##           vhost, GHCR-токен ≠ skip (когда нода тянет приватные GHCR-образы). FAIL → raise
##           PlatformFatalError (exit 10) — bootstrap НЕ репортит READY при нарушенном
##           end-state. Идемпотентна: done-статус фазы → повтор = no-op (checkpoint/resume).
## @scope    Фаза INIT-mode (НЕ update): добавляется в INIT_PHASE_ORDER после converge_services;
##           dispatch PHASE_DISPATCH[final_verify]; dependency converge_services → final_verify.
## @invariants
##   - 4 assertion-а: (a) серты всех доменов node.yaml на диске (ssl_certs_converged_on_disk);
##     (b) secrets.env полный — verify_required_sops_secrets (enc-файл-gated, тот же канон φ4);
##     (c) каждый project с доменом имеет отрендеренный vhost-conf (overlay nginx dir, как R6);
##     (d) GHCR_PULL_TOKEN присутствует, если нода включает hermes-agent ИЛИ ≥1 project
##         (GHCR-private потребители); infra-only без токена → WARN (не блок)
##   - Любой FAIL assertion → PlatformFatalError (exit 10) с человекочитаемой причиной
##   - Undeterminable (None экстрактор сертов) → FAIL (fail-closed, T3-семантика)
##   - env DI: SECRETS_ENV_FILE/NODE_CONFIGS_DIR/NODE_NAME/GHCR_PULL_TOKEN (как φ4/φ9)
##   - Фаза НЕ мутирует: только проверки на диске/env
## @rationale D3 (владелец): фаза, не healthcheck-расширение — даёт checkpoint/идемпотентность
##            и exit 10 через существующую state machine без ре-ордеринга φ8 (readiness-гонки
##            старта остаются P2 — start_period). P0-честность exit 0.
## @changes  2026-09-02 · DevPlan 029 T5 — created
## @changes  2026-09-03 · DevPlan 030 — assertion (b) module-aware: _assert_secrets_env резолвит
##           enabled_modules (resolve_enabled_modules, тот же канон φ4) и передаёт в
##           verify_required_sops_secrets — minimal-контекст (asi-group без postgres/minio/
##           telegram) больше не фейлит на required∧sops чужих модулей
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from core.internal.shared.deploy_paths import node_configs_remote, secrets_env_file
from core.internal.shared.exceptions import PlatformError, PlatformFatalError

logger = logging.getLogger(__name__)


# region FUNC__projects_with_domains
## @purpose  Список проектов node.yaml с доменом (expected-vhost множество, консистентно с R6).
## @io       ⇥ node_yaml: str → ⎋ list[dict[str, object]] (проекты с name+domain)
## @complexity O(P) — P = число проектов (делегирование converge.projects.parse_projects_yaml)
def _projects_with_domains(node_yaml: str) -> list[dict[str, object]]:
    """Projects carrying a domain — the expected rendered-vhost set (R6-consistent).

    Локальный парсинг через NodeYaml (0 импорт converge/ — cross-layer/acyclic чист).
    """
    from core.internal.shared.node_yaml import NodeYaml  # lazy: import-light фаза

    try:
        raw: object = NodeYaml(node_yaml).get("projects", default=[])
    except (PlatformError, OSError) as exc:
        logger.error("[IMP:10][final_verify] Cannot parse projects from %s: %s", node_yaml, exc)
        msg = f"final_verify: cannot parse projects from node.yaml: {exc}"
        raise PlatformFatalError(msg) from exc
    projects: list[dict[str, object]] = []
    raw_items = cast("list[object]", raw) if isinstance(raw, list) else []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        if entry.get("name") and entry.get("domain"):
            projects.append(entry)
    return projects


# endregion FUNC__projects_with_domains


# region FUNC__assert_certs_on_disk
## @purpose  Assertion (a): серты всех доменов node.yaml на диске (on-disk convergence check).
##           None (экстрактор недоступен) → fail-closed (undeterminable ≠ converged, T3).
## @io       ⇥ core_dir, node_yaml → ⎋ None ⚡ PlatformFatalError
## @complexity O(D) — D = домены (делегирование domains.ssl_certs_converged_on_disk)
def _assert_certs_on_disk(core_dir: str, node_yaml: str) -> None:
    """(a) Every node.yaml domain has fullchain.pem on disk — else exit-10 fail."""
    from core.internal.bootstrap.lifecycle.helpers import domains  # lazy: import-light фаза

    converged = domains.ssl_certs_converged_on_disk(core_dir, node_yaml)
    if converged is True:
        logger.info("[IMP:9][final_verify] (a) certs OK — all domains have certs on disk")
        return
    if converged is False:
        msg = "final_verify FAIL (a): one or more node.yaml domains have NO certificate on disk"
        logger.error("[IMP:10][final_verify] %s", msg)
        raise PlatformFatalError(msg)
    msg = (
        "final_verify FAIL (a): cert convergence UNDETERMINABLE (extractor unavailable) — "
        "cannot certify end-state (fail-closed)"
    )
    logger.error("[IMP:10][final_verify] %s", msg)
    raise PlatformFatalError(msg)


# endregion FUNC__assert_certs_on_disk


# region FUNC__assert_secrets_env
## @purpose  Assertion (b): secrets.env полный — re-run verify_required_sops_secrets (тот же
##           postcondition канон, что у φ4 REF-0013/DATA-1006). Enc-file-gated: нет enc-файла →
##           autogen-политика из node.yaml (allow_autogen; None → легаси-skip).
## @io       ⇥ core_dir, node_name, node_yaml, env → ⎋ None ⚡ PlatformFatalError
## @complexity O(N) — N = манифестные required∧sops секреты (делегирование)
def _assert_secrets_env(core_dir: str, node_name: str, node_yaml: str, env: Mapping[str, str]) -> None:
    """(b) required∧sops secrets present after bootstrap — else exit-10 fail."""
    from core.internal.bootstrap.lifecycle.helpers import secrets as secrets_helpers  # lazy

    source: Mapping[str, str] = os.environ if env is None else env
    secrets_env = source.get("SECRETS_ENV_FILE", str(secrets_env_file()))
    configs_dir = source.get("NODE_CONFIGS_DIR", str(node_configs_remote()))
    enc_file = str(Path(configs_dir) / "secrets" / f"{node_name}.enc.yaml")
    manifest_path = str(Path(core_dir) / "secrets-manifest.yaml")
    if not Path(manifest_path).is_file():
        # Манифест отсутствует (дегенеративное тест-окружение/голая core) — верифицировать
        # нечего; φ4-постусловие с enc-файлом уже отработало бы fail-fast на реальной ноде.
        logger.info("[IMP:8][final_verify] (b) secrets-manifest.yaml absent — skip (nothing to verify)")
        return

    # allow_autogen — тот же резолв, что φ4 (одна точка чтения: NodeYaml.get default False);
    # None при нечитаемом node.yaml = легаси-поведение verifier'а (skip без fail-loud).
    allow_autogen: bool | None
    try:
        from core.internal.shared.node_yaml import NodeYaml

        raw = NodeYaml(node_yaml).get("secrets.allow_autogen", default=False)
        allow_autogen = bool(raw) if isinstance(raw, bool) else str(raw).strip().lower() == "true"
    except PlatformError as exc:
        logger.warning("[IMP:7][final_verify] cannot resolve allow_autogen from %s: %s", node_yaml, exc)
        allow_autogen = None

    # F15/module-aware (DevPlan 030): minimal-контекст (asi-group: nginx/logging/status-page,
    # БЕЗ postgres/minio/telegram) не должен фейлить на required∧sops чужих модулей — тот же
    # module-aware резолв, что φ4 (resolve_enabled_modules → SKIP «no enabled consumer module»).
    from core.internal.shared.enabled_modules import resolve_enabled_modules

    enabled_modules = resolve_enabled_modules(node_name=node_name, env=source)

    try:
        secrets_helpers.verify_required_sops_secrets(
            manifest_path=manifest_path,
            secrets_env=secrets_env,
            enc_file=enc_file,
            enabled_modules=enabled_modules,
            allow_autogen=allow_autogen,
        )
    except PlatformError as exc:
        msg = f"final_verify FAIL (b): secrets.env postcondition violated: {exc}"
        logger.error("[IMP:10][final_verify] %s", msg)
        raise PlatformFatalError(msg) from exc
    logger.info("[IMP:9][final_verify] (b) secrets.env OK — required∧sops present")


# endregion FUNC__assert_secrets_env


# region FUNC__resolve_nginx_overlay_dir
## @purpose  Локальный резолв overlay-nginx директории (аналог converge R6 resolve) — БЕЗ импорта
##           converge/ (cross-layer/acyclic чистота): /opt/{ctx}/platform/modules/nginx →
##           /opt/{node}/overlays/nginx → /opt/node-configs/{node}/overlays/nginx.
## @io       ⇥ node_yaml, node_name → ⎋ str | None
## @complexity O(1) — isdir кандидатов
def _resolve_nginx_overlay_dir(node_yaml: str, node_name: str) -> str | None:
    """Resolve the nginx vhost overlay directory (context-first, node-configs fallback)."""
    from core.internal.shared.node_yaml import NodeYaml  # lazy

    try:
        ctx = NodeYaml(node_yaml).get_context()
    except (PlatformError, OSError):
        ctx = ""
    if ctx:
        candidate = f"/opt/{ctx}/platform/modules/nginx"
        if Path(candidate).is_dir():
            return candidate
    for cand in (f"/opt/{node_name}/overlays/nginx", f"/opt/node-configs/{node_name}/overlays/nginx"):
        if Path(cand).is_dir():
            return cand
    return None


# endregion FUNC__resolve_nginx_overlay_dir


# region FUNC__assert_exposed_vhosts_rendered
## @purpose  Assertion (c): каждый project с доменом имеет отрендеренный vhost-conf в overlay
##           nginx-директории (rendered-count == expected; отсутствие = дрейф, fail-loud).
## @io       ⇥ node_yaml, node_name → ⎋ None ⚡ PlatformFatalError
## @complexity O(P) — P = проекты с доменом (резолв overlay один раз + isfile per domain)
def _assert_exposed_vhosts_rendered(node_yaml: str, node_name: str) -> None:
    """(c) Rendered vhost conf exists for every domain-bearing project — else exit-10 fail."""
    projects = _projects_with_domains(node_yaml)
    if not projects:
        logger.info("[IMP:8][final_verify] (c) no domain-bearing projects — trivially satisfied")
        return
    overlay_dir = _resolve_nginx_overlay_dir(node_yaml, node_name)
    missing: list[str] = []
    for proj in projects:
        domain = str(proj.get("domain", ""))
        conf = str(Path(overlay_dir or "") / f"{domain}.conf")
        if not overlay_dir or not Path(conf).is_file():
            missing.append(domain)
    if missing:
        msg = (
            "final_verify FAIL (c): rendered vhost conf missing for domain(s): "
            f"{', '.join(missing)} (overlay nginx dir: {overlay_dir or 'UNRESOLVED'})"
        )
        logger.error("[IMP:10][final_verify] %s", msg)
        raise PlatformFatalError(msg)
    logger.info("[IMP:9][final_verify] (c) vhosts OK — %d rendered conf(s) present", len(projects))


# endregion FUNC__assert_exposed_vhosts_rendered


# region FUNC__node_requires_ghcr
## @purpose  True если нода тянет приватные GHCR-образы (hermes-agent enabled ИЛИ ≥1 project
##           с доменом); None — node.yaml нечитаем (undeterminable, не блокируем дважды).
## @io       ⇥ node_yaml: str → ⎋ bool | None
## @complexity O(M+P) — NodeYaml
def _node_requires_ghcr(node_yaml: str) -> bool | None:
    """Whether the node pulls private GHCR images (hermes-agent enabled and/or projects)."""
    try:
        from core.internal.shared.node_yaml import NodeYaml

        node = NodeYaml(node_yaml)
        raw_modules = node.get("modules", default=[])
    except (PlatformError, OSError) as exc:
        logger.warning("[IMP:7][final_verify] (d) cannot resolve GHCR need from %s: %s", node_yaml, exc)
        return None
    if isinstance(raw_modules, list):
        modules = cast("list[dict[str, object]]", raw_modules)
        if any(
            str(m.get("name", "")) == "hermes-agent" and bool(m.get("enabled")) for m in modules if isinstance(m, dict)
        ):
            return True
    return bool(_projects_with_domains(node_yaml))


# endregion FUNC__node_requires_ghcr


# region FUNC__assert_ghcr_not_skipped
## @purpose  Assertion (d): GHCR-токен ≠ skip для GHCR-private потребителей (hermes-agent модуль
##           ИЛИ ≥1 project). Infra-only нода без токена — WARN (docker hub rate-limit семантика
##           прекондишенов φ6, НЕ блок); цель — не дать «skip auth» молча стать успехом, когда
##           ноде нужны приватные GHCR-образы.
## @io       ⇥ env, node_yaml → ⎋ None ⚡ PlatformFatalError
## @complexity O(M+P) — enabled-модули + проекты (NodeYaml)
def _assert_ghcr_not_skipped(env: Mapping[str, str], node_yaml: str) -> None:
    """(d) GHCR_PULL_TOKEN present when the node pulls private GHCR images — else exit-10 fail."""
    source: Mapping[str, str] = os.environ if env is None else env
    token = (source.get("GHCR_PULL_TOKEN", "") or "").strip()
    if not token:
        # F12: token lives in secrets.env file, not necessarily os.environ at φf
        from core.internal.bootstrap.lifecycle.secrets_manager import source_secrets_env

        secrets_env_path = source.get("SECRETS_ENV_FILE", str(secrets_env_file()))
        token = (source_secrets_env(secrets_env_path).get("GHCR_PULL_TOKEN", "") or "").strip()

    needs_ghcr = _node_requires_ghcr(node_yaml)
    if needs_ghcr is None:
        logger.warning("[IMP:7][final_verify] (d) cannot resolve GHCR need from %s", node_yaml)
        return  # node.yaml нечитаем — (b)/(c) уже fail-loud; не блокируем дважды

    if not needs_ghcr:
        logger.info("[IMP:8][final_verify] (d) GHCR не требуется (infra-only без проектов) — skip OK")
        return
    if not token:
        msg = (
            "final_verify FAIL (d): GHCR_PULL_TOKEN missing, но нода тянет приватные GHCR-образы "
            "(hermes-agent и/или проекты) — registry-auth skip не должен быть тихим успехом"
        )
        logger.error("[IMP:10][final_verify] %s", msg)
        raise PlatformFatalError(msg)
    logger.info("[IMP:9][final_verify] (d) GHCR token present (≠ skip)")


# endregion FUNC__assert_ghcr_not_skipped


# region FUNC_phase_final_verify
## @purpose  φ-final-verify (init-only): 4 end-state assertion'а ПОСЛЕ converge_services —
##           честный exit 0 бутстрапа. Fail любого assertion → PlatformFatalError (exit 10).
## @io       ⇥ core_dir, node_name, node_yaml (канон-сигнатура фаз), env: Mapping | None (DI)
##           → ⎋ True ⚡ PlatformFatalError
## @complexity O(D + N + P) — серты + секреты + vhosts (лёгкие isfile/env проверки)
## @invariants
##   - Идемпотентна на уровне state machine (done → skip; повтор = no-op, AC8)
##   - Фаза только INIT-mode (state_machine INIT_PHASE_ORDER)
##   - НЕ мутирует (verify-only): серты/секреты/vhosts/токен — проверка, не лечение
def phase_final_verify(
    core_dir: str,
    node_name: str,
    node_yaml: str,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """φ-final-verify: end-state assertions (certs/secrets.env/vhosts/GHCR) after converge."""
    logger.info("[IMP:8][final_verify] START: final-verify — 4 end-state assertions (DevPlan 029 T5)")
    if not node_yaml or not Path(node_yaml).is_file():
        msg = "final_verify: node.yaml not found — cannot verify end-state"
        logger.error("[IMP:10][final_verify] %s", msg)
        raise PlatformFatalError(msg)

    _assert_certs_on_disk(core_dir, node_yaml)
    _assert_secrets_env(core_dir, node_name, node_yaml, env=env if env is not None else {})
    _assert_exposed_vhosts_rendered(node_yaml, node_name)
    _assert_ghcr_not_skipped(env=env if env is not None else {}, node_yaml=node_yaml)

    logger.info("[IMP:9][final_verify] φ-final-verify PASS — end-state verified (certs/secrets.env/vhosts/GHCR)")
    return True


# endregion FUNC_phase_final_verify
