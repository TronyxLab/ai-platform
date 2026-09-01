# GREP_SUMMARY: verify-sweep, collect-endpoints, node-context, node-yaml, node-host-map, nginx-server-names, ssh-collect, vhost-conf, r4-fail-not-skip, devplan-153
# STRUCTURE: ▶ collect_endpoints ┌node, mode┐ → ◇ NodeContext (node.yaml + host + remote_conf_dir) → ◇ _collect_local (node.yaml projects + overlays/nginx server_names) | ◇ _collect_remote (ssh cat conf.d) → ⊕ _dedup_endpoints (by fqdn) → ⎋ list[Endpoint]
# region MODULE_CONTRACT
## @purpose  Коллекция endpoints ноды для sweep-верификации (DevPlan 136 T5.1): local —
##           node.yaml projects (domain) + рендеренные vhost .conf server_names; remote —
##           SSH чтение nginx conf.d. Host резолвится NODE_HOST_MAP env → node.yaml#node.host.
## @scope    Вся логика сбора + pure-парсер nginx server_name + резолв путей/хоста.
##           Проверки (HTTP/TLS) — в http_check.py / tls_check.py.
## @invariants
##   - node.yaml резолвится ТОЛЬКО через NodeYaml.resolve (3-path канон) и парсится
##     ТОЛЬКО NodeYaml.get_project_entries (инвариант 13 root AGENTS.md)
##   - R4: ssh-недоступен (remote) → EndpointCollectionError(exit_code=1) — FAIL, не skip;
##     node.yaml не найден / node.host пуст → exit_code=2 (config)
##   - Dedup по fqdn: первый источник выигрывает (node.yaml проект важнее vhost-conf)
##   - Пустой вывод ssh (нет conf.d файлов) → 0 endpoints (голый test-node, НЕ ошибка)
## @rationale Декомпозиция монолита verify_sweep.py (план 170 W7-E1, research-A §7):
##            collect_endpoints 89 LOC/CC10 → NodeContext dataclass + _dedup_endpoints +
##            _collect_local/_collect_remote. Сигнатура collect_endpoints сохранена 1:1
##            (тесты вызывают её с теми же kwargs — изменены только импорты).
## @changes  2026-08-15 | План 170 W7-E1 — выделено из verify_sweep.py (чистый move + NodeContext)
## @usecases
##   - main() (__init__.py): collect_endpoints(node, mode, ...) → sweep checks
##   - tests/unit/test_verify_sweep.py: T1-T3 (local/remote/R4-ssh-fail), T18 (_default_remote_nginx_conf_dir)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.node_yaml.projects import ProjectEntry
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT

# Единый SSH-раннер — канон vps_readiness.
# Сигнатура (host, user, cmd, timeout, ssh_lib_path=None) -> tuple[int, str] идентична;
# timeout-семантика (Python-level = bash timeout + 5s) сохранена без изменений.
# ⚠️ TRAP[DECISION] · 2026-08-05 · — · SSH-раннер дедуплицирован (DevPlan 139 W3 T4)
# · Rejected: держать verbatim-копию default_ssh_runner в verify_sweep (дрейф: 2 копии,
# ·   timeout-семантика может разойтись; AC W3b: rg def default_ssh_runner → 1 def)
# · Reason: канон — core.internal.shared.vps_readiness.default_ssh_runner (DevPlan 105);
# ·   сигнатура (host, user, cmd, timeout, ssh_lib_path=None) идентична, Python-level
# ·   timeout = bash timeout + 5s сохранён (сверено до импорта). DI-точка ssh_runner
# ·   в _collect_remote не менялась — name разрешается в импортированный канон.
# · Rev: если потребуется иная timeout-семантика для e2e-verify — параметризовать канон,
# ·   не копировать.
from core.internal.shared.vps_readiness import default_ssh_runner
from core.internal.verify_sweep.models import Endpoint, EndpointCollectionError

logger = logging.getLogger(__name__)

# v1.0.1 TRAP[BUG] (Фаза 6): ci-deploy — forced-command канал receive-глаголов ПРОЕКТОВ;
# `cat conf.d` не входит в CANONICAL_VERBS → «unknown verb» rc=4 → e2e-verify коллекция
# падала на реальной ноде. e2e-verify — операторская диагностика → root (операторский
# SSH-ключ), как check-security/converge.
DEFAULT_SSH_USER: str = "root"
"""## @invariant SSH-пользователь remote-collect (паритет vps_readiness.SSH_USER)."""

