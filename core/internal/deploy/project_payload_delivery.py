#!/usr/bin/env python3
# GREP_SUMMARY: project-payload-delivery, bootstrap, deliver, pending-projects, awaiting-deploy, no_local_source, context, operator-sources, ForcedCommandChannel, receive, exit-2, DevPlan-017
# STRUCTURE: ▶ CLI --node/--node-yaml → ∋ resolve context (env → node.yaml contexts[0].name) + host + projects_root → ○ per-project: ◇ <base>/<context>/<name> dir? → ✎ deliver (orchestrator_cli deliver, in-process, 0 subprocess) → ⊕ DeliverySummary → ⎋ exit 0|2
# region MODULE_CONTRACT
## @purpose  Локальная фаза bootstrap (P0, DevPlan 017): доставка payload'ов проектов контекста
##           на только что забутстрапленную ноду. Реальные исходники проектов лежат на
##           операторской машине в \\<projects_root\\>/\\<context\\>/\\<project\\>/ (docker-compose.yml +
##           sources); φ8 (context_deployer) на ноде создаёт GENERATED-STUB compose и помечает
##           каждый недоставленный проект status="awaiting_deploy" (DevPlan 153 T6 N1). Эта фаза
##           вызывается из bootstrap.sh ПОСЛЕ успешного SSH-exec remote lifecycle init и
##           доставляет payload'ы через ТОТ ЖЕ канал, что make deploy-project —
##           ForcedCommandChannel receive \\<project\\> \\<version\\> (orchestrator_cli deliver).
##           receive на ноде идемпотентен и сам поднимает compose (compose up + healthcheck) —
##           предпроверка «уже здоров» не нужна. Критерий владельца: голая нода + ОДНА команда
##           make bootstrap-node завершается при ЖИВЫХ проектах контекста.
## @scope    Вызывается из core/entrypoints/bootstrap.sh (тонкий фасад: только вызов + exit-код).
##           Модуль живёт в deploy-слое (core/internal/deploy/) — операторская delivery-логика,
##           import-linter independence-bootstrap-deploy (DevPlan 163): deploy → bootstrap запрещён,
##           поэтому резолв проектов контекста — на каноническом shared-парсере NodeYaml
##           get_project_entries() (single parser canon, DevPlan 116 B6), а не context_deployer.
##           Кросс-модульные зависимости: orchestrator_cli.main (соседний deploy-слой — легально),
##           shared/node_yaml (NodeYaml + ProjectEntry), node_resolver.extract_node_host,
##           shared/deploy_paths.projects_base.
## @invariants
##   - 0 subprocess: deliver вызывается нативно через orchestrator_cli.main (публичный API) —
##     приватный _handle_deliver НЕ импортируется (гейт private-imports, allowlist пуст)
##   - deploy → bootstrap НЕ импортируется (forbidden-deploy-bootstrap): резолв проектов — локальный
##     _resolve_context_projects на shared-парсере get_project_entries (семантика resolve_context_projects)
##   - Контекст резолвится тем же способом, что deploy_context(): env CONTEXT → node.yaml
##     contexts[0].name (NodeYaml.get_context); пустой контекст → ВСЕ проекты skipped(no_context)
##   - Локальный каталог \\<projects_root\\>/\\<context\\>/\\<name\\> отсутствует → skipped(no_local_source)
##     [IMP:7] — НЕ failure (оператор не хранит проект локально, CI доставит)
##   - Реальная ошибка доставки (deliver rc≠0 / raise) → failed; ≥1 failed → exit 2
##     (строгий INIT критерий: bootstrap не считается успешным при мёртвых проектах контекста)
##   - Exit-код фазы: 0 = ok (delivered/skipped-no-local), 2 = ≥1 failed; 1 = конфигурационная
##     ошибка (node.yaml не найден/не читается — bootstrap.sh сюда не попадает: NODE_YAML
##     резолвится раньше и передаётся явно)
##   - no_local_source/no_remote_host — НЕ failure: фаза не фейлит bootstrap при failed=0
##   - Stub-guard контекст-деплойера (_deploy_single_project_via_orchestrator, DevPlan 153 T6)
##     НЕ трогается — receive реально кладёт payload до того, как следующий deploy_context
##     доберётся до проекта; на ЭТОМ прогоне bootstrap проект остаётся awaiting_deploy до
##     нашей фазы, после неё — жив (compose up выполнил receive)
## @rationale Q: Почему модуль в deploy/, а не bootstrap/deploy/?
##            A: import-linter independence-bootstrap-deploy (DevPlan 163 W-D): единственные
##            контрактные точки bootstrap→deploy перечислены в .importlinter ignore_imports;
##            фаза доставки payload'ов (или целиком операторская delivery-логика) живёт в
##            deploy-слое — направление bootstrap→deploy оркестрационное (AGENTS.md G3), и
##            перенос устраняет нарушение без ignore-записи.
##            Q: Почему нативный вызов orchestrator_cli.main, а не subprocess python3 -m?
##            A: 1) языковая политика — 0 subprocess для бизнес-логики; 2) _handle_deliver
##            приватный → RED гейт private-imports; main() — публичный диспетчер с той же
##            семантикой (deliver → ForcedCommandChannel receive \\<p\\> \\<v\\>); 3) одна точка правды
##            для команды make deploy-project (makefiles/deploy.mk __deploy_via_deliver) —
##            никакого дублирования ассемблинга payload'а.
##            Q: Почему контекст не через context_deployer._resolve_context (приватный)?
##            A: тот же гейт private-imports + forbidden-deploy-bootstrap; локальное зеркало
##            (env → get_context) использует публичные строительные блоки (platform_config
##            sentinel семантически = "") — поведение байт-идентично _resolve_context("", path).
## @changes  2026-08-27 | DevPlan 017 (P0) — Created (в bootstrap/deploy/)
## @changes  2026-08-27 | import-linter (independence-bootstrap-deploy) — Перенесён в
##            core/internal/deploy/: зависимость context_deployer заменена локальным
##            _resolve_context_projects (shared NodeYaml.get_project_entries); bootstrap.sh
##            вызывает python3 -m core.internal.deploy.project_payload_delivery
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# deploy/ — соседний слой для себя: orchestrator_cli.main — публичный CLI-диспетчер
# deliver-канала (0 subprocess, приватные имена НЕ импортируются — гейт private-imports).
# bootstrap НЕ импортируется (forbidden-deploy-bootstrap): резолв проектов контекста — на
# каноническом shared-парсере NodeYaml.get_project_entries (single parser canon, DevPlan 116 B6).
from core.internal.deploy import orchestrator_cli
from core.internal.shared.deploy_paths import projects_base
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.node_resolver import extract_node_host, resolve_node_yaml
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.node_yaml.projects import ProjectEntry

