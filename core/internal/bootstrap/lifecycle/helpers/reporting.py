#!/usr/bin/env python3
# GREP_SUMMARY: reporting-helpers, run-healthchecks, write-audit-log, notify-event, telegram, audit-log, severity-routing
# STRUCTURE: ▶ run_healthchecks ┌NodeYaml modules → invoke_module_interface liveness┐ → ⚡ write_audit_log ┌/var/log/platform/audit.jsonl JSON-lines (write_audit_entry)┐ → ⚡ send_telegram ┌notify_event (shared/notifications, severity errors→critical)┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Reporting I/O-хелперы bootstrap-фаз (healthchecks, audit log, Telegram notify) —
##           извлечены из state_machine (B9 T1, U-08). Все функции публичные.
## @scope    reporting.py: run_healthchecks, write_audit_log, send_telegram.
##           write_audit_log/send_telegram принимают sm: StateMachineProtocol — читают только
##           sm.state (duck-typing через Protocol, БЕЗ импорта state_machine — разрыв цикла
##           state_machine→phases→helpers→reporting→state_machine, W10-design п.2 / W5-C2).
##           Используются phases.py (φ11 healthcheck) и lifecycle/cli.py (audit/telegram после run).
## @invariants
##   - Все reporting-функции non-fatal (best-effort) — не блокируют lifecycle
##   - run_healthchecks: invoke_module_interface — bash-функция, запускается через bash -c
##     с source paths.sh (TRAP[BUG] 2026-07-24 P0)
##   - send_telegram: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID отсутствуют → skip
##   - StateMachineProtocol — read-only вью: reporting НЕ вызывает методы sm, только читает
##     sm.state.{mode,node,warnings,errors} (SimpleNamespace-дубли в тестах совместимы)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита (DevPlan 116 B9 D1).
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
## @changes  2026-08-14 · DevPlan 170 W1-A3 — proxy-URL порт из SoT firewall.PRIVOXY_PORT
## @changes  2026-08-15 · DevPlan 170 W5-C2 (W10-design п.2) — тип-only импорт StateMachine
##           заменён на StateMachineProtocol (duck-typing) —
##           цикл импортов state_machine→phases→helpers→reporting разорван
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import pathlib
import time
from collections.abc import Callable
from typing import Protocol, cast

from core.internal.bootstrap.firewall import PRIVOXY_PORT
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError

# B3: канонический platform root — shared/deploy_paths; T3.8: platform_remote_base
# удалён вместе с последним inline bash-вызовом (канон — module_interface.invoke)
from core.internal.shared.module_interface import invoke as module_interface_invoke
from core.internal.shared.timeouts import HEALTHCHECK_CMD_TIMEOUT


class _StateView(Protocol):
    """Read-only вью BootstrapState — контракт reporting-хелперов (mode/node/warnings/errors)."""

    @property
    def mode(self) -> str: ...

    @property
    def node(self) -> str | None: ...

    @property
    def warnings(self) -> list[str]: ...

    @property
    def errors(self) -> list[str]: ...


class StateMachineProtocol(Protocol):
    """Duck-typing протокол StateMachine для reporting (W10-design п.2).

    Заменяет тип-only импорт StateMachine: reporting читает ТОЛЬКО sm.state — структурная
    совместимость с StateMachine (state_store.BootstrapState) и тестовыми
    SimpleNamespace-дублями; импорт state_machine отсутствует → цикл разорван.
    """

    @property
    def state(self) -> _StateView: ...


logger = logging.getLogger(__name__)


# region FUNC_run_healthchecks
## @purpose  Run healthchecks on all deployed modules (liveness via invoke_module_interface).
## @io       ⇥ node_yaml → ⎋ None (non-fatal)
## @complexity O(M * R) where M = modules, R = retries
## @invariants
##   - invoke_module_interface — bash-функция из module-interface.sh (НЕ executable) —
##     вызывается через bash -c с source paths.sh (TRAP[BUG] 2026-07-24 P0)
# region FUNC__enabled_module_items
## @purpose  Нормализация node.yaml#modules (dict|list) → [(name, value)] только enabled
##           (T3.8: извлечён из run_healthchecks для C901 ≤10; единственный потребитель).
## @io       ⇥ node_yaml: str → ⎋ list[tuple[str, object]]
## @complexity O(M)
def _enabled_module_items(node_yaml: str) -> list[tuple[str, object]]:
    """Parse node.yaml modules section → enabled (name, value) pairs."""
    from core.internal.shared.node_yaml import NodeYaml

    node = NodeYaml(node_yaml)
    modules = cast(
        "object", node.get("modules", default=dict[str, object]())
    )  # W11-G1 cross-file: NodeYaml.get → Any; default типизирован
    if isinstance(modules, dict):
        items = list(cast("dict[str, object]", modules).items())  # W11-G1: каскад от node_yaml.get → Any
    elif isinstance(modules, list):
        mod_list = cast("list[dict[str, object]]", modules)  # W11-G1: каскад от node_yaml.get → Any
        items = [(cast("str", mod.get("name", "")), cast("object", mod)) for mod in mod_list]
    else:
        items = []
    result: list[tuple[str, object]] = []
    for name, value in items:
        if not name:
            continue
        if isinstance(value, dict):
            enabled = str(cast("dict[str, object]", value).get("enabled", True)).lower()  # W11-G1 каскад Any
        else:
            enabled = str(value).lower()
        if enabled == "true":
            result.append((name, value))
    return result