REMOTE_NGINX_CONF_DIR: str = "/etc/nginx/conf.d/overlay"
"""## @invariant Remote-директория nginx vhost conf.d (include /etc/nginx/conf.d/overlay/*.conf, nginx_harness.py:123).

DevPlan 153 T5 (N2): путь ВНУТРИ контейнера nginx — НЕ существует на хосте. Канонический
remote-путь на хосте: /opt/node-configs/{node}/overlays/nginx (см. _default_remote_nginx_conf_dir).
Константа сохранена как последний fallback для обратной совместимости явных вызовов.
"""

_NODE_HOST_MAP_ENV = "NODE_HOST_MAP"

_SERVER_NAME_RE = re.compile(r"\bserver_name\s+(.+?)\s*;", re.MULTILINE)

# Тип ssh_runner DI: (host, user, cmd, timeout) -> (rc, stdout) — паттерн vps_readiness.
SshRunner = Callable[[str, str, str, int], tuple[int, str]]


@dataclass
class NodeContext:
    """Резолвнутый контекст ноды для коллекции endpoints (план 170 W7-E1).

    ## @purpose — Один проход резолва node.yaml/host/remote_conf_dir, переиспользуемый
    ##            _collect_local и _collect_remote (без повторного YAML-парсинга).
    ## @io — ⇥ node + DI-параметры collect_endpoints → ⎋ NodeContext (готовые поля)
    ## @invariants
    ##   - node_yaml_path — абсолютный путь (NodeYaml.resolve, 3-path канон)
    ##   - host — резолвнут (NODE_HOST_MAP env → node.yaml#node.host); пусто → exit 2 решает вызывающий
    ##   - remote_conf_dir — дефолт из имени ноды (DevPlan 153 T5) либо явный CLI-оверрайд
    """

    node: str
    host: str
    node_yaml_path: str
    node_configs_dir: str | None = None
    ssh_runner: SshRunner | None = None
    remote_conf_dir: str | None = None
    ssh_user: str = DEFAULT_SSH_USER
    platform_root: str | None = None
    _remote_conf_dir_resolved: str | None = field(default=None, repr=False)


# region FUNC_default_remote_nginx_conf_dir
def default_remote_nginx_conf_dir(node: str) -> str:
    """Резолв канонического пути nginx overlay на хосте из имени ноды (DevPlan 153 T5).

    ▶ ┌node┐ → node_configs_remote() → ⊕ f"/{node}/overlays/nginx" → ⎋ str

    ## @purpose — Устраняет дефолт-путь внутри контейнера nginx (/etc/nginx/conf.d/overlay):
    ##            remote-коллекция читает vhost-конфиги через ssh с ХОСТА, где канонический
    ##            путь — <NODE_CONFIGS_REMOTE_BASE>/<node>/overlays/nginx.
    ##            Публичное имя (без _): __init__.py re-export'ит его как приватный алиас
    ##            _default_remote_nginx_conf_dir — тест-контракт tests/unit/test_verify_sweep.py:628
    ##            (private-imports гейт U-07: from X import name as _alias — публичная сущность,
    ##            приватный только алиас — легально).
    ## @io — ⇥ node: str → ⎋ str (host path)
    ## @complexity — O(1)
    ## @invariants — Не raise; node_configs_remote() резолвит env → /opt/node-configs
    """
    from core.internal.shared.deploy_paths import node_configs_remote

    return f"{node_configs_remote()}/{node}/overlays/nginx"


# endregion FUNC_default_remote_nginx_conf_dir


