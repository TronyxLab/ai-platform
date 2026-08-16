#!/usr/bin/env python3
# GREP_SUMMARY: security-posture S4 sshd sshd-T maxstartups hardening drop-in apply-sshd parse_sshd_effective_config classify-directive MACs KexAlgorithms AllowUsers
# STRUCTURE: ▶ parse_sshd_effective_config(sshd -T) → dict[key]=value ┌базовые 3 директивы + maxstartups + 9 расширенных┐ → ○ _classify_directive (5 форм) → ○ problems → ⎋ CheckResult ┤
#            ○ apply_sshd_dropin: content-match no-op → ⚡ atomic write + remove superseded → ○ systemctl reload → fallback service → ⎋ bool
# region MODULE_CONTRACT
## @purpose  S4: SSH-поверхность ноды (DevPlan 134 L2, W3/W10/162 W2-1). Проверка эффективного
##           конфига через sshd -T (PermitRootLogin/PasswordAuthentication/PubkeyAuthentication +
##           MaxStartups ≥ 30:50:200 + 9 расширенных директив W10 T10.4) и идемпотентный apply
##           hardening drop-in (99-platform-ssh-hardening.conf, вызов из φ1 --apply-sshd).
##           Извлечено из монолита security_posture.py (план 170 W6-D1): god check_sshd
##           (73 LOC/CC29) → parse_sshd_effective_config (PURE) + _classify_directive (PURE,
##           data-driven 5 форм) + _check_maxstartups.
## @scope    Вызывается run_all_checks (run.py, check-режим) и CLI --apply-sshd (bootstrap φ1,
##           lifecycle/phases/system.py шаг 5.6). Импортирует _shared + shared/timeouts +
##           shared/atomic_writer (канон E5) — циклических зависимостей нет.
## @invariants
##   - Требует root (sshd -T) — гарантируется root-check в main
##   - sshd -T печатает ЭФФЕКТИВНЫЙ конфиг (включая drop-in sshd_config.d) — проверяем эффективное
##     значение, не исходный sshd_config; ненаблюдаемое значение → PASS (graceful)
##   - Расширенные директивы — ТОЛЬКО при наличии в выводе sshd -T; allowusers пуст → FAIL,
##     отсутствует строка → skip (graceful)
##   - apply: content-match no-op (reload НЕ вызывается); reload только при изменении ИЛИ удалении
##     superseded-файла (systemctl → service fallback); запись удалась, reload не удался → False
##   - S4: не мутирует систему в check-режиме; --apply-sshd — единственная мутация (идемпотентная)
## @rationale apply в sshd_policy (не в lifecycle/phases): sshd-политика живёт в одном модуле
##            с S4-проверкой (единый SoT эффективного значения); фаза вызывает CLI.
##            Максимум-инвариант (134 D4): MaxStartups drop-in переживает apt-обновления sshd_config.
## @changes 2026-08-15 | план 170 W6-D1 — извлечено из security_posture.py (S4 + apply, 1:1 тела);
##            check_sshd CC29 → parse_sshd_effective_config + _classify_directive + _check_maxstartups
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from core.internal.shared.atomic_writer import atomic_write_text  # E5: канон атомарной записи (drop-in)
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT

from ._shared import SSHD_PARTS_MIN, STATUS_FAIL, STATUS_PASS, CheckResult
from ._shared import probe as _probe

logger = logging.getLogger(__name__)

# ── sshd MaxStartups (DevPlan 136 W3) ──
# drop-in в sshd_config.d (канон drop-in, НЕ правка основного sshd_config) — переживает
# apt-обновления sshd_config; sshd -T (S4) читает эффективное значение ВКЛЮЧАЯ drop-in.
SSHD_MAXSTARTUPS_DROPIN = "/etc/ssh/sshd_config.d/99-platform-maxstartups.conf"
# Минимально допустимое эффективное значение MaxStartups (start:rate:full).
# 30:50:200 — защита SSH от connection-storm при параллельных деплоях/healthcheck-прокидываниях.
# Дефолт OpenSSH = 10:30:100 < минимума → FAIL, пока drop-in не применён бутстрапом.
SSHD_MAXSTARTUPS_MIN = (30, 50, 200)
SSHD_MAXSTARTUPS_STR = "30:50:200"
_MAXSTARTUPS_RE = re.compile(r"^(\d+):(\d+):(\d+)$")