logger = logging.getLogger(__name__)

# ── Exit-контракт фазы ────────────────────────────────────────────────────────
_EXIT_OK = 0
_EXIT_CONFIG_ERROR = 1  # node.yaml не найден/не читается (недостижимо из bootstrap.sh)
_EXIT_DELIVERY_FAILED = 2  # строгий INIT: ≥1 проект failed реальной ошибкой доставки

# Outcome-константы per-project строк (машиночитаемые причины).
_OUTCOME_DELIVERED = "delivered"
_OUTCOME_SKIPPED = "skipped"
_OUTCOME_FAILED = "failed"
_REASON_NO_LOCAL_SOURCE = "no_local_source"
_REASON_NO_CONTEXT = "no_context"
_REASON_NO_REMOTE_HOST = "no_remote_host"


# region DATACLASS_ProjectDeliveryLine
@dataclass(frozen=True)
class ProjectDeliveryLine:
    """Per-project результат локальной фазы доставки payload'а.

    ## @purpose — Одна строка DeliverySummary: проект + outcome (delivered/skipped/failed) +
    ##            причина/detail. detail для delivered — JSON-ответ receive с VPS (DEPLOYED),
    ##            для failed — error_message deliver / текст исключения.
    ## @io — ⇥ project: str, outcome: str, detail: str → ⎋ ProjectDeliveryLine
    ## @complexity — O(1)
    """

    project: str
    outcome: str
    detail: str = ""


