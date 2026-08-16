#!/usr/bin/env python3
# GREP_SUMMARY: setup-node sudoers visudo atomic node node-lifecycle platform PRIVESC hardening S-9 T10.7 python-facade W3.5-1
# STRUCTURE: ▶ ┌NODE_NAME|hostname┐ → ◇ validate ^[a-zA-Z0-9_-]+$ (S-9, T10.7) → ⚡ render_sudoers (NOPASSWD-платформенные, 1:1 heredoc) → ⚡ temp 0440 (tmp_dir) → ◇ visudo -c -f (runner) → ⚡ os.replace atomic (lockout-safe) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Генерация sudoers для платформенной ноды безопасно (DevPlan 164 W3.5-1, SH→Python) —
##           прямое замещение shell core/internal/bootstrap/setup-node.sh (135 LOC). Бизнес-логика
##           generate_sudoers: валидация NODE_NAME (S-9/T10.7, инъекция пути) → рендер sudoers-
##           контента 1:1 с heredoc → temp-файл 0440 → visudo -c -f валидация → os.replace
##           (atomic mv, lockout-safe SC5). ТОЛЬКО sudoers: пользователи (platform/ci-deploy) и
##           SSH-ключи создаются Python-фазой φ2 (lifecycle/helpers/users.py) — НЕ дублируются.
##           Содержимое sudoers НЕ МЕНЯЕТСЯ (1:1): сужение docker/rsync NOPASSWD (PRIVESC S-1/S-2/S-3),
##           nginx systemctl НЕТ (все ноды Docker), audit-путь /var/log/platform/audit.jsonl.
## @scope    Вызывается из lifecycle/phases/system.py φ3 (phase_platform_setup) через setup-node.sh
##           (фасад, `bash setup-node.sh` — аргументы игнорируются, NODE_NAME env | hostname).
##           Чистые функции: validate_node_name / render_sudoers / utc_timestamp (без I/O).
##           Оркестратор: SudoersInstaller (DI runner/facts/tmp_dir/sudoers_dir).
## @invariants
##   - sudoers: temp-файл → visudo -c -f → os.replace (atomic mv, lockout-safe SC5)
##   - On visudo -c failure: оригинальный sudoers НЕ тронут, temp удалён, bootstrap abort (exit 1)
##   - NODE_NAME валидируется `^[a-zA-Z0-9_-]+$` ДО рендера sudoers-фрагмента (S-9, T10.7) —
##     /etc/sudoers.d/platform-${NODE_NAME} не принимает инъекцию пути
##   - Содержимое генерируемого sudoers БАЙТ-ЭКВИВАЛЕНТНО прежнему heredoc (T10.1 сужение):
##     platform NOPASSWD: node-lifecycle.sh + диагностика (ufw status verbose / cat audit.jsonl /
##     ss -tlnp / iptables -t nat -L -n); НИКАКИХ docker compose/exec/ps/logs/restart/stats и
##     rsync NOPASSWD (S-1/S-2/S-3 PRIVESC); НИКАКИХ sudo systemctl nginx (Docker-ноды)
##   - ci-deploy role SEPARATE от ci role (06 §4.2) — в этом sudoers только platform-записи
##   - audit-путь — /var/log/platform/audit.jsonl (ЕДИНЫЙ файл audit_logger.py; audit.log не используется)
##   - temp-файл создаётся в tmp_dir (default /tmp, shell mktemp /tmp/platform-sudoers-XXXXXX),
##     режим 0440 до visudo (sudoers-контракт); финальный файл — os.replace (atomic, переносит mode)
##   - visudo через runner (DI): rc != 0 → FAIL + cleanup + SudoersError; visudo отсутствует
##     (FileNotFound → rc=127 graceful) → тот же FAIL-путь (shell `! visudo` parity)
##   - main() -> int канон: sys.exit только в __main__; root-guard через facts.is_root() (W4b)
## @rationale Q: Почему Python, а не shell?
##            A: Языковая политика (root AGENTS.md) — BUSINESS_LOGIC >100 LOC на Python.
##            generate_sudoers — уникальная бизнес-логика (парсинг-не нужен, но рендер + visudo +
##            atomic-mv протокол тестируем только через DI). Shell-фасад <100 LOC (прямое замещение).
##            Q: Почему содержимое НЕ меняется?
##            A: Решение W10 T10.1 (DevPlan 136): сужение docker/rsync NOPASSWD верифицировано на
##            test-VPS (core_deliverer rsync user=root БЕЗ sudo; platform/ci-deploy в docker group).
##            Любое изменение = ослабление hardening — гейт tests/gates/test_gate_sudoers_hardening.py
##            фиксирует контент как инвариант (теперь через render_sudoers()).
##            Q: Почему chmod после записи, а не до (shell chmod до cat)?
##            A: Конечное состояние идентично (0440 — sudoers-контракт); root обходит perms при
##            записи, а при тестах (non-root) запись в 0440 до записи невозможна. Безопасность
##            не меняется — файл НЕ является валидным sudoers до visudo -c (SC5).
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — Created (SH→Python setup-node.sh 135 LOC → фасад <100)
## ⚠️ TRAP[DECISION] · 2026-08-14 · HI · sudoers сужение platform: docker/rsync NOPASSWD удалены (T10.1)
## · (мигрирован из шапки прежнего setup-node.sh, TRAP[DECISION] 2026-08-05)
## · Rejected: оставить docker compose */exec */rsync * (риск: root-escape / root-запись — S-1/S-2/S-3)
## · Reason: верификация W10 на test-VPS (103.88.243.151, test-e2e): (а) core_deliverer.py rsync идёт
## ·   user=root по SSH БЕЗ sudo (grep: 0 вызовов sudo docker / sudo rsync в core/); (б) platform и
## ·   ci-deploy в docker group (φ2 phases/system.py:262-287) → docker-команды доступны напрямую,
## ·   sudo не нужен; (в) deploy-modules/DeployOrchestrator исполняются как root (bootstrap) —
## ·   sudo не задействован. Оставлено: node-lifecycle.sh (контролируемый операционный скрипт,
## ·   /opt/platform root-owned — не symlink-вектор), диагностика (ufw/ss/iptables), cat audit.jsonl.
## · Rev: если появится реальный потребитель sudo docker/rsync (платформенный, не user-проект) —
## ·   добавить точечные записи с конкретными флагами + gate-тест; консенсус-пересмотр 2026-10-21.
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import re
import socket
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from core.internal.shared import deploy_paths  # SoT платформенных баз (DEFAULT_PLATFORM_BASE)
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.subprocess_io import CommandRunner, default_command_runner
from core.internal.shared.timeouts import SUDOERS_CMD_TIMEOUT