# ── sshd hardening (DevPlan 162 W2-1) ──
# Полный харденинг-набор drop-in (superset старого maxstartups drop-in: MaxStartups включён).
# Платформенный канон эффективных значений сверяется в S4 (sshd -T читает включая drop-in).
# AllowUsers — статический список имён, создаваемых в φ2 (см. TRAP[DECISION] ниже).
SSHD_HARDENING_DROPIN = "/etc/ssh/sshd_config.d/99-platform-ssh-hardening.conf"
# Имена ssh-пользователей платформы (создаются в φ2 lifecycle phases):
# root (оператор), platform (сервисный), ci-deploy (forced-command deploy-канал).
SSHD_ALLOW_USERS: tuple[str, ...] = ("root", "platform", "ci-deploy")
# MACs-политика: ТОЛЬКО *-etm@openssh.com (без hmac-sha1/umac-64 — слабые алгоритмы S4-канона).
# Модульная константа (не литерал в docstring/строке контента) — doxygen не парсит @openssh
# как команду в коде; желаемый drop-in использует её через f-string.
SSHD_HARDENING_MACS = "hmac-sha2-256-etm@openssh.com,hmac-sha2-512-etm@openssh.com"

# ── S4 (W10 T10.4): расширенные sshd-директивы (проверяемы через sshd -T) ──
# Каждая директива: (ключ sshd -T, ожидание, fail-сообщение). Проверяется ТОЛЬКО если директива
# присутствует в выводе sshd -T (ненаблюдаемые → skip, graceful — фикстуры без строки не падают).
# AllowUsers: отсутствие строки в sshd -T = нода БЕЗ allowlist (Ubuntu печатает allowusers только
# при явной настройке) → skip (graceful — не ложнопозитивный FAIL на дефолтных нодах);
# allowusers задан ПУСТЫМ (директива присутствует без списка) → FAIL (явная политика нарушена).
_SSHD_WEAK_KEX = ("diffie-hellman-group1-sha1", "diffie-hellman-group14-sha1", "diffie-hellman-group-exchange-sha1")
_SSHD_WEAK_CIPHERS = ("arcfour", "3des-cbc", "aes128-cbc", "aes192-cbc", "aes256-cbc", "des-cbc", "blowfish-cbc")
_SSHD_WEAK_MACS = ("hmac-md5", "hmac-md5-96", "hmac-sha1", "hmac-sha1-96", "umac-64", "umac-64@openssh.com")
SSHD_CLIENT_ALIVE_INTERVAL_MIN = 300
SSHD_LOGIN_GRACE_TIME_MAX = 120
# (sshd -T key, expected-or-checker, label)
# checker-формы: ("eq", value) — равенство; ("gte", n) — >=; ("lte", n) — <=;
# ("not_contains_any", weak_list) — ни один слабый алгоритм; ("present_nonempty",) — не пуст
_SSHD_EXTRA_DIRECTIVES: list[tuple[str, tuple[object, ...], str]] = [
    ("allowusers", ("present_nonempty",), "AllowUsers unset (no user allowlist — every user may ssh)"),
    (
        "clientaliveinterval",
        ("gte", SSHD_CLIENT_ALIVE_INTERVAL_MIN),
        f"ClientAliveInterval < {SSHD_CLIENT_ALIVE_INTERVAL_MIN}s (idle connections linger)",
    ),
    ("permituserenvironment", ("eq", "no"), "PermitUserEnvironment=yes (env injection into sshd session)"),
    ("x11forwarding", ("eq", "no"), "X11Forwarding=yes (X11 channel exposure)"),
    ("allowtcpforwarding", ("eq", "no"), "AllowTcpForwarding=yes (TCP tunnel via ssh)"),
    ("kexalgorithms", ("not_contains_any", _SSHD_WEAK_KEX), "weak KexAlgorithms present (diffie-hellman-*-sha1)"),
    ("ciphers", ("not_contains_any", _SSHD_WEAK_CIPHERS), "weak Ciphers present (arcfour/cbc/3des)"),
    ("macs", ("not_contains_any", _SSHD_WEAK_MACS), "weak MACs present (md5/sha1/umac-64)"),
    (
        "logingracetime",
        ("lte", SSHD_LOGIN_GRACE_TIME_MAX),
        f"LoginGraceTime > {SSHD_LOGIN_GRACE_TIME_MAX}s (slow-brute window)",
    ),
]
# UsePAM сознательно НЕ проверяется (9 директив ≥ 8 по T10.4): самостоятельной security-ценности
# не имеет — связка «PasswordAuthentication=no + PubkeyAuthentication=yes» уже закрывает парольный
# вход; ожидание UsePAM зависит от PAM-стека (ложно-позитивный риск, документировано W10 T10.4).