# endregion DATACLASS_ProjectDeliveryLine


# region DATACLASS_DeliverySummary
@dataclass
class DeliverySummary:
    """Агрегированный результат локальной фазы доставки payload'ов.

    ## @purpose — Счётчики delivered/skipped/failed + per-project строки. skipped включает
    ##            no_local_source/no_context/no_remote_host (НЕ failure); failed — только
    ##            реальные ошибки доставки (deliver rc≠0 / исключение).
    ## @io — ⇥ constructor → ⎋ DeliverySummary (add() инкрементит счётчики)
    ## @complexity — O(1) per add
    ## @invariants — delivered+skipped+failed == len(lines)
    """

    delivered: int = 0
    skipped: int = 0
    failed: int = 0
    lines: list[ProjectDeliveryLine] = field(default_factory=list)

    def add(self, line: ProjectDeliveryLine) -> None:
        """Add a per-project line and increment the matching counter."""
        self.lines.append(line)
        if line.outcome == _OUTCOME_DELIVERED:
            self.delivered += 1
        elif line.outcome == _OUTCOME_SKIPPED:
            self.skipped += 1
        elif line.outcome == _OUTCOME_FAILED:
            self.failed += 1

    def to_dict(self) -> dict[str, object]:
        """Serialize to JSON-compatible dict (per-project lines + summary)."""
        return {
            "results": [
                {"project": line.project, "outcome": line.outcome, "detail": line.detail} for line in self.lines
            ],
            "summary": {"delivered": self.delivered, "skipped": self.skipped, "failed": self.failed},
        }


# endregion DATACLASS_DeliverySummary


# region FUNC__resolve_node_context
# 🧐 TRAP[DECISION] · 2026-08-27 · — · Контекст — локальное зеркало context_deployer._resolve_context
# · Rejected: импорт приватного _resolve_context (from-import _name — RED гейт private-imports) /
# ·   импорт context_deployer (deploy→bootstrap — RED forbidden-deploy-bootstrap после переноса в deploy/)
# · Reason: тот же E7-контракт на публичных строительных блоках (os.environ CONTEXT → NodeYaml.get_context);
# ·   platform_config.default_context_sentinel() = "" — os.environ.get("CONTEXT", "") байт-эквивалентен
# · Rev: если _resolve_context станет публичным — заменить зеркало прямым вызовом
def _resolve_node_context(node_yaml_path: str) -> str:
    """Резолв CONTEXT фазы: env CONTEXT → node.yaml contexts[0].name (семантика deploy_context).

    ▶ ┌node_yaml_path┐ → ◇ env CONTEXT? → ⎋ → ◇ NodeYaml.get_context() → ⎋ context (может быть "")

    ## @purpose  Тот же контракт, что context_deployer._resolve_context(context="", ...) —
    ##            deploy_context() получает контекст именно так (E7): os.environ CONTEXT →
    ##            NodeYaml.get_context() (contexts[0].name). Приватный _resolve_context НЕ
    ##            импортируется (гейт private-imports) — локальное зеркало на публичных
    ##            строительных блоках (platform_config.default_context_sentinel() = "").
    ## @io — ⇥ node_yaml_path: str → ⎋ str (context или "" — graceful-degradation на ошибках)
    ## @complexity — O(N) — YAML parse
    ## @invariants
    ##   - env CONTEXT приоритетен (непустой → как есть)
    ##   - Ошибки NodeYaml (ConfigNotFoundError/ConfigParseError) → WARN + "" (не raise)
    ##   - Пустой результат → фаза помечает все проекты skipped(no_context)
    """
    context = os.environ.get("CONTEXT", "")
    if not context:
        try:
            context = NodeYaml(node_yaml_path).get_context()
        except (ConfigNotFoundError, ConfigParseError) as exc:
            logger.warning("[IMP:7][projects][context] Cannot read context from %s: %s", node_yaml_path, exc)
    logger.info("[IMP:8][projects][context] Resolved context=%r from %s", context, node_yaml_path)
    return context


