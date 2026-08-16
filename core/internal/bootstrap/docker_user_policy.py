#!/usr/bin/env python3
# GREP_SUMMARY: docker-user DOCKER-USER iptables FORWARD defense-in-depth bridge policy apply-idempotent -C-guard drop-last privoxy-input-rule build-firewall-rule 172.16.0.0/12 hermes-proxy docker-bridges DI runner-param DevPlan-170-W6-D2
# STRUCTURE: ▶ ┌iptables-домен (DOCKER-USER FORWARD + privoxy INPUT)┐ → ○ desired_docker_user_rules (established+80+443+bridge-ACCEPT+DROP-last)
#            → ○ apply_docker_user_policy (-C guard → -A add, DI run_cmd) → ○ build_firewall_rule (INPUT privoxy rule, dport из SoT)
#            → ⎋ bool/структура; leaf-модуль — НЕ импортирует firewall (PRIVOXY_PORT передаёт вызывающий)
# region MODULE_CONTRACT
## @purpose  iptables-домен платформенной firewall-политики (DevPlan 170 W6-D2, research-A §5):
##           DOCKER-USER FORWARD-chain defence-in-depth (DevPlan 162 W2-3) + INPUT-правило доступа
##           к Privoxy с docker-мостов (экс-build_firewall_rule из install_tor_proxy, W4c T4.3).
##           Вынесен из firewall.py (ufw-домен остаётся фасадом) и install_tor_proxy (дубль
##           iptables-генератора консолидирован) — ВЕСЬ iptables-код платформы в одном leaf-модуле.
## @scope    core/internal/bootstrap: вызывается из firewall.py (re-export + CLI --apply-docker-user),
##           install_tor_proxy.py (configure_firewall_docker → build_firewall_rule), systemd drop-in
##           docker.service ExecStartPost (docker_installer.py → firewall.py CLI). НЕ вызывается
##           напрямую из shell — CLI-канал через firewall.py.
## @invariants
##   - Leaf-модуль: НЕ импортирует другие core.internal.bootstrap-модули (PRIVOXY_PORT из SoT
##     firewall.py передаёт вызывающий — dport обязательный параметр build_firewall_rule).
##     8118-литералов НЕТ (port-parity гейт GATE_PRIVOXY_PORT_SOLE: единственный литерал — firewall.py).
##   - DOCKER-USER: DROP — ВСЕГДА последний; established/related — egress; 80/443 — публичный
##     nginx by-design; bridge-ACCEPT — трафик между docker-сетями платформы (W5-2 address-pools)
##   - apply_docker_user_policy: -C rc==0 → skip (идемпотентность); -A rc!=0 → False (честный отказ);
##     существующие сторонние правила в DOCKER-USER НЕ трогаются; IPv6 сознательно не применяется
##     (docker-мосты платформы IPv4-only)
##   - DI (W-H DevPlan 163): run_cmd=None → subprocess.run (канон); тесты передают fake-канал
##   - build_firewall_rule: порядок аргументов канона shell (-p tcp --dport D -s NET -j ACCEPT
##     -m comment --comment C); dport — ОБЯЗАТЕЛЬНЫЙ (вызывающий берёт из PRIVOXY_PORT SoT)
## @rationale Q: Почему отдельный leaf-модуль, а не часть firewall.py?
##            A: firewall.py 576 LOC смешивал ufw- и iptables-домены (research-A §5) — DOCKER-USER
##            FORWARD-policy и INPUT-правило privoxy имеют другую модель (chains, а не ufw status);
##            сплит даёт leaf (0 зависимостей внутри bootstrap) — acyclic-internal-domains зелёный
##            без ignore-рёбер. PRIVOXY_PORT остаётся в firewall.py (SoT, W1-A3) — docker_user_policy
##            не дублирует значение, а требует его от вызывающего (порт как параметр правила).
## @changes  2026-08-15 | DevPlan 170 W6-D2 — создан: DOCKER-USER (firewall.py:162 W2-3) +
##                      build_firewall_rule (install_tor_proxy W4c T4.3) консолидированы в iptables-домен
## @see      core/internal/bootstrap/firewall.py (ufw-фасад + re-export),
##           core/internal/bootstrap/install_tor_proxy.py (build_firewall_rule обёртка),
##           core/internal/bootstrap/docker_installer.py (systemd ExecStartPost drop-in)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import cast

logger = logging.getLogger(__name__)