# region FUNC_parse_sshd_effective_config
## @purpose  PURE: парсинг вывода sshd -T в dict[key]=value (W10 T10.4 — пусто-значные директивы
##           типа allowusers без списка тоже фиксируются: value "").
## @io       ⇥ text: str (stdout sshd -T) → ⎋ dict[str, str] (ключи lowercase, value lowercase/пусто)
## @complexity O(n) — n = строк вывода
## @invariants  Пустые строки пропускаются; value = второе поле (lowercase) или "" при одном поле;
##              исторический парсер требовал ≥2 частей → present_nonempty для AllowUsers был
##              мёртвым кодом — фиксация пустых значений чинит это
def parse_sshd_effective_config(text: str) -> dict[str, str]:
    """Parse `sshd -T` output → {directive: value} (lowercase; empty value preserved)."""
    settings: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        # W10 T10.4: пусто-значные директивы (allowusers без списка) тоже фиксируются — value ""
        settings[parts[0].lower()] = parts[1].lower() if len(parts) >= SSHD_PARTS_MIN else ""
    return settings


# endregion FUNC_parse_sshd_effective_config


# region FUNC__classify_directive
## @purpose  PURE: data-driven классификатор директивы sshd-конфига против checker-формы
##           (5 форм: eq/neq/in/gte/lte/not_contains_any/present_nonempty). Возвращает
##           fail-сообщение или None (директива соответствует политике).
## @io       ⇥ key: str, value: str (из parse_sshd_effective_config), check: tuple[object, ...]
##              (форма + аргумент), fail_msg: str → ⎋ str | None
## @complexity O(1) — одна форма, константные ветки
## @invariants  Сообщения 1:1 с каноном монолита (тесты проверяют подстроки "KexAlgorithms",
##              "ClientAliveInterval", "AllowUsers", ...); gte/lte при нечисловом value → fail
##              "(unparseable integer)"; not_contains_any → первые 4 найденных слабых алгоритма
def _classify_directive(key: str, value: str, check: tuple[object, ...], fail_msg: str) -> str | None:
    """Classify a single sshd directive against its checker-form → fail message or None."""
    kind = check[0]
    if kind == "eq" and value != check[1]:
        return f"{fail_msg} (current: {value})"
    if kind == "neq" and value == check[1]:
        return fail_msg
    if kind == "in" and value not in cast(tuple[str, ...], check[1]):
        return fail_msg.format(value=value)
    if kind == "gte":
        try:
            if int(value) < int(cast(int, check[1])):  # gte-аргумент — числовой порог (int в _SSHD_EXTRA_DIRECTIVES)
                return f"{fail_msg} (current: {value})"
        except ValueError:
            return f"{key}={value} (unparseable integer)"
        return None
    if kind == "lte":
        try:
            if int(value) > int(cast(int, check[1])):  # lte-аргумент — числовой порог (int в _SSHD_EXTRA_DIRECTIVES)
                return f"{fail_msg} (current: {value})"
        except ValueError:
            return f"{key}={value} (unparseable integer)"
        return None
    if kind == "present_nonempty" and not value:
        return fail_msg
    if kind == "not_contains_any":
        weak = [w for w in cast(tuple[str, ...], check[1]) if w in value]
        if weak:
            return f"{fail_msg} (found: {', '.join(weak[:4])})"
    return None