# endregion FUNC__resolve_node_context


# region FUNC__resolve_context_projects
# 🧐 TRAP[DECISION] · 2026-08-27 · — · Резолв проектов контекста — локальный фильтр, НЕ context_deployer
# · Rejected: импорт core.internal.bootstrap.deploy.context_deployer.resolve_context_projects
# ·   (deploy→bootstrap — RED forbidden-deploy-bootstrap; модуль перенесён в deploy-слой)
# · Reason: get_project_entries() — канонический shared-парсер (single parser canon, DevPlan 116 B6);
# ·   фильтр context==<context> — та же семантика, что resolve_context_projects (пустой context → все)
# · Rev: если контекстный фильтр проектов станет публичным shared-API — заменить локальную копию
def _resolve_context_projects(node_yaml_path: str, context: str) -> list[ProjectEntry]:
    """Резолв проектов контекста: NodeYaml.get_project_entries() + фильтр по context.

    ▶ ┌node_yaml_path, context┐ → ◇ is_file? → ⚡ NodeYaml.get_project_entries() → ○ filter
    │      (context и entry.context != context → skip) → ⎋ list[ProjectEntry]

    ## @purpose  Тот же контракт, что context_deployer.resolve_context_projects (публичный резолв
    ##            проектов контекста в bootstrap-слое) — на каноническом shared-парсере
    ##            get_project_entries(). Пустой context → ВСЕ проекты (фаза помечает их
    ##            skipped(no_context)). Ошибки чтения/парсинга → [] (fail-visible WARN/ERROR,
    ##            фаза продолжается — резолв НЕ raise).
    ## @io — ⇥ node_yaml_path: str, context: str → ⎋ list[ProjectEntry]
    ## @complexity — O(N) — YAML parse + фильтр
    ## @invariants
    ##   - Пустой context → все записи (не фильтруется)
    ##   - entry.context пуст → включается (проект без context = проект контекста ноды)
    ##   - node.yaml отсутствует/не читается/malformed → [] (НЕ raise — graceful)
    """
    if not node_yaml_path or not Path(node_yaml_path).is_file():
        logger.warning("[IMP:7][projects][resolve] node.yaml not found: %s", node_yaml_path)
        return []
    try:
        node = NodeYaml(node_yaml_path)
        entries = node.get_project_entries()
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError, OSError) as exc:
        logger.error("[IMP:10][projects][resolve] Cannot read projects from %s: %s", node_yaml_path, exc)
        return []
    return [e for e in entries if not (context and e.context and e.context != context)]


# endregion FUNC__resolve_context_projects


# region FUNC__resolve_projects_root
def _resolve_projects_root(projects_root: str | None) -> Path:
    """Резолв базы локальных исходников проектов: явный override → projects_base().

    ▶ ┌projects_root┐ → ◇ задан? → Path → ⎋ projects_base() (env PROJECTS_BASE → /opt/projects
    │                                → dev-fallback ~/projects)

    ## @purpose  Канон layout оператора: \\<projects_root\\>/\\<context\\>/\\<project\\>/ (у владельца
    ##            ~/projects/tronyx-lab/tronyx-site). projects_base() (shared/deploy_paths)
    ##            на dev-машине возвращает ~/projects (F-017 dev-fallback), на ноде/CI —
    ##            /opt/projects (там эта фаза не запускается — bootstrap.sh remote-путь).
    ## @io — ⇥ projects_root: str | None → ⎋ Path
    ## @complexity — O(1)
    ## @invariants — Никогда не raise (projects_base всегда возвращает Path)
    """
    if projects_root:
        return Path(projects_root).expanduser()
    return projects_base()


# endregion FUNC__resolve_projects_root