# region FUNC_parse_nginx_server_names
def parse_nginx_server_names(conf_text: str) -> list[str]:
    """Извлечь server_name FQDN из HTTPS (443 ssl) server-блоков nginx conf.

    ▶ ┌conf_text┐ → ○ split server-блоки → ◇ listen 443 ssl? → ○ server_name (…;) → ⊕ split +
    strip ';' → ○ lowercase + dedup → ⎋ list[str]

    ## @purpose — Pure-парсер server_name директив nginx (vhost .conf и remote conf.d cat).
    ##            Используется локальной (overlays/nginx/*.conf) и remote (ssh cat) коллекцией.
    ## @io — ⇥ conf_text: str — содержимое одного/нескольких nginx conf файлов
    ##       → ⎋ list[str] — lowercase уникальные FQDN из 443-блоков (пустой при отсутствии)
    ## @complexity — O(L) где L = строки конфига
    ## @invariants
    ##   - ТОЛЬКО server-блоки с `listen 443` (HTTPS) — порт-80-only vhost (redirect-заглушки,
    ##     apex без проекта) НЕ endpoint: sweep проверяет https:// (релиз 1.0.0: asiteam.ru
    ##     apex 444-stealth давал вечный FAIL)
    ##   - server_name может содержать несколько имён через пробел — все извлекаются
    ##   - Терминальная ';' обрезается; регистр нормализуется в lowercase
    ##   - Пустые значения / подчёркнутые виртуальные имена (_) игнорируются
    ##   - Duplicate FQDN → уникализируются (set); невалидный текст → [] (graceful)
    ##   - server_name_in_redirect НЕ матчится (\b-якорь, R5-negative тест T5)
    """
    names: list[str] = []
    # 022-launch-validation F-12: comment-строки вырезаются ДО парсинга — иначе
    # doc-строки вида «##   - Точный server_name login.asiteam.ru (overlay-правило
    # платформы; НЕ default_server)» матчатся _SERVER_NAME_RE и порождают мусорные
    # fqdn-токены («(overlay-правило», «платформы») → ложные FAIL в e2e-verify.
    conf_text = "\n".join(line for line in conf_text.splitlines() if not line.lstrip().startswith("#"))
    # Сплит по server-блокам: блок = от «server {» до парного «}» (нестрогий рекурсивный
    # сплит не нужен — nginx-конфиги однострочные директивы, вложенных фигурных скобок
    # в server-блоках нет).
    blocks = re.split(r"(?=\bserver\s*\{)", conf_text)
    for block in blocks:
        if not re.search(r"listen\s+(\[[^\]]+\]\s*:\s*)?443\b", block):
            continue
        for match in _SERVER_NAME_RE.finditer(block):
            raw = match.group(1).strip()
            for raw_token in raw.split():
                token = raw_token.strip().rstrip(";")
                if not token or token == "_":
                    continue
                token = token.lower()
                if token not in names:
                    names.append(token)
    logger.info("[IMP:9][parse_nginx_server_names] Parsed %d server_name(s)", len(names))
    return names


# endregion FUNC_parse_nginx_server_names