logger = logging.getLogger(__name__)

# ── Канонические константы (shell-parity с прежним setup-node.sh) ──
# Канон имени ноды (S-9): sudoers-файл получает имя из NODE_NAME —
# инъекция пути (/, .., пробел) в имя = запись в произвольный файл /etc/sudoers.d/….
NODE_NAME_RE: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]+$")
DEFAULT_SUDOERS_DIR: str = "/etc/sudoers.d"
DEFAULT_TMP_DIR: str = "/tmp"
DEFAULT_PLATFORM_ROOT: str = deploy_paths.DEFAULT_PLATFORM_BASE
# sudoers-контракт: файл читаем только owner+group (sudoers strict mode check)
SUDOERS_FILE_MODE: int = 0o440
TMP_PREFIX: str = "platform-sudoers-"
# Exit-контракт setup-node.sh: 0=ok, 1=любая ошибка (root-guard / invalid name / visudo fail)
EXIT_OK: int = 0
EXIT_ERROR: int = 1


# region EXCEPTION_SudoersError
class SudoersError(Exception):
    """Любая ошибка generate_sudoers — main() маппит в EXIT_ERROR=1 (shell exit 1 parity).

    ## @purpose — Fail-fast: invalid NODE_NAME / visudo -c FAIL → sudoers НЕ записан.
    ## @rationale — Единый тип для обеих fail-веток shell (exit 1): валидация имени и visudo.
    """


