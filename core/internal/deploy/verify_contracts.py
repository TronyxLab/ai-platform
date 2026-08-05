#!/usr/bin/env python3
# GREP_SUMMARY: verify-contracts, K3, VPS, L1, L2, L3, secrets-in-compose, ports-published, healthcheck-present, external-networks, env-file-contract, platform-labels, compose-config-valid, drift-practices, build-check, legacy-grace, PRACTICES-BLOCK, audit, allowlist
# STRUCTURE: ▶ verify_project_contracts(project_dir) → load_manifest (canon: allowed_external_networks) → read practices.lock (state) → resolve compose → 6×L1 статика (secrets/ports/healthcheck/networks/env-file/labels) → 3×L2 (compose config / drift-version / build-check) → ◇ legacy-grace (env PRACTICES_LEGACY_GRACE=1) → ⊕ severity (L1: block|grace-warn; L2/L3: block|active-full else warn) → ⊕ VerifyReport → audit_logger (event=verify_contracts) → ⎋ has_blocking/has_warnings/format_for_ssh
# region MODULE_CONTRACT
## @purpose  Контракт-проверки проекта на VPS (DevPlan 137 W4, K3-канал — расширение verify
##           verb forced-command диспетчера): 9 контрактов по таблице §5 W4. L1-контракты —
##           инварианты платформы (docs/platform-project-contract.md §2.2: «НЕ публикуй порты»,
##           «НЕ храни секреты», ingress = nginx proxy-net), переведённые в машиночитаемые
##           проверки; исполняются ПРИ ЛЮБОМ уровне практик (безопасность платформы, §3.1 п.4,
##           §4.5). L2 — контракты качества (compose config, дрейф practices.lock version,
##           build --check) по state из practices.lock. Legacy-проект (нет practices.lock) →
##           grace-режим: L1 warning-only + [PRACTICES:LEGACY] (env PRACTICES_LEGACY_GRACE=1,
##           TRAP §10.2 — 2-стадийный rollout W4: сначала shadow, потом блок).
## @scope    Вызывается из orchestrator_cli dispatch verb=verify ПОСЛЕ успешной HTTPS-проверки
##           (domain_verifier, DevPlan 125 T1 — НЕ дублируется). Библиотечная функция +
##           CLI main() для ручной диагностики. НЕ вычисляет maturity и НЕ вызывает
##           escalator.evaluate (на VPS нет git) — применяет готовый state из practices.lock.
## @invariants
##   - L1 (secrets-in-compose/ports-published/healthcheck-present/external-networks/
##     env-file-contract/platform-labels) — БЛОК всегда; L2/L3 — блок только в active-full
##   - allowed_external_networks — ИЗ КАНОНА practices_manifest.yaml (не хардкод; TRAP §10.2)
##   - healthcheck-present — СТАТИЧЕСКАЯ проверка наличия ключа healthcheck: ИЛИ labels:
##     platform.healthcheck=...; канон healthcheck_poller НЕ дублируется (без runtime inspect)
##   - practices.lock отсутствует → legacy; PRACTICES_LEGACY_GRACE=1 → L1 warning-only +
##     [PRACTICES:LEGACY]; без grace → L1 блок (2-я стадия rollout)
##   - docker-зависимые L2 (compose-config-valid/build-check) исполняются только при наличии
##     бинарника docker (на VPS гарантирован bootstrap'ом); отсутствие → skip (не warning)
##   - Аудит через shared/audit_logger (единый writer, DevPlan 116 B11 T2) — event=verify_contracts
##   - Exit-коды из shared/contracts.py (0/1) — НЕ хардкодить; main() -> int (контракт core)
## @rationale  Платформа деплоит проекты без единой проверки качества (deploy-project.yml:
##             ping→receive→verify; verify не проверял код). L1 = защита платформы (не качество
##             проекта) — публикация портов ломает ingress/TLS-модель nginx, секреты в compose
##             утекают в git, external-сети вне allowlist — чужие сети. Legacy-grace (TRAP §10.2)
##             — L1-контракты ломают легаси-деплои (риск HI), rollout 2 стадиями.
## @changes  2026-08-05 · DevPlan 137 W4 — создан (K3-канал, 9 контрактов §5 W4)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.internal.practices.generators import read_lock
from core.internal.practices.manifest import load_manifest
from core.internal.shared.audit_logger import DEFAULT_LOG_FILE, write_audit_entry
from core.internal.shared.compose_files import resolve_compose_file
from core.internal.shared.contracts import EXIT_GENERIC, EXIT_OK
from core.internal.shared.exceptions import ConfigValidationError, PlatformError