# ── DOCKER-USER defence-in-depth (DevPlan 162 W2-3) ──
# Docker вставляет свои правила в FORWARD (DNAT→FORWARD→DOCKER), НЕ через ufw INPUT — любой
# 0.0.0.0-published порт (исключение/будущий дрейф) доступен из интернета в обход ufw.
# DOCKER-USER — first-chain в FORWARD: политика accept established/related + 80/443 (nginx
# public by-design) + трафик между docker-мостами; всё остальное — DROP (defence-in-depth
# ПОВЕРХ compose-gate W2-2, НЕ замена). Правила применяются через systemd drop-in
# docker.service ExecStartPost (Docker 20.10+ пересоздаёт цепочки при рестарте — статический
# iptables-restore сервис не переживает; ExecStartPost гарантирует восстановление).
DOCKER_USER_CHAIN: str = "DOCKER-USER"
# Docker bridge-пулы платформы: встроенный 172.16.0.0/12 (RFC 1918, docker default
# address-pools) + 10.32.0.0/16 (default-address-pools в daemon.json, DevPlan 162 W5-2).
# Трафик МЕЖДУ мостами разрешён (контейнеры общаются через сети платформы).
DOCKER_BRIDGE_NETS: tuple[str, ...] = ("172.16.0.0/12", "10.32.0.0/16")
# IPv6: сознательно НЕ применяется (platform docker-мосты IPv4-only: TOR_PRIVOXY_NET
# 172.16.0.0/12, address-pools 10.32.0.0/16; ip6tables DOCKER-USER не существует в Docker).

# ── INPUT-правило Privoxy (экс-install_tor_proxy, W4c T4.3) ──
# Catch-all для ВСЕХ Docker bridge-сетей (RFC 1918 172.16-31.x.x) — per-interface правила
# пропускают сети, созданные после bootstrap. Значение --dport передаёт вызывающий
# (install_tor_proxy.FIREWALL_DPORT из SoT firewall.PRIVOXY_PORT) — литерала 8118 здесь нет.
# 🧐 TRAP[DECISION] · 2026-08-15 · — · dport — обязательный параметр build_firewall_rule (leaf-сплит W6-D2)
# · Rejected: прямой импорт PRIVOXY_PORT из firewall.py
# · Reason: прямой импорт создал бы цикл firewall↔docker_user_policy (firewall re-export'ит
# ·   iptables-функции для тестов и CLI --apply-docker-user) → acyclic-internal-domains RED +
# ·   ignore-ребро в .importlinter; leaf-вариант держит 7/7 контрактов и GATE_PRIVOXY_PORT_SOLE
# · Rev: если build_firewall_rule понадобится вызывать без явного dport из других модулей →
# ·   пересмотреть SoT-размещение PRIVOXY_PORT
FIREWALL_COMMENT: str = "hermes-proxy-docker-bridges"
FIREWALL_SRC_NET: str = "172.16.0.0/12"


# region FUNC_desired_docker_user_rules
## @purpose  Чистая функция желаемой DOCKER-USER политики (DevPlan 162 W2-3) — список
##           iptables-аргументов БЕЗ префикса (-A/-C добавляется при применении). Порядок
##           критичен: ACCEPT-правила до catch-all DROP.
## @io       ⇥ — → ⎋ list[list[str]] — established/related, 80, 443, по одному на docker-мост
##           (DOCKER_BRIDGE_NETS), финальный DROP (итого 3 + B + 1 правил)
## @complexity O(B) — B = число bridge-сетей
## @invariants  DROP — ВСЕГДА последний (catch-all; любое ACCEPT после него — мёртвое правило)
##              80/443 — публичный ingress nginx by-design (W2-2 allowlist)
##              established/related — ответный трафик контейнеров наружу (иначе ломается egress)
##              Мостовые ACCEPT — трафик между docker-сетями платформы (микросервисные связи)
## @rationale  Правила как чистые аргументы (не полные команды) — тестируемость (-C/-A guard
##             добавляется в apply); единый SoT для проверок идемпотентности.
def desired_docker_user_rules() -> list[list[str]]:
    """Desired DOCKER-USER iptables rules (без -A/-C префикса, DROP последним)."""
    return [
        ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ["-p", "tcp", "--dport", "80", "-j", "ACCEPT"],
        ["-p", "tcp", "--dport", "443", "-j", "ACCEPT"],
        *(["-s", net, "-j", "ACCEPT"] for net in DOCKER_BRIDGE_NETS),
        ["-j", "DROP"],
    ]


# endregion FUNC_desired_docker_user_rules