# endregion EXCEPTION_SudoersError


# region FUNC_validate_node_name
def validate_node_name(node_name: str) -> bool:
    """Валидация NODE_NAME по канону S-9 (T10.7): `^[a-zA-Z0-9_-]+$`.

    ▶ ┌node_name┐ → ◇ NODE_NAME_RE.fullmatch? → ⎋ True | False

    ## @purpose — Валидация ДО рендера sudoers-фрагмента: имя ноды попадает в путь
    ##            /etc/sudoers.d/platform-${NODE_NAME} и в контент — инъекция пути
    ##            (/, .., пробел, точка) = запись в произвольный файл sudoers.d.
    ## @io — ⇥ node_name: str → ⎋ bool (True = безопасное имя)
    ## @complexity — O(N) — длина имени
    ## @invariants — Допустимы ТОЛЬКО [a-zA-Z0-9_-] (shell [[ =~ ]] parity, полное совпадение)
    """
    return bool(node_name) and NODE_NAME_RE.fullmatch(node_name) is not None


# endregion FUNC_validate_node_name


# region FUNC_utc_timestamp
def utc_timestamp(now: datetime | None = None) -> str:
    """UTC-метка генерации — shell `date -u '+%Y-%m-%dT%H:%M:%SZ'` parity.

    ▶ ┌now?┐ → ◇ None? datetime.now(timezone.utc) → ⊕ strftime → ⎋ str

    ## @purpose — Чистый генератор timestamp (DI-часы: тесты передают фиксированный datetime;
    ##            production — реальные UTC-часы). Вставляется в шапку sudoers-контента.
    ## @io — ⇥ now: Optional[datetime] (None = сейчас) → ⎋ str "YYYY-MM-DDTHH:MM:SSZ"
    ## @complexity — O(1)
    """
    current = now if now is not None else datetime.now(timezone.utc)
    return current.strftime("%Y-%m-%dT%H:%M:%SZ")


# endregion FUNC_utc_timestamp


