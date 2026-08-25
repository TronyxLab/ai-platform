#!/usr/bin/env python3
# GREP_SUMMARY: verify-contracts, K3, VPS, L1, L2, L3, secrets-in-compose, ports-published, healthcheck-present, external-networks, env-file-contract, platform-labels, limits-present, privileged, cap-add, devices, dangerous-volumes, socket-mount, host-binds, named-volumes, host-mode-keys, network-mode-host, pid, userns, sysctls, l1_only, compose-config-valid, drift-practices, build-check, PRACTICES-BLOCK, audit, allowlist
# STRUCTURE: ▶ verify_project_contracts(project_dir) → load_manifest (canon: allowed_external_networks) → read practices.lock (state) → resolve compose → 12×L1 статика (secrets/ports/healthcheck/networks/env-file/labels/limits/privileged/cap-add/devices/volumes/host-modes) → ◇ l1_only? → 3×L2 (compose config / drift-version / build-check) → ⊕ severity (L1: block; l1_only: parse-fail block; L2/L3: block|active-full else warn) → ⊕ VerifyReport → audit_logger (event=verify_contracts) → ⎋ has_blocking/has_warnings/format_for_ssh
# region MODULE_CONTRACT
## @purpose  Контракт-проверки проекта на VPS (DevPlan 137 W4, K3-канал — расширение verify
##           verb forced-command диспетчера): 13 контрактов по таблице §5 W4 + limits-present
##           (DevPlan 162 W4-3) + privileged/cap_add/devices (DevPlan 176 A.1, C1 root-эскалация)
##           + dangerous-volumes/host-mode-keys (REF-0006, DevPlan 11 В2 — volumes/socket/
##           host-binds/named-volumes и network_mode:host/pid/userns_mode/cgroup/sysctls).
##           L1-контракты — инварианты платформы
##           (см. секцию «Контракт окружения проекта» root AGENTS.md: «НЕ публикуй порты»,
##           «НЕ храни секреты», ingress = nginx proxy-net, привилегии проектам запрещены),
##           переведённые в машиночитаемые
##           проверки; исполняются ПРИ ЛЮБОМ уровне практик (безопасность платформы, §3.1 п.4,
##           §4.5). L2 — контракты качества (compose config, дрейф practices.lock version,
##           build --check) по state из practices.lock. Проект без practices.lock →
##           state=unmanaged, L1 блокирует деплой (контракт обязателен всегда).
## @scope    Вызывается из orchestrator_cli dispatch verb=verify ПОСЛЕ успешной HTTPS-проверки
##           (domain_verifier, DevPlan 125 T1 — НЕ дублируется). Библиотечная функция +
##           CLI main() для ручной диагностики. НЕ вычисляет maturity и НЕ вызывает
##           escalator.evaluate (на VPS нет git) — применяет готовый state из practices.lock.
## @invariants
##   - L1 (secrets-in-compose/ports-published/healthcheck-present/external-networks/
##     env-file-contract/platform-labels/limits-present/privileged/cap-add/devices/
##     dangerous-volumes/host-mode-keys) — БЛОК всегда;
##     L2/L3 — блок только в active-full
##   - privileged/cap_add/devices (176 A.1, C1): проектам привилегии запрещены ПОЛНОСТЬЮ —
##     privileged truthy, ЛЮБОЙ cap_add/devices ключ (в т.ч. пустой список) → violation;
##     платформенные модули — через gated allowlist вне скоупа этих контрактов
##   - dangerous-volumes (REF-0006): socket-маунты (/var/run/docker.sock и пр.) — deny
##     безусловно; абсолютные host-binds — только из _ALLOWED_ABSOLUTE_HOST_BINDS (минимальный
##     allowlist); относительные (./ ../) и не-статически-резолвимые (${VAR}) источники —
##     deny; персистентность проектов — ТОЛЬКО named volumes
##   - host-mode-keys (REF-0006): network_mode:host / pid:host / userns_mode:host /
##     cgroup:host → violation; cgroup_parent/sysctls — ЛЮБОЕ присутствие ключа → violation
##     (паритет cap_add/devices)
##   - l1_only=True (176 A.2 pre-deploy gate receive + REF-0006 pre-apply gate deploy):
##     ТОЛЬКО L1-статика — drift и docker-зависимые L2 (compose-config-valid/build-check)
##     НЕ исполняются (pre-up гейт без docker-латентности);
##     ⚠️ ИСКЛЮЧЕНИЕ: непарсящийся compose (compose-config-valid parse-fail) в l1_only —
##     БЛОКИРУЮЩИЙ (сломанный YAML больше не проходит pre-deploy гейт как L2-warning,
##     REF-0006); audit пишется (block-события фиксируются)
##   - allowed_external_networks — ИЗ КАНОНА practices_manifest.yaml (не хардкод; TRAP §10.2)
##   - healthcheck-present — СТАТИЧЕСКАЯ проверка наличия ключа healthcheck: ИЛИ labels:
##     platform.healthcheck=...; канон healthcheck_poller НЕ дублируется (без runtime inspect)
##   - limits-present — НАЛИЧИЕ deploy.resources.limits.memory И deploy.resources.limits.cpus
##     у каждого сервиса (DevPlan 162 W4-3: лимиты 128M/0.25CPU проектам, K3/L1-практики;
##     проверка НЕ валидирует значения — только наличие)
##   - practices.lock отсутствует → state=unmanaged; L1 блокирует (переходный grace-период удалён)
##   - docker-зависимые L2 (compose-config-valid/build-check) исполняются только при наличии
##     бинарника docker (на VPS гарантирован bootstrap'ом); отсутствие → skip (не warning)
##   - Аудит через shared/audit_logger (единый writer, DevPlan 116 B11 T2) — event=verify_contracts
##   - Exit-коды из shared/contracts.py (0/1) — НЕ хардкодить; main() -> int (контракт core)
## @rationale  Платформа деплоит проекты без единой проверки качества (deploy-project.yml:
##             ping→receive→verify; verify не проверял код). L1 = защита платформы (не качество
##             проекта) — публикация портов ломает ingress/TLS-модель nginx, секреты в compose
##             утекают в git, external-сети вне allowlist — чужие сети,
##             privileged/cap_add/devices (176 C1) — root-эскалация: ci-deploy в группе docker
##             исполняет произвольный compose ДО любых проверок; pre-up L1-гейт receive (A.2)
##             закрывает канал ДО запуска контейнеров.
##             limits-present (162 W4-3): проектные контейнеры без memory/CPU лимитов — OOM-риск
##             общего стека (лимиты 128M/0.25CPU, K3/L1-практики); PidsLimit 256 платформенным.
## @changes  2026-08-05 · DevPlan 137 W4 — создан (K3-канал, 9 контрактов §5 W4)
## @changes  2026-08-13 · DevPlan 162 W4-3 — +L1 limits-present (наличие memory+cpus лимитов)
## @changes  2026-08-16 · DevPlan 176 A.1/A.2 — +L1 privileged/cap_add/devices (C1 root-эскалация);
##           +l1_only режим (pre-deploy gate receive_flow) + audit_project_name override
## @changes  2026-08-25 · REF-0006 (DevPlan 11 В2) — +L1 dangerous-volumes (socket-mounts deny,
##           абсолютные host-binds вне минимального allowlist, named-volumes requirement) +
##           host-mode-keys (network_mode:host/pid/userns_mode/cgroup/sysctls); l1_only:
##           compose-config-valid parse-fail → БЛОК (сломанный YAML не проходит pre-deploy гейт)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from core.internal.practices.generators import PracticesLock, read_lock
from core.internal.practices.manifest import load_manifest
from core.internal.shared.audit_logger import DEFAULT_LOG_FILE, write_audit_entry
from core.internal.shared.compose_files import PROJECT_OVERRIDE_FILENAMES, resolve_compose_file
from core.internal.shared.contracts import EXIT_GENERIC, EXIT_OK
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.exceptions import ConfigValidationError, PlatformError

logger = logging.getLogger(__name__)

# ── Классы контрактов (паритет канону practices_manifest.yaml class: L1/L2/L3) ──
KLASS_L1: str = "L1"
KLASS_L2: str = "L2"
KLASS_L3: str = "L3"

# ── Severity (блокировка): "block" — деплой заблокирован; "warning" — non-blocking ──
SEVERITY_BLOCK: str = "block"
SEVERITY_WARNING: str = "warning"

# ── Ключи секретов (L1 secrets-in-compose): матчинг по суффиксу нормализованного ключа ──
_SECRET_KEY_SUFFIXES: tuple[str, ...] = ("password", "api_key", "token")

# ── Единственный допустимый env_file проекта (L1 env-file-contract) ──
ENV_FILE_PLATFORM: str = ".env.platform"

# ── Префикс платформенных labels (L1 platform-labels): platform.type/platform.domain/... ──
_PLATFORM_LABEL_PREFIX: str = "platform."

# ── Docker-команды (таймауты) ──
_COMPOSE_CONFIG_TIMEOUT: int = 30
_BUILD_CHECK_TIMEOUT: int = 120