# region FUNC_collect_endpoints
def collect_endpoints(
    node: str,
    *,
    mode: str = "remote",
    node_configs_dir: str | None = None,
    platform_root: str | None = None,
    ssh_runner: Callable[[str, str, str, int], tuple[int, str]] | None = None,
    remote_conf_dir: str | None = None,
    ssh_user: str = DEFAULT_SSH_USER,
) -> list[Endpoint]:
    """Собрать endpoints ноды: local (node.yaml + overlays/nginx) или remote (ssh nginx conf.d).

    ▶ ┌node, mode┐ → ◇ NodeContext (node.yaml 3-path, host, remote_conf_dir)
      → ◇ mode=local? _collect_local (node.yaml projects + overlays/nginx/*.conf)
      → ◇ mode=remote? _collect_remote (ssh cat conf.d → parse server_names)
      → ⊕ _dedup_endpoints (by fqdn, первый источник выигрывает) → ⎋ list[Endpoint]

    ## @purpose — Источники endpoints по дизайну (DevPlan 136 §2.2): local — node.yaml
    ##            projects + server_names из рендеренных vhost .conf; remote — SSH чтение
    ##            nginx conf.d (conf-парсинг, R4: ssh-недоступен → EndpointCollectionError
    ##            exit 1, не skip). host (IP ноды) резолвится: NODE_HOST_MAP env → node.host.
    ## @io — ⇥ node: str; mode: 'local'|'remote'; node_configs_dir: str | None;
    ##         platform_root: str | None; ssh_runner DI; remote_conf_dir: str; ssh_user: str
    ##       → ⎋ list[Endpoint] (dedup by fqdn; пустой на голой ноде)
    ## @complexity — O(P + F) где P = проекты node.yaml, F = conf-файлы (+ SSH round-trip в remote)
    ## @raises — EndpointCollectionError: node.yaml не найден (exit 2), node.host пуст (exit 2),
    ##           SSH недоступен (remote, exit 1 — R4 FAIL)
    ## @invariants
    ##   - node.yaml резолвится через NodeYaml.resolve (3-path канон)
    ##   - Проекты парсятся ТОЛЬКО NodeYaml.get_project_entries (не yaml.safe_load)
    ##   - Endpoint.host = node.host; NODE_HOST_MAP (JSON env) имеет приоритет (паритет vps_readiness)
    ##   - Local: vhost .conf server_names добавляются как дополнительные endpoints (дрейф-детект)
    ##   - Remote: `cat {remote_conf_dir}/*.conf` через ssh_runner (пустой вывод = 0 endpoints)
    ##   - Dedup по fqdn: первый источник выигрывает (node.yaml проект важнее vhost-conf)
    """
    logger.info("[IMP:7][collect_endpoints] node=%s mode=%s", node, mode)

    # ── Step 0: resolve remote nginx conf dir default from node name (DevPlan 153 T5) ──
    # Явный --nginx-conf-dir (CLI) передаётся как remote_conf_dir и имеет приоритет.
    resolved_remote_conf_dir = remote_conf_dir
    if mode == "remote" and resolved_remote_conf_dir is None:
        resolved_remote_conf_dir = default_remote_nginx_conf_dir(node)
        logger.info(
            "[IMP:8][collect_endpoints] Default remote_conf_dir resolved from node: %s", resolved_remote_conf_dir
        )

    # ── Step 1+2: NodeContext (node.yaml 3-path → node.host) ─────────
    ctx = _build_node_context(
        node,
        node_configs_dir=node_configs_dir,
        platform_root=platform_root,
        ssh_runner=ssh_runner,
        remote_conf_dir=resolved_remote_conf_dir,
        ssh_user=ssh_user,
    )
    if not ctx.host:
        msg = f"node.host not resolvable for node {node!r} (NODE_HOST_MAP env or node.yaml#node.host)"
        raise EndpointCollectionError(
            msg,
            exit_code=2,
        )
    logger.info("[IMP:8][collect_endpoints] Resolved host=%s for node=%s", ctx.host, node)

    if mode == "local":
        endpoints = _collect_local(ctx)
    elif mode == "remote":
        endpoints = _collect_remote(ctx)
    else:
        msg = f"Unknown mode {mode!r} — expected 'local' | 'remote'"
        raise EndpointCollectionError(msg, exit_code=2)

    # ── Step 3: dedup by fqdn (первый источник выигрывает) ──────────
    unique = _dedup_endpoints(endpoints)
    logger.info("[IMP:9][collect_endpoints] Collected %d unique endpoint(s) mode=%s", len(unique), mode)
    return unique


# endregion FUNC_collect_endpoints


# region FUNC__build_node_context
def _build_node_context(
    node: str,
    *,
    node_configs_dir: str | None,
    platform_root: str | None,
    ssh_runner: Callable[[str, str, str, int], tuple[int, str]] | None,
    remote_conf_dir: str | None,
    ssh_user: str,
) -> NodeContext:
    """Резолв node.yaml (3-path) + node.host — единый контекст коллекции.

    ▶ ┌node, DI┐ → ◇ _resolve_node_yaml_path (ConfigNotFoundError → exit 2) → ◇ _resolve_node_host → ⎋ NodeContext

    ## @purpose — Разделение резолва и коллекции (план 170 W7-E1): NodeContext готовится
    ##            один раз, _collect_local/_collect_remote получают готовые поля.
    ## @io — ⇥ node + DI-параметры → ⎋ NodeContext
    ## @raises — EndpointCollectionError(exit_code=2): node.yaml не найден
    ## @complexity — O(P + N) — 3-path probe + YAML parse
    ## @invariants — node_yaml_path через NodeYaml.resolve; host через _resolve_node_host
    """
    try:
        node_yaml_path = _resolve_node_yaml_path(node, platform_root)
    except ConfigNotFoundError as exc:
        raise EndpointCollectionError(str(exc), exit_code=2) from exc

    host = _resolve_node_host(node, node_yaml_path)
    return NodeContext(
        node=node,
        host=host,
        node_yaml_path=node_yaml_path,
        node_configs_dir=node_configs_dir,
        ssh_runner=ssh_runner,
        remote_conf_dir=remote_conf_dir,
        ssh_user=ssh_user,
        platform_root=platform_root,
    )


