#!/usr/bin/env python3
# GREP_SUMMARY: context-promote git-mirror ssh audit context_promoter deploy push ls-remote mirror-verify
# STRUCTURE: ▶ check_ssh_available ◇ → SSH: git push --mirror git@github.com:<ctx>/ai-platform.git → ls-remote HEAD → ◇ verify mirror==source → audit DONE/FAIL → ⎋ exit 0/1
# region MODULE_CONTRACT
## @purpose  Promote the ai-platform to a context GitHub org via `git push --mirror`.
##           SSH-only channel (HTTPS fallback удалён — 177 W2.1), post-push HEAD mirror
##           verification, JSON-lines audit trail.
## @scope    Single consumer: core/entrypoints/context-promote.sh (thin facade).
##           Invoked as `python3 -m core.internal.deploy.context_promoter <CONTEXT>`.
##           CONTEXT from argv[1] (единственный вход — токены не принимаются).
## @invariants
##   1. SSH primary (единственный канал): `ssh -T <SSH_OPTS> git@github.com` — флаги из
##      единого SoT shared/ssh_opts.py (DevPlan 118 C2, 0 ручных -o флагов); availability
##      определяется AUTH MESSAGE в output, НЕ exit code (`ssh -T git@github.com` exits 1
##      даже на успех — GitHub предоставляет no shell).
##   2. SSH unavailable → FATAL exit 1 (без HTTPS-fallback; mirror.yml SSH-only с 2026-07-23).
##   3. Mirror verification: `git ls-remote <target> HEAD` == `git rev-parse HEAD` (AC6).
##   4. Audit entries (START/DONE/FAIL) via shared/audit_logger.write_audit_entry()
##      with tag `context-promote:<ctx>`; log file overridable via AUDIT_LOG_FILE env.
## @rationale SSH-only: context-promote выполняется локально на машине оператора —
##            ssh-agent имеет операторский ключ; PAT-based HTTPS-fallback (GIT_MIRROR_TOKEN)
##            удалён как исторический (mirror.yml официально SSH-only с 2026-07-23,
##            MIRROR_SSH_KEY заменяет GIT_MIRROR_TOKEN — 177 W2.1, решение S4).
## @changes  2026-07-31 | DevPlan 103 — создан (Python-модуль context-promote)
##           2026-08-01 | DevPlan 116 B5 T2 — ConnectTimeout outlier (10) → SSH_CONNECT_TIMEOUT=30 (U-15)
##           2026-08-02 | DevPlan 118 C2 — ручные -o флаги → SSH_OPTS (единый SoT, 0 ручных флагов)
##           2026-08-16 | DevPlan 177 W2.1 — HTTPS-fallback удалён: promote_via_https, token-параметры,
##                       GIT_ASKPASS-механика; GIT_MIRROR_TOKEN → tier: removed
##           2026-08-25 | REF-0103 — GIT_SSH_COMMAND из ssh_opts + DEPLOY_TIMEOUT/SSH_READ_TIMEOUT
##                       на mirror-push/ls-remote; promote_context ловит SubprocessError
## 🧐 TRAP[DECISION] · RESOLVED · 2026-08-16 · SSH primary, HTTPS fallback (DevPlan 103)
## · Rejected: HTTPS-only (required token, unavailable locally — B4)
## · Reason: fallback удалён по Rev-условию — mirror.yml официально SSH-only (2026-07-23),
##   MIRROR_SSH_KEY заменяет GIT_MIRROR_TOKEN; CI-driven context-promote не введён.
## · Rev: если CI-driven context-promote будет введён (runner без SSH key) → вернуть
##   HTTPS+token канал через secret-инфраструктуру CI (НЕ node secrets).
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]

from core.internal.shared import audit_logger

# SSH_OPTS — единый SoT флагов (shared/ssh_opts.py): 0 ручных -o флагов (AC-C2).
# REF-0103: build_rsync_ssh_opts — единственная реализация "ssh <flags>" → GIT_SSH_COMMAND.
from core.internal.shared.ssh_opts import SSH_OPTS, build_rsync_ssh_opts
from core.internal.shared.timeouts import DEPLOY_TIMEOUT, SSH_READ_TIMEOUT  # REF-0103: mirror-бюджеты

logger = logging.getLogger(__name__)

# GitHub SSH auth greeting markers — контент-детекция по merged 2>&1 output
# (не по exit code: `ssh -T git@github.com` выходит 1 даже на успех).
_SSH_AUTH_MARKERS = ("successfully authenticated", "Hi ")