# ── REF-0006 (dangerous-volumes): socket/docker-маунты — root-эквивалент ноды.
# Точное совпадение ИЛИ вложение в каталог-префикс (/var/run/docker/*). Суффиксное
# сопоставление не используем: "/evil/var/run/docker.sock" как host-path легитимного
# кейса не имеет, а ложные срабатывания на container-path недопустимы — матчим ТОЛЬКО
# source (host-сторону) маунта.
_DANGEROUS_SOCKET_SOURCES: tuple[str, ...] = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/var/run/docker",
    "/run/docker",
    "/var/run/containerd/containerd.sock",
    "/run/containerd/containerd.sock",
    "/run/buildkit/buildkitd.sock",
)

# ── REF-0006 (dangerous-volumes): минимальный allowlist абсолютных host-binds.
# Пуст по умолчанию: проектам bind-mounts host-путей запрещены, персистентность —
# named volumes. Запись допускает точный путь ИЛИ каталог-префикс (запись "/srv/data"
# разрешает "/srv/data/sub"). Расширение = правка этой константы + R5-negative на новый путь.
_ALLOWED_ABSOLUTE_HOST_BINDS: frozenset[str] = frozenset()

# ── REF-0006 (host-mode-keys): compose-ключи host-namespace-доступа → значение "host"
# запрещено (network/pid/userns/cgroup namespace ноды = root-эквивалент).
# QA C3 (DevPlan 14 T1.2): ipc/uts добавлены — host IPC (shared memory с произвольными
# процессами ноды) и host UTS (shared hostname) — тот же класс root-эквивалента.
_HOST_MODE_VALUE_KEYS: tuple[tuple[str, str], ...] = (
    ("network_mode", "host"),
    ("pid", "host"),
    ("userns_mode", "host"),
    ("cgroup", "host"),
    ("ipc", "host"),
    ("uts", "host"),
)


# region FUNC_ContractFinding
## @purpose  Frozen-находка контракт-проверки: contract_id (id из таблицы §5 W4), klass
##           (L1/L2/L3), severity (block|warning — вычислен по state), message.
## @io       ⇥ contract_id/klass/severity/message → ⎋ ContractFinding
## @complexity O(1)
@dataclass(frozen=True)
class ContractFinding:
    """Single contract finding (contract_id + class + severity + message)."""

    contract_id: str
    klass: str  # L1 | L2 | L3
    severity: str  # block | warning
    message: str

    ## @purpose  Сериализация в dict (для audit_logger findings и JSON-вывода).
    ## @io       ⎋ dict[str, str]
    ## @complexity O(1)
    def to_dict(self) -> dict[str, str]:
        """Serialize finding to plain dict (audit/JSON compatible)."""
        return {
            "id": self.contract_id,
            "class": self.klass,
            "severity": self.severity,
            "message": self.message,
        }


# endregion FUNC_ContractFinding


# region FUNC_VerifyReport
## @purpose  Frozen-отчёт verify_contracts: state (baseline|proposed|active-full|unmanaged),
##           findings. Методы: has_blocking_violation()/has_warnings()/
##           exit_code/format_for_ssh() (вывод [PRACTICES:BLOCK]/[PRACTICES:PROPOSE]/
##           [PRACTICES:UNMANAGED] — §5 W4 интеграция в orchestrator_cli).
## @io       ⇥ project_dir/state/findings → ⎋ VerifyReport
## @complexity O(F) где F = findings
@dataclass(frozen=True)
class VerifyReport:
    """Report of a verify_contracts run (project contracts, K3)."""

    project_dir: Path
    state: str  # baseline | proposed | active-full | unmanaged
    findings: tuple[ContractFinding, ...]

    ## @purpose  Есть ли блокирующее нарушение (severity=block). L1 всегда block;
    ##           L2/L3 — block в active-full.
    ## @io       ⎋ bool
    ## @complexity O(F)
    def has_blocking_violation(self) -> bool:
        """True если хотя бы один finding имеет severity=block (деплой блокируется)."""
        return any(f.severity == SEVERITY_BLOCK for f in self.findings)

    ## @purpose  Есть ли non-blocking warning: severity=warning.
    ## @io       ⎋ bool
    ## @complexity O(F)
    def has_warnings(self) -> bool:
        """True если есть warnings (non-blocking)."""
        return any(f.severity == SEVERITY_WARNING for f in self.findings)

    ## @purpose  Exit-код для forced-command: 1 при блокирующем нарушении, иначе 0.
    ## @io       ⎋ int (EXIT_GENERIC | EXIT_OK — из shared/contracts.py)
    ## @complexity O(F)
    @property
    def exit_code(self) -> int:
        """Exit code: 1 если blocking violation, иначе 0 (контракт §5 W4)."""
        return EXIT_GENERIC if self.has_blocking_violation() else EXIT_OK

    ## @purpose  Формат вывода для SSH forced-command (stdout verify): [PRACTICES:BLOCK] для
    ##           блокирующих, [PRACTICES:PROPOSE] для warning, [PRACTICES:UNMANAGED] для
    ##           проекта без practices.lock, [PRACTICES:OK] при чистом прогоне,
    ##           [PRACTICES:RESULT] сводка.
    ## @io       ⎋ str — многострочный отчёт (по одной строке на finding)
    ## @complexity O(F)
    def format_for_ssh(self) -> str:
        """Render [PRACTICES:...] report for SSH forced-command output (agent-visible)."""
        lines: list[str] = []
        if self.state == "unmanaged":
            lines.append(
                "[PRACTICES:UNMANAGED] practices.lock not found — L1-контракты блокируют деплой. "
                "Run: make project-sync-practices / make adopt-project"
            )
        for f in self.findings:
            tag = "BLOCK" if f.severity == SEVERITY_BLOCK else "PROPOSE"
            lines.append(f"[PRACTICES:{tag}][{f.klass}][{f.contract_id}] {f.message}")
        if not lines:
            lines.append("[PRACTICES:OK] all contracts pass")
        n_block = sum(1 for f in self.findings if f.severity == SEVERITY_BLOCK)
        lines.append(
            f"[PRACTICES:RESULT] state={self.state} findings={len(self.findings)} blocking={n_block} exit={self.exit_code}"
        )
        return "\n".join(lines)


# endregion FUNC_VerifyReport


# region FUNC__RawFinding
## @purpose  Внутренний (не-public) промежуточный finding до вычисления severity: contract_id +
##           klass + message. Проверки возвращают _RawFinding; verify_project_contracts
##           мапит в ContractFinding с severity по state (единая точка политики).
## @io       ⇥ contract_id/klass/message → ⎋ _RawFinding
## @complexity O(1)
@dataclass(frozen=True)
class _RawFinding:
    """Internal pre-severity finding (severity computed centrally by state policy)."""

    contract_id: str
    klass: str
    message: str


# endregion FUNC__RawFinding