# endregion FUNC__build_node_context


# region FUNC__resolve_node_yaml_path
def _resolve_node_yaml_path(node: str, platform_root: str | None) -> str:
    """3-path резолв node.yaml через NodeYaml.resolve (канон).

    ▶ ┌node, platform_root┐ → NodeYaml.resolve (env PLATFORM_ROOT/HOME) → ⎋ path
    ## @purpose — Единая точка резолва node.yaml (делегирует NodeYaml.resolve, инвариант
    ##            «единая точка чтения node.yaml»). platform_root → config_dir (hermetic DI).
    ## @io — ⇥ node: str; platform_root: str | None → ⎋ str (абсолютный путь)
    ## @raises — ConfigNotFoundError (не найдён ни в одном из 3 путей)
    ## @complexity — O(P + N) — 3-path probe + YAML parse
    """
    resolved = NodeYaml.resolve(node_name=node, config_dir=platform_root)
    path = str(resolved._path)
    logger.info("[IMP:9][_resolve_node_yaml_path] Resolved node.yaml: %s", path)
    return path


# endregion FUNC__resolve_node_yaml_path


# region FUNC__resolve_node_host
def _resolve_node_host(node: str, node_yaml_path: str) -> str:
    """Резолв IP ноды: NODE_HOST_MAP (JSON env) приоритетнее node.yaml#node.host.

    ▶ ┌node, node_yaml_path┐ → ◇ NODE_HOST_MAP JSON → host | ◇ NodeYaml.get(node.host) → ⎋ str

    ## @purpose — Единый резолв host для checks и SSH (паритет vps_readiness._resolve_node_host:
    ##            NODE_HOST_MAP env — JSON node→host; fallback — node.yaml#node.host).
    ## @io — ⇥ node: str; node_yaml_path: str → ⎋ str (host или '')
    ## @complexity — O(1) + YAML parse
    ## @invariants
    ##   - NODE_HOST_MAP валиден JSON + node в нём → env выигрывает
    ##   - Иначе → NodeYaml.get("node.host", default="")
    ##   - Пусто в обоих → '' (вызывающий решает exit 2)
    """
    raw_map = os.environ.get(_NODE_HOST_MAP_ENV)
    if raw_map:
        try:
            node_host_map = cast(dict[str, str], json.loads(raw_map))
            if isinstance(node_host_map, dict) and node_host_map.get(node):
                host = str(node_host_map[node])
                logger.info("[IMP:9][_resolve_node_host] NODE_HOST_MAP: %s → %s", node, host)
                return host
        except json.JSONDecodeError:
            logger.warning("[IMP:7][_resolve_node_host] NODE_HOST_MAP is not valid JSON — falling back to node.yaml")

    try:
        ny = NodeYaml(node_yaml_path)
        host = str(ny.get("node.host", default="") or "")
    except (ConfigNotFoundError, ConfigParseError) as exc:
        logger.warning("[IMP:7][_resolve_node_host] node.yaml unreadable: %s", exc)
        return ""
    if host:
        logger.info("[IMP:9][_resolve_node_host] node.yaml#node.host: %s", host)
    return host


# endregion FUNC__resolve_node_host


# region FUNC__endpoint_expose_enabled
## @purpose  Expose-фильтр endpoint'а (plan 012 T15 / F-034): e2e-verify ожидает ответ ТОЛЬКО
##           от exposed-доменов node.yaml. Проект с expose=false в ai-platform.yaml НЕ даёт
##           endpoint (иначе sweep проверял бы неэкспонированный домен → ложный FAIL/TLS-шум).
##           Проект без локального ai-platform.yaml → True (домен node.yaml авторитетен).
## @io       ⇥ entry: ProjectEntry → ⎋ bool (True = endpoint включается)
## @complexity O(1) — resolve project dir + read ai-platform.yaml
## @invariants
##   - Тот же контракт, что vhost_renderer._project_expose_enabled (единый expose-смысл)
##   - project dir: `{projects_base}/{context}/{name}` (fallback name-only)
##   - ai-platform.yaml отсутствует → True (не блокировать развёрнутые домены)
def _endpoint_expose_enabled(entry: ProjectEntry) -> bool:
    """Return True if project ai-platform.yaml has expose:true (or config absent — keep)."""
    from core.internal.shared import project_yaml as shared_project_yaml
    from core.internal.shared.deploy_paths import projects_base

    base = projects_base()
    project_dir = base / entry.context / entry.name if entry.context else base / entry.name
    data = shared_project_yaml.load_project_yaml(project_dir)
    if not data:
        logger.warning(
            "[IMP:7][_endpoint_expose_enabled] ai-platform.yaml not found for %s (resolved %s) — "
            "keep endpoint (node.yaml domain authoritative)",
            entry.name,
            project_dir,
        )
        return True
    return bool(shared_project_yaml.get_expose(data))