# region FUNC__build_default_deliver
# 🧐 TRAP[DECISION] · 2026-08-27 · — · deliver — через orchestrator_cli.main (публичный диспетчер)
# · Rejected: прямой вызов приватного _handle_deliver (from-import _name — RED гейт private-imports) /
# ·   subprocess python3 -m (языковая политика — 0 subprocess для бизнес-логики)
# · Reason: main() — единственный публичный шов с той же deliver-семантикой (makefiles/deploy.mk
# ·   __deploy_via_deliver вызывает тот же CLI); stdout перехватывается redirect_stdout
# · Rev: если orchestrator_cli введёт публичный deliver-callable — заменить main()-вызов
def _build_default_deliver(host: str) -> Callable[[str, Path], tuple[bool, str]]:
    """Фабрика дефолтного deliver-вызова, связанного с резолвнутым host.

    ▶ ┌host┐ → ⎋ _deliver(project_name, project_dir) → orchestrator_cli.main(deliver argv) →
    │      capture stdout (VPS JSON) → ⎋ (rc==0, stdout|rc-текст)

    ## @purpose  In-process вызов ТОГО ЖЕ канала, что make deploy-project (makefiles/deploy.mk
    ##            __deploy_via_deliver): orchestrator_cli deliver --project --project-dir --host
    ##            → PayloadDeliverer.assemble_payload → ForcedCommandChannel receive <p> <v>.
    ##            0 subprocess (языковая политика); stdout deliver (JSON receive с VPS)
    ##            перехватывается redirect_stdout и возвращается как detail-строка.
    ## @io — ⇥ host: str (node.host из node.yaml) → ⎋ Callable[[str, Path], tuple[bool, str]]
    ## @complexity — O(P) где P = размер payload (assemble + ssh-доставка)
    ## @invariants
    ##   - version дефолт "latest" (локально sha недоступен; реальная версия — из CI)
    ##   - key_file НЕ передаётся → канонический дефолт ForcedCommandChannel (~/.ssh/ci_deploy_key)
    ##   - rc из orchestrator_cli.main: 0 = DEPLOYED/SKIPPED, 1 = FAILED (REF-0003)
    ##   - Исключение НЕ глотается здесь — первичный guard в deliver_pending_projects (per-project)
    """

    def _deliver(project_name: str, project_dir: Path) -> tuple[bool, str]:
        argv = [
            "deliver",
            "--project",
            project_name,
            "--project-dir",
            str(project_dir),
            "--host",
            host,
        ]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = orchestrator_cli.main(argv)
        detail = out.getvalue().strip()
        return rc == 0, detail if detail else f"deliver rc={rc}"

    return _deliver


# endregion FUNC__build_default_deliver


