#!/usr/bin/env python3
# GREP_SUMMARY: reporting-helpers, run-healthchecks, write-audit-log, send-telegram, telegram-notifier, audit-log
# STRUCTURE: ▶ run_healthchecks ┌NodeYaml modules → invoke_module_interface liveness┐ → ⚡ write_audit_log ┌/var/log/platform/audit.jsonl JSON-lines (write_audit_entry)┐ → ⚡ send_telegram ┌telegram_notifier (non-fatal)┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Reporting I/O-хелперы bootstrap-фаз (healthchecks, audit log, Telegram notify) —
##           извлечены из state_machine (B9 T1, U-08). Все функции публичные.
## @scope    reporting.py: run_healthchecks, write_audit_log, send_telegram.
##           write_audit_log/send_telegram принимают sm (StateMachine) — читают только sm.state
##           (duck-typing; тип импортируется через TYPE_CHECKING — без цикла импортов).
##           Используются phases.py (φ11 healthcheck) и lifecycle/cli.py (audit/telegram после run).
## @invariants
##   - Все reporting-функции non-fatal (best-effort) — не блокируют lifecycle
##   - run_healthchecks: invoke_module_interface — bash-функция, запускается через bash -c
##     с source paths.sh (TRAP[BUG] 2026-07-24 P0)
##   - send_telegram: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID отсутствуют → skip
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита (DevPlan 116 B9 D1).
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from typing import TYPE_CHECKING

# B3: канонический platform root — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.telegram_notifier import send_telegram as _shared_send_telegram

if TYPE_CHECKING:
    from core.internal.bootstrap.lifecycle.state_machine import StateMachine

logger = logging.getLogger(__name__)


# region FUNC_run_healthchecks
## @purpose  Run healthchecks on all deployed modules (liveness via invoke_module_interface).
## @io       ⇥ node_yaml → ⎋ None (non-fatal)
## @complexity O(M * R) where M = modules, R = retries
## @invariants
##   - invoke_module_interface — bash-функция из module-interface.sh (НЕ executable) —
##     вызывается через bash -c с source paths.sh (TRAP[BUG] 2026-07-24 P0)
def run_healthchecks(node_yaml: str) -> None:
    """Run healthchecks on all deployed modules."""
    if not node_yaml or not os.path.isfile(node_yaml):
        logger.warning("[IMP:7][healthcheck] NODE_YAML not set or not found — skipping healthchecks")
        return

    hc_max_retries = 10
    hc_retry_interval = 10
    hc_fail = 0

    try:
        from core.internal.shared.node_yaml import NodeYaml

        node = NodeYaml(node_yaml)
        modules = node.get("modules", default={})
        if isinstance(modules, dict):
            module_items = modules.items()
        elif isinstance(modules, list):
            module_items = [(m.get("name", ""), m) for m in modules]
        else:
            module_items = []

        for mod_name, mod_value in module_items:
            if not mod_name:
                continue
            if isinstance(mod_value, dict):
                enabled = str(mod_value.get("enabled", True)).lower()
            else:
                enabled = str(mod_value).lower()
            if enabled != "true":
                continue

            passed = False
            # ⚠️ TRAP[BUG] · 2026-07-24 · P0 · invoke_module_interface is a bash function, not an executable
            # · Symptom: subprocess.run(["invoke_module_interface", ...]) → FileNotFoundError
            # · Root: invoke_module_interface is sourced from module-interface.sh (via paths.sh)
            # · Fix: wrap in bash -c with proper sourcing
            platform_root = str(platform_remote_base())
            for attempt in range(1, hc_max_retries + 1):
                try:
                    hc_cmd = (
                        f"source {shlex.quote(platform_root + '/core/lib/paths.sh')} && "
                        f"invoke_module_interface {shlex.quote(mod_name)} healthcheck liveness"
                    )
                    hc_result = subprocess.run(
                        ["bash", "-c", hc_cmd],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if hc_result.returncode == 0:
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
                            hc_result.stderr.strip()[-200:] if hc_result.stderr else "(empty)",
                        )
                except subprocess.TimeoutExpired:
                    logger.warning("[IMP:7][healthcheck:%s] Timeout (attempt %d/%d)", mod_name, attempt, hc_max_retries)
                except FileNotFoundError:
                    logger.warning(
                        "[IMP:7][healthcheck:%s] bash not found (attempt %d/%d)", mod_name, attempt, hc_max_retries
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
def write_audit_log(sm: StateMachine) -> None:
    """Write bootstrap/update audit summary to the unified audit log (JSON-lines, D1)."""
    from core.internal.shared.audit_logger import write_audit_entry

    try:
        mode = sm.state.mode
        node = sm.state.node or "unknown"
        warnings_count = len(sm.state.warnings)
        errors_count = len(sm.state.errors)
        summary = f"bootstrap:{mode} DONE | node={node} | warnings={warnings_count} | errors={errors_count}"
        write_audit_entry(
            tag=f"bootstrap:{mode}",
            status="DONE" if errors_count == 0 else "ERROR",
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
            "[IMP:9][audit] Audit entries written (bootstrap:%s, %d warnings, %d errors)",
            mode,
            warnings_count,
            errors_count,
        )
    except OSError as e:
        logger.warning("[IMP:7][audit] Failed to write audit entries: %s", e)


# endregion FUNC_write_audit_log


# region FUNC_send_telegram
## @purpose  Send Telegram notification with bootstrap/update results (non-fatal).
## @io       ⇥ sm → ⎋ None (non-fatal)
## @complexity O(1)
## @changes 2026-07-30 | T19 — Replaced inline urllib with shared telegram_notifier.send_telegram()
def send_telegram(sm: StateMachine) -> None:
    """Send Telegram notification with bootstrap/update results."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        logger.info("[IMP:9][telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — notifications disabled")
        return

    ts = time.strftime("%d.%m.%Y %H:%M:%S")
    node = sm.state.node or "unknown"
    warnings_count = len(sm.state.warnings)
    errors_count = len(sm.state.errors)

    status_suffix = "⚠️ Warnings/Errors:" if errors_count > 0 or warnings_count > 0 else "✅"

    msg = f"🚀 [node: {node}] Узел обновлён {status_suffix}\nВремя: {ts}"
    if warnings_count > 0:
        for w in sm.state.warnings:
            msg += f"\n- ⚠️ {w}"
    if errors_count > 0:
        for e in sm.state.errors:
            msg += f"\n- ❌ {e}"

    proxy_url = os.environ.get("TELEGRAM_PROXY_URL", "http://127.0.0.1:8118")
    success = _shared_send_telegram(msg, bot_token, chat_id, proxy_url)
    if success:
        logger.info("[IMP:9][telegram] Notification sent to chat %s", chat_id)


# endregion FUNC_send_telegram