# endregion FUNC__endpoint_expose_enabled


# region FUNC__collect_local
def _collect_local(ctx: NodeContext) -> list[Endpoint]:
    """Локальная коллекция: node.yaml projects (domain) + overlays/nginx server_names.

    ▶ ┌ctx┐ → ○ NodeYaml.get_project_entries → ⊕ projects-with-domain
      → ○ glob overlays/nginx/*.conf → ⊕ parse_nginx_server_names → ⎋ list[Endpoint]

    ## @purpose — Источник №1 (node.yaml projects с domain) + источник №2 (рендеренные
    ##            vhost .conf server_names — дрейф-детект задеплоенных доменов).
    ## @io — ⇥ ctx: NodeContext → ⎋ list[Endpoint]
    ## @complexity — O(P + F) где P = проекты, F = conf-файлы
    ## @invariants
    ##   - Проект без domain → пропускается (не endpoint — не HTTP-рутируется)
    ##   - overlays_dir = <node_configs_dir>/<node>/overlays/nginx (если node_configs_dir задан);
    ##     отсутствие директории → 0 дополнительных endpoints (не ошибка)
    ##   - Парсинг conf-файлов — parse_nginx_server_names (pure)
    ##   - source: 'node-yaml' | 'vhost-conf' (для дрейф-аудита)
    """
    endpoints: list[Endpoint] = []

    try:
        ny = NodeYaml(ctx.node_yaml_path)
        entries: list[ProjectEntry] = ny.get_project_entries()
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        logger.warning("[IMP:7][_collect_local] node.yaml projects unreadable: %s", exc)
        entries = []

    for entry in entries:
        fqdn = entry.domain.strip().lower()
        if not fqdn:
            logger.info("[IMP:7][_collect_local] Project %s has no domain — skip", entry.name)
            continue
        # plan 012 T15 (F-034): e2e-verify ожидает ответ только от EXPOSED-доменов.
        # node.yaml#projects несёт домены и expose=false проектов; без сверки с
        # ai-platform.yaml sweep проверял бы неэкспонированные домены → ложные FAIL/TLS-шумы.
        # Проект без локального ai-platform.yaml → консервативно включаем (домен node.yaml
        # авторитетен для развёрнутых проектов; remote-mode читает conf.d — фильтр только local).
        if not _endpoint_expose_enabled(entry):
            logger.info(
                "[IMP:8][_collect_local] Project %s expose=false — endpoint skipped (F-034)",
                entry.name,
            )
            continue
        endpoints.append(Endpoint(name=entry.name, fqdn=fqdn, host=ctx.host, source="node-yaml"))
    logger.info("[IMP:8][_collect_local] %d endpoint(s) from node.yaml projects", len(endpoints))

    if ctx.node_configs_dir:
        overlays_dir = Path(ctx.node_configs_dir) / ctx.node / "overlays" / "nginx"
        if overlays_dir.is_dir():
            for conf_file in sorted(overlays_dir.glob("*.conf")):
                try:
                    conf_text = conf_file.read_text(encoding="utf-8")
                except OSError as exc:
                    logger.warning("[IMP:7][_collect_local] Unreadable vhost conf %s: %s", conf_file, exc)
                    continue
                endpoints.extend(
                    Endpoint(name=conf_file.stem, fqdn=fqdn, host=ctx.host, source="vhost-conf")
                    for fqdn in parse_nginx_server_names(conf_text)
                )
            logger.info("[IMP:8][_collect_local] Scanned overlays/nginx: %s", overlays_dir)
        else:
            logger.info("[IMP:7][_collect_local] No overlay dir %s — vhost-conf source empty", overlays_dir)

    logger.info("[IMP:9][_collect_local] %d local endpoint(s)", len(endpoints))
    return endpoints