# region FUNC_verify_project_contracts
## @purpose  Главная точка входа K3: прогон всех контрактов по каталогу проекта → VerifyReport.
##           Порядок: канон → practices.lock (state) → compose (L1-статика + L2 docker) →
##           drift-version → severity (L1 block; L2/L3 block в active-full) → audit.
##           l1_only (176 A.2): pre-deploy gate receive — ТОЛЬКО L1-статика (без docker-L2).
## @io       ⇥ project_dir: Path, audit_log_file: str | None (None = канонический /var/log/platform/audit.jsonl),
##           facts: EnvironmentFacts | None (which docker DI),
##           l1_only: bool = False (ТОЛЬКО L1-статика — pre-deploy gate, 176 A.2),
##           audit_project_name: str | None = None (override audit project-поля — receive-гейт
##           передаёт реальное имя проекта; None = project_dir.name)
##           → ⎋ VerifyReport
##           ⚡ ConfigValidationError — сломанный канон practices_manifest.yaml (exit 4)
## @complexity O(S) где S = размер compose + subprocess docker (config/build)
## @invariants
##   - Проект-директория отсутствует → пустой отчёт (state=unmanaged, 0 findings), аудит НЕ пишется
##   - lock отсутствует → state="unmanaged"
##   - L1 severity: block всегда
##   - L2/L3 severity: block только в state=active-full, иначе warning
##   - l1_only=True → drift-practices + docker-L2 (compose-config-valid/build-check) пропускаются;
##     аудит блок-событий пишется всегда
##   - docker отсутствует → L2 docker-проверки пропускаются (на VPS docker гарантирован)
def verify_project_contracts(
    project_dir: Path,
    *,
    audit_log_file: str | None = None,
    facts: EnvironmentFacts | None = None,
    l1_only: bool = False,
    audit_project_name: str | None = None,
) -> VerifyReport:
    """Run contract verification for a project dir → VerifyReport (exit 0/1 semantics)."""
    project_dir = Path(project_dir)

    if not project_dir.is_dir():
        logger.warning(
            "[IMP:7][verify_contracts][skip] project dir not found: %s — contract verification skipped",
            project_dir,
        )
        logger.info(
            "[IMP:9][verify_contracts][skip] project=%s dir missing → 0 findings, exit 0 (non-blocking)",
            project_dir.name,
        )
        return VerifyReport(project_dir=project_dir, state="unmanaged", findings=())

    manifest = load_manifest()
    lock = read_lock(project_dir)
    state = lock.state if lock is not None else "unmanaged"
    logger.info(
        "[IMP:8][verify_contracts][start] project=%s state=%s canon_v%d",
        project_dir.name,
        state,
        manifest.version,
    )

    raw: list[_RawFinding] = []
    compose_path = resolve_compose_file(project_dir)
    # DevPlan 16 T1.E (P1-11): мульти-compose — сканируем ВСЕ существующие файлы проекта
    # (канон + override-слой): override может нести security_opt/devices/gpus, невидимые
    # single-file сканом. Findings агрегируются с именем файла в сообщении.
    scan_files: list[Path] = []
    if compose_path is not None:
        scan_files.append(compose_path)
    for override_name in PROJECT_OVERRIDE_FILENAMES:
        candidate = project_dir / override_name
        if candidate.is_file() and candidate not in scan_files:
            scan_files.append(candidate)

    for scan_file in scan_files:
        file_tag = f"[{scan_file.name}] "
        data, parse_err = _parse_compose(scan_file)
        if data is None:
            raw.append(
                _RawFinding(
                    "compose-config-valid", KLASS_L2, f"{file_tag}compose file не парсится как YAML: {parse_err}"
                )
            )
            continue
        services = data.get("services")
        networks = data.get("networks")

        def _tagged(check_findings: list[_RawFinding], _tag: str = file_tag) -> list[_RawFinding]:
            """Префикс имени файла в каждом finding (мульти-compose агрегация, T1.E)."""
            return [_RawFinding(f.contract_id, f.klass, _tag + f.message) for f in check_findings]

        # QA C3 (DevPlan 14 T1.2): top-level volumes сканируется НЕЗАВИСИМО от валидности
        # services — bind через driver_opts определения named volume обходит services-scan
        raw.extend(_tagged(_check_top_level_volumes(data.get("volumes"))))
        if not isinstance(services, dict):
            raw.append(
                _RawFinding("compose-config-valid", KLASS_L2, f"{file_tag}compose file не содержит services (dict)")
            )
            continue
        raw.extend(_tagged(_check_secrets_in_compose(services)))
        raw.extend(_tagged(_check_ports_published(services)))
        raw.extend(_tagged(_check_healthcheck_present(services)))
        raw.extend(_tagged(_check_external_networks(networks, manifest.allowed_external_networks)))
        raw.extend(_tagged(_check_env_file(services)))
        raw.extend(_tagged(_check_platform_labels(services)))
        raw.extend(_tagged(_check_limits_present(services)))
        # 176 A.1 (C1 root-эскалация): привилегии проектам запрещены полностью
        raw.extend(_tagged(_check_privileged(services)))
        raw.extend(_tagged(_check_cap_add(services)))
        raw.extend(_tagged(_check_devices(services)))
        # DevPlan 16 T1.E (P1-10/P1-11): GPU/device-reservations + security_opt
        raw.extend(_tagged(_check_device_reservations(services)))
        raw.extend(_tagged(_check_security_opt(services)))
        # REF-0006 (DevPlan 11 В2): volumes/socket/host-binds + host-mode-ключи
        raw.extend(_tagged(_check_dangerous_volumes(services)))
        raw.extend(_tagged(_check_host_mode_keys(services)))

    # DevPlan 16 T1.E (P0-6 / P1-9): unmanaged-проект на pre-deploy гейте блокируется:
    # l1_only + lock отсутствует → L1-finding (SEVERITY_BLOCK через _severity_for).
    # Наличие lock → прежняя семантика (drift-check в l1_only остаётся skip).
    if l1_only and state == "unmanaged":
        raw.append(
            _RawFinding(
                "drift-practices-unmanaged",
                KLASS_L1,
                "practices.lock отсутствует — unmanaged-проект блокирует pre-deploy деплой "
                "(L1: секреты/порты/healthcheck без носителя state; adopt-project или "
                "project-set-practices обязателен)",
            )
        )

    # ── L2: drift practices.lock (носитель state на VPS) + docker-контракты ──
    # 176 A.2: l1_only (pre-deploy gate receive) — ТОЛЬКО L1-статика, без drift/docker-L2
    # (pre-up гейт не должен тянуть docker-латентность; полный K3-прогон — verify-verb пост-деплой)
    if not l1_only:
        raw.extend(_check_drift_practices(lock, manifest.version))
        if compose_path is not None:
            raw.extend(_check_compose_config_validate(project_dir, facts))
        raw.extend(_check_build_check(project_dir, facts))

    findings = tuple(
        ContractFinding(
            contract_id=r.contract_id,
            klass=r.klass,
            severity=_severity_for(r.klass, state, contract_id=r.contract_id, l1_only=l1_only),
            message=r.message,
        )
        for r in raw
    )
    report = VerifyReport(
        project_dir=project_dir,
        state=state,
        findings=findings,
    )

    _audit_report(report, audit_log_file, project_name=audit_project_name)
    n_block = sum(1 for f in report.findings if f.severity == SEVERITY_BLOCK)
    logger.info(
        "[IMP:9][verify_contracts][done] project=%s state=%s findings=%d blocking=%d",
        audit_project_name or report.project_dir.name,
        report.state,
        len(report.findings),
        n_block,
    )
    return report


# endregion FUNC_verify_project_contracts


# region FUNC__severity_for
## @purpose  Политика блокировки L1/L2/L3 (§4.5): L1 — block всегда; L2/L3 — block только
##           в active-full (по state из practices.lock), baseline/proposed/unmanaged — warning.
##           REF-0006: в l1_only (pre-deploy/pre-apply гейт) compose-config-valid parse-fail —
##           БЛОК: сломанный YAML не проходит pre-up гейт как L2-warning (docker-L2 subprocess
##           в l1_only не исполняется, но статический parse-fail = деплой невалидного compose).
## @io       ⇥ klass: str, state: str, contract_id: str = "", *, l1_only: bool = False
##           → ⎋ "block" | "warning"
## @complexity O(1)
def _severity_for(klass: str, state: str, contract_id: str = "", *, l1_only: bool = False) -> str:
    """Compute severity by class + state + l1_only policy (DevPlan 137 §4.5 / REF-0006)."""
    if klass == KLASS_L1:
        return SEVERITY_BLOCK
    if l1_only and contract_id == "compose-config-valid":
        return SEVERITY_BLOCK
    if state == "active-full":
        return SEVERITY_BLOCK
    return SEVERITY_WARNING


# endregion FUNC__severity_for