# endregion FUNC__enabled_module_items


def run_healthchecks(node_yaml: str) -> None:
    """Run healthchecks on all deployed modules."""
    if not node_yaml or not pathlib.Path(node_yaml).is_file():
        logger.warning("[IMP:7][healthcheck] NODE_YAML not set or not found — skipping healthchecks")
        return

    hc_max_retries = 10
    hc_retry_interval = 10
    hc_fail = 0

    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        module_items = _enabled_module_items(node_yaml)

        for mod_name, _mod_value in module_items:
            passed = False
            # ⚠️ TRAP[BUG] · 2026-07-24 · P0 · invoke_module_interface — bash-функция, не executable
            # · Symptom: subprocess.run(["invoke_module_interface", ...]) → FileNotFoundError
            # · Root: invoke_module_interface sourced из module-interface.sh (через paths.sh)
            # · Fix (T3.8): дублирующий bash -c-конструктор удалён → канонический
            # ·   shared.module_interface.invoke() (централизованный sourcing paths.sh +
            # ·   module-interface.sh + timeout/OSError-обработка). Retry 10×10s остался здесь —
            # ·   в invoke/check_module ретраев НЕТ (сверено с планом).
            # · Prevention: вызовы модулей — ТОЛЬКО через module_interface.invoke.
            for attempt in range(1, hc_max_retries + 1):
                # AI-0012r (DevPlan 17 T1.5): канон HEALTHCHECK_CMD_TIMEOUT (60) вместо literal 30 —
                # тот же `<mod> healthcheck liveness` получает один бюджет на всех путях
                ok, err = module_interface_invoke(mod_name, "healthcheck", "liveness", timeout=HEALTHCHECK_CMD_TIMEOUT)
                if ok:
                    logger.info(
                        "[IMP:9][healthcheck:%s] Healthcheck PASS (attempt %d/%d)",
                        mod_name,
                        attempt,
                        hc_max_retries,
                    )
                    passed = True
                    break
                if attempt == 1:
                    logger.warning(
                        "[IMP:7][healthcheck:%s] stderr: %s",
                        mod_name,
                        (err or "(empty)").strip()[-200:] if err else "(empty)",
                    )
                if attempt < hc_max_retries:
                    time.sleep(hc_retry_interval)

            if not passed:
                logger.warning("[IMP:7][healthcheck:%s] Healthcheck FAILED after %d attempts", mod_name, hc_max_retries)
                hc_fail += 1
    except ImportError:
        logger.warning("[IMP:7][healthcheck] NodeYaml library not available — skipping inline healthchecks")
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as e:
        logger.warning("[IMP:7][healthcheck] Failed to parse node.yaml: %s", e)

    if hc_fail > 0:
        logger.warning("[IMP:7][healthcheck] %d healthcheck(s) failed — node partially ready", hc_fail)
    else:
        logger.info("[IMP:9][healthcheck] All healthchecks passed")


# endregion FUNC_run_healthchecks


# region FUNC_write_audit_log
## @purpose  Write bootstrap/update audit summary to the ЕДИНЫЙ audit log (shared audit_logger).
## @io       ⇥ sm → ⎋ None (side-effect: writes JSON-lines entries to /var/log/platform/audit.jsonl)
## @complexity O(1)
## @changes 2026-08-01 | DevPlan 116 B11 T2 (U-10, D1): free-text pipe → shared write_audit_entry;
##           единый файл audit.jsonl; warnings/errors — отдельные WARN/ERROR записи
## @changes 2026-08-05 | DevPlan 136 W9 T9.6 (L-5/L-11): +result param — FAILED-записи из
##           failure-путей run_init/run_update (audit больше не только в успешном хвосте)
def write_audit_log(sm: StateMachineProtocol, result: str | None = None) -> None:
    """Write bootstrap/update audit summary to the unified audit log (JSON-lines, D1).

    result="FAILED" → summary status FAILED (failure paths, T9.6); default — DONE/ERROR по errors.
    """
    from core.internal.shared.audit_logger import write_audit_entry

    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        mode = sm.state.mode
        node = sm.state.node or "unknown"
        warnings_count = len(sm.state.warnings)
        errors_count = len(sm.state.errors)
        summary_status = result if result is not None else ("DONE" if errors_count == 0 else "ERROR")
        summary = f"bootstrap:{mode} {summary_status} | node={node} | warnings={warnings_count} | errors={errors_count}"
        write_audit_entry(
            tag=f"bootstrap:{mode}",
            status=summary_status,
            message=summary,
            node=node,
            warnings_count=warnings_count,
            errors_count=errors_count,
        )
        for w in sm.state.warnings:
            write_audit_entry(tag=f"bootstrap:{mode}", status="WARN", message=str(w), node=node)
        for e in sm.state.errors:
            write_audit_entry(tag=f"bootstrap:{mode}", status="ERROR", message=str(e), node=node)
        logger.info(
            "[IMP:9][audit] Audit entries written (bootstrap:%s %s, %d warnings, %d errors)",
            mode,
            summary_status,
            warnings_count,
            errors_count,
        )
    except OSError as e:
        logger.warning("[IMP:7][audit] Failed to write audit entries: %s", e)