# endregion FUNC__collect_local


# region FUNC__collect_remote
def _collect_remote(ctx: NodeContext) -> list[Endpoint]:
    """Remote-коллекция: SSH чтение nginx conf.d → parse server_names (conf-парсинг).

    ▶ ┌ctx┐ → ⚡ ssh cat {remote_conf_dir}/*.conf → ◇ rc!=0 → EndpointCollectionError(exit 1, R4)
      → ○ parse_nginx_server_names → ⊕ Endpoint(name=file, fqdn, host, source='remote-nginx') → ⎋ list

    ## @purpose — Источник remote (DevPlan 136 T5.1: «remote — через SSH чтение nginx conf.d»):
    ##            фактические задеплоенные server_names на ноде (истинное состояние).
    ##            R4: ssh-недоступен → EndpointCollectionError(exit_code=1) — FAIL, не skip.
    ## @io — ⇥ ctx: NodeContext → ⎋ list[Endpoint]
    ## @complexity — O(1) SSH round-trip + O(L) парсинг
    ## @raises — EndpointCollectionError(exit_code=1): ssh rc != 0 / timeout / bash missing
    ## @invariants
    ##   - Команда: `cat {remote_conf_dir}/*.conf` (shell glob на remote-стороне)
    ##   - Пустой stdout (нет conf.d файлов) → 0 endpoints (голый test-node, НЕ ошибка)
    ##   - ssh_runner DI (host, user, cmd, timeout) -> (rc, stdout) — паттерн vps_readiness;
    ##     None → default_ssh_runner (канон)
    ##   - Таймаут SSH: SSH_CONNECT_TIMEOUT (shared/timeouts канон)
    """
    if ctx.remote_conf_dir is None:
        msg = "remote_conf_dir not resolved for remote collect"
        raise EndpointCollectionError(msg, exit_code=2)

    ssh_runner: SshRunner = ctx.ssh_runner or default_ssh_runner
    cmd = f"cat {ctx.remote_conf_dir}/*.conf 2>/dev/null"
    logger.info("[IMP:7][_collect_remote] SSH %s@%s: %s", ctx.ssh_user, ctx.host, cmd)
    rc, stdout = ssh_runner(ctx.host, ctx.ssh_user, cmd, SSH_CONNECT_TIMEOUT)
    if rc != 0:
        msg = f"SSH unavailable for remote collect: {ctx.ssh_user}@{ctx.host} rc={rc} (R4: FAIL, not skip)"
        raise EndpointCollectionError(
            msg,
            exit_code=1,
        )

    names = parse_nginx_server_names(stdout)
    endpoints = [
        Endpoint(name=f"remote-nginx-{i}", fqdn=fqdn, host=ctx.host, source="remote-nginx")
        for i, fqdn in enumerate(names)
    ]
    logger.info("[IMP:9][_collect_remote] %d remote endpoint(s) via SSH conf.d read", len(endpoints))
    return endpoints


# endregion FUNC__collect_remote


# region FUNC__dedup_endpoints
def _dedup_endpoints(endpoints: list[Endpoint]) -> list[Endpoint]:
    """Дедупликация endpoints по fqdn — первый источник выигрывает.

    ▶ ┌endpoints┐ → ○ seen set → ◇ fqdn уже был? → skip | ⊕ keep → ⎋ list[Endpoint]

    ## @purpose — Единая точка дедупа (план 170 W7-E1): node.yaml проект важнее vhost-conf,
    ##            vhost-conf важнее remote-nginx (порядок источников в списке коллекции).
    ## @io — ⇥ endpoints: list[Endpoint] → ⎋ list[Endpoint] (уникальные по fqdn)
    ## @complexity — O(E) где E = endpoints
    ## @invariants — Первое вхождение fqdn сохраняется; порядок остальных не меняется
    """
    seen: set[str] = set()
    unique: list[Endpoint] = []
    for ep in endpoints:
        if ep.fqdn in seen:
            logger.info("[IMP:7][_dedup_endpoints] Dedup fqdn=%s (keep first source=%s)", ep.fqdn, ep.source)
            continue
        seen.add(ep.fqdn)
        unique.append(ep)
    return unique


# endregion FUNC__dedup_endpoints