# region FUNC__parse_compose
## @purpose  Безопасный YAML-парсинг compose-файла → (data, error). Синтаксическая ошибка
##           НЕ роняет прогон — отдаёт (None, сообщение) для L2 compose-config-valid finding.
## @io       ⇥ compose_path: Path → ⎋ tuple[dict | None, str]
## @complexity O(S) где S = размер файла
def _parse_compose(compose_path: Path) -> tuple[dict[str, object] | None, str]:
    """Parse compose YAML → (dict, "") or (None, error-message)."""
    try:
        # yaml.safe_load → Any; object-граница — isinstance-check ниже (W11)
        data: object = cast(object, yaml.safe_load(compose_path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "root не object"
    return data, ""


# endregion FUNC__parse_compose


# ═══════════════════════════════════════════════════════════════════
# L1-контракты (инварианты платформы, блок всегда)
# ═══════════════════════════════════════════════════════════════════


# region CONTRACT_secrets_in_compose
## @purpose  L1 secrets-in-compose: в compose НЕТ литералов password:/api_key:/token: —
##           только ${VAR} интерполяция (см. секцию «Контракт окружения проекта» root AGENTS.md, DO NOT #4:
##           «НЕ храни секреты/токены/ключи в файлах проекта»). Сканирует рекурсивно
##           (environment dict/list, build.args, labels) — ключи по суффиксу
##           (password|api_key|token) с не-интерполированным значением.
## @io       ⇥ services: dict → ⎋ list[_RawFinding] (0..N — по одному на сервис/ключ)
## @complexity O(K) где K = число ключей compose
def _check_secrets_in_compose(services: dict[str, object]) -> list[_RawFinding]:
    """L1: literal secret values (password:/api_key:/token:) → violation."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        for path, key, value in _iter_secret_literals(svc):
            findings.append(
                _RawFinding(
                    "secrets-in-compose",
                    KLASS_L1,
                    f"service '{svc_name}': literal secret '{key}' = '{value}' at {path} — "
                    f"только ${'{VAR}'} интерполяция (секреты НЕ в compose, см. секцию «Контракт окружения проекта» root AGENTS.md)",
                )
            )
    return findings


# endregion CONTRACT_secrets_in_compose


# region CONTRACT_ports_published
## @purpose  L1 ports-published: в services НЕ должно быть `ports:` — ingress/TLS делает
##           nginx-модуль платформы (сеть proxy-net, external). `expose:` — OK (внутренний,
##           не публикует host-порт). Инвариант секции «Контракт окружения проекта» root AGENTS.md (DO NOT #2).
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S) где S = число сервисов
def _check_ports_published(services: dict[str, object]) -> list[_RawFinding]:
    """L1: services.*.ports (host-port publication) → violation; expose: OK."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        ports = svc.get("ports")
        if ports:
            findings.append(
                _RawFinding(
                    "ports-published",
                    KLASS_L1,
                    f"service '{svc_name}' publishes ports: {ports} — host-порты запрещены "
                    f"(ingress = nginx platform proxy-net, см. секцию «Контракт окружения проекта» root AGENTS.md DO NOT #2); expose: — OK",
                )
            )
    return findings


# endregion CONTRACT_ports_published


# region CONTRACT_healthcheck_present
## @purpose  L1 healthcheck-present: каждый service имеет `healthcheck:` ИЛИ
##           `labels: platform.healthcheck=...` (два канона, DevPlan 137 §7 риск «ложный блок»).
##           СТАТИЧЕСКАЯ проверка наличия ключа — runtime-healthcheck (inspect) — отдельная
##           фаза deploy_engine/healthcheck_poller (канон НЕ дублируется, §5 W4 п.4).
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S)
def _check_healthcheck_present(services: dict[str, object]) -> list[_RawFinding]:
    """L1: service без healthcheck: И без labels platform.healthcheck → violation."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        if "healthcheck" in svc:
            continue
        labels = _normalize_labels(svc.get("labels"))
        if "platform.healthcheck" in labels:
            continue
        findings.append(
            _RawFinding(
                "healthcheck-present",
                KLASS_L1,
                f"service '{svc_name}' не имеет healthcheck: (или labels: platform.healthcheck=...) — "
                f"healthcheck обязателен (канон healthcheck_poller, DevPlan 137 §5 W4)",
            )
        )
    return findings


# endregion CONTRACT_healthcheck_present


# region CONTRACT_external_networks
## @purpose  L1 external-networks: networks[NAME].external: true — ТОЛЬКО из канона
##           allowed_external_networks (practices_manifest.yaml, НЕ хардкод — TRAP §10.2).
##           Кастомная external-сеть → violation (проект не должен подключаться к чужим сетям).
## @io       ⇥ networks: Any, allowlist: tuple[str, ...] → ⎋ list[_RawFinding]
## @complexity O(N) где N = число сетей
def _check_external_networks(networks: object, allowlist: tuple[str, ...]) -> list[_RawFinding]:
    """L1: external:true сеть вне allowed_external_networks канона → violation."""
    findings: list[_RawFinding] = []
    if not isinstance(networks, dict):
        return findings
    for net_name, conf in networks.items():
        if not isinstance(conf, dict):
            continue
        ext = conf.get("external")
        is_external = ext is True or (isinstance(ext, str) and ext.lower() == "true")
        if is_external and net_name not in allowlist:
            findings.append(
                _RawFinding(
                    "external-networks",
                    KLASS_L1,
                    f"external network '{net_name}' вне allowlist канона {sorted(allowlist)} — "
                    f"разрешены только сети платформы (practices_manifest.yaml allowed_external_networks); "
                    f"добавление сети модуля = правка канона",
                )
            )
    return findings


# endregion CONTRACT_external_networks


# region CONTRACT_env_file_contract
## @purpose  L1 env-file-contract: env_file: = .env.platform (НЕ .env, НЕ абсолютный путь).
##           .env.platform — единственный машиночитаемый источник hosts/ports/DSN/URL
##           (см. секцию «Контракт окружения проекта» root AGENTS.md). Секреты проекта —
##           только .platform-db.env на ноде (0600, вне payload), не в .env.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S * E) где E = env_file записей
def _check_env_file(services: dict[str, object]) -> list[_RawFinding]:
    """L1: env_file ≠ .env.platform (или абсолютный путь) → violation."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        env_file = svc.get("env_file")
        if env_file is None:
            continue
        files: list[object] = [env_file] if isinstance(env_file, str) else list(env_file or [])
        for item in files:
            fname = str(item)
            if fname != ENV_FILE_PLATFORM:
                findings.append(
                    _RawFinding(
                        "env-file-contract",
                        KLASS_L1,
                        f"service '{svc_name}': env_file: {fname} — допустим только '{ENV_FILE_PLATFORM}' "
                        f"(не .env, не абсолютный путь; секреты — .platform-db.env на ноде)",
                    )
                )
    return findings


# endregion CONTRACT_env_file_contract


# 🧐 TRAP[DECISION] · 2026-08-05 · — · platform-labels: match по platform.* префиксу, не строгий набор
# · Rejected: строгая проверка platform.project + platform.module (таблица §5 W4) — ложноблокировала
# ·   собственные scaffold-проекты (шаблоны используют platform.type + platform.domain, проверено 2026-08-05)
# · Reason: реальная платформенная идентификация — platform.* группа; intent плана сохранён.
# ·   Правка плана (таблица §5 W4 platform-labels) — на оркестратора/архитектора.
# · Rev: если появится канонический набор platform-лейблов scaffold → ужесточить до точного набора.
# region CONTRACT_platform_labels
## @purpose  L1 platform-labels: каждый service несёт platform.* labels (платформенная
##           идентификация сервиса: platform.type/platform.domain/platform.project/
##           platform.module/platform.healthcheck).
##           ⚠️ TRAP[DECISION] — план-таблица §5 W4 указывала строго platform.project +
##           platform.module, но реальные шаблоны (template-{backend,frontend}) и
##           scaffold используют platform.type + platform.domain (проверено 2026-08-05) —
##           строгий набор ложноблокировал бы собственные scaffold-проекты платформы (риск
##           §7 HI). Имплементация: наличие ≥1 platform.* label (семантическая группа),
##           intent плана (сервис идентифицирован платформой) сохранён.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S)
def _check_platform_labels(services: dict[str, object]) -> list[_RawFinding]:
    """L1: service без platform.* labels → violation (идентификация сервиса платформой)."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        labels = _normalize_labels(svc.get("labels"))
        if not any(str(k).startswith(_PLATFORM_LABEL_PREFIX) for k in labels):
            findings.append(
                _RawFinding(
                    "platform-labels",
                    KLASS_L1,
                    f"service '{svc_name}' не имеет platform.* labels (platform.type/platform.domain/"
                    f"platform.project/...) — обязательная платформенная идентификация сервиса",
                )
            )
    return findings


# endregion CONTRACT_platform_labels


# 🧐 TRAP[DECISION] · 2026-08-13 · — · limits-present: проверка НАЛИЧИЯ, не значений
# · Rejected: валидация конкретных значений (memory == "128M", cpus == "0.25" — DevPlan 162 W4-3)
# · Reason: план задаёт целевые значения 128M/0.25CPU, но реальные проекты (тяжёлые
# ·   сервисы) могут обоснованно требовать больше; K3/L1-контракт = защита платформы от
# ·   контейнера БЕЗ лимита (OOM-риск общего стека), не диктат размера. Жёсткая сверка значений
# ·   ложноблокировала бы легаси-деплои (риск HI, паттерн TRAP §10.2). Лимиты >0 = OOM-защита.
# · Rev: если оператор введёт канонические размеры лимитов для классов проектов — ужесточить
# ·   до value-контракта (отдельный L2-чек, не расширять L1).
# region CONTRACT_limits_present
## @purpose  L1 limits-present (DevPlan 162 W4-3, K3/L1-практики): каждый service обязан иметь
##           deploy.resources.limits.memory И deploy.resources.limits.cpus. Контейнер без
##           memory/cpu лимитов — OOM-риск общего стека (суммарные лимиты платформы ~10.5G >
##           7.8G RAM ноды); PidsLimit 256 платформенным модулям. Проверяет НАЛИЧИЕ ключей
##           (не значения) — защита от незалимиченного контейнера.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S) где S = число сервисов
## @invariants
##   - Для КАЖДОГО сервиса: deploy.resources.limits.memory и .cpus обязаны присутствовать
##   - Значения НЕ валидируются (TRAP[DECISION] выше — проекты с большими лимитами
##     не блокируются; наличие лимита = OOM-защита)
##   - init/one-shot сервисы (restart: "no") — НЕ исключаются: лимиты применяются и к ним
def _check_limits_present(services: dict[str, object]) -> list[_RawFinding]:
    """L1: service без deploy.resources.limits.memory ИЛИ .cpus → violation (OOM-риск, 162 W4-3)."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        deploy = svc.get("deploy")
        resources = deploy.get("resources") if isinstance(deploy, dict) else None
        limits = resources.get("limits") if isinstance(resources, dict) else None
        has_memory = isinstance(limits, dict) and "memory" in limits
        has_cpus = isinstance(limits, dict) and "cpus" in limits
        if has_memory and has_cpus:
            logger.info(
                "[IMP:8][verify_contracts][limits] service '%s' limits present (memory=%s, cpus=%s)",
                svc_name,
                limits.get("memory"),
                limits.get("cpus"),
            )
            continue
        missing = [name for name, present in (("memory", has_memory), ("cpus", has_cpus)) if not present]
        findings.append(
            _RawFinding(
                "limits-present",
                KLASS_L1,
                f"service '{svc_name}': отсутствует deploy.resources.limits.{' и '.join(missing)} — "
                f"лимиты обязательны (OOM-защита стека, DevPlan 162 W4-3: 128M/0.25CPU проектам, "
                f"K3/L1-практики)",
            )
        )
    return findings


# endregion CONTRACT_limits_present


# ⚠️ TRAP[BUG] · 2026-08-16 · P0 · C1 root-эскалация: ci-deploy (docker-группа) исполнял произвольный
# · compose ДО любых L1-проверок (K3 только пост-деплой в verify-verb) — владелец CI_DEPLOY_KEY
# · слал privileged:true + /:/host → root ноды
# · Symptom: receive_flow.py:373 (orchestrator.deploy) без единой проверки привилегий;
# ·   в 7 L1-контрактах НЕ было privileged/cap_add/devices (верифицировано роем 2026-08-16)
# · Root: pre-up гейт отсутствовал в receive-канале; L1-набор не покрывал капабилити-векторы
# · Fix: L1 privileged/cap_add/devices (A.1) + pre-up L1-гейт в receive_flow (A.2) — блок ДО
# ·   orchestrator.deploy (контейнеры не запускаются); SKIP_PREFLIGHT=1 НЕ применим к receive
# ·   (это security-гейт, обход = та же дыра; SKIP остаётся скоупом `make up` платформенных модулей)
# · Prevention: новые капабилити-векторы compose (pid:, sysctls, userns_mode) — в R5-negative-набор
# region CONTRACT_privileged
## @purpose  L1 privileged (DevPlan 176 A.1, C1): services.*.privileged: true → violation.
##           Привилегированный контейнер = root-эквивалент ноды (privileged:true + /:/host —
##           точный вектор находки C1). Проектам привилегии запрещены ПОЛНОСТЬЮ; платформенные
##           модули — через gated allowlist вне скоупа этого контракта (если понадобится).
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S) где S = число сервисов
## @invariants
##   - privileged: true / "true" / "1" / "yes" (truthy) → violation
##   - privileged: false / null → OK (docker-дефолт, привилегий не даёт — НЕ блокируем)
def _check_privileged(services: dict[str, object]) -> list[_RawFinding]:
    """L1: services.*.privileged (truthy) → violation (root-контейнеры проектам запрещены, C1)."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        privileged = svc.get("privileged")
        if _is_truthy(privileged):
            findings.append(
                _RawFinding(
                    "privileged",
                    KLASS_L1,
                    f"service '{svc_name}': privileged: {privileged} — привилегированные контейнеры "
                    f"проектам запрещены полностью (root-эскалация C1, DevPlan 176 A.1)",
                )
            )
    return findings


# endregion CONTRACT_privileged


# region CONTRACT_cap_add
## @purpose  L1 cap-add (DevPlan 176 A.1, C1): services.*.cap_add — ЛЮБОЕ присутствие ключа
##           → violation (добавление Linux-капабилити контейнеру — привилегий-вектор;
##           проектам запрещено полностью). Пустой список тоже блокирует: явное намерение
##           манипулировать капабилити — вне контракта проектов.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S) где S = число сервисов
## @invariants
##   - cap_add присутствует (значение ≠ None, включая []) → violation
##   - cap_add: null → OK (compose трактует как отсутствие ключа)
def _check_cap_add(services: dict[str, object]) -> list[_RawFinding]:
    """L1: services.*.cap_add (любое значение) → violation (капабилити проектам запрещены, C1)."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        cap_add = svc.get("cap_add")
        if cap_add is not None:
            findings.append(
                _RawFinding(
                    "cap-add",
                    KLASS_L1,
                    f"service '{svc_name}': cap_add: {cap_add} — Linux-капабилити проектам "
                    f"запрещены полностью (root-эскалация C1, DevPlan 176 A.1)",
                )
            )
    return findings