logger = logging.getLogger(__name__)

# ── Env-флаг grace-режима легаси (TRAP §10.2: PRACTICES_LEGACY_GRACE=1 → L1 warning-only) ──
LEGACY_GRACE_ENV: str = "PRACTICES_LEGACY_GRACE"

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


# region FUNC_ContractFinding
## @purpose  Frozen-находка контракт-проверки: contract_id (id из таблицы §5 W4), klass
##           (L1/L2/L3), severity (block|warning — вычислен по state/grace), message.
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
## @purpose  Frozen-отчёт verify_contracts: state (baseline|proposed|active-full|legacy),
##           legacy_grace-флаг, findings. Методы: has_blocking_violation()/has_warnings()/
##           exit_code/format_for_ssh() (вывод [PRACTICES:BLOCK]/[PRACTICES:PROPOSE]/
##           [PRACTICES:LEGACY] — §5 W4 интеграция в orchestrator_cli).
## @io       ⇥ project_dir/state/legacy_grace/findings → ⎋ VerifyReport
## @complexity O(F) где F = findings
@dataclass(frozen=True)
class VerifyReport:
    """Report of a verify_contracts run (project contracts, K3)."""

    project_dir: Path
    state: str  # baseline | proposed | active-full | legacy
    legacy_grace: bool
    findings: tuple[ContractFinding, ...]

    ## @purpose  Есть ли блокирующее нарушение (severity=block). L1 всегда block (кроме
    ##           legacy-grace); L2/L3 — block в active-full.
    ## @io       ⎋ bool
    ## @complexity O(F)
    def has_blocking_violation(self) -> bool:
        """True если хотя бы один finding имеет severity=block (деплой блокируется)."""
        return any(f.severity == SEVERITY_BLOCK for f in self.findings)

    ## @purpose  Есть ли non-blocking warning: legacy-grace-маркер ИЛИ severity=warning.
    ## @io       ⎋ bool
    ## @complexity O(F)
    def has_warnings(self) -> bool:
        """True если есть warnings (non-blocking) — legacy-маркер или warning-findings."""
        return self.legacy_grace or any(f.severity == SEVERITY_WARNING for f in self.findings)

    ## @purpose  Exit-код для forced-command: 1 при блокирующем нарушении, иначе 0.
    ## @io       ⎋ int (EXIT_GENERIC | EXIT_OK — из shared/contracts.py)
    ## @complexity O(F)
    @property
    def exit_code(self) -> int:
        """Exit code: 1 если blocking violation, иначе 0 (контракт §5 W4)."""
        return EXIT_GENERIC if self.has_blocking_violation() else EXIT_OK

    ## @purpose  Формат вывода для SSH forced-command (stdout verify): [PRACTICES:BLOCK] для
    ##           блокирующих, [PRACTICES:PROPOSE] для warning, [PRACTICES:LEGACY] для grace,
    ##           [PRACTICES:OK] при чистом прогоне, [PRACTICES:RESULT] сводка.
    ## @io       ⎋ str — многострочный отчёт (по одной строке на finding)
    ## @complexity O(F)
    def format_for_ssh(self) -> str:
        """Render [PRACTICES:...] report for SSH forced-command output (agent-visible)."""
        lines: list[str] = []
        if self.legacy_grace:
            lines.append(
                "[PRACTICES:LEGACY] practices.lock not found — legacy project; "
                "L1-контракты в warning-only (grace). Run: make project-sync-practices / make adopt-project"
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
##           мапит в ContractFinding с severity по state/grace (единая точка политики).
## @io       ⇥ contract_id/klass/message → ⎋ _RawFinding
## @complexity O(1)
@dataclass(frozen=True)
class _RawFinding:
    """Internal pre-severity finding (severity computed centrally by state/grace policy)."""

    contract_id: str
    klass: str
    message: str


# endregion FUNC__RawFinding


# region FUNC_verify_project_contracts
## @purpose  Главная точка входа K3: прогон всех контрактов по каталогу проекта → VerifyReport.
##           Порядок: канон → practices.lock (state) → compose (L1-статика + L2 docker) →
##           drift-version → severity (L1 block/grace; L2/L3 block в active-full) → audit.
## @io       ⇥ project_dir: Path, env: dict | None (None = os.environ; legacy-grace флаг),
##           audit_log_file: str | None (None = канонический /var/log/platform/audit.jsonl)
##           ⎋ VerifyReport
##           ⚡ ConfigValidationError — сломанный канон practices_manifest.yaml (exit 4)
## @complexity O(S) где S = размер compose + subprocess docker (config/build)
## @invariants
##   - Проект-директория отсутствует → пустой отчёт (state=legacy, 0 findings), аудит НЕ пишется
##   - lock отсутствует → state="legacy"; grace = lock отсутствует И PRACTICES_LEGACY_GRACE=1
##   - L1 severity: block всегда, кроме legacy-grace → warning
##   - L2/L3 severity: block только в state=active-full, иначе warning
##   - docker отсутствует → L2 docker-проверки пропускаются (на VPS docker гарантирован)
def verify_project_contracts(
    project_dir: Path,
    *,
    env: dict[str, str] | None = None,
    audit_log_file: str | None = None,
) -> VerifyReport:
    """Run contract verification for a project dir → VerifyReport (exit 0/1 semantics)."""
    project_dir = Path(project_dir)
    source = os.environ if env is None else env

    if not project_dir.is_dir():
        logger.warning(
            "[IMP:7][verify_contracts][skip] project dir not found: %s — contract verification skipped",
            project_dir,
        )
        logger.info(
            "[IMP:9][verify_contracts][skip] project=%s dir missing → 0 findings, exit 0 (non-blocking)",
            project_dir.name,
        )
        return VerifyReport(project_dir=project_dir, state="legacy", legacy_grace=False, findings=())

    manifest = load_manifest()
    lock = read_lock(project_dir)
    state = lock.state if lock is not None else "legacy"
    legacy_grace = lock is None and str(source.get(LEGACY_GRACE_ENV, "0") or "0") == "1"
    logger.info(
        "[IMP:8][verify_contracts][start] project=%s state=%s legacy_grace=%s canon_v%d",
        project_dir.name,
        state,
        legacy_grace,
        manifest.version,
    )

    raw: list[_RawFinding] = []
    compose_path = resolve_compose_file(project_dir)
    if compose_path is not None:
        data, parse_err = _parse_compose(compose_path)
        if data is None:
            raw.append(_RawFinding("compose-config-valid", KLASS_L2, f"compose file не парсится как YAML: {parse_err}"))
        else:
            services = data.get("services")
            networks = data.get("networks")
            if not isinstance(services, dict):
                raw.append(_RawFinding("compose-config-valid", KLASS_L2, "compose file не содержит services (dict)"))
            else:
                raw.extend(_check_secrets_in_compose(services))
                raw.extend(_check_ports_published(services))
                raw.extend(_check_healthcheck_present(services))
                raw.extend(_check_external_networks(networks, manifest.allowed_external_networks))
                raw.extend(_check_env_file(services))
                raw.extend(_check_platform_labels(services))

    # ── L2: drift practices.lock (носитель state на VPS) + docker-контракты ──
    raw.extend(_check_drift_practices(lock, manifest.version))
    if compose_path is not None:
        raw.extend(_check_compose_config_validate(project_dir))
    raw.extend(_check_build_check(project_dir))

    findings = tuple(
        ContractFinding(
            contract_id=r.contract_id,
            klass=r.klass,
            severity=_severity_for(r.klass, state, legacy_grace),
            message=r.message,
        )
        for r in raw
    )
    report = VerifyReport(
        project_dir=project_dir,
        state=state,
        legacy_grace=legacy_grace,
        findings=findings,
    )

    _audit_report(report, audit_log_file)
    n_block = sum(1 for f in report.findings if f.severity == SEVERITY_BLOCK)
    logger.info(
        "[IMP:9][verify_contracts][done] project=%s state=%s findings=%d blocking=%d",
        project_dir.name,
        report.state,
        len(report.findings),
        n_block,
    )
    return report


# endregion FUNC_verify_project_contracts


# region FUNC__severity_for
## @purpose  Политика блокировки L1/L2/L3 (§4.5): L1 — block всегда, кроме legacy-grace
##           (warning-only, TRAP §10.2); L2/L3 — block только в active-full (по state из
##           practices.lock), baseline/proposed/legacy — warning (non-blocking).
## @io       ⇥ klass: str, state: str, legacy_grace: bool → ⎋ "block" | "warning"
## @complexity O(1)
def _severity_for(klass: str, state: str, legacy_grace: bool) -> str:
    """Compute severity by class + state + legacy-grace (DevPlan 137 §4.5 policy)."""
    if klass == KLASS_L1:
        return SEVERITY_WARNING if legacy_grace else SEVERITY_BLOCK
    if state == "active-full":
        return SEVERITY_BLOCK
    return SEVERITY_WARNING


# endregion FUNC__severity_for


# region FUNC__parse_compose
## @purpose  Безопасный YAML-парсинг compose-файла → (data, error). Синтаксическая ошибка
##           НЕ роняет прогон — отдаёт (None, сообщение) для L2 compose-config-valid finding.
## @io       ⇥ compose_path: Path → ⎋ tuple[dict | None, str]
## @complexity O(S) где S = размер файла
def _parse_compose(compose_path: Path) -> tuple[dict[str, Any] | None, str]:
    """Parse compose YAML → (dict, "") or (None, error-message)."""
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
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
##           только ${VAR} интерполяция (docs/platform-project-contract.md §2.2 DO NOT #4:
##           «НЕ храни секреты/токены/ключи в файлах проекта»). Сканирует рекурсивно
##           (environment dict/list, build.args, labels) — ключи по суффиксу
##           (password|api_key|token) с не-интерполированным значением.
## @io       ⇥ services: dict → ⎋ list[_RawFinding] (0..N — по одному на сервис/ключ)
## @complexity O(K) где K = число ключей compose
def _check_secrets_in_compose(services: dict[str, Any]) -> list[_RawFinding]:
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
                    f"только ${'{VAR}'} интерполяция (секреты НЕ в compose, docs/platform-project-contract.md §2.2)",
                )
            )
    return findings