# region FUNC_render_sudoers
def render_sudoers(node_name: str, platform_root: str, timestamp: str) -> str:
    """Рендер sudoers-контента — БАЙТ-ЭКВИВАЛЕНТ прежнего heredoc setup-node.sh (1:1).

    ▶ ┌node_name, platform_root, timestamp┐ → ⊕ f-string шаблон → ⎋ str (контент + trailing \n)

    ## @purpose — Единственный источник sudoers-контента (W3.5-1): гейт test_gate_sudoers_hardening
    ##            парсит ЭТУ функцию (вместо heredoc .sh), unit-тесты проверяют правила.
    ##            Содержимое НЕ МЕНЯЕТСЯ от shell-версии (T10.1 сужение, keep-правила, audit.jsonl).
    ## @io — ⇥ node_name (валидирован ДО вызова), platform_root (PLATFORM_ROOT | /opt/platform),
    ##         timestamp (utc_timestamp) → ⎋ str — полный sudoers-файл (последняя строка + \n)
    ## @complexity — O(C) — C = длина контента
    ## @invariants
    ##   - 1:1 heredoc: `${node_name}`, `${PLATFORM_ROOT}`, `$(date -u ...)` — все три подстановки
    ##     shell-heredoc воспроизведены параметрами (незакавыченный <<EOF = expansion)
    ##   - PRIVESC-паттерны (docker compose/exec/run, rsync *) — ТОЛЬКО в комментариях-объяснениях
    ##     (гейт сканирует некомментарные строки — allowlist пуст)
    ##   - audit-путь /var/log/platform/audit.jsonl (D1/T10.9 — ЕДИНЫЙ audit-файл)
    ##   - Гейт-инвариант: platform ALL=(root) NOPASSWD: {platform_root}/core/internal/bootstrap/node-lifecycle.sh
    """
    return (
        f"# core sudoers — {node_name}\n"
        f"# Generated by setup-node.sh at {timestamp}\n"
        "# DO NOT edit manually — managed by core bootstrap\n"
        "#\n"
        "# sudoers СУЖЕН: docker compose/exec/ps/logs/restart/stats и rsync NOPASSWD удалены (PRIVESC root-escape).\n"
        "# platform/ci-deploy в docker group (φ2) → docker-операции выполняются напрямую, sudo не нужен.\n"
        "# Доставка core — rsync user=root (SSH), sudo rsync не используется.\n"
        "# Gate: tests/gates/test_gate_sudoers_hardening.py.\n"
        "# nginx systemctl sudoers удалены — обе ноды Docker (nginx в контейнере, systemctl unit not found).\n"
        "# Rev: вернуть при появлении non-Docker ноды.\n"
        "\n"
        "# platform user: platform operations (контролируемый операционный скрипт, /opt/platform root-owned)\n"
        f"platform ALL=(root) NOPASSWD: {platform_root}/core/internal/bootstrap/node-lifecycle.sh\n"
        "\n"
        "# platform user: diagnostic commands\n"
        "platform ALL=(root) NOPASSWD: /usr/sbin/ufw status verbose\n"
        "platform ALL=(root) NOPASSWD: /usr/bin/cat /var/log/platform/audit.jsonl\n"
        "platform ALL=(root) NOPASSWD: /usr/sbin/ss -tlnp\n"
        "platform ALL=(root) NOPASSWD: /usr/sbin/iptables -t nat -L -n\n"
        "\n"
        "# ci-deploy user: NO docker/nginx commands via sudo\n"
        "# ci-deploy is in docker group → direct docker socket access (no sudo needed)\n"
        "# /usr/bin/docker compose * intentionally NOT granted — principle of least privilege (06 §4.2)\n"
        "# Role ci-deploy is SEPARATE from role ci — different scope, different sudoers entries\n"
        "# nginx systemctl reload/status не предоставляются (nginx — Docker module, systemctl unit not found)\n"
    )


# endregion FUNC_render_sudoers


# region FUNC_resolve_platform_root
def resolve_platform_root(env: Mapping[str, str] | None = None) -> str:
    """Резолв PLATFORM_ROOT для sudoers-контента (shell paths.sh `${PLATFORM_ROOT:-/opt/platform}` parity).

    ▶ ┌env?┐ → ◇ PLATFORM_ROOT? → value | DEFAULT_PLATFORM_ROOT → ⎋ str

    ## @purpose — platform_root интерполируется в NOPASSWD node-lifecycle.sh путь.
    ##            Shell брал значение из paths.sh (env → /opt/platform). Здесь — тот же канон,
    ##            с hermetic DI (env=None → os.environ).
    ## @io — ⇥ env: Optional[Mapping] → ⎋ str platform_root
    ## @complexity — O(1)
    """
    env_map: Mapping[str, str] = env if env is not None else os.environ
    return env_map.get("PLATFORM_ROOT") or DEFAULT_PLATFORM_ROOT


# endregion FUNC_resolve_platform_root