# region FUNC_resolve_org
## @purpose  Resolve GitHub org name for a context — overlay context.yaml#org is the SoT
##           (correct case: TronyxLab), context name is the fallback (historical behavior).
## @io       ⇥ context: str → ⎋ str — GitHub org name
## @rationale GitHub SSH paths are CASE-SENSITIVE: push to tronyx-lab/ai-platform fails
##            ("Repository not found"), TronyxLab/ai-platform works. context.yaml#org
##            (create-context канон) несёт точное имя org.
## @changes 2026-08-05 | DevPlan 139 W2 — _resolve_org → resolve_org (публичный контракт D9:
##            неотъемлемый бизнес-контракт резолва org, тестируется публичным путём; private
##            доступ из тестов запрещён — top-10 private-доступов закрыты)
##           2026-09-01 | DevPlan 022 TASK-3 — legacy-кандидат `<ctx>/context.yaml` удалён:
##                       единственный кандидат-путь — platform/context.yaml (единый overlay
##                       layout `<ctx>-overlay`, D2; зеркальный wipe больше не затрагивает org)
def resolve_org(context: str, env: Mapping[str, str] | None = None) -> str:
    """Resolve GitHub org for a context: overlay context.yaml org field, else context name.

    ▶ ┌context┐ → ○ base ∈ {PROJECTS_BASE, ~/projects} → ◇ <base>/<ctx>/platform/context.yaml#org? → ⎋ org │ ⎋ context-name

    ## @purpose — Публичный контракт: org из overlay context.yaml (SoT,
    ##            точный регистр); fallback — имя контекста (историческое поведение).
    ## @io — ⇥ context: str, env: Mapping | None = None (DI, W-H DevPlan 163 — override
    ##            PROJECTS_BASE; None = os.environ) → ⎋ str (GitHub org name)
    ## @complexity — O(C) — C = кандидаты context.yaml (PROJECTS_BASE + ~/projects, ×1 путь)
    ## @invariants — org из context.yaml#org (если задан) ВСЕГДА приоритетнее имени контекста;
    ##              единственный кандидат-путь — `<base>/<ctx>/platform/context.yaml` (DevPlan 022
    ##              TASK-3: legacy `<ctx>/context.yaml` удалён — канонический layout контекстной
    ##              папки = overlay-контейнер platform/, D2); парсинг best-effort
    ##              (ошибка YAML → fallback, не raise)
    """
    source: Mapping[str, str] = os.environ if env is None else env
    candidates: list[Path] = []
    for base in (source.get("PROJECTS_BASE", ""), str(Path.home() / "projects")):
        if not base:
            continue
        candidates.append(Path(base) / context / "platform" / "context.yaml")
    for ctx_yaml in candidates:
        if not ctx_yaml.is_file():
            continue
        try:
            # yaml.safe_load → Any; object-граница (W11) — org-чтение через isinstance-safe dict
            data: object = cast(object, yaml.safe_load(ctx_yaml.read_text(encoding="utf-8")))
            org = data.get("org") if isinstance(data, dict) else None
            if org:
                logger.info("[IMP:8][resolve_org] org=%s from %s", org, ctx_yaml)
                return str(org)
        except (OSError, yaml.YAMLError) as e:  # noqa: EXC — best-effort org resolution (resolve_org: YAML-ошибка → fallback, не raise)
            logger.warning("[IMP:7][resolve_org] Failed to parse %s: %s — using context name", ctx_yaml, e)
    logger.info("[IMP:7][resolve_org] No context.yaml org found — using context name: %s", context)
    return context


# endregion FUNC_resolve_org