# endregion CONTRACT_cap_add


# region CONTRACT_devices
## @purpose  L1 devices (DevPlan 176 A.1, C1): services.*.devices — ЛЮБОЕ присутствие ключа
##           → violation (маппинг host-устройств в контейнер — прямой доступ к ноде,
##           напр. /dev/sda; root-эскалация C1). Проектам запрещено полностью.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S) где S = число сервисов
## @invariants
##   - devices присутствует (значение ≠ None, включая []) → violation
##   - devices: null → OK (compose трактует как отсутствие ключа)
def _check_devices(services: dict[str, object]) -> list[_RawFinding]:
    """L1: services.*.devices (любое значение) → violation (host-устройства проектам запрещены, C1)."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        devices = svc.get("devices")
        if devices is not None:
            findings.append(
                _RawFinding(
                    "devices",
                    KLASS_L1,
                    f"service '{svc_name}': devices: {devices} — host-устройства проектам "
                    f"запрещены полностью (root-эскалация C1, DevPlan 176 A.1)",
                )
            )
    return findings


# endregion CONTRACT_devices


# region CONTRACT_security_opt
## @purpose  L1 security-opt (DevPlan 16 T1.E / P1-9): seccomp=unconfined (и apparmor=unconfined)
##           в ЛЮБОЙ форме — list[str] И dict — violation: отключение seccomp-профиля = снятие
##           kernel-сандрбокса контейнера. Детект case-insensitive по нормализованным парам
##           key=value.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S * O) где O = опции сервиса
## @invariants
##   - list[str]-форма ("seccomp=unconfined" | "seccomp:unconfined") нормируется к key=value
##   - dict-форма ({seccomp: unconfined}) — точный вход аудита 15 P1-9, ранее не детектилась
def _check_security_opt(services: dict[str, object]) -> list[_RawFinding]:
    """L1: services.*.security_opt с unconfined-профилем (list или dict форма) → violation."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        raw_opts = svc.get("security_opt")
        if raw_opts is None:
            continue
        # Нормализация обеих форм → список пар key=value (case-insensitive детект ниже)
        pairs: list[tuple[str, str]] = []
        if isinstance(raw_opts, list):
            for entry in raw_opts:
                if isinstance(entry, str):
                    key, _, value = entry.replace(":", "=").partition("=")
                    pairs.append((key.strip().lower(), value.strip().lower()))
        elif isinstance(raw_opts, dict):
            for key, value in raw_opts.items():
                pairs.append((str(key).strip().lower(), str(value).strip().lower()))
        else:
            findings.append(
                _RawFinding(
                    "security-opt",
                    KLASS_L1,
                    f"service '{svc_name}': security_opt неожидаемой формы {raw_opts!r} (fail-closed, DevPlan 16 T1.E)",
                )
            )
            continue
        for opt_key, opt_value in pairs:
            if opt_value == "unconfined":
                findings.append(
                    _RawFinding(
                        "security-opt",
                        KLASS_L1,
                        f"service '{svc_name}': security_opt {opt_key}=unconfined — отключение "
                        f"профиля изоляции запрещено (kernel-сандрбокс снят, DevPlan 16 T1.E)",
                    )
                )
    return findings


# endregion CONTRACT_security_opt


# region CONTRACT_device_reservations
## @purpose  L1 device-reservations (DevPlan 16 T1.E / P1-10): GPU/device-доступ мимо закрытого
##           service-level `devices` — deploy.resources.reservations.devices[*] (любой device,
##           включая GPU), top-level/service gpus:, device_cgroup_rules → violation.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S) где S = число сервисов
## @invariants
##   - Любой элемент reservations.devices (пустой dict тоже) → violation (device-доступ)
##   - top-level gpus: (compose swarm-форма) и сервисный gpus: — оба вектор
##   - device_cgroup_rules (bpf/cgroup-доступ к устройствам) → violation
def _check_device_reservations(services: dict[str, object]) -> list[_RawFinding]:
    """L1: GPU/device-reservation векторы (reservations.devices / gpus / device_cgroup_rules)."""
    findings: list[_RawFinding] = []

    def _reserve_violations(svc_name: str, deploy_cfg: object) -> list[str]:
        msgs: list[str] = []
        if not isinstance(deploy_cfg, dict):
            return msgs
        resources = deploy_cfg.get("resources")
        if not isinstance(resources, dict):
            return msgs
        reservations = resources.get("reservations")
        if not isinstance(reservations, dict):
            return msgs
        devices = reservations.get("devices")
        if devices:
            if isinstance(devices, list) and len(devices) > 0:
                msgs.append(
                    f"service '{svc_name}': deploy.resources.reservations.devices[{len(devices)}] "
                    f"— device-reservation (GPU и пр.) проектам запрещена (DevPlan 16 T1.E)"
                )
            elif not isinstance(devices, list):
                msgs.append(f"service '{svc_name}': reservations.devices неожидаемой формы {devices!r}")
        return msgs

    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        findings.extend(
            _RawFinding("device-reservations", KLASS_L1, msg)
            for msg in _reserve_violations(svc_name, svc.get("deploy"))
        )
        if svc.get("gpus") is not None:
            findings.append(
                _RawFinding(
                    "device-reservations",
                    KLASS_L1,
                    f"service '{svc_name}': gpus: {svc['gpus']!r} — GPU-доступ проектам запрещён (DevPlan 16 T1.E)",
                )
            )
        if svc.get("device_cgroup_rules") is not None:
            findings.append(
                _RawFinding(
                    "device-reservations",
                    KLASS_L1,
                    f"service '{svc_name}': device_cgroup_rules присутствует — cgroup-доступ к "
                    f"устройствам запрещён (DevPlan 16 T1.E)",
                )
            )
    return findings