# region FUNC_apply_docker_user_policy
## @purpose  Применить DOCKER-USER политику идемпотентно (-C guard → -A add), НЕ деструктивно
##           (существующие сторонние правила в DOCKER-USER не трогаются). Вызывается из
##           systemd drop-in docker.service ExecStartPost (docker_installer.py) и CLI
##           firewall.py --apply-docker-user. ⚠️ ПРЕДУСЛОВИЕ: порт-аудит W2-2 завершён (иначе DROP
##           отрежет легитимный ingress) — см. TRAP[DECISION] в docker_installer.py.
## @io       ⇥ dry: bool = False, run_cmd: Callable | None → ⎋ bool (True = политика применена/no-op)
## @complexity O(R) — R = число правил, каждое до 2 subprocess
## @invariants  -C rc==0 → правило существует → skip (идемпотентность, no-op)
##              -A rc!=0 → False (честный отказ: цепочка может отсутствовать без docker)
##              Первое правило (established/related) применяется первым — DROP не отрежет
##              ответный трафик в момент применения (атомарности нет, порядок минимизирует окно)
##              IPv6 сознательно не применяется (docker-мосты платформы IPv4-only)
def apply_docker_user_policy(dry: bool = False, run_cmd: Callable[..., object] | None = None) -> bool:
    """Apply DOCKER-USER ingress policy idempotently (-C guard, then -A add).

    DI (W-H DevPlan 163): run_cmd=None → subprocess.run (канон); тесты передают fake-канал.
    """
    if dry:
        logger.info("[IMP:7][docker-user][policy] dry-run: skip iptables (policy defined)")
        return True
    for rule in desired_docker_user_rules():
        check = _run_iptables_quiet(["iptables", "-C", DOCKER_USER_CHAIN, *rule], dry=dry, run_cmd=run_cmd)
        if check == 0:
            logger.info("[IMP:8][docker-user][policy] Rule exists (no-op): -A %s %s", DOCKER_USER_CHAIN, " ".join(rule))
            continue
        add = _run_iptables_quiet(["iptables", "-A", DOCKER_USER_CHAIN, *rule], dry=dry, run_cmd=run_cmd)
        if add != 0:
            logger.error(
                "[IMP:10][docker-user][policy] FAILED to add: iptables -A %s %s (rc=%s) — "
                "DOCKER-USER chain missing? docker daemon не запущен?",
                DOCKER_USER_CHAIN,
                " ".join(rule),
                add,
            )
            return False
        logger.info("[IMP:9][docker-user][policy] Added: iptables -A %s %s", DOCKER_USER_CHAIN, " ".join(rule))
    logger.info("[IMP:9][docker-user][policy] DOCKER-USER ingress policy applied (idempotent)")
    return True


# endregion FUNC_apply_docker_user_policy


# region FUNC_run_iptables_quiet
## @purpose  Обёртка subprocess для iptables (rc, graceful — никогда не raise). DI-шов для
##           тестов: run_cmd параметр (W-H DevPlan 163).
## @io       ⇥ cmd: list[str], dry: bool, run_cmd: Callable | None → ⎋ int (rc; 127 = бинарь недоступен)
## @complexity O(1)
def _run_iptables_quiet(cmd: list[str], dry: bool = False, run_cmd: Callable[..., object] | None = None) -> int:
    """Run iptables command gracefully, return returncode.

    DI (W-H DevPlan 163): run_cmd=None → subprocess.run (канон); тесты передают fake-канал.
    """
    runner = subprocess.run if run_cmd is None else run_cmd
    if dry:
        logger.info("[IMP:7][docker-user][dry] %s", " ".join(cmd))
        return 0
    try:
        result = runner(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        logger.error("[IMP:10][docker-user] iptables not available: %s", exc)
        return 127
    return cast("int", result.returncode)  # pyright: ignore[reportAttributeAccessIssue] — DI run_cmd: Callable[..., object] (тест-fakes возвращают произвольные типы); W11-G3: каст returncode → int


# endregion FUNC_run_iptables_quiet


# region FUNC_build_firewall_rule
## @purpose  Чистый генератор iptables-правила INPUT: Docker bridge → Privoxy (для -C/-I).
##           Единая реализация (экс-install_tor_proxy W4c T4.3, дубль консолидирован сюда).
## @io       ⇥ dport: str (ОБЯЗАТЕЛЬНЫЙ — вызывающий берёт из SoT firewall.PRIVOXY_PORT,
##           str для --dport аргумента), src_net/comment (keyword-only, дефолты = канонные
##           константы) → ⎋ list[str]
## @complexity O(1)
## @invariants — Порядок аргументов канона shell: -p tcp --dport D -s NET -j ACCEPT -m comment
##               --comment C (совпадает с прежним литералом configure_firewall_docker)
## @rationale — dport обязателен (без литерала 8118 — GATE_PRIVOXY_PORT_SOLE; значение из SoT
##              firewall.PRIVOXY_PORT передаёт install_tor_proxy через FIREWALL_DPORT)
def build_firewall_rule(
    *,
    dport: str,
    src_net: str = FIREWALL_SRC_NET,
    comment: str = FIREWALL_COMMENT,
) -> list[str]:
    """Собрать iptables-правило Docker bridge → Privoxy (для -C/-I INPUT)."""
    return [
        "-p",
        "tcp",
        "--dport",
        dport,
        "-s",
        src_net,
        "-j",
        "ACCEPT",
        "-m",
        "comment",
        "--comment",
        comment,
    ]


# endregion FUNC_build_firewall_rule