# region FUNC_check_ssh_available
def check_ssh_available() -> bool:
    """Determine whether the operator's SSH key authenticates to github.com.

    ▶ ┌ssh -T git@github.com (ConnectTimeout=<SSH_CONNECT_TIMEOUT>, BatchMode=yes)┐ → ○ merge stdout+stderr → ◇ auth marker? → ⎋ bool

    ## @purpose — SSH primary-channel availability probe: контент-детекция по merged
    ##            `2>&1` output (grep GitHub auth greeting). SSH-флаги — единый SoT
    ##            shared/ssh_opts.SSH_OPTS (C2).
    ## @io — ⇥ None → ⎋ bool — True when output contains an auth marker
    ## @complexity — O(1) subprocess + O(len(output))
    ## @invariants
    ##   - Availability is CONTENT-based, not exit-code-based: `ssh -T git@github.com`
    ##     ALWAYS exits 1 on success (GitHub provides no shell) — exit code is not a signal.
    ##   - Both stdout and stderr are inspected (SSH writes the auth greeting to stderr;
    ##     потоки объединяются для детекции).
    ##   - Missing ssh binary (FileNotFoundError) → False, never raises.
    """
    try:
        result = subprocess.run(
            ["ssh", "-T", *SSH_OPTS, "git@github.com"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("[IMP:7][check_ssh_available] ssh binary not found — SSH unavailable")
        return False

    combined = f"{result.stdout}\n{result.stderr}"
    # ⚠️ Exit code deliberately IGNORED: `ssh -T git@github.com` exits 1 even when
    # authenticated ("no shell access"). The auth-message marker is the only reliable
    # signal (content-based детекция по merged 2>&1).
    if any(marker in combined for marker in _SSH_AUTH_MARKERS):
        logger.info("[IMP:8][check_ssh_available] SSH key for github.com available — will use SSH primary channel")
        return True

    logger.info("[IMP:8][check_ssh_available] SSH key not available or timeout — will attempt fallback")
    return False


# endregion FUNC_check_ssh_available


# region FUNC_promote_via_ssh
# 🧐 TRAP[DECISION] · 2026-09-01 · — · Роли репо разделены (DevPlan 022 D3): target <org>/ai-platform
# ·   = CI-зеркало исходника; контекстное состояние (context.yaml, node-configs, projects) живёт
# ·   в отдельном overlay-репо <org>/<ctx>-overlay
# · Rejected: хранить context.yaml/node-configs в зеркальном репо (Option A из 01-DevPlan)
# · Reason: `git push --mirror` force-update'ит все refs и удаляет отсутствующие в source —
#   контекстные коммиты wipe'ились (прецедент tronyx-lab, squash-реконсиляция 4868320);
#   отдельный overlay-репо делает wipe безвредным, promote_via_ssh не меняется ни строкой
# · Rev: если context-promote перестанет быть mirror-push (или появится второй writer
#   в <org>/ai-platform) → пересмотреть разделение ролей репо
def promote_via_ssh(context: str) -> str:
    """Mirror all refs to <context>/ai-platform via SSH; return remote HEAD sha.

    ▶ ┌git push --mirror git@github.com:<ctx>/ai-platform.git┐ → ○ git ls-remote <target> HEAD → ⊕ split()[0] → ⎋ MIRROR_HEAD

    ## @purpose — Complete ref synchronization (all branches + tags) to the context
    ##            GitHub org over SSH using the operator's ssh-agent key.
    ## @io — ⇥ context: str — GitHub org name → ⎋ str — remote HEAD sha (MIRROR_HEAD)
    ## @complexity — O(refs) push + O(1) ls-remote
    ## @invariants
    ##   - `git push --mirror` raises CalledProcessError on non-zero exit (check=True)
    ##   - REF-0103: GIT_SSH_COMMAND = build_rsync_ssh_opts() (единый SoT ssh_opts) в env —
    ##     git-транспорт получает канонические SSH-флаги (BatchMode/ConnectTimeout/ServerAlive);
    ##   - REF-0103: timeout=DEPLOY_TIMEOUT на push, SSH_READ_TIMEOUT на ls-remote — зависший
    ##     registry/SSH больше не морозил release-checklist step 4 бессрочно;
    ##   - Empty ls-remote HEAD (empty target repo) → returns "" (surfaces as mirror mismatch later)
    ##   - Target repo must already exist (created by `make new-context`)
    """
    target = f"git@github.com:{context}/ai-platform.git"
    logger.info("[IMP:9][promote_via_ssh] Promoting platform to context org: %s", context)
    logger.info("[IMP:8][promote_via_ssh] SSH target: %s", target)

    # REF-0103: SSH-флаги для git-транспорта — через единый SoT (ssh_opts), не ручные -o.
    # GIT_SSH_COMMAND читается самим git; операторский агент остаётся источником ключа.
    git_env = {**os.environ, "GIT_SSH_COMMAND": build_rsync_ssh_opts()}

    subprocess.run(
        ["git", "push", "--mirror", target],
        check=True,
        capture_output=True,
        text=True,
        timeout=DEPLOY_TIMEOUT,
        env=git_env,
    )
    logger.info("[IMP:9][promote_via_ssh] SSH push to %s/ai-platform successful", context)

    ls = subprocess.run(
        ["git", "ls-remote", target, "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=SSH_READ_TIMEOUT,
        env=git_env,
    )
    parts = ls.stdout.split()
    mirror_head = parts[0] if parts else ""
    logger.info("[IMP:8][promote_via_ssh] Mirror HEAD (ls-remote): %s", mirror_head[:7])
    return mirror_head


# endregion FUNC_promote_via_ssh


# region FUNC_verify_mirror
def verify_mirror(mirror_head: str, source_head: str) -> bool:
    """Compare remote mirror HEAD against local HEAD — post-push verification (AC6).

    ▶ ┌mirror_head, source_head┐ → ◇ equal? → ├─yes: [IMP:9] Mirror sync verified ┤ └─no: [IMP:10] FAIL → ⎋ bool

    ## @purpose — Ensure the mirror is consistent before declaring success: the promoted
    ##            org's HEAD must equal the local repository HEAD.
    ## @io — ⇥ context: str — GitHub org (diagnostics only); mirror_head: str — ls-remote HEAD;
    ##       source_head: str — git rev-parse HEAD → ⎋ bool — True when heads match
    ## @complexity — O(1)
    ## @invariants
    ##   - Match → IMP:9 log, True; mismatch → IMP:10 log, False (never raises)
    ##   - Empty mirror_head (empty target repo) is a mismatch → False
    """
    logger.info("[IMP:8][verify_mirror] Source HEAD: %s", source_head)
    logger.info("[IMP:8][verify_mirror] Mirror HEAD: %s", mirror_head)

    if mirror_head == source_head:
        logger.info("[IMP:9][verify_mirror] Mirror sync verified: %s", source_head[:7])
        return True

    logger.error(
        "[IMP:10][verify_mirror] FAIL: mirror HEAD (%s) != source HEAD (%s)",
        mirror_head[:7],
        source_head[:7],
    )
    return False


# endregion FUNC_verify_mirror


# region FUNC_promote_context
def promote_context(
    context: str,
    *,
    audit_log_file: str | None = None,
    ssh_available_fn: Callable[[], bool] | None = None,
    secrets_fn: Callable[[str, str], bool] | None = None,
) -> int:
    """Orchestrator: audit START → SSH push → verify → org-secrets → audit DONE/FAIL → exit code.

    ▶ write_audit_entry(START) → ◇ check_ssh_available → SSH: promote_via_ssh (FATAL if unavailable) → git rev-parse HEAD → ◇ verify_mirror → ◇ ensure_context_secrets (best-effort) → audit DONE/FAIL → ⎋ 0|1

    ## @purpose — Single entry point for `make context-promote`: full lifecycle with audit
    ##            trail and fail-fast diagnostics. Returns the process exit code (0/1).
    ## @io — ⇥ context: str — GitHub org name
    ##       → ⎋ int — 0 success, 1 failure
    ## @complexity — O(refs) push + O(1) verification
    ## @invariants
    ##   - Audit: START at entry; DONE (rc=0) or FAIL on every non-zero return path
    ##   - Fail-fast: SSH unavailable → IMP:10 FATAL, exit 1 (SSH-only channel, 177 W2.1)
    ##   - CalledProcessError / OSError from git/ssh → IMP:10 FAILED + audit FAIL + return 1
    ##   - org-secrets: best-effort (не отменяет успешный promote; сбой → DONE с WARN)
    ##   - Audit log file: AUDIT_LOG_FILE env override, else audit_logger.DEFAULT_LOG_FILE
    ##   - Never raises on operational failures — always returns 0/1
    """
    if not context:
        logger.error(
            "[IMP:10][promote_context] ERROR: CONTEXT required — usage: make context-promote CONTEXT=<context>"
        )
        return 1

    log_file = (
        os.environ.get("AUDIT_LOG_FILE", audit_logger.DEFAULT_LOG_FILE) if audit_log_file is None else audit_log_file
    )
    tag = f"context-promote:{context}"
    audit_logger.write_audit_entry(
        tag, "START", f"starting context promote to {context}/ai-platform", log_file=log_file
    )

    # GitHub SSH case-sensitive: org из overlay context.yaml (TronyxLab), не имя контекста.
    org = resolve_org(context)

    ssh_ok = check_ssh_available if ssh_available_fn is None else ssh_available_fn
    if not ssh_ok():
        # 177 W2.1: HTTPS-fallback удалён — SSH единственный канал (mirror.yml SSH-only с 2026-07-23).
        logger.error("[IMP:10][promote_context] FATAL: SSH unavailable — no fallback channel (177 W2.1)")
        logger.error(
            "[IMP:10][promote_context] Ensure ssh-agent has a key for git@github.com "
            "(MIRROR_SSH_KEY replaces GIT_MIRROR_TOKEN)"
        )
        audit_logger.write_audit_entry(tag, "FAIL", "FATAL: SSH unavailable (SSH-only channel)", log_file=log_file)
        return 1
    logger.info("[IMP:9][promote_context] Promoting platform to context org: %s", org)
    try:
        mirror_head = promote_via_ssh(org)
    # REF-0103: +subprocess.SubprocessError — TimeoutExpired от mirror-бюджетов (DEPLOY_TIMEOUT/
    # SSH_READ_TIMEOUT) должен давать честный FAIL-audit, а не crash оператора
    except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as exc:
        logger.error("[IMP:10][promote_context] FAILED: SSH push to %s/ai-platform failed: %s", org, exc)
        logger.error(
            "[IMP:10][promote_context] Check that target org %s/ai-platform exists and operator has push access",
            org,
        )
        logger.error("[IMP:10][promote_context] FATAL: create %s/ai-platform first", org)
        audit_logger.write_audit_entry(tag, "FAIL", f"SSH push failed ({exc})", log_file=log_file)
        return 1

    try:
        source_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.error("[IMP:10][promote_context] FAILED: cannot resolve local HEAD via git rev-parse: %s", exc)
        audit_logger.write_audit_entry(tag, "FAIL", f"git rev-parse HEAD failed ({exc})", log_file=log_file)
        return 1

    if verify_mirror(mirror_head, source_head):
        # 2026-08-16: авто-провижининг org-секретов контекстной организации (best-effort) —
        # mirror-org CI (core-deploy/deploy-project) без ручной настройки UI.
        secrets_ok = True
        try:
            if secrets_fn is not None:
                secrets_ok = secrets_fn(org, context)
            else:
                from core.internal.deploy.org_secrets_provisioner import ensure_context_secrets  # лениво

                secrets_ok = ensure_context_secrets(org, context)
        # ruff: ignore[BLE001] — best-effort: сбой gh/секретов не отменяет успешный promote
        except Exception as exc:  # noqa: EXC — org-secrets best-effort (promote уже успешен)
            logger.info("[IMP:10][promote_context] org-secrets provisioning error (non-fatal): %s", exc)
            secrets_ok = False
        if secrets_ok:
            logger.info("[IMP:9][promote_context] SUCCESS: platform promoted to %s/ai-platform", org)
            audit_logger.write_audit_entry(tag, "DONE", "completed successfully (rc=0)", log_file=log_file)
        else:
            logger.info(
                "[IMP:10][promote_context] SUCCESS with WARN: platform promoted to %s/ai-platform, "
                "но org-секреты настроены не полностью (см. org-secrets строки выше)",
                org,
            )
            audit_logger.write_audit_entry(
                tag,
                "DONE",
                "completed with org-secrets WARN (rc=0) — see log",
                log_file=log_file,
            )
        return 0

    logger.error("[IMP:10][promote_context] FAIL: mirror HEAD != source HEAD — mirror verification failed")
    audit_logger.write_audit_entry(tag, "FAIL", "mirror verification failed: HEAD mismatch", log_file=log_file)
    return 1


# endregion FUNC_promote_context


# region FUNC_main
def main(
    argv: list[str] | None = None,
    *,
    audit_log_file: str | None = None,
    ssh_available_fn: Callable[[], bool] | None = None,
) -> int:
    """CLI entry point: CONTEXT from argv[1] (SSH-only channel — токены не читаются).

    ▶ ┌sys.argv[1:]┐ → ◇ empty? → [IMP:10] ERROR exit 1 → ○ promote_context(context) → ⎋ rc

    ## @purpose — Process the CLI invocation: parse context and delegate to promote_context().
    ##            Called via `python3 -m core.internal.deploy.context_promoter <CONTEXT>`.
    ## @io — ⇥ argv: list[str] | None — CLI args (default sys.argv[1:]),
    ##          audit_log_file: str | None (DI, W-H DevPlan 163 — audit-файл; None = env/канон),
    ##          ssh_available_fn: Callable | None (DI — SSH-доступность; None = check_ssh_available)
    ##          → ⎋ int — exit code
    ## @complexity — O(1) + promote_context
    ## @invariants
    ##   - Missing CONTEXT → IMP:10 ERROR + return 1 (never IndexError)
    ##   - Никакие токены не принимаются (argv/env) — SSH-only канал (177 W2.1)
    ##   - __main__ block translates the return code via sys.exit() → SystemExit(rc)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        logger.error(
            "[IMP:10][main] ERROR: CONTEXT required — usage: python3 -m core.internal.deploy.context_promoter <CONTEXT>"
        )
        return 1
    context = argv[0]
    return promote_context(context, audit_log_file=audit_log_file, ssh_available_fn=ssh_available_fn)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