# endregion CONTRACT_device_reservations


# 🧐 TRAP[DECISION] · 2026-08-25 · HI · SEC-0013 residual: ci-deploy остаётся в группе docker
# · Rejected: socket-proxy/rootless-docker/per-project docker-изоляция (multi-tenant hardening)
# · Reason: freeze P3 п.16 (launch week) — flat-trust модель задокументирована; deny-set выше
# ·   закрывает ВЕКТОР (compose с docker.sock), но не саму способность docker-группы;
# ·   владелец CI_DEPLOY_KEY при компрометации сохраняет docker-доступ вне payload-канала.
# · Rev: первый multi-tenant инцидент ИЛИ пост-launch окно → socket-proxy/rootless
# region CONTRACT_dangerous_volumes
## @purpose  L1 dangerous-volumes (REF-0006, DevPlan 11 В2, SEC-0011/SEC-0030): volumes сервисов
##           НЕ содержат (а) socket/docker-маунтов (/var/run/docker.sock и пр. — root-эквивалент:
##           docker API ноды = все секреты + root в контейнерах), (б) абсолютных host-binds вне
##           минимального allowlist (_ALLOWED_ABSOLUTE_HOST_BINDS), (в) относительных host-paths
##           (./ ../ — traversal за пределы проектного каталога) и не-статически-резолвимых
##           источников (${VAR} — fail-closed). Персистентность проектов — ТОЛЬКО named volumes /
##           anonymous container-only volumes. Обрабатывает short-syntax ("- vol:/data") и
##           long-syntax ({type: bind|volume, source: ...}) формы.
## @io       ⇥ services: dict → ⎋ list[_RawFinding] (0..N — по одному на нарушающий entry)
## @complexity O(S*V) где S = сервисы, V = volume-записи
## @invariants
##   - Матчится ТОЛЬКО source (host-сторона) маунта; container-path ("/data" target) не триггерит
##   - Socket-source: точное совпадение или вложение в каталог-префикс deny-set
##   - Named volume ([A-Za-z0-9][A-Za-z0-9_.-]*) и bare container-path (anonymous) — OK
##   - tmpfs / source-less long-syntax — OK (host-FS не затрагивается)
def _check_dangerous_volumes(services: dict[str, object]) -> list[_RawFinding]:
    """L1: socket-mounts / absolute host-binds outside allowlist / non-named sources → violation."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        volumes = svc.get("volumes")
        if volumes is None:
            continue
        entries: list[object]
        if isinstance(volumes, list):
            entries = list(volumes)
        elif isinstance(volumes, dict):
            # long-syntax map-форма не канонична для compose services.volumes, но fail-open на
            # неожиданной форме недопустим: обходим значения как long-syntax записи
            entries = list(volumes.values())
        else:
            findings.append(
                _RawFinding(
                    "dangerous-volumes",
                    KLASS_L1,
                    f"service '{svc_name}': volumes неожидаемой формы {volumes!r} — "
                    f"named volumes обязателен (fail-closed, REF-0006)",
                )
            )
            continue
        for entry in entries:
            message = _volume_entry_violation(svc_name, entry)
            if message is not None:
                findings.append(_RawFinding("dangerous-volumes", KLASS_L1, message))
    return findings


# endregion CONTRACT_dangerous_volumes


# region FUNC__volume_entry_violation
## @purpose  Классификация ОДНОЙ volume-записи → текст violation или None (OK).
##           Short-syntax строки парсятся по ":" (mode-суффиксы ro/rw/z/Z/nocopy отсекаются);
##           long-syntax dict — по type/source. Единая точка правил REF-0006.
## @io       ⇥ svc_name: str, entry: Any → ⎋ str | None
## @complexity O(1)
def _volume_entry_violation(svc_name: str, entry: object) -> str | None:
    """Return violation message for a single volumes entry, or None if allowed."""
    if isinstance(entry, dict):
        return _long_syntax_volume_violation(svc_name, entry)
    if not isinstance(entry, str):
        return (
            f"service '{svc_name}': volumes entry {entry!r} не строка/dict — "
            f"named volumes обязателен (fail-closed, REF-0006)"
        )
    raw = entry.strip()
    parts = [p for p in raw.split(":") if p]  # mode-суффиксы остаются последним элементом
    # bare container-path (anonymous volume) или named volume без target — host-FS не затронут
    min_mount_parts = 2  # source + container-target
    if len(parts) < min_mount_parts:
        return None
    source = parts[0]
    return _classify_volume_source(svc_name, raw, source)


# endregion FUNC__volume_entry_violation


# region FUNC__long_syntax_volume_violation
## @purpose  Long-syntax volume-запись ({type:, source:, target:, bind:,...}): tmpfs/source-less
##           — OK; иначе та же классификация source, что short-syntax.
## @io       ⇥ svc_name: str, entry: dict → ⎋ str | None
## @complexity O(1)
def _long_syntax_volume_violation(svc_name: str, entry: dict[str, object]) -> str | None:
    """Classify long-syntax volumes entry (type/source)."""
    vtype = entry.get("type")
    if isinstance(vtype, str) and vtype.strip().lower() == "tmpfs":
        return None
    source = entry.get("source")
    if source is None or (isinstance(source, str) and not source.strip()):
        return None  # anonymous volume — host-FS не затронут
    return _classify_volume_source(svc_name, str(source), str(source))


# endregion FUNC__long_syntax_volume_violation


# region FUNC__classify_volume_source
## @purpose  Правила REF-0006 для источника маунта (порядок: socket → absolute-bind →
##           relative-traversal → unresolved-var → named-volume regex fail-closed).
## @io       ⇥ svc_name: str, raw: str (исходная запись — для сообщения), source: str
##           → ⎋ str | None
## @complexity O(D) где D = |_DANGEROUS_SOCKET_SOURCES|
def _classify_volume_source(svc_name: str, raw: str, source: str) -> str | None:
    """Apply REF-0006 rules to a mount source. Returns violation message or None."""

    def _deny(reason: str) -> str:
        return (
            f"service '{svc_name}': volumes '{raw}' — {reason}; персистентность проектов — "
            f"ТОЛЬКО named volumes (REF-0006/SEC-0011: bind host-path = доступ к ФС ноды)"
        )

    normalized = os.path.normpath(source).rstrip("/")
    # 1. Socket/docker маунты — deny безусловно (точное совпадение или каталог-префикс)
    for denied in _DANGEROUS_SOCKET_SOURCES:
        d = denied.rstrip("/")
        if normalized == d or normalized.startswith(d + "/"):
            return _deny(f"socket/docker маунт '{source}' запрещён (docker API = root ноды)")
    # 2. Абсолютные host-binds — только из минимального allowlist
    if source.startswith("/"):
        for allowed in sorted(_ALLOWED_ABSOLUTE_HOST_BINDS):
            a = os.path.normpath(allowed).rstrip("/")
            if normalized == a or normalized.startswith(a + "/"):
                return None
        return _deny(f"абсолютный host-bind '{source}' вне allowlist (_ALLOWED_ABSOLUTE_HOST_BINDS)")
    # 3. Относительные host-paths — traversal за пределы проектного каталога
    if source.startswith((".", "..")):
        return _deny(f"относительный host-path '{source}' (traversal) запрещён")
    # 4. Не-статически-резолвимые источники (${VAR}, команды) — fail-closed
    if "$" in source or "`" in source:
        return _deny(f"источник '{source}' не резолвится статически")
    # 5. Named volume — каноническая персистентность; всё остальное — fail-closed
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", source):
        return _deny(f"источник '{source}' не является named volume")
    return None


# endregion FUNC__classify_volume_source


# region CONTRACT_host_mode_keys
## @purpose  L1 host-mode-keys (REF-0006, DevPlan 11 В2 + QA C3 DevPlan 14 T1.2):
##           namespace/isolation-ключи compose → violation:
##           network_mode/pid/userns_mode/cgroup/ipc/uts == host (шаринг namespace ноды =
##           root-эквивалент); cgroup_parent/sysctls — присутствие ключа → violation
##           (kernel-контур вне контракта проектов); security_opt содержащий unconfined
##           (seccomp/apparmor/systempaths off = root-эквивалент); volumes_from — присутствие
##           (наследование чужих маунтов обходит dangerous-volumes скана; паритет cap_add/devices).
## @io       ⇥ services: dict → ⎋ list[_RawFinding] (0..N — по одному на ключ/значение)
## @complexity O(S*K) где S = сервисы, K = контролируемые ключи (6 value + 2 presence + 2 isolation)
## @invariants
##   - Значение сравнивается case-insensitively со "host"; прочие значения ("bridge",
##     "none", "container:x", "service:x") — OK (host-namespace не шарится)
##   - cgroup_parent/sysctls/volumes_from: присутствие (≠ None, включая {}) → violation
##   - security_opt: любой элемент строки содержащий "unconfined" (case-insensitive) → violation
def _check_host_mode_keys(services: dict[str, object]) -> list[_RawFinding]:
    """L1: network_mode/pid/userns_mode/cgroup == host, cgroup_parent/sysctls present → violation."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        for key, forbidden in _HOST_MODE_VALUE_KEYS:
            value = svc.get(key)
            if isinstance(value, str) and value.strip().lower() == forbidden:
                findings.append(
                    _RawFinding(
                        "host-mode-keys",
                        KLASS_L1,
                        f"service '{svc_name}': {key}: {value} — host-namespace доступ проектам "
                        f"запрещён (root-эквивалент ноды, REF-0006)",
                    )
                )
        for presence_key in ("cgroup_parent", "sysctls"):
            value = svc.get(presence_key)
            if value is not None:
                findings.append(
                    _RawFinding(
                        "host-mode-keys",
                        KLASS_L1,
                        f"service '{svc_name}': {presence_key}: {value} — kernel-контур ноды "
                        f"проектам запрещён (паритет cap_add/devices, REF-0006)",
                    )
                )
        # QA C3 (DevPlan 14 T1.2): security_opt с unconfined снимает seccomp/apparmor/
        # systempaths-профили (root-эквивалент); volumes_from наследует чужие маунты целиком
        # (обход per-service dangerous-volumes скана) — паритет cap_add/devices.
        security_opt = svc.get("security_opt")
        if security_opt is not None:
            opt_values: list[object] = security_opt if isinstance(security_opt, list) else [security_opt]
            findings.extend(
                _RawFinding(
                    "host-mode-keys",
                    KLASS_L1,
                    f"service '{svc_name}': security_opt '{item}' — unconfined профили "
                    "запрещены проектам (seccomp/apparmor/systempaths off = root-эквивалент, REF-0006)",
                )
                for item in opt_values
                if isinstance(item, str) and "unconfined" in item.lower()
            )
        volumes_from = svc.get("volumes_from")
        if volumes_from is not None:
            findings.append(
                _RawFinding(
                    "host-mode-keys",
                    KLASS_L1,
                    f"service '{svc_name}': volumes_from: {volumes_from!r} — наследование чужих "
                    "маунтов запрещено (обход dangerous-volumes скана, паритет cap_add/devices, REF-0006)",
                )
            )
    return findings