# region FUNC_deliver_pending_projects
## @purpose  Основная логика фазы: resolve контекста + проектов + host → per-project доставка
##           payload'ов через deliver-канал. Возвращает DeliverySummary (никогда не raise).
## @io       ⇥ node_name: str, node_yaml_path: str, projects_root: str | None = None,
##              deliver_fn: Callable[[str, Path], tuple[bool, str]] | None = None (DI-шов —
##              тесты передают fake; None = _build_default_deliver(host))
##           → ⎋ DeliverySummary
## @complexity — O(P * D) где P = проекты контекста, D = deliver lifecycle
## @invariants
##   - Контекст пуст → ВСЕ проекты skipped(no_context) [IMP:7] — фаза НЕ фейлит bootstrap
##   - Локальный каталог \\<base\\>/\\<context\\>/\\<name\\> отсутствует → skipped(no_local_source) [IMP:7]
##   - host пуст → проекты с локальным каталогом skipped(no_remote_host) [IMP:7]
##   - Исключение deliver_fn → failed (per-project, остальные продолжаются) — fail-visible IMP:10
##   - НЕ мутирует node.yaml / overlay'и (только читает); доставка — на ноду через SSH
# region FUNC_deliver_pending_projects_body
def deliver_pending_projects(
    node_name: str,
    node_yaml_path: str,
    projects_root: str | None = None,
    *,
    deliver_fn: Callable[[str, Path], tuple[bool, str]] | None = None,
) -> DeliverySummary:
    """Deliver pending project payloads to a freshly bootstrapped node (P0, DevPlan 017)."""
    summary = DeliverySummary()

    # ── 1. Контекст (тем же способом, что deploy_context()) ──
    context = _resolve_node_context(node_yaml_path)
    if not context:
        logger.error(
            "[IMP:10][projects][context] CONTEXT not resolved for %s — cannot locate operator sources",
            node_yaml_path,
        )
        projects_any = _resolve_context_projects(node_yaml_path, "")
        for proj in projects_any:
            summary.add(ProjectDeliveryLine(proj.name, _OUTCOME_SKIPPED, _REASON_NO_CONTEXT))
        return summary

    # ── 2. Проекты контекста (тот же резолв, что deploy_context) ──
    projects = _resolve_context_projects(node_yaml_path, context)
    if not projects:
        logger.info(
            "[IMP:7][projects] No context projects in %s (context=%s) — nothing to deliver", node_yaml_path, context
        )
        return summary

    # ── 3. host + база локальных исходников ──
    host = extract_node_host(node_yaml_path)
    base = _resolve_projects_root(projects_root)
    logger.info(
        "[IMP:8][projects] Delivering %d pending project(s) (node=%s context=%s base=%s host=%s)",
        len(projects),
        node_name,
        context,
        base,
        host or "<none>",
    )
    deliver: Callable[[str, Path], tuple[bool, str]] = (
        deliver_fn if deliver_fn is not None else _build_default_deliver(host)
    )

    # ── 4. Per-project доставка (skipped ≠ failure; failed → exit 2) ──
    for proj in projects:
        project_dir = base / context / proj.name
        if not project_dir.is_dir():
            logger.info(
                "[IMP:7][projects] %s — no local source at %s (skipped: %s)",
                proj.name,
                project_dir,
                _REASON_NO_LOCAL_SOURCE,
            )
            summary.add(ProjectDeliveryLine(proj.name, _OUTCOME_SKIPPED, f"{_REASON_NO_LOCAL_SOURCE}: {project_dir}"))
            continue
        if not host:
            logger.warning(
                "[IMP:7][projects] %s — no node.host in %s (skipped: %s)",
                proj.name,
                node_yaml_path,
                _REASON_NO_REMOTE_HOST,
            )
            summary.add(ProjectDeliveryLine(proj.name, _OUTCOME_SKIPPED, _REASON_NO_REMOTE_HOST))
            continue

        logger.info("[IMP:8][projects] Delivering %s from %s (host=%s)", proj.name, project_dir, host)
        try:
            ok, detail = deliver(proj.name, project_dir)
        except Exception as exc:  # noqa: EXC — best-effort per-project guard: одна ошибка не останавливает остальные (fail-visible IMP:10); # ruff: ignore[BLE001]
            logger.error("[IMP:10][projects] %s — deliver raised: %s", proj.name, exc)
            summary.add(ProjectDeliveryLine(proj.name, _OUTCOME_FAILED, f"deliver raised: {exc}"))
            continue
        if ok:
            logger.info("[IMP:9][projects] %s — DELIVERED (receive compose-up on node)", proj.name)
            summary.add(ProjectDeliveryLine(proj.name, _OUTCOME_DELIVERED, detail or "ok"))
        else:
            logger.error("[IMP:10][projects] %s — delivery FAILED: %s", proj.name, detail or "deliver rc != 0")
            summary.add(ProjectDeliveryLine(proj.name, _OUTCOME_FAILED, detail or "deliver rc != 0"))
    return summary


# endregion FUNC_deliver_pending_projects_body
# endregion FUNC_deliver_pending_projects


# region FUNC_delivery_exit_code
def delivery_exit_code(summary: DeliverySummary) -> int:
    """Чистый маппинг DeliverySummary → exit-код фазы.

    ▶ ┌summary┐ → ◇ failed>0? → ⎋ 2 → ⎋ 0

    ## @purpose  Контракт строгого INIT: bootstrap не считается успешным при ≥1 проекте с
    ##            реальной ошибкой доставки. skipped (no_local_source/no_context/no_remote_host)
    ##            — НЕ failure → 0. Чистая функция (тестируется напрямую).
    ## @io — ⇥ summary: DeliverySummary → ⎋ int (0 | 2)
    ## @complexity — O(1)
    ## @invariants — Единственное исключение из «exit 2 при failed>0» — конфигурационные ошибки
    ##              (1) обрабатываются в main(); здесь только 0/2
    """
    return _EXIT_DELIVERY_FAILED if summary.failed > 0 else _EXIT_OK