# endregion FUNC__classify_directive


# region FUNC__parse_maxstartups
## @purpose  Парсер эффективного MaxStartups 'start:rate:full' → (start, rate, full).
## @io       ⇥ value: str (lowercased из sshd -T) → ⎋ tuple[int,int,int] | None (malformed)
## @complexity O(1) — regex
## @invariants  Не-числовой формат (напр. 'random:50:200', OpenSSH ≥9.6) → None → FAIL в S4
##              (политика должна быть явной; числовой канон платформы — 30:50:200)
def _parse_maxstartups(value: str) -> tuple[int, int, int] | None:
    """Parse 'start:rate:full' → (start, rate, full); None if malformed."""
    m = _MAXSTARTUPS_RE.match(value.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


# endregion FUNC__parse_maxstartups


# region FUNC__check_maxstartups
## @purpose  MaxStartups-проверка эффективного конфига (DevPlan 136 W3): покомпонентный порог
##           ≥ 30:50:200. Отдельная функция — снижение CC check_sshd (извлечение из god-цикла).
## @io       ⇥ settings: Mapping[str, str] (из parse_sshd_effective_config) → ⎋ list[str]
##              (fail-сообщения; пусто = соответствует)
## @complexity O(1) — одна директива + regex
## @invariants  Ненаблюдаемый maxstartups (нет в sshd -T) → пусто (PASS, graceful — тест-фикстуры
##              без строки; реальный sshd -T всегда печатает дефолт 10:30:100 → FAIL)
def _check_maxstartups(settings: Mapping[str, str]) -> list[str]:
    """Check effective MaxStartups >= 30:50:200 → fail messages (empty = compliant)."""
    raw = settings.get("maxstartups", "")
    if not raw:
        return []
    ms = _parse_maxstartups(raw)
    if ms is None:
        return [f"MaxStartups={raw} (unparseable — expected start:rate:full)"]
    if any(a < b for a, b in zip(ms, SSHD_MAXSTARTUPS_MIN, strict=True)):
        return [f"MaxStartups={raw} < {SSHD_MAXSTARTUPS_STR} (drop-in 99-platform-maxstartups.conf missing or too low)"]
    return []


# endregion FUNC__check_maxstartups


# region FUNC_check_sshd
## @purpose  S4: SSH-поверхность через sshd -T (эффективный конфиг): PermitRootLogin
##           prohibit-password|no, PasswordAuthentication no, PubkeyAuthentication yes,
##           MaxStartups ≥ 30:50:200 (покомпонентно; DevPlan 136 W3) + 9 расширенных директив
##           (AllowUsers, ClientAliveInterval, PermitUserEnvironment, X11Forwarding,
##           AllowTcpForwarding, KexAlgorithms, Ciphers, MACs, LoginGraceTime — DevPlan 136 W10 T10.4).
##           Data-driven: parse отдельно (pure) + таблица _SSHD_EXTRA_DIRECTIVES через
##           _classify_directive (pure) + _check_maxstartups.
## @io       ⇥ probe: Callable | None (lazy default _probe) → ⎋ CheckResult
## @complexity O(1) — один subprocess + regex; CC-нагрузка вынесена в pure-хелперы
## @invariants  Требует root (sshd -T) — гарантируется root-check в main
##              sshd -T печатает ЭФФЕКТИВНЫЙ конфиг (включая drop-in sshd_config.d) —
##              проверяем именно эффективное значение, не исходный sshd_config
##              Ненаблюдаемое значение (нет maxstartups в выводе) → PASS (graceful:
##              тест-фикстуры без строки; реальный sshd -T всегда печатает дефолт 10:30:100 → FAIL)
##              Расширенные директивы проверяются ТОЛЬКО при наличии в выводе sshd -T;
##              allowusers задан пустым (директива есть без списка) → FAIL; отсутствует строка → skip
def check_sshd(*, probe: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> CheckResult:
    """S4: sshd effective config — root login restricted, password auth off, pubkey on,
    MaxStartups >= 30:50:200, +9 hardening-директив (W10 T10.4)."""
    probe = probe or _probe
    result = probe(["sshd", "-T"], timeout=CONVERGE_DOCKER_TIMEOUT)
    if result.returncode != 0:
        return CheckResult("S4", STATUS_FAIL, f"sshd -T failed (rc={result.returncode})")
    settings = parse_sshd_effective_config(str(getattr(result, "stdout", "")))
    problems: list[str] = []
    root_login = settings.get("permitrootlogin", "")
    # v1.0.1 (Фаза 6, tronyx-vps Ubuntu 24.04): sshd -T отдаёт «without-password» —
    # OpenSSH-алиас prohibit-password (переименование с 7.x→9.x, семантика идентична:
    # пароль root запрещён, ключ разрешён). Оба значения — PASS.
    if root_login not in {"no", "prohibit-password", "without-password"}:
        problems.append(f"PermitRootLogin={root_login or 'unset'} (expected no|prohibit-password)")
    if settings.get("passwordauthentication", "") == "yes":
        problems.append("PasswordAuthentication=yes (password auth enabled)")
    if settings.get("pubkeyauthentication", "") != "yes":
        problems.append("PubkeyAuthentication != yes")
    # MaxStartups (DevPlan 136 W3): sshd -T = ЭФФЕКТИВНЫЙ конфиг (включая drop-in из
    # sshd_config.d) — проверяем именно эффективное значение. Дефолт OpenSSH 10:30:100
    # < 30:50:200 → FAIL, пока 99-platform-maxstartups.conf не применён (apply_sshd_dropin).
    problems.extend(_check_maxstartups(settings))
    # Расширенные директивы (W10 T10.4) — только присутствующие в sshd -T
    for key, check, fail_msg in _SSHD_EXTRA_DIRECTIVES:
        if key not in settings:
            logger.info("[IMP:8][posture][S4] %s not in sshd -T output — skipped (graceful)", key)
            continue
        problem = _classify_directive(key, settings[key], check, fail_msg)
        if problem is not None:
            problems.append(problem)
    if problems:
        return CheckResult("S4", STATUS_FAIL, "; ".join(problems))
    detail = f", MaxStartups={settings.get('maxstartups', '')}" if settings.get("maxstartups", "") else ""
    logger.info("[IMP:9][posture][S4] SSH surface hardened%s", detail)
    return CheckResult("S4", STATUS_PASS, f"sshd: root login restricted, password auth off, pubkey on{detail}")


# endregion FUNC_check_sshd


# region FUNC_desired_maxstartups_dropin
## @purpose  Желаемое содержимое /etc/ssh/sshd_config.d/99-platform-maxstartups.conf.
## @io       ⇥ — → ⎋ str — drop-in (комментарий + директива MaxStartups)
## @complexity O(1)
## @invariants  Файл помечен «Generated — DO NOT EDIT MANUALLY» (политика управления —
##              файлы перезаписываются платформой, канон security_updates.py)
##              Директива — ТОЛЬКО MaxStartups (другие sshd-директивы — вне скоупа W3)
def desired_maxstartups_dropin() -> str:
    """99-platform-maxstartups.conf: MaxStartups 30:50:200 (защита от connection-storm)."""
    return (
        "# Generated by ai-platform security_posture.py (DevPlan 136 W3) — DO NOT EDIT MANUALLY\n"
        "# MaxStartups 30:50:200 — защита SSH от connection-storm при параллельных деплоях/\n"
        "# healthcheck-прокидываниях. sshd_config.d drop-in — НЕ правка основного sshd_config;\n"
        "# sshd -T (S4) читает эффективное значение ВКЛЮЧАЯ drop-in.\n"
        f"MaxStartups {SSHD_MAXSTARTUPS_STR}\n"
    )


# endregion FUNC_desired_maxstartups_dropin


# region FUNC_desired_ssh_hardening_dropin
## @purpose  Желаемое содержимое /etc/ssh/sshd_config.d/99-platform-ssh-hardening.conf (DevPlan 162 W2-1).
##           Полный харденинг-набор, закрывающий SSH-drift против канона платформы: root-login
##           prohibit-password, password-auth off, allowlist пользователей, X11/TCP-forwarding off,
##           ClientAliveInterval 300, MACs ТОЛЬКО *-etm (без hmac-sha1/umac-64),
##           MaxStartups 30:50:200 (superset старого 99-platform-maxstartups.conf).
## @io       ⇥ — → ⎋ str — drop-in (комментарий + 8 директив)
## @complexity O(1)
## @invariants  Файл помечен «Generated — DO NOT EDIT MANUALLY» (политика управления —
##              файлы перезаписываются платформой, канон security_updates.py)
##              Содержимое — superset desired_maxstartups_dropin(): старый maxstartups-файл
##              удаляется при apply (дубликат MaxStartups безвреден — последняя директива
##              побеждает — но чистота каталога важнее)
##              AllowUsers — статический список (root/platform/ci-deploy): sshd валидирует
##              allowlist при подключении, НЕ при парсинге конфига — φ1-apply до φ2 безопасен
def desired_ssh_hardening_dropin() -> str:
    """99-platform-ssh-hardening.conf — полный sshd-харденинг (DevPlan 162 W2-1)."""
    return (
        "# Generated by ai-platform security_posture.py (DevPlan 162 W2-1) — DO NOT EDIT MANUALLY\n"
        "# SSH hardening drop-in — закрывает SSH-drift (PermitRootLogin yes / PasswordAuthentication yes\n"
        "# на свежих облачных образах). Superset 99-platform-maxstartups.conf: MaxStartups включён;\n"
        "# sshd -T (S4) читает эффективное значение ВКЛЮЧАЯ этот drop-in.\n"
        "PermitRootLogin prohibit-password\n"
        "PasswordAuthentication no\n"
        f"AllowUsers {' '.join(SSHD_ALLOW_USERS)}\n"
        "X11Forwarding no\n"
        "AllowTcpForwarding no\n"
        "ClientAliveInterval 300\n"
        "MACs " + SSHD_HARDENING_MACS + "\n"
        f"MaxStartups {SSHD_MAXSTARTUPS_STR}\n"
    )


# endregion FUNC_desired_ssh_hardening_dropin

# 🧐 TRAP[DECISION] · 2026-08-13 · MED · AllowUsers = статический список root/platform/ci-deploy (φ2-создаваемые)
# · Rejected: динамический рендер AllowUsers из node.yaml (φ1-apply не имеет users до φ2;
# ·   node.yaml-формат списка ssh-пользователей не канонизирован — premature coupling)
# · Reason: пользователи создаются в lifecycle φ2 — на момент apply (φ1 шаг 5.6) они ещё
# ·   не существуют; sshd валидирует AllowUsers при ПОДКЛЮЧЕНИИ, не при парсинге конфига
# ·   (unknown user в allowlist не ломает конфиг) — статический список безопасен и
# ·   детерминирован (DevPlan 162 W2-1, S4-канон уже ожидает эти имена)
# · Rev: если состав ssh-пользователей станет динамическим (новый пользователь вне списка
# ·   на проде) → вынести список в node.yaml и рендерить drop-in из φ1-конфига


# region FUNC__write_if_changed
## @purpose  Content-match idempotent write: существующий файл с идентичным содержимым → no-op.
## @io       ⇥ path: Path, desired: str, label: str = "sshd", write_fn: Callable | None → ⎋ (changed: bool, ok: bool)
## @complexity O(1) — одно чтение + при необходимости атомарная запись
## @invariants  НИКОГДА не пишет на диск при совпадении содержимого (строгая идемпотентность)
##              Атомарная запись через shared/atomic_writer (temp + fsync + os.replace, 0644)
##              Ошибка записи → (False, False) — вызывающий решает fatal/non-fatal
def _write_if_changed(
    path: Path, desired: str, label: str = "sshd", write_fn: Callable[..., object] | None = None
) -> tuple[bool, bool]:
    """Write file only when content differs. Returns (changed, ok).

    DI (W-H DevPlan 163): write_fn=None → atomic_write_text (канон); тесты передают fake.
    """
    writer = atomic_write_text if write_fn is None else write_fn
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    if existing == desired:
        logger.info("[IMP:8][posture][%s][noop] %s unchanged — no-op (idempotent)", label, path)
        return False, True
    try:
        writer(str(path), desired, mode=0o644)
    except OSError as e:
        logger.error("[IMP:10][posture][%s][write] Cannot write %s: %s", label, path, e)
        return False, False
    logger.info("[IMP:9][posture][%s][write] %s %s", label, path, "updated" if existing else "created")
    return True, True


# endregion FUNC__write_if_changed


# region FUNC__reload_sshd
## @purpose  Reload sshd: systemctl reload sshd → fallback service ssh reload. True = успех.
## @io       ⇥ probe_fn: Callable | None (lazy default _probe) → ⎋ bool
## @complexity O(2) — до двух subprocess-проб
## @invariants  fallback на `service ssh reload` — systemd-отсутствие (container/chroot) не
##              должно ломать apply; обе пробы через _probe (graceful, никогда не raise)
def _reload_sshd(
    probe_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> bool:
    """Reload sshd effective config — systemctl reload sshd, fallback service ssh reload.

    DI (W-H DevPlan 163): probe_fn=None → _probe (канон); тесты передают fake-probe.
    """
    probe_impl = _probe if probe_fn is None else probe_fn
    for cmd in (["systemctl", "reload", "sshd"], ["service", "ssh", "reload"]):
        result = probe_impl(cmd, timeout=CONVERGE_DOCKER_TIMEOUT)
        if result.returncode == 0:
            logger.info("[IMP:9][posture][reload] sshd reloaded via %s", " ".join(cmd))
            return True
        logger.warning("[IMP:8][posture][reload] %s failed (rc=%s) — trying fallback", " ".join(cmd), result.returncode)
    logger.error("[IMP:10][posture][reload] sshd reload failed (systemctl + service both non-zero)")
    return False


# endregion FUNC__reload_sshd


# region FUNC_apply_sshd_dropin
## @purpose  Применить sshd hardening drop-in идемпотентно (DevPlan 136 W3 MaxStartups +
##           DevPlan 162 W2-1 полный харденинг): content-match no-op; при изменении — атомарная
##           запись + удаление superseded maxstartups-файла + reload sshd. Вызывается из φ1
##           phase_system_bootstrap (CLI --apply-sshd), НЕ из check-потока.
## @io       ⇥ hardening_dropin: str | None, superseded_dropin: str | None,
##              probe_fn: Callable | None, write_fn: Callable | None (все — lazy-default DI)
##              → ⎋ bool (True = применено/no-op; False = ошибка записи ИЛИ reload)
## @complexity O(1) + до 2 reload-проб
## @invariants  no-op при совпадении содержимого (reload НЕ вызывается)
##              reload — только при изменении содержимого ИЛИ удалении superseded-файла
##              (systemctl → service fallback)
##              Запись удалась, но reload не удался → False (конфиг не активен — честный отказ)
##              superseded.resolve() != path.resolve() — защита от self-delete при коллизии путей
## @rationale  apply в sshd_policy (не в phases/system.py): sshd-политика живёт в одном
##             модуле с S4-проверкой (единый SoT эффективного значения); фаза вызывает CLI.
##             W2-1: сигнатура сохранена — существующий вызов φ1 (`--apply-sshd`) автоматически
##             получает полный харденинг без правки lifecycle (system.py вне скоупа W2-1).
def apply_sshd_dropin(
    *,
    hardening_dropin: str | None = None,
    superseded_dropin: str | None = None,
    probe_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    write_fn: Callable[..., object] | None = None,
) -> bool:
    """Apply sshd hardening drop-in idempotently (content-match no-op; reload on change).

    DI (W-H DevPlan 163): hardening_dropin/superseded_dropin/probe_fn/write_fn — None → канонические
    SSHD_HARDENING_DROPIN/SSHD_MAXSTARTUPS_DROPIN/_probe/atomic_write_text; тесты передают
    tmp_path/fake-каналы (0 патчей модульных констант/функций).
    """
    dropin_path = SSHD_HARDENING_DROPIN if hardening_dropin is None else hardening_dropin
    superseded_path = SSHD_MAXSTARTUPS_DROPIN if superseded_dropin is None else superseded_dropin
    probe_impl = _probe if probe_fn is None else probe_fn
    write_impl = atomic_write_text if write_fn is None else write_fn
    path = Path(dropin_path)
    changed, ok = _write_if_changed(path, desired_ssh_hardening_dropin(), label="sshd-hardening", write_fn=write_impl)
    if not ok:
        logger.error("[IMP:10][posture][sshd-hardening] Drop-in apply aborted — write failed")
        return False
    # W2-1: superseded maxstartups-only drop-in — superseded (hardening содержит MaxStartups).
    # Дубликат MaxStartups безвреден (последняя директива побеждает в sshd), но удаляем
    # для чистоты каталога sshd_config.d — ровно один платформенный drop-in.
    superseded = Path(superseded_path)
    superseded_removed = False
    if superseded.is_file() and superseded.resolve() != path.resolve():
        try:
            superseded.unlink()
            superseded_removed = True
            logger.info(
                "[IMP:9][posture][sshd-hardening] Superseded %s removed (superseded by hardening drop-in)", superseded
            )
        except OSError as e:
            logger.warning("[IMP:8][posture][sshd-hardening] Cannot remove superseded %s: %s", superseded, e)
    # v1.0.1 TRAP[BUG] (Фаза 6, tronyx-vps): Ubuntu cloud-init пишет 50-cloud-init.conf
    # с «PasswordAuthentication yes». sshd Include *.conf читает файлы в алфавитном порядке,
    # первое значение побеждает → 50-й перекрывает 99-platform hardening → sshd -T давал
    # passwordauthentication yes при запрете в drop-in. Нейтрализация: переименование в
    # .disabled (Include *.conf не матчит) — обратимо, cloud-init повторно не пишет.
    cloudinit = Path("/etc/ssh/sshd_config.d/50-cloud-init.conf")
    cloudinit_neutralized = False
    if cloudinit.is_file():
        needs_neutralize = False
        try:
            cloudinit_text = cloudinit.read_text(encoding="utf-8")
        except OSError:
            cloudinit_text = ""
        if re.search(
            r"(?m)^(PasswordAuthentication|PermitRootLogin|KbdInteractiveAuthentication)\s+(yes|without-password)",
            cloudinit_text,
        ):
            needs_neutralize = True
        if needs_neutralize:
            try:
                disabled_path = Path(str(cloudinit) + ".disabled")
                cloudinit.rename(disabled_path)
                cloudinit_neutralized = True
                logger.info(
                    "[IMP:9][posture][sshd-hardening] cloud-init ssh drop-in neutralized → %s", disabled_path.name
                )
            except OSError as e:
                logger.warning("[IMP:8][posture][sshd-hardening] Cannot neutralize cloud-init drop-in: %s", e)
    if changed or superseded_removed or cloudinit_neutralized:
        if not _reload_sshd(probe_fn=probe_impl):
            logger.error(
                "[IMP:10][posture][sshd-hardening] Drop-in written but sshd reload FAILED — "
                "новый hardening не активен до перезапуска sshd"
            )
            return False
        logger.info("[IMP:9][posture][sshd-hardening] Drop-in applied + sshd reloaded")
    else:
        logger.info("[IMP:8][posture][sshd-hardening] Drop-in already current — no-op (idempotent)")
    return True


# endregion FUNC_apply_sshd_dropin