# endregion CONTRACT_host_mode_keys


# region CONTRACT_top_level_volumes
## @purpose  L1 top-level volumes (QA C3, DevPlan 14 T1.2): named-volume ОПРЕДЕЛЕНИЕ с непустым
##           driver_opts → violation. Все вызывающие verify_project_contracts — проектный payload
##           (receive_flow.py/orchestrator.py/orchestrator_cli.py, l1_only=True): платформенный
##           стек (postgres driver_opts-bind'ы) этим путём НЕ сканируется, легитимных
##           bind-driver_opts у проектов нет по канону персистентности («named docker-managed
##           volume») — правило «присутствие → violation» паритетно cap_add/devices и не требует
##           эвристик путей. Вектор C3: {driver: local, driver_opts:{type:none,o:bind,
##           device:/var/run/docker.sock}} + сервисный named-ref «sock» проходил services-scan —
##           ловится здесь; имя device включается в сообщение (defense-in-depth).
## @io       ⇥ top_volumes: Any (compose data["volumes"]) → ⎋ list[_RawFinding]
## @complexity O(V) где V = именованные определения
## @invariants
##   - driver_opts присутствует и truthy ({}/None/"" — docker-managed определение, OK) → violation
##   - Не-dict определения пропускаются (compose-config-valid отловит структурный мусор отдельно)
def _check_top_level_volumes(top_volumes: object) -> list[_RawFinding]:
    """L1: top-level named-volume definition with non-empty driver_opts → violation."""
    logger.info("[IMP:8][verify_contracts][top-volumes] scanning %s", type(top_volumes).__name__)
    findings: list[_RawFinding] = []
    if not isinstance(top_volumes, dict):
        return findings
    for vol_name, definition in top_volumes.items():
        if not isinstance(definition, dict):
            continue
        driver_opts = definition.get("driver_opts")
        if not driver_opts:  # None / {} / "" — легитимное docker-managed определение
            continue
        device = driver_opts.get("device") if isinstance(driver_opts, dict) else None
        device_note = f" (device: {device!r})" if isinstance(device, str) and device.strip() else ""
        logger.info(
            "[IMP:9][verify_contracts][top-volumes] violation: volume=%r driver_opts keys=%s",
            vol_name,
            sorted(driver_opts) if isinstance(driver_opts, dict) else type(driver_opts).__name__,
        )
        findings.append(
            _RawFinding(
                "dangerous-volumes",
                KLASS_L1,
                f"top-level volume '{vol_name}': driver_opts{device_note} запрещены проектам — "
                "bind через определение named volume обходит services-scan "
                "(QA C3/REF-0006); персистентность проектов — ТОЛЬКО docker-managed named volumes",
            )
        )
    return findings


# endregion CONTRACT_top_level_volumes