# region FUNC_resolve_node_name
def resolve_node_name(env: Mapping[str, str] | None = None) -> str:
    """Резолв имени ноды: NODE_NAME env → hostname (shell `${NODE_NAME:-$(hostname)}` parity).

    ▶ ┌env?┐ → ◇ NODE_NAME? → value | socket.gethostname() → ⎋ str

    ## @purpose — setup-node.sh main() без аргументов: имя из env или hostname.
    ##            Аргументы CLI ИГНОРИРУЮТСЯ (shell main "$@" не передавал в generate_sudoers —
    ##            main сам резолвил node_name и вызывал generate_sudoers "$node_name").
    ## @io — ⇥ env: Optional[Mapping] → ⎋ str node_name
    ## @complexity — O(1)
    ## @invariants — NODE_NAME может быть пустым/пробельным → валидация S-9 отклонит (exit 1)
    """
    env_map: Mapping[str, str] = env if env is not None else os.environ
    return env_map.get("NODE_NAME") or socket.gethostname()


# endregion FUNC_resolve_node_name


# region CLASS_SudoersInstaller
class SudoersInstaller:
    """Оркестратор генерации sudoers (W4c-паттерн: конструкторная DI runner/facts/пути).

    ## @purpose — temp-файл 0440 → visudo -c -f (runner) → os.replace (atomic mv, lockout-safe SC5).
    ##            Все I/O-каналы инъектируемы: runner (visudo subprocess), tmp_dir (mktemp-каталог),
    ##            sudoers_dir (целевой каталог), platform_root/timestamp (данные контента).
    ## @io — ⇥ runner/facts/tmp_dir/sudoers_dir (None = дефолты) → ⎋ экземпляр оркестратора
    ## @complexity — O(1) конструкция; install() — O(C + V) (C=контент, V=visudo)
    ## @invariants
    ##   - runner=None → default_command_runner() (канон run_subprocess, C10/B4)
    ##   - facts=None → default_env_facts() (is_root для main; в install не используется)
    ##   - tmp_dir=None → DEFAULT_TMP_DIR (/tmp); sudoers_dir=None → DEFAULT_SUDOERS_DIR (/etc/sudoers.d)
    ##   - install(): invalid node_name → SudoersError; visudo rc!=0 → SudoersError (temp удалён,
    ##     целевой файл НЕ тронут); success → os.replace (atomic, target mode = temp mode 0440)
    ## @rationale — W4c (DevPlan 160 AF-4): тесты передают FakeRunner/FakeFacts + tmp_path —
    ##            ноль monkeypatch (W-H DevPlan 163). Разделение чистой генерации (render_sudoers)
    ##            и I/O (install) — контент тестируется без файловой системы.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        facts: EnvironmentFacts | None = None,
        tmp_dir: str | Path | None = None,
        sudoers_dir: str | Path | None = None,
    ) -> None:
        """Инициализация с ленивыми дефолтами (default_command_runner / default_env_facts).

        ▶ ┌runner?, facts?, tmp_dir?, sudoers_dir?┐ → ◇ None → дефолт → ⊕ self.* → ⎋ None

        ## @invariants — Пути как str|Path → Path нормализация на хранение
        """
        self.runner: CommandRunner = runner if runner is not None else default_command_runner()
        self.facts: EnvironmentFacts = facts if facts is not None else default_env_facts()
        self.tmp_dir: Path = Path(tmp_dir) if tmp_dir is not None else Path(DEFAULT_TMP_DIR)
        self.sudoers_dir: Path = Path(sudoers_dir) if sudoers_dir is not None else Path(DEFAULT_SUDOERS_DIR)

    # region FUNC_SudoersInstaller_install
    def install(
        self,
        node_name: str,
        *,
        platform_root: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        """Генерация sudoers: validate → render → temp 0440 → visudo -c → os.replace (SC5).

        ▶ ┌node_name, platform_root?, timestamp?┐ → ◇ validate? SudoersError → ⚡ render + temp write 0440
          → ◇ visudo -c (runner, SUDOERS_CMD_TIMEOUT) → rc!=0? cleanup + SudoersError │ → ⚡ os.replace → ⎋ None

        ## @purpose — Ядро generate_sudoers (shell 1:1): mktemp → chmod 0440 → cat → visudo -c -f →
        ##            mv. Финальный файл /etc/sudoers.d/platform-${node_name}.
        ## @io — ⇥ node_name (валидируется здесь ДО рендера — S-9), platform_root (None → env|/opt/platform),
        ##         timestamp (None → utc_timestamp()) → ⎋ None ⚡ SudoersError (invalid name / visudo fail)
        ## @complexity — O(C + V) — рендер + visudo subprocess
        ## @invariants
        ##   - Валидация имени ДО рендера и ДО создания файла (S-9/T10.7)
        ##   - tempfile.mkstemp(dir=tmp_dir, prefix=TMP_PREFIX) — /tmp/platform-sudoers-XXXXXX parity
        ##   - temp mode 0440 (SUDOERS_FILE_MODE) после записи; os.replace переносит mode на target
        ##   - visudo fail: temp удалён (missing_ok), целевой файл НЕ существует/НЕ тронут (SC5)
        ##   - os.replace — atomic mv: нет окна частично записанного sudoers (lockout-safe)
        ##   - Целевой путь: sudoers_dir / f"platform-{node_name}" (без . — sudoers.d канон)
        """
        if not validate_node_name(node_name):
            msg = f"NODE_NAME '{node_name}' invalid — must match ^[a-zA-Z0-9_-]+$ (S-9, T10.7)"
            raise SudoersError(msg)

        effective_platform_root = platform_root if platform_root is not None else resolve_platform_root()
        effective_timestamp = timestamp if timestamp is not None else utc_timestamp()
        content = render_sudoers(node_name, effective_platform_root, effective_timestamp)
        logger.info("[IMP:8][setup-node][sudoers] START: Generating sudoers for platform node: %s", node_name)

        target = self.sudoers_dir / f"platform-{node_name}"
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=TMP_PREFIX, dir=str(self.tmp_dir))
        tmp_path = Path(tmp_name)
        try:
            self._write_temp(tmp_path, tmp_fd, content)
            self._validate_visudo(tmp_path)
            tmp_path.replace(target)  # os.replace — atomic mv (lockout-safe SC5, mode переносится)
            logger.info("[IMP:9][setup-node][sudoers] DONE: sudoers generated and validated: %s", target)
        except SudoersError:
            tmp_path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            msg = f"sudoers write failed: {exc}"
            raise SudoersError(msg) from exc

    # endregion FUNC_SudoersInstaller_install

    # region FUNC_SudoersInstaller__write_temp
    @staticmethod
    def _write_temp(tmp_path: Path, tmp_fd: int, content: str) -> None:
        """Запись temp-файла 0440 (shell `cat > tmp + chmod 0440` parity).

        ▶ ┌tmp_path, tmp_fd, content┐ → ○ fdopen write → ○ chmod 0440 → ⎋ None

        ## @purpose — mktemp-файл: контент + sudoers-режим 0440. chmod ПОСЛЕ записи
        ##            (финальное состояние = shell parity; root обходит perms при записи,
        ##            а non-root тест-процесс не смог бы писать в 0440 до записи — @rationale).
        ## @io — ⇥ tmp_path (mkstemp Path), tmp_fd (mkstemp fd — закрывается fdopen), content → ⎋ None
        ## @complexity — O(C) — длина контента
        ## @invariants — tmp_fd закрывается fdopen-context'ом; mode = SUDOERS_FILE_MODE (0440)
        """
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        tmp_path.chmod(SUDOERS_FILE_MODE)
        logger.info("[IMP:8][setup-node][sudoers] Temp sudoers written (mode %o): %s", SUDOERS_FILE_MODE, tmp_path)

    # endregion FUNC_SudoersInstaller__write_temp

    # region FUNC_SudoersInstaller__validate_visudo
    def _validate_visudo(self, tmp_path: Path) -> None:
        """visudo -c -f guard (SC5): rc != 0 → SudoersError (оригинал НЕ тронут).

        ▶ ┌tmp_path┐ → ○ runner.run(visudo -c -f, SUDOERS_CMD_TIMEOUT) → ◇ rc!=0? SudoersError │ → ⎋ None

        ## @purpose — [IMP:10] CRITICAL: validate before atomic replace — lockout-safe.
        ##            Вынесено из install (внутренняя функция — абстракция raise, ruff raise-within-try).
        ## @io — ⇥ tmp_path: Path (temp-файл 0440) → ⎋ None ⚡ SudoersError (visudo rc!=0)
        ## @complexity — O(V) — visudo subprocess
        ## @invariants — visudo отсутствует (FileNotFound → rc=127 graceful) → тот же FAIL-путь
        ##               (shell `! visudo` parity: sudoers НЕ записан, temp удаляется вызывающим)
        """
        proc = self.runner.run(
            ["visudo", "-c", "-f", str(tmp_path)],
            check=False,
            timeout=SUDOERS_CMD_TIMEOUT,
        )
        if proc.returncode != 0:
            visudo_err = (proc.stderr or "").strip() or f"visudo rc={proc.returncode}"
            logger.error(
                "[IMP:8][setup-node][sudoers] FAIL: visudo -c FAILED: %s — original sudoers NOT touched",
                visudo_err,
            )
            msg = f"visudo -c FAILED: {visudo_err} — original sudoers NOT touched"
            raise SudoersError(msg)

    # endregion FUNC_SudoersInstaller__validate_visudo


# endregion CLASS_SudoersInstaller


# region FUNC_main
def main(
    argv: Sequence[str] | None = None,  # ruff: ignore[ARG001] — аргументы ИГНОРИРУЮТСЯ (shell main "$@" parity)
    *,
    env: Mapping[str, str] | None = None,
    facts: EnvironmentFacts | None = None,
    runner: CommandRunner | None = None,
    tmp_dir: str | Path | None = None,
    sudoers_dir: str | Path | None = None,
) -> int:
    """Entry point: root-guard → node_name (NODE_NAME|hostname) → install → exit 0|1.

    ▶ ┌argv?, env?, facts?, runner?, tmp_dir?, sudoers_dir?┐ → ◇ is_root? FATAL exit 1
      → ⚡ resolve_node_name → ⚡ install (SudoersError → FATAL exit 1) → ⎋ exit 0

    ## @purpose — main()-канон: sys.exit только в __main__; DI facts/runner/env (W4b/W4c).
    ##            root-guard через facts.is_root() (shell `id -u` parity, exit 1).
    ## @io — ⇥ DI-каналы → ⎋ int exit {0,1}
    ## @complexity — O(C + V) — install
    ## @invariants
    ##   - НЕ root → [IMP:10] FATAL "must run as root" exit 1 (shell parity)
    ##   - node_name = NODE_NAME env | hostname (shell `${NODE_NAME:-$(hostname)}` parity);
    ##     argv игнорируется (shell main "$@" не использовал аргументы)
    ##   - Любой SudoersError (invalid name / visudo fail / OSError) → FATAL exit 1
    ##   - Успех → log_step DONE + exit 0
    """
    env_map: Mapping[str, str] = env if env is not None else os.environ
    facts_resolved = facts if facts is not None else default_env_facts()
    if not facts_resolved.is_root():
        logger.error("[IMP:10][setup-node][main] ERROR: must run as root")
        return EXIT_ERROR

    node_name = resolve_node_name(env_map)
    installer = SudoersInstaller(runner=runner, facts=facts_resolved, tmp_dir=tmp_dir, sudoers_dir=sudoers_dir)
    try:
        installer.install(node_name, platform_root=resolve_platform_root(env_map), timestamp=None)
    except SudoersError as exc:
        logger.error("[IMP:10][setup-node][main] ERROR: %s", exc)
        return EXIT_ERROR

    logger.info("[IMP:9][setup-node][main] DONE: Node setup complete: sudoers generated for node=%s", node_name)
    return EXIT_OK


# endregion FUNC_main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