# endregion FUNC_write_audit_log


# region FUNC_send_telegram
## @purpose  Send Telegram notification with bootstrap/update results (non-fatal).
##           DevPlan 003 B3: send_telegram → notify_event (единый конверт/severity-роутинг);
##           severity: errors>0 → critical, warnings>0 → warning, иначе info.
## @io       ⇥ sm → ⎋ None (non-fatal); notifier: Callable | None (W4b DI — ленивый default shared send_telegram)
## @complexity O(1)
## @changes 2026-07-30 | T19 — Replaced inline urllib with shared telegram_notifier.send_telegram()
## @changes 2026-08-13 | DevPlan 160 W4b — +notifier (инъекция фабрики send_telegram)
## @changes 2026-08-16 | DevPlan 003 B3 — миграция на shared/notifications.notify_event
##           (единый конверт + severity-роутинг; W4b DI-контракт notifier сохранён)
def send_telegram(
    sm: StateMachineProtocol,
    *,
    notifier: Callable[..., object] | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> None:
    """Send Telegram notification with bootstrap/update results."""
    resolved_token = os.environ.get("TELEGRAM_BOT_TOKEN", "") if bot_token is None else bot_token
    resolved_chat = os.environ.get("TELEGRAM_CHAT_ID", "") if chat_id is None else chat_id
    if not resolved_token or not resolved_chat:
        logger.info("[IMP:9][telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — notifications disabled")
        return

    ts = time.strftime("%d.%m.%Y %H:%M:%S")
    node = sm.state.node or "unknown"
    warnings_count = len(sm.state.warnings)
    errors_count = len(sm.state.errors)

    status_suffix = "⚠️ Warnings/Errors:" if errors_count > 0 or warnings_count > 0 else "✅"
    msg = f"🚀 [node: {node}] Узел обновлён {status_suffix}\nВремя: {ts}"
    details: list[str] = [f"⚠️ {w}" for w in sm.state.warnings] + [f"❌ {e}" for e in sm.state.errors]

    # DevPlan 003 B3: severity по состоянию (errors → critical, warnings → warning, иначе info)
    severity = "critical" if errors_count > 0 else ("warning" if warnings_count > 0 else "info")

    proxy_url = os.environ.get("TELEGRAM_PROXY_URL", f"http://127.0.0.1:{PRIVOXY_PORT}")
    # W4b (160 T4.2): notifier параметром + ленивый default = shared send_telegram (ровно текущее)
    # 🧐 TRAP[DECISION] · 2026-08-13 · — · send_telegram DI: notifier в ПОТРЕБИТЕЛЕ, не http_opener в самой send_telegram
    # · Rejected: добавить http_opener/opener_factory в shared telegram_notifier.send_telegram (инъекция HTTP-транспорта)
    # · Reason: send_telegram — публичный API с 6+ потребителями (shell-фасады, notify CLI, watchdog subprocess);
    # ·   существующие тесты telegram_notifier патчат urllib.OpenerDirector.open/build_opener (рабочий паттерн);
    # ·   изменение сигнатуры несло бы риск без выгоды — T4.2 «клиент параметром» = инъекция в потребителя.
    # · Rev: если тесты telegram_notifier начнут требовать fake-транспорт без патчей urllib → добавить http_opener.
    # DevPlan 003 B3: DI-контракт сохранён — notifier пробрасывается в notify_event (send_fn).
    if notifier is not None:

        def _di_send(envelope: str, **kw: object) -> bool:
            return bool(notifier(envelope, kw.get("bot_token"), kw.get("chat_id"), kw.get("proxy_url")))

        send_fn: Callable[..., bool] | None = _di_send
    else:
        send_fn = None

    from core.internal.shared.notifications import Notification, notify_event

    notify_event(
        Notification(
            severity=severity,
            context="bootstrap",
            event="bootstrap.report",
            message=msg,
            details=details,
            # mode через getattr: тестовые дубли StateMachine не всегда несут mode (W4b DI)
            corr_id=f"bootstrap-{getattr(sm.state, 'mode', 'update')}-{node}",
            action="Check bootstrap/update audit log",
        ),
        env=dict(os.environ, TELEGRAM_BOT_TOKEN=resolved_token, TELEGRAM_CHAT_ID=resolved_chat),
        proxy_url=proxy_url,
        send_fn=send_fn,
    )


# endregion FUNC_send_telegram