# region FUNC__is_truthy
## @purpose  Truthy-нормализация compose-значения (privileged: bool/str) — true|"true"|"1"|"yes"|"on".
##           Паритет паттерну external-networks (ext is True or str.lower() == "true"), расширен
##           на compose-канонические строковые формы.
## @io       ⇥ value: Any → ⎋ bool
## @complexity O(1)
def _is_truthy(value: object) -> bool:
    """True для compose-truthy значений (bool True / 'true' / '1' / 'yes' / 'on')."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


# endregion FUNC__is_truthy


# ═══════════════════════════════════════════════════════════════════
# L2-контракты (качество — блок только в active-full)
# ═══════════════════════════════════════════════════════════════════


# 🧐 TRAP[DECISION] · 2026-08-05 · — · docker-отсутствие → skip (не warning) для L2 docker-контрактов
# · Rejected: WARN-finding при отсутствии бинарника docker (check_project семантика missing tool → WARN)
# · Reason: verify_contracts исполняется НА VPS, где docker гарантирован bootstrap'ом (φ1/φ8);
# ·   отсутствие = тестовый/dev-контекст, где WARN шумел бы в format_for_ssh и ломал
# ·   детерминизм baseline-green теста (0 violations). Реальная неработоспособность docker
# ·   на VPS ловится фазой deploy (compose up) — не verify.
# · Rev: если L2 docker-контракты станут обязательной частью деплой-гейта на VPS →
# ·   вернуть WARN-finding при отсутствии docker.
# region CONTRACT_compose_config_valid
## @purpose  L2 compose-config-valid: `docker compose config --quiet` exit 0 (валидный синтаксис
##           + резолв). На VPS docker гарантирован (bootstrap) — отсутствие бинарника = skip
##           (тестовая среда), НЕ warning (иначе легаси-деплои в тесте шумят).
## @io       ⇥ project_dir: Path, facts: EnvironmentFacts | None (which docker DI) → ⎋ list[_RawFinding]
## @complexity O(1) + subprocess docker compose config
def _check_compose_config_validate(project_dir: Path, facts: EnvironmentFacts | None = None) -> list[_RawFinding]:
    """L2: docker compose config --quiet (статическая валидность compose)."""
    if (facts or default_env_facts()).which("docker") is None:
        logger.info("[IMP:7][verify_contracts][compose-config] docker not available — check skipped")
        return []
    rc, out, err = _run_docker(["docker", "compose", "config", "--quiet"], project_dir, _COMPOSE_CONFIG_TIMEOUT)
    if rc == 0:
        return []
    return [
        _RawFinding(
            "compose-config-valid",
            KLASS_L2,
            f"docker compose config --quiet failed (rc={rc}): {_tail(err or out)}",
        )
    ]


# endregion CONTRACT_compose_config_valid


# region CONTRACT_drift_practices
## @purpose  L2 drift-practices: на VPS practices.lock отсутствует (unmanaged) ИЛИ
##           lock.version < версии канона на ноде → [PRACTICES:DRIFT-VERSION] warning
##           (файловый дрейф проверяется локально K1 и в CI K2 — там полный checkout; на VPS
##           lock — единственный носитель state, DevPlan 137 §5 W4). Локальный ремонт:
##           make project-sync-practices.
## @io       ⇥ lock: PracticesLock | None, canon_version: int → ⎋ list[_RawFinding]
## @complexity O(1)
def _check_drift_practices(lock: PracticesLock | None, canon_version: int) -> list[_RawFinding]:
    """L2: practices.lock missing/stale-version → warning (repair: project-sync-practices)."""
    if lock is None:
        return [
            _RawFinding(
                "drift-practices",
                KLASS_L2,
                "practices.lock not found (unmanaged) — state неизвестен; run: make project-sync-practices "
                "(или make adopt-project для существующего проекта)",
            )
        ]
    if lock.version < canon_version:
        return [
            _RawFinding(
                "drift-practices",
                KLASS_L2,
                f"practices.lock version {lock.version} < canon {canon_version} — [PRACTICES:DRIFT-VERSION]; "
                f"run: make project-sync-practices",
            )
        ]
    return []


# endregion CONTRACT_drift_practices


# region CONTRACT_build_check
## @purpose  L2 build-check: `docker build --check` (BuildKit статический) если есть Dockerfile.
##           Docker-бинарник отсутствует → skip (на VPS гарантирован).
## @io       ⇥ project_dir: Path, facts: EnvironmentFacts | None (which docker DI) → ⎋ list[_RawFinding]
## @complexity O(1) + subprocess docker build --check
def _check_build_check(project_dir: Path, facts: EnvironmentFacts | None = None) -> list[_RawFinding]:
    """L2: docker build --check (BuildKit static) при наличии Dockerfile."""
    if not (project_dir / "Dockerfile").is_file():
        return []
    if (facts or default_env_facts()).which("docker") is None:
        logger.info("[IMP:7][verify_contracts][build-check] docker not available — check skipped")
        return []
    rc, out, err = _run_docker(["docker", "build", "--check", "."], project_dir, _BUILD_CHECK_TIMEOUT)
    if rc == 0:
        return []
    return [
        _RawFinding(
            "build-check",
            KLASS_L2,
            f"docker build --check failed (rc={rc}): {_tail(err or out)}",
        )
    ]


# endregion CONTRACT_build_check


# region FUNC__run_docker
## @purpose  Безопасный subprocess docker (timeout, никогда не кидает на ненулевом rc).
## @io       ⇥ cmd, cwd, timeout → ⎋ tuple[int, str, str] (rc, stdout, stderr)
## @complexity O(1)
def _run_docker(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
    """Run docker subprocess with timeout; returns (rc, stdout, stderr) — never raises."""
    try:
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    else:
        return result.returncode, result.stdout, result.stderr


# endregion FUNC__run_docker


# region FUNC__iter_secret_literals
## @purpose  Рекурсивный обход compose-структуры: выдаёт (path, key, value) для ключей с
##           суффиксом password|api_key|token и НЕ-интерполированным значением. Обрабатывает
##           dict-форму (environment: {DB_PASSWORD: x}) и list-форму (environment: [DB_PASSWORD=x]).
## @io       ⇥ node: Any, path: str → ⎋ Iterator[(path, key, value)]
## @complexity O(K) где K = число ключей
def _iter_secret_literals(node: object, path: str = "") -> Iterable[tuple[str, str, str]]:
    """Yield (path, key, literal_value) for secret-suffixed keys with non-interpolated values."""
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            key_norm = _normalize_key(str(key))
            if _is_secret_key(key_norm):
                if isinstance(value, (str, int, float, bool)) and not _is_interpolated(value):
                    yield child_path, str(key), str(value)
            else:
                yield from _iter_secret_literals(value, child_path)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            child_path = f"{path}[{i}]"
            if isinstance(item, str) and "=" in item:
                key, _, val = item.partition("=")
                if _is_secret_key(_normalize_key(key)) and not _is_interpolated(val):
                    yield child_path, key, val
            yield from _iter_secret_literals(item, child_path)


# endregion FUNC__iter_secret_literals


# region FUNC__is_secret_key
## @purpose  Ключ является секретным: нормализованный ключ заканчивается на password|api_key|token
##           (покрывает POSTGRES_PASSWORD, db_password, auth_token — не только точные имена).
## @io       ⇥ key_norm: str → ⎋ bool
## @complexity O(1)
def _is_secret_key(key_norm: str) -> bool:
    """True если ключ (нормализованный) заканчивается на password|api_key|token."""
    return key_norm.endswith(_SECRET_KEY_SUFFIXES)


# endregion FUNC__is_secret_key


# region FUNC__is_interpolated
## @purpose  Значение интерполировано: содержит ${...} (compose-подстановка env). Литерал
##           БЕЗ ${ — потенциальный секрет (только ${VAR} допустим, §5 W4 контракт).
## @io       ⇥ value: Any → ⎋ bool
## @complexity O(1)
def _is_interpolated(value: object) -> bool:
    """True если значение содержит ${ (compose env interpolation) — не литерал."""
    return "${" in str(value)


# endregion FUNC__is_interpolated


# region FUNC__normalize_key
## @purpose  Нормализация ключа для суффикс-матчинга: lowercase + не-alnum → _.
## @io       ⇥ key: str → ⎋ str
## @complexity O(L) где L = len(key)
def _normalize_key(key: str) -> str:
    """Normalize key for suffix matching (lowercase, non-alnum → _)."""
    return re.sub(r"[^a-z0-9_]", "_", key.lower())


# endregion FUNC__normalize_key


# region FUNC__normalize_labels
## @purpose  Нормализация labels (dict-форма ИЛИ list "k=v" форма) → dict[str, str].
##           Единая точка для healthcheck-present/platform-labels контрактов.
## @io       ⇥ labels: Any → ⎋ dict[str, str]
## @complexity O(L) где L = число labels
def _normalize_labels(labels: object) -> dict[str, str]:
    """Normalize labels (dict | list of 'k=v') → dict."""
    if labels is None:
        return {}
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}
    if isinstance(labels, list):
        result: dict[str, str] = {}
        for item in labels:
            if isinstance(item, str) and "=" in item:
                key, _, value = item.partition("=")
                result[key.strip()] = value.strip()
        return result
    return {}


# endregion FUNC__normalize_labels


# region FUNC__audit_report
## @purpose  Аудит прогона через shared/audit_logger (единый writer, DevPlan 116 B11 T2):
##           event=verify_contracts, status BLOCKED|WARN|OK, findings (id/class/severity/message),
##           project/state. Best-effort: OSError → False (не блокирует деплой).
##           project_name (176 A.2): override project-поля аудита — pre-deploy gate receive
##           проверяет staging-dir, но аудит обязан нести РЕАЛЬНОЕ имя проекта.
## @io       ⇥ report: VerifyReport, log_file: str | None, project_name: str | None (override)
##           → ⎋ bool (True=записано)
## @complexity O(F)
def _audit_report(report: VerifyReport, log_file: str | None, project_name: str | None = None) -> bool:
    """Write audit entry (event=verify_contracts) — best-effort, never blocks deploy."""
    n_block = sum(1 for f in report.findings if f.severity == SEVERITY_BLOCK)
    status = "BLOCKED" if n_block else ("WARN" if report.has_warnings() else "OK")
    audit_project = project_name or report.project_dir.name
    try:
        ok = write_audit_entry(
            tag="verify_contracts",
            status=status,
            message=(
                f"verify contracts {audit_project}: {len(report.findings)} findings, "
                f"{n_block} blocking, state={report.state}"
            ),
            project=audit_project,
            state=report.state,
            findings=[f.to_dict() for f in report.findings],
            log_file=log_file or DEFAULT_LOG_FILE,
        )
    except OSError as exc:  # pragma: no cover — audit best-effort
        logger.warning("[IMP:7][verify_contracts][audit] write failed: %s", exc)
        return False
    logger.info("[IMP:8][verify_contracts][audit] written=%s status=%s", ok, status)
    return ok


# endregion FUNC__audit_report


# region FUNC__tail
## @purpose  Обрезка вывода команды для сообщений (bounded).
## @io       ⇥ text: str, limit: int → ⎋ str
## @complexity O(1)
def _tail(text: str, limit: int = 200) -> str:
    """Bound command output snippet for messages (first `limit` chars)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


# endregion FUNC__tail


# region FUNC_main
## @purpose  CLI для ручной диагностики K3: python3 -m core.internal.deploy.verify_contracts
##           --project-dir DIR [--audit-log FILE]. Печатает format_for_ssh, exit 0/1.
## @io       stdout: [PRACTICES:...] отчёт; stderr: LDD logs
## @exitcode 0 — нет блокирующих нарушений; 1 — L1-блок или L2/L3-блок в active-full
def main(argv: list[str] | None = None) -> int:
    """CLI for verify_contracts (manual K3 diagnostics; exit 0/1)."""
    logging.basicConfig(
        # getattr(logging, str) → Any (typeshed); уровень — int (logging.INFO) (W11)
        level=cast(int, getattr(logging, os.environ.get("LOG_LEVEL", "INFO"))),
        format="[%(levelname)s][verify_contracts] %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="Verify project contracts (K3, VPS)")
    parser.add_argument("--project-dir", required=True, type=str, help="Project directory to verify")
    parser.add_argument("--audit-log", type=str, default="", help="Audit log file (default: platform audit.jsonl)")
    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    from dataclasses import dataclass

    @dataclass
    class _CliArgs:
        project_dir: str
        audit_log: str

    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    try:
        report = verify_project_contracts(
            Path(args.project_dir),
            audit_log_file=args.audit_log or None,
        )
    except ConfigValidationError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return exc.exit_code
    except PlatformError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return exc.exit_code

    print(report.format_for_ssh())
    logger.info("[IMP:9][verify_contracts][main] exit=%d", report.exit_code)
    return report.exit_code


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