# endregion CONTRACT_secrets_in_compose


# region CONTRACT_ports_published
## @purpose  L1 ports-published: в services НЕ должно быть `ports:` — ingress/TLS делает
##           nginx-модуль платформы (сеть proxy-net, external). `expose:` — OK (внутренний,
##           не публикует host-порт). Инвариант docs/platform-project-contract.md §2.2 DO NOT #2.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S) где S = число сервисов
def _check_ports_published(services: dict[str, Any]) -> list[_RawFinding]:
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
                    f"(ingress = nginx platform proxy-net, docs/platform-project-contract.md §2.2 DO NOT #2); expose: — OK",
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
def _check_healthcheck_present(services: dict[str, Any]) -> list[_RawFinding]:
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
def _check_external_networks(networks: Any, allowlist: tuple[str, ...]) -> list[_RawFinding]:
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
##           (docs/platform-project-contract.md §Машиночитаемая фактура). Секреты проекта —
##           только .platform-db.env на ноде (0600, вне payload), не в .env.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S * E) где E = env_file записей
def _check_env_file(services: dict[str, Any]) -> list[_RawFinding]:
    """L1: env_file ≠ .env.platform (или абсолютный путь) → violation."""
    findings: list[_RawFinding] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        env_file = svc.get("env_file")
        if env_file is None:
            continue
        files: list[Any] = [env_file] if isinstance(env_file, str) else list(env_file or [])
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
##           platform.module, но реальные шаблоны (template-{backend,frontend,fullstack}) и
##           scaffold используют platform.type + platform.domain (проверено 2026-08-05) —
##           строгий набор ложноблокировал бы собственные scaffold-проекты платформы (риск
##           §7 HI). Имплементация: наличие ≥1 platform.* label (семантическая группа),
##           intent плана (сервис идентифицирован платформой) сохранён.
## @io       ⇥ services: dict → ⎋ list[_RawFinding]
## @complexity O(S)
def _check_platform_labels(services: dict[str, Any]) -> list[_RawFinding]:
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
## @io       ⇥ project_dir: Path → ⎋ list[_RawFinding]
## @complexity O(1) + subprocess docker compose config
def _check_compose_config_validate(project_dir: Path) -> list[_RawFinding]:
    """L2: docker compose config --quiet (статическая валидность compose)."""
    if shutil.which("docker") is None:
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
## @purpose  L2 drift-practices: на VPS practices.lock отсутствует (legacy) ИЛИ
##           lock.version < версии канона на ноде → [PRACTICES:DRIFT-VERSION] warning
##           (файловый дрейф проверяется локально K1 и в CI K2 — там полный checkout; на VPS
##           lock — единственный носитель state, DevPlan 137 §5 W4). Локальный ремонт:
##           make project-sync-practices.
## @io       ⇥ lock: PracticesLock | None, canon_version: int → ⎋ list[_RawFinding]
## @complexity O(1)
def _check_drift_practices(lock: Any, canon_version: int) -> list[_RawFinding]:
    """L2: practices.lock missing/stale-version → warning (repair: project-sync-practices)."""
    if lock is None:
        return [
            _RawFinding(
                "drift-practices",
                KLASS_L2,
                "practices.lock not found (legacy) — state неизвестен; run: make project-sync-practices "
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
## @io       ⇥ project_dir: Path → ⎋ list[_RawFinding]
## @complexity O(1) + subprocess docker build --check
def _check_build_check(project_dir: Path) -> list[_RawFinding]:
    """L2: docker build --check (BuildKit static) при наличии Dockerfile."""
    if not (project_dir / "Dockerfile").is_file():
        return []
    if shutil.which("docker") is None:
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
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


# endregion FUNC__run_docker


# region FUNC__iter_secret_literals
## @purpose  Рекурсивный обход compose-структуры: выдаёт (path, key, value) для ключей с
##           суффиксом password|api_key|token и НЕ-интерполированным значением. Обрабатывает
##           dict-форму (environment: {DB_PASSWORD: x}) и list-форму (environment: [DB_PASSWORD=x]).
## @io       ⇥ node: Any, path: str → ⎋ Iterator[(path, key, value)]
## @complexity O(K) где K = число ключей
def _iter_secret_literals(node: Any, path: str = "") -> Iterable[tuple[str, str, str]]:
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
def _is_interpolated(value: Any) -> bool:
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
def _normalize_labels(labels: Any) -> dict[str, str]:
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
##           project/state/legacy_grace. Best-effort: OSError → False (не блокирует деплой).
## @io       ⇥ report: VerifyReport, log_file: str | None → ⎋ bool (True=записано)
## @complexity O(F)
def _audit_report(report: VerifyReport, log_file: str | None) -> bool:
    """Write audit entry (event=verify_contracts) — best-effort, never blocks deploy."""
    n_block = sum(1 for f in report.findings if f.severity == SEVERITY_BLOCK)
    status = "BLOCKED" if n_block else ("WARN" if report.has_warnings() else "OK")
    try:
        ok = write_audit_entry(
            tag="verify_contracts",
            status=status,
            message=(
                f"verify contracts {report.project_dir.name}: {len(report.findings)} findings, "
                f"{n_block} blocking, state={report.state}"
            ),
            project=report.project_dir.name,
            state=report.state,
            legacy_grace=report.legacy_grace,
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
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="[%(levelname)s][verify_contracts] %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="Verify project contracts (K3, VPS)")
    parser.add_argument("--project-dir", required=True, type=str, help="Project directory to verify")
    parser.add_argument("--audit-log", type=str, default="", help="Audit log file (default: platform audit.jsonl)")
    args = parser.parse_args(argv)

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