# endregion FUNC_delivery_exit_code


# region FUNC_build_parser
def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for the bootstrap project-delivery phase.

    ## @purpose — argparse: --node/--node-yaml/--projects-root/--platform-root.
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        prog="core.internal.deploy.project_payload_delivery",
        description="Deliver context project payloads to a bootstrapped node (P0, DevPlan 017).",
    )
    parser.add_argument("--node", required=True, help="Node name (bootstrap NODE_NAME)")
    parser.add_argument(
        "--node-yaml",
        default="",
        help="Local node.yaml path (bootstrap_resolver resolved); empty → resolve_node_yaml(--node)",
    )
    parser.add_argument(
        "--projects-root", default=None, help="Override operator projects base (default: projects_base())"
    )
    parser.add_argument(
        "--platform-root",
        default=None,
        help="Base config dir for node.yaml 3-path search (only used when --node-yaml empty)",
    )
    return parser


# endregion FUNC_build_parser


# region CLASS_CliArgs
class _CliArgs(argparse.Namespace):
    """Типизированный argparse-Namespace (паттерн bootstrap_resolver._CliArgs, W11)."""

    def __init__(self) -> None:
        super().__init__()
        self.node: str
        self.node_yaml: str
        self.projects_root: str | None
        self.platform_root: str | None


# endregion CLASS_CliArgs


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint фазы. sys.exit вызывается только в __main__ (канон core/AGENTS.md).

    ▶ ┌argv┐ → ◇ resolve node_yaml (--node-yaml | resolve_node_yaml) → ○ deliver_pending_projects →
    │      → ○ print summary lines + IMP:9 → ⎋ 0|1|2

    ## @purpose  Интерфейс для bootstrap.sh: `python3 -m core.internal.deploy.
    ##            project_payload_delivery --node X --node-yaml Y`. Печатает per-project строки
    ##            и summary в stdout (видимость оператору), IMP:7-10 — через logger (stderr).
    ##            Exit-контракт: 0 = ok, 2 = ≥1 failed (строгий INIT), 1 = node.yaml не найден.
    ## @io — ⇥ argv: list[str] | None → ⎋ int (0 | 1 | 2)
    ## @complexity — O(P * D)
    ## @invariants
    ##   - LDD: module-logger ребандлится к ТЕКУЩЕМУ sys.stderr (паттерн node_resolver.main);
    ##     propagation сохранён — caplog-телеметрия в pytest работает
    ##   - node.yaml не найден (только прямой CLI-вызов без --node-yaml) → IMP:10 + exit 1
    ##   - main() -> int канон (core/AGENTS.md): sys.exit только в __main__
    """
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    cli_handler = logging.StreamHandler(sys.stderr)
    cli_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(cli_handler)
    logger.setLevel(logging.INFO)

    parser = build_parser()
    args = parser.parse_args(argv, namespace=_CliArgs())

    yaml_path = args.node_yaml
    if not yaml_path:
        try:
            yaml_path = resolve_node_yaml(node_name=args.node, platform_root=args.platform_root)
        except ConfigNotFoundError as exc:
            logger.error("[IMP:10][projects] node.yaml not found for node=%s: %s", args.node, exc)
            return _EXIT_CONFIG_ERROR

    summary = deliver_pending_projects(
        node_name=args.node,
        node_yaml_path=yaml_path,
        projects_root=args.projects_root,
    )

    for line in summary.lines:
        print(f"[bootstrap][projects] project={line.project} outcome={line.outcome} detail={line.detail}")
    logger.info(
        "[IMP:9][bootstrap][projects] delivered=%d skipped=%d failed=%d",
        summary.delivered,
        summary.skipped,
        summary.failed,
    )
    print(f"[bootstrap][projects] delivered={summary.delivered} skipped={summary.skipped} failed={summary.failed}")
    return delivery_exit_code(summary)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
