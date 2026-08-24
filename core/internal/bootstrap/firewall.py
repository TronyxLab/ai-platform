#!/usr/bin/env python3
# GREP_SUMMARY: firewall ufw declarative idempotent 22 80 443 5432-deny extra_ports deny-incoming allow-outgoing port-validation docker-user DOCKER-USER iptables bridge defense-in-depth zabbix-monitoring timeweb 10050 peer-firewall placement platform-peer build-peer-rules consumer-of publish-ports stale-reconcile delete-from-source cross-node multi-node
# STRUCTURE: ▶ parse extra_ports args → ○ validate_ports (1-65535, forbid 2375/2376) → ○ build_peer_rules (placement → peer-allow) → ○ apply_rules (ufw enable→defaults→baseline→extra→PEER→deny 5432) → ○ verify (ufw status, peer-source-aware) → ⎋ exit 0|1 ┤
#            ▶ --apply-docker-user (162 W2-3) → ◇ root? → ○ DOCKER-USER policy (-C guard → -A, DROP last) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Declarative ufw baseline firewall: deny all incoming, allow outgoing, open exactly
##           22/80/443 + extra_ports (только с `from <ip>` — НИКОГДА 0.0.0.0/Anywhere), explicitly
##           deny module-internal ports (реестр platform-infra.yaml). Python-порт firewall.sh (DevPlan 118 E3).
##           DevPlan 136 W10: (T10.6/S-8) extra_ports IP-scoped + FORBIDDEN/CHECK по реестру портов модулей;
##           (T10.10/S-14) инкрементальный apply БЕЗ disable+reset — enable+default-deny ПЕРВЫМИ,
##           stale platform-правила удаляются точечно (ufw delete) — окна «firewall выключен» нет.
## @scope    Called during bootstrap phase φ1 (phases.py) via thin facade core/internal/bootstrap/firewall.sh.
## @invariants
##   - НИКОГДА `ufw disable`/`ufw reset` — firewall не отключается (S-14, T10.10): enable+default-deny
##     применяются ПЕРВЫМИ, затем allow-правила (ssh 22 первым — lockout-safe), затем deny модульных
##     портов; stale allow-правила с комментарием platform-* удаляются точечно (идемпотентность без reset)
##   - Порты 2375/2376 (Docker API) НИКОГДА не добавляются (FORBIDDEN — validate rejects)
##   - Модульные внутренние порты (реестр platform-infra.yaml provides/env_defaults: postgres 5432,
##     redis 6379, clickhouse 8123/9000, minio 9000/9001, litellm 4000, langfuse 3001, loki 3100,
##     grafana 3000, prometheus 9090, hermes 9119/8642, nginx-exporter 9113, node-exporter 9100) —
##     DENY на уровне ufw (defense-in-depth) И запрещены в extra_ports (FORBIDDEN) — S-8, T10.6
##   - 8080 НЕ входит в deny-реестр: cadvisor/status-page слушают 127.0.0.1 (loopback = контроль),
##     user-проекты часто публикуют 8080 (тест-проект test-project-web на test-VPS) — не ломать их
##   - extra_ports валидируются как integers 1-65535; non-numeric/out-of-range/forbidden → fail-fast
##   - extra_ports ТРЕБУЮТ `--source-ip <ip>` (allow from <ip>, НЕ Anywhere) — S-8, T10.6;
##     extra_ports без source-ip → ConfigValidationError (fail-fast)
##   - DevPlan 010 T2.3 (peer-firewall): кросс-нодовые порты (PEER_PUBLISH_PORTS) открываются ТОЛЬКО
##     для IP нод-пиров из placement.yaml — `ufw allow from <peer> to any port <p>/tcp comment
##     platform-peer-<p>-<peer>`; peer-ALLOW вставляется ПЕРЕД module-deny (ufw first-match — иначе
##     deny выигрывает у allow); Anywhere-публикация этих портов запрещена (verify FAIL);
##     прямой 5432 НЕ публикуется (потребители едут на data-ноду вместе с postgres — DevPlan 010 §8)
##   - Stale-reconcile peer-правил (DevPlan 010 инвариант 4): delete-команда ОБЯЗАНА нести source IP
##     (`ufw delete allow from <peer> to any port <p>/tcp`) — форма `delete allow <port>/tcp` неоднозначна
##     при ≥2 пирах на порту (firewall.py:268 баг). delete обязан точно совпадать с allow-формой.
##   - Single-node no-op: отсутствие placement.yaml → build_peer_rules возвращает [] (peer_rules пусты,
##     поведение байт-идентично прежнему; verify peer_ips=None → легаси-строгие проверки)
##   - exit 0 только если ufw status показывает ожидаемые порты (baseline ALLOW, модульные DENY,
##     нет 2375/2376, нет module-port ALLOW; peer-ALLOW от известного пира = PASS)
##   - subprocess ufw — тестируемость: validate_ports/build_rules/build_peer_rules/parse_ufw_status/
##     verify_firewall/collect_stale_platform_rules pure
## @rationale Additive ufw rules accumulate over re-runs; declarative replace guarantees idempotency.
##            disable+reset создавал окно без файрвола (S-14) — инкрементальный apply закрывает его.
##            Strangler E3: ufw-оркестрация в Python (порты из node.yaml firewall-поддомен).
## @changes  2026-08-02 | DevPlan 118 E3 — Created (Python-порт firewall.sh, 167 LOC)
## @changes  2026-08-05 | DevPlan 136 W10 — T10.6/T10.10: --source-ip, MODULE_PORTS_DENY,
##                      инкрементальный apply (без disable/reset), stale-reconcile
## @changes  2026-08-13 | DevPlan 162 W2-3 — DOCKER-USER defence-in-depth: desired_docker_user_rules()
##                      + apply_docker_user_policy() (идемпотентный -C/-A guard, DROP last) + CLI
##                      --apply-docker-user (root-check); persistence — docker.service ExecStartPost
##                      drop-in в docker_installer.py. ПОСЛЕ port-audit W2-2 (предусловие).
## @changes  2026-08-13 | DevPlan 164 W0-3.1 — zabbix-monitoring rules: 3 официальных IP Timeweb
##                      (zabbix.repo.timeweb.ru) → 10050/tcp allow (default ON, --no-zabbix-monitoring
##                      отключает); desired_allow + verify-проверка 10050 ALLOW.
## @changes  2026-08-14 | DevPlan 170 W1-A3 — +PRIVOXY_PORT (SoT 8118, консолидация дублей);
##                      TOR_PRIVOXY_PORT = алиас PRIVOXY_PORT
## @changes  2026-08-15 | DevPlan 170 W6-D2 — iptables-домен (DOCKER-USER + privoxy INPUT rule)
##                      вынесен в docker_user_policy.py; здесь — re-export (firewall.desired_docker_user_rules/
##                      apply_docker_user_policy/DOCKER_USER_CHAIN/DOCKER_BRIDGE_NETS сохранены для
##                      тестов и CLI --apply-docker-user; импорт-пути потребителей не меняются)
## @changes  2026-08-22 | DevPlan 010 T2.3/T2.4 — peer-scoped firewall + reconcile fix:
##                      PEER_PUBLISH_PORTS (матрица кросс-нодовых портов из platform_ports),
##                      CONSUMER_OF (эвристика потребитель→поставщик), build_peer_rules(placement)
##                      (peer-allow ПЕРЕД module-deny), collect_stale_platform_rules full-spec
##                      delete `from <peer>` (инвариант 4: форма delete allow <port>/tcp неоднозначна
##                      при ≥2 пирах), verify_firewall(peer_ips=...) peer-ALLOW=PASS / Anywhere=FAIL,
##                      run/main(--placement <path>) — single-node no-op без placement.yaml
## @see      core/internal/bootstrap/firewall.sh (тонкий фасад),
##           core/internal/bootstrap/docker_user_policy.py (iptables-домен)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from typing import cast

from core.internal.bootstrap.docker_user_policy import (
    DOCKER_BRIDGE_NETS,  # ruff: ignore[F401] — re-export (test_firewall/test_docker_installer контракт)
    DOCKER_USER_CHAIN,  # ruff: ignore[F401] — re-export (iptables-домен в docker_user_policy, W6-D2)
    apply_docker_user_policy,  # используется в main (--apply-docker-user); re-export
    desired_docker_user_rules,  # ruff: ignore[F401] — re-export (test_firewall DOCKER-USER контракт)
)
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.placement import Placement, load_placement, resolve_node_modules
from core.internal.shared.platform_ports import (
    CADVISOR,
    CLICKHOUSE_NATIVE_PEER,
    LOKI_HTTP,
    NGINX_EXPORTER,
    NODE_EXPORTER,
    PLATFORM_PORT_CLICKHOUSE,
    PLATFORM_PORT_MINIO,
    PLATFORM_PORT_PGBOUNCER,
    PLATFORM_PORT_REDIS,
    POSTGRES_EXPORTER,
    REDIS_EXPORTER,
)

logger = logging.getLogger(__name__)

# Baseline ports always open
BASELINE_PORTS: tuple[int, ...] = (22, 80, 443)
# Forbidden ports — Docker API must never be exposed
FORBIDDEN_PORTS: tuple[int, ...] = (2375, 2376)
# Explicit deny — managed PostgreSQL provider may host-forward
PORT_MAX: int = 65535  # верхняя граница TCP/UDP порта

DENY_PORT = 5432
# Модульные внутренние порты — реестр platform-infra.yaml (provides + env_defaults):
#   postgres 5432 (DENY_PORT), redis 6379, clickhouse 8123/9000, minio 9000/9001, litellm 4000,
#   langfuse 3001, loki 3100, grafana 3000, prometheus 9090, hermes 9119/8642,
#   nginx-exporter 9113, node-exporter 9100.
# S-8/T10.6: DENY на уровне ufw (defense-in-depth поверх 127.0.0.1-bind в compose) И запрещены в
# extra_ports (FORBIDDEN расширен). 8080 НЕ включён: cadvisor/status-page 127.0.0.1-bound,
# user-проекты часто публикуют 8080 (тест-проект на test-VPS) — не блокировать.
MODULE_PORTS_DENY: tuple[int, ...] = (
    6379,
    8123,
    9000,
    9001,
    4000,
    3001,
    3100,
    9090,
    3000,
    9119,
    8642,
    9113,
    9100,
)
# Полный запрет extra_ports: Docker API + модульные порты + явный deny 5432
FORBIDDEN_EXTRA_PORTS: tuple[int, ...] = (*FORBIDDEN_PORTS, DENY_PORT, *MODULE_PORTS_DENY)
_PORT_RE = re.compile(r"^[0-9]+$")

# ⚠️ 142 W6 (A2/A3): Privoxy/Tor доступ с docker-мостов.
# · 2-й цикл 141: после reboot/переустановки privoxy слушал только 127.0.0.1, а ufw-правило
# · `allow 172.16.0.0/12:8118` добавлялось ВРУЧНУЮ (A2/A3). Теперь — декларативный baseline:
# · при TOR_ENABLED=1 открываем 8118 для docker-моста 172.16.0.0/12 (grafana/контейнеры ходят
# · на host.docker.internal:8118 = docker-gateway), verify сверяет правило (W6 Фикс 2).
TOR_PRIVOXY_NET: str = "172.16.0.0/12"
# ⚠️ SoT приватного порта Privoxy (DevPlan 170 W1-A3): единая int-константа 8118 —
# консолидация дублей (privoxy_config, install_tor_proxy, tor_proxy_check, reporting, cli).
# Значение 8118 — порт по умолчанию Privoxy (dpkg-канон); TOR_PRIVOXY_PORT — алиас
# firewall-контекста (142 W6) того же порта. Типы: int в коде, str только в f-string.
PRIVOXY_PORT: int = 8118
TOR_PRIVOXY_PORT: int = PRIVOXY_PORT

# Zabbix-мониторинг провайдера (Timeweb Cloud, DevPlan 162 W2-4 → 164 W0-3.1):
# агент провайдера слушает 0.0.0.0:10050 (passive checks); ufw default-deny его режет —
# явный allow ТОЛЬКО с 3 официальных IP мониторинга (Server= из официального конфига
# zabbix.repo.timeweb.ru), НЕ вся подсеть 92.53.116.0/24.
ZABBIX_MONITORING_IPS: tuple[str, ...] = ("92.53.116.12", "92.53.116.111", "92.53.116.119")
ZABBIX_PORT: int = 10050

# ── Peer-scoped firewall (DevPlan 010 T2.3/T2.4) ────────────────────────────────
# Кросс-нодовые порты публикуются ТОЛЬКО для IP нод-пиров из placement.yaml (инвариант 4):
# `ufw allow from <peer_host> to any port <p>/tcp comment platform-peer-<p>-<peer>`.
# Матрица КАНОНИЧНА (DevPlan 010 §6.1 T2.2, TRAP §3): 6432 (pgbouncer), 6379 (redis),
# 9000 (minio API), 8123 (CH HTTP) + 19000 (CH native peer), 3100 (loki push),
# 9100+8080 (node-metrics), 9187/9121/9113 (service-exporters). Прямой 5432 НЕ публикуется
# (все потребители едут на data-ноду вместе с postgres — DevPlan 010 §8). Значения — ТОЛЬКО
# из shared/platform_ports.py (порт-литералы запрещены гейтом test_gate_port_parity).
# Ключи = имена placement-модулей (core/modules/<name>): pgbouncer-фасад 6432 живёт в модуле
# postgres, loki-push 3100 — в модуле logging.
PEER_PUBLISH_PORTS: dict[str, tuple[int, ...]] = {
    "postgres": (PLATFORM_PORT_PGBOUNCER,),  # pgbouncer 6432 — единственный кросс-нодовый PG-фасад
    "redis": (PLATFORM_PORT_REDIS,),  # 6379
    "minio": (PLATFORM_PORT_MINIO,),  # 9000 API
    "clickhouse": (PLATFORM_PORT_CLICKHOUSE, CLICKHOUSE_NATIVE_PEER),  # 8123 HTTP + 19000 native-peer
    "logging": (LOKI_HTTP,),  # 3100 loki-push (центральный приём логов)
    "node-metrics": (NODE_EXPORTER, CADVISOR),  # 9100 + 8080 (scrape monitoring)
    "service-exporters": (POSTGRES_EXPORTER, REDIS_EXPORTER, NGINX_EXPORTER),  # 9187, 9121, 9113
}

# Эвристика потребитель→поставщик (DevPlan 010 T2.3 «упрощение v1»): нода B размещает хотя бы
# один модуль из CONSUMER_OF[service] → B — потребитель сервиса, её IP получает peer-ALLOW.
# 🧐 TRAP[DECISION] · 2026-08-22 · — · CONSUMER_OF — эвристика-константа: нода-потребитель = та,
# что размещает модуль из списка (по данным placement); инфра-зависимости (nginx и пр.) НЕ
# порождают правил · Rejected: полный граф зависимости по module.yaml#depends_on (все deps —
# включая инфра-упорядочивающие — стали бы кросс-нодовыми открытиями: false-openings легитимных
# топологий) · Reason: v1-упрощение по плану; «Открытия S2/S3» (§8) показывают потребность явно ·
# Rev: первый кейс ложного/пропущенного открытия (consumer-модуль вне списка или модуль в списке
# без реального потребления) → расширить матрицу или перейти на module.yaml#depends_on-граф
CONSUMER_OF: dict[str, frozenset[str]] = {
    "postgres": frozenset({"litellm", "langfuse", "hermes-agent"}),  # pgbouncer 6432
    "redis": frozenset({"hermes-agent", "langfuse"}),
    "minio": frozenset({"langfuse"}),
    "clickhouse": frozenset({"langfuse"}),
    "logging": frozenset({"log-collector"}),  # loki-push с чужих нод
    "node-metrics": frozenset({"monitoring"}),  # scrape 9100/8080
    "service-exporters": frozenset({"monitoring"}),  # scrape 9187/9121/9113
}

# Нода-хост проектов: nginx (ingress) ⇒ на ноде живут user-проекты ⇒ потребитель data-plane
# сервисов (PLATFORM_*_URL проектов: PLATFORM_POSTGRES_DSN=pgbouncer:6432 и т.д.).
# 🧐 TRAP[DECISION] · 2026-08-22 · — · nginx-размещение = маркер «нода-хост проектов» для
# data-plane сервисов · Rejected: игнорировать ingress-ноду в потребителях (CONSUMER_OF строго
# из платформенных модулей) · Reason: DevPlan 010 §8 S3 «Открытия: data-1 → 6432 (agent-1,
# apps-1)» и Acceptance W2 — «ufw dry-run S3 показывает allow from 10.8.0.13 to port 6432»;
# проекты (вне placement.yaml, привязаны ai-platform.yaml#target_node) потребляют pgbouncer/
# redis/minio/clickhouse с ingress-ноды; без маркера acceptance-критерий S3 не выполняется ·
# Rev: первый кейс ложного открытия (ingress-нода без проектов, потребляющих data-сервисы) →
# перенести привязку проектов в placement (project-секция) и строить потребителей оттуда
PROJECT_HOST_SERVICES: frozenset[str] = frozenset({"postgres", "redis", "minio", "clickhouse"})

# ── DOCKER-USER defence-in-depth → docker_user_policy.py (DevPlan 170 W6-D2) ──
# iptables-домен (DOCKER-USER FORWARD + privoxy INPUT) консолидирован в docker_user_policy.py;
# firewall.py re-export'ит desired_docker_user_rules/apply_docker_user_policy/DOCKER_USER_CHAIN/
# DOCKER_BRIDGE_NETS (импорт-пути потребителей и CLI --apply-docker-user сохранены).


# region FUNC_validate_ports
## @purpose  Валидация extra_ports: integer 1-65535, запрет FORBIDDEN_EXTRA_PORTS (Docker API +
##           модульные внутренние порты, fail-fast) — S-8/T10.6.
## @io       ⇥ ports: list[str] → ⎋ list[int] — валидные порты
## @complexity O(P) — P = число портов
## @raises   ConfigValidationError на невалидный/запрещённый порт (контракт: exit 1 через main)
def validate_ports(ports: list[str]) -> list[int]:
    """Validate extra_ports (1-65535, no Docker API, no module-internal ports)."""
    result: list[int] = []
    for port in ports:
        if not _PORT_RE.match(port) or not (1 <= int(port) <= PORT_MAX):
            msg = f"Invalid port '{port}' — must be integer 1-65535"
            raise ConfigValidationError(msg)
        if int(port) in FORBIDDEN_PORTS:
            msg = f"SECURITY: Port {port} is a Docker API port — forbidden in extra_ports"
            raise ConfigValidationError(msg)
        if int(port) in FORBIDDEN_EXTRA_PORTS:
            msg = (
                f"SECURITY: Port {port} is a module-internal port (platform-infra.yaml registry) — "
                "forbidden in extra_ports (S-8, T10.6)"
            )
            raise ConfigValidationError(msg)
        result.append(int(port))
    logger.info("[IMP:8][firewall][validate] extra_ports validated: %s", ports or "none")
    return result


# endregion FUNC_validate_ports


# region FUNC_build_rules
## @purpose  Построить упорядоченный список ufw-команд инкрементальной политики (S-14, T10.10):
##           enable→defaults→ssh-first→baseline→extra(from ip)→tor-privoxy(142 W6)→PEER(010 T2.3)→
##           deny модульных→stale-reconcile.
##           НИКАКОГО disable/reset — firewall активен на всём протяжении (нет окна без файрвола).
## @io       ⇥ extra_ports: list[int], source_ip: str|None, tor_enabled: bool = False,
##           zabbix_monitoring: bool = True, peer_rules: list[list[str]]|None = None
##           → ⎋ list[list[str]] — команды для subprocess
## @complexity O(B + P + M + Z + R) — B = baseline, P = extra, M = module-deny, Z = zabbix IPs, R = peer
## @raises   ConfigValidationError если extra_ports заданы без source_ip (S-8: никогда Anywhere)
def build_rules(
    extra_ports: list[int],
    source_ip: str | None = None,
    tor_enabled: bool = False,
    zabbix_monitoring: bool = True,
    peer_rules: list[list[str]] | None = None,
) -> list[list[str]]:
    """Build the ordered ufw command list (incremental, firewall никогда не выключается)."""
    if extra_ports and not source_ip:
        msg = "SECURITY: extra_ports require --source-ip <ip> (allow from <ip>, никогда 0.0.0.0/Anywhere) — S-8, T10.6"
        raise ConfigValidationError(msg)
    rules: list[list[str]] = [
        # 1. Firewall активен с первой команды (S-14) — нет окна disable/reset
        ["ufw", "--force", "enable"],
        # 2. Default-deny ПЕРЕД allow-правилами — ничего не открыто по умолчанию
        ["ufw", "default", "deny", "incoming"],
        ["ufw", "default", "allow", "outgoing"],
        # 3. SSH первым — lockout-safe при переконфигурации
        ["ufw", "allow", "22/tcp", "comment", "platform-baseline"],
    ]
    rules.extend(["ufw", "allow", f"{port}/tcp", "comment", "platform-baseline"] for port in (80, 443))
    # Zabbix-мониторинг провайдера (162 W2-4): 3 официальных IP → 10050/tcp.
    # Внешний nc с постороннего IP → refuse (default-deny), метрики провайдера собираются.
    if zabbix_monitoring:
        rules.extend(
            [
                "ufw",
                "allow",
                "from",
                ip,
                "to",
                "any",
                "port",
                str(ZABBIX_PORT),
                "proto",
                "tcp",
                "comment",
                "platform-zabbix",
            ]
            for ip in ZABBIX_MONITORING_IPS
        )
    # extra_ports — ТОЛЬКО с явным источником (S-8): allow from <ip> to any port <p>/tcp
    # str(source_ip): guard выше (extra_ports && !source_ip → raise) гарантирует non-None
    # при непустом extra_ports; при пустом — генератор не выполняется (str(None) недостижим)
    rules.extend(
        ["ufw", "allow", "from", str(source_ip), "to", "any", "port", f"{port}/tcp", "comment", "platform-extra"]
        for port in extra_ports
    )
    # 142 W6 (A2/A3): Tor/Privoxy — доступ с docker-моста (172.16.0.0/12) к privoxy :8118.
    # Контейнеры (grafana telegram-канал, B14: host.docker.internal = docker-gateway) ходят
    # на privoxy; правило было ручным ufw allow — теперь декларативный baseline при TOR_ENABLED.
    if tor_enabled:
        rules.append([
            "ufw",
            "allow",
            "from",
            TOR_PRIVOXY_NET,
            "to",
            "any",
            "port",
            str(TOR_PRIVOXY_PORT),
            "proto",
            "tcp",
            "comment",
            "platform-tor-privoxy",
        ])
    # Peer-scoped allow (DevPlan 010 T2.3): вставка ПЕРЕД module-deny — ufw first-match.
    # Правило deny <port>/tcp (ниже) выиграло бы у allow на том же порту, если бы шло раньше;
    # peer-ALLOW обязан быть ДО deny, иначе кросс-нодовый трафик пиров режется deny.
    if peer_rules:
        rules.extend(peer_rules)
    # Модульные внутренние порты — явный deny (defense-in-depth поверх 127.0.0.1-bind)
    rules.extend(
        ["ufw", "deny", f"{port}/tcp", "comment", "platform-module-deny"] for port in sorted(MODULE_PORTS_DENY)
    )
    rules.append(["ufw", "deny", f"{DENY_PORT}/tcp", "comment", "explicit-deny-postgresql"])
    return rules


# endregion FUNC_build_rules


# region FUNC__peer_publish_ports
def _peer_publish_ports() -> set[int]:
    """Flatten PEER_PUBLISH_PORTS values into a set (verify/reconcile consumers).

    ## @purpose  Единое множество кросс-нодово публикуемых портов (DevPlan 010 T2.3): verify
    ##            отличает peer-ALLOW от Anywhere на этих портах; reconcile не удаляет их как stale.
    ## @io — ⇥ → ⎋ set[int]
    ## @complexity — O(P) где P = порты матрицы
    ## @invariants  Порты только из PEER_PUBLISH_PORTS (единый источник, 0 литералов).
    """
    return {p for ports in PEER_PUBLISH_PORTS.values() for p in ports}


# endregion FUNC__peer_publish_ports


# region FUNC_build_peer_rules
## @purpose  Peer-scoped allow-правила из placement.yaml (DevPlan 010 T2.3): для каждой ноды A,
##           размещающей сервис с портами PEER_PUBLISH_PORTS[S], и каждой ДРУГОЙ ноды B-потребителя
##           (B размещает модуль из CONSUMER_OF[S] или nginx-project-host для data-plane) —
##           `ufw allow from <B.host> to any port <p>/tcp comment platform-peer-<p>-<B>`.
##           Отсутствие placement.yaml → [] (single-node no-op: bind 127.0.0.1, правил нет).
## @io       ⇥ placement: Placement | None → ⎋ list[list[str]] — ufw-команды (детерминированный порядок)
## @complexity O(N² × S × K) — N = ноды, S = сервисы матрицы, K = порты сервиса
## @invariants
##   - placement None → [] (single-node байт-совместимость, DevPlan 010 §1.1)
##   - КАЖДОЕ правило несёт `from <peer>` — Anywhere-публикация кросс-нодовых портов запрещена (инвариант 4)
##   - 5432 НЕ публикуется: postgres не входит в PEER_PUBLISH_PORTS (потребители едут на data-ноду, §8)
##   - Инфра-зависимости НЕ порождают правил (CONSUMER_OF — только data-plane/scrape потребители)
##   - Результат отсортирован по (service, provider, consumer, port) — детерминизм для diff/гейтов
## @raises   ConfigValidationError: неизвестная нода (пробрасывается из resolve_node_modules)
def build_peer_rules(placement: Placement | None) -> list[list[str]]:
    """Build peer-scoped ufw allow rules from placement.yaml (DevPlan 010 T2.3)."""
    if placement is None:
        logger.info("[IMP:8][firewall][peer] No placement.yaml — single-node no-op, peer rules = []")
        return []
    # Эффективные модули каждой ноды (резолв форм singleton/all-nodes/nodes[]/off — канон W0)
    placed_by_node: dict[str, set[str]] = {node: set(resolve_node_modules(placement, node)) for node in placement.nodes}
    rules: list[list[str]] = []
    for service in sorted(PEER_PUBLISH_PORTS):
        consumer_modules = set(CONSUMER_OF[service])
        if service in PROJECT_HOST_SERVICES:
            consumer_modules.add("nginx")  # нода-хост проектов (TRAP[DECISION] выше)
        for provider in sorted(placement.nodes):
            if service not in placed_by_node[provider]:
                continue
            for consumer in sorted(placement.nodes):
                if consumer == provider:
                    continue  # co-located — Docker DNS, без кросс-нодового правила
                if consumer_modules.isdisjoint(placed_by_node[consumer]):
                    continue  # нода B не размещает ни одного потребителя — открытия нет
                for port in PEER_PUBLISH_PORTS[service]:
                    peer_host = placement.nodes[consumer]
                    rules.append([
                        "ufw",
                        "allow",
                        "from",
                        peer_host,
                        "to",
                        "any",
                        "port",
                        f"{port}/tcp",
                        "comment",
                        f"platform-peer-{port}-{consumer}",
                    ])
                    logger.info(
                        "[IMP:8][firewall][peer] allow from %s to %d/tcp (provider=%s consumer=%s)",
                        peer_host,
                        port,
                        provider,
                        consumer,
                    )
    logger.info(
        "[IMP:9][firewall][peer] Built %d peer rules from placement context=%s nodes=%d",
        len(rules),
        placement.context,
        len(placement.nodes),
    )
    return rules


# endregion FUNC_build_peer_rules


# region FUNC_collect_stale_platform_rules
## @purpose  Детекция stale allow-правил платформы (комментарий platform-*): порты, которые больше
##           НЕ в желаемом allow-наборе (baseline + extra) и не в deny-наборе → подлежат удалению.
##           Идемпотентность БЕЗ `ufw reset` (S-14): удаляем точечно ТОЛЬКО свои правила.
##           DevPlan 010 инвариант 4: delete-команда ОБЯЗАНА нести source IP из строки статуса —
##           форма `ufw delete allow <port>/tcp` неоднозначна при ≥2 пирах на порту (firewall.py:268
##           баг); delete точно совпадает с allow-формой (peer/extra: `from <src>`; baseline: bare).
## @io       ⇥ status_text: str (текущий `ufw status verbose`), desired_allow: set[int],
##           peer_ports: set[int]|None (порты peer-матрицы — не stale, пока placement активен)
##           → ⎋ list[list[str]]
## @complexity O(L) — L = строк статуса
def collect_stale_platform_rules(
    status_text: str,
    desired_allow: set[int],
    peer_ports: set[int] | None = None,
) -> list[list[str]]:
    """Delete-команды для platform-* allow-правил, чьи порты вышли из желаемого набора.

    Full-spec delete (DevPlan 010 инвариант 4): источник из строки статуса (`ALLOW IN <src>`)
    переносится в delete — `ufw delete allow from <src> to any port <p>/tcp`. Bare delete
    (`delete allow <p>/tcp`) применим только к правилам без источника (Anywhere-baseline).
    """
    deletes: list[list[str]] = []
    denied = set(MODULE_PORTS_DENY) | {DENY_PORT}
    for line in status_text.splitlines():
        # Формат: `22/tcp ALLOW IN Anywhere  # platform-baseline` /
        #         `6432/tcp ALLOW IN 10.8.0.12  # platform-peer-6432-agent-1` (source в статусе)
        if "# platform-" not in line:
            continue
        m = re.match(r"^(\d+)/tcp(?:\s+\(v6\))?\s+ALLOW\s+IN\s+(\S+)", line.strip())
        if not m:
            continue
        port = int(m.group(1))
        source = m.group(2)
        if port in desired_allow or port in denied:
            continue
        if peer_ports and port in peer_ports:
            continue  # peer-матричный порт — управляется placement, не baseline-reconcile (T2.3)
        if source == "Anywhere":
            # Правило без источника (baseline/legacy) — bare delete совпадает с allow-формой
            deletes.append(["ufw", "delete", "allow", f"{port}/tcp"])
        else:
            # Peer/extra-правило: delete ОБЯЗАН нести source IP (инвариант 4 — точное совпадение)
            deletes.append(["ufw", "delete", "allow", "from", source, "to", "any", "port", f"{port}/tcp"])
        logger.info("[IMP:8][firewall][reconcile] Stale platform allow %d/tcp (source %s) → delete", port, source)
    return deletes


# endregion FUNC_collect_stale_platform_rules


# region FUNC_parse_ufw_status
## @purpose  Разобрать `ufw status verbose` на статус-активность + allow/deny-порты (verify-критерий).
## @io       ⇥ status_text: str → ⎋ tuple[bool, dict[int, str]] — (active, port→action map)
## @complexity O(L) — L = строк статуса
def parse_ufw_status(status_text: str) -> tuple[bool, dict[int, str]]:
    """Parse ufw status verbose → (active, {port: ALLOW|DENY})."""
    active = "Status: active" in status_text
    port_actions: dict[int, str] = {}
    for line in status_text.splitlines():
        # v1.0.1 TRAP[BUG] (Фаза 6): IPv6-секция ufw пишет «22/tcp (v6) ALLOW IN Anywhere (v6)» —
        # прежний regex захватывал «(v6)» вместо ALLOW/DENY и ПЕРЕЗАПИСЫВАЛ IPv4-значения
        # (v6-секция идёт позже) → S3 ложно FAIL «22/tcp not ALLOW» на реально настроенной ноде.
        m = re.match(r"^(\d+)/tcp(?:\s+\(v6\))?\s+(\S+)", line.strip())
        if m:
            port_actions[int(m.group(1))] = m.group(2)
    return active, port_actions


# endregion FUNC_parse_ufw_status


# region FUNC__allow_sources_for_port
def _allow_sources_for_port(status_text: str, port: int) -> set[str]:
    r"""Источники ALLOW-строк порта в `ufw status verbose` (DevPlan 010 T2.3).

    ▶ ┌(status, port)┐ → ○ filter ALLOW-строки порта → ⊕ источники (после `ALLOW IN`) → ⎋ set[str]

    ## @purpose  Source-aware проверка публикации: verify отличает peer-ALLOW (`ALLOW IN 10.8.0.12`)
    ##            от Anywhere (`ALLOW IN Anywhere`) на кросс-нодовых портах. ufw status показывает
    ##            для dual-правил (peer-allow + module-deny) ОБЕ строки — port-сводка parse_ufw_status
    ##            не различает источники, поэтому источники парсятся построчно.
    ## @io — ⇥ status_text: str, port: int → ⎋ set[str] — источники ALLOW-строк (пусто = не публикуется)
    ## @complexity — O(L) где L = строк статуса
    ## @invariants  Учитываются ТОЛЬКО ALLOW-строки (DENY игнорируются); IPv6-строки нормализованы
    ##              через (?:\s+\(v6\))? — источник без суффикса (v6).
    """
    sources: set[str] = set()
    for line in status_text.splitlines():
        m = re.match(rf"^{port}/tcp(?:\s+\(v6\))?\s+ALLOW\s+IN\s+(\S+)", line.strip())
        if m:
            sources.add(m.group(1))
    return sources


# endregion FUNC__allow_sources_for_port


# region FUNC_verify_firewall
## @purpose  Verify ufw status: active, baseline ALLOW, forbidden NOT ALLOW, 5432 DENY,
##           модульные порты NOT ALLOW (S-8/T10.6 CHECK по реестру модулей),
##           tor-privoxy правило 8118 при TOR_ENABLED (142 W6),
##           zabbix-мониторинг 10050 ALLOW (162 W2-4) при zabbix_monitoring,
##           peer-семантика (DevPlan 010 T2.3): peer-ALLOW от известного пира = PASS,
##           Anywhere-публикация на кросс-нодовых портах = FAIL.
## @io       ⇥ status_text: str, tor_enabled: bool = False, zabbix_monitoring: bool = True,
##           peer_ips: set[str]|None = None (известные IP нод-пиров из placement)
##           → ⎋ bool
## @complexity O(1) — parse + проверки
def verify_firewall(
    status_text: str,
    tor_enabled: bool = False,
    zabbix_monitoring: bool = True,
    peer_ips: set[str] | None = None,
) -> bool:
    """Verify ufw status against the policy. True = compliant."""
    active, port_actions = parse_ufw_status(status_text)
    if not active:
        logger.error("[IMP:10][firewall][verify] ufw is NOT active after apply")
        return False
    for port in BASELINE_PORTS:
        if port_actions.get(port) != "ALLOW":
            logger.error("[IMP:10][firewall][verify] Expected port %d/tcp ALLOW not found", port)
            return False
    # 162 W2-4: zabbix-мониторинг провайдера — 10050 обязано быть ALLOW (иначе потеря мониторинга
    # при default-deny). ufw status показывает `10050/tcp ALLOW IN <ip>  # platform-zabbix`.
    if zabbix_monitoring and not re.search(rf"^\s*{ZABBIX_PORT}/tcp\s+ALLOW", status_text, re.M):
        logger.error(
            "[IMP:10][firewall][verify] SECURITY: zabbix-monitoring rule %d/tcp ALLOW missing (162 W2-4)",
            ZABBIX_PORT,
        )
        return False
    # 142 W6 (A3): при TOR_ENABLED правило privoxy (172.16.0.0/12 → 8118) ОБЯЗАНО быть в статусе.
    # ufw status verbose показывает его как `8118/tcp ALLOW IN 172.16.0.0/12  # platform-tor-privoxy`.
    if tor_enabled and not re.search(rf"^\s*{TOR_PRIVOXY_PORT}/tcp\s+ALLOW", status_text, re.M):
        logger.error(
            "[IMP:10][firewall][verify] SECURITY: tor-privoxy rule %s→%d ALLOW missing (142 W6)",
            TOR_PRIVOXY_NET,
            TOR_PRIVOXY_PORT,
        )
        return False
    for port in FORBIDDEN_PORTS:
        if port_actions.get(port) == "ALLOW":
            logger.error("[IMP:10][firewall][verify] SECURITY: Docker API port %d is open in ufw", port)
            return False
    if port_actions.get(DENY_PORT) != "DENY":
        logger.error("[IMP:10][firewall][verify] SECURITY: Port %d is not DENIED in ufw", DENY_PORT)
        return False
    peer_publish = _peer_publish_ports()
    # Модульные внутренние порты (S-8). Peer-матричные из них (6379/8123/9000/3100/9100/9113) в
    # multi-node получают peer-ALLOW — проверка source-aware: peer-ALLOW от известного пира = PASS,
    # ALLOW от Anywhere/неизвестного источника = FAIL (DevPlan 010 T2.3).
    for port in MODULE_PORTS_DENY:
        if port in peer_publish and peer_ips:
            bad = _allow_sources_for_port(status_text, port) - set(peer_ips)
            if bad:
                logger.error(
                    "[IMP:10][firewall][verify] SECURITY: module-internal port %d ALLOW from "
                    "non-peer source %s (peer-only, DevPlan 010)",
                    port,
                    sorted(bad),
                )
                return False
        elif port_actions.get(port) == "ALLOW":
            logger.error("[IMP:10][firewall][verify] SECURITY: module-internal port %d is ALLOW in ufw (S-8)", port)
            return False
    # Кросс-нодовые порты вне deny-реестра (6432/19000/8080/9187/9121): Anywhere-публикация запрещена
    # (инвариант 4). Single-node (peer_ips=None): FAIL только на Anywhere (IP-scoped allow — S-8-легитимно);
    # multi-node: FAIL на ЛЮБОЙ источник вне peer_ips (включая Anywhere).
    for port in sorted(peer_publish - set(MODULE_PORTS_DENY)):
        allow_sources = _allow_sources_for_port(status_text, port)
        if not allow_sources:
            continue  # не публикуется — ок
        # Single-node (peer_ips=None): FAIL только на Anywhere (IP-scoped allow — S-8-легитимно);
        # multi-node: FAIL на ЛЮБОЙ источник вне peer_ips (включая Anywhere)
        bad = allow_sources - set(peer_ips) if peer_ips else {s for s in allow_sources if s == "Anywhere"}
        if bad:
            logger.error(
                "[IMP:10][firewall][verify] SECURITY: cross-node port %d ALLOW from non-peer source %s "
                "(Anywhere-публикация запрещена, DevPlan 010)",
                port,
                sorted(bad),
            )
            return False
    logger.info(
        "[IMP:9][firewall][verify] Firewall verified: active, 22/80/443 open, Docker ports closed, "
        "module ports denied"
        + (", peer-allow от известных пиров PASS" if peer_ips else "")
        + (" + tor-privoxy 8118 allow" if tor_enabled else "")
    )
    return True


# endregion FUNC_verify_firewall


# region FUNC_apply_rules_subprocess
## @purpose  Применить ufw-команды через subprocess. Первая (enable) fail-fast; остальные fail-fast
##           (инкрементальный apply — любая ошибка = политика не применена, честный отказ).
## @io       ⇥ rules: list[list[str]] → ⎋ bool
## @complexity O(R) — R = команд
def _apply_rules_subprocess(rules: list[list[str]], run_cmd: Callable[..., object] | None = None) -> bool:
    """Run ufw commands via subprocess. All fail-fast (S-14 — no best-effort disable window).

    DI (W-H DevPlan 163): run_cmd=None → subprocess.run (канон); тесты передают fake-канал.
    """
    runner = subprocess.run if run_cmd is None else run_cmd
    for cmd in rules:
        try:
            result = cast(
                "subprocess.CompletedProcess[str]",
                runner(cmd, capture_output=True, text=True, check=False),
            )  # W11-G3: DI run_cmd → object; каст к CompletedProcess (канон subprocess.run)
        except OSError as exc:
            logger.error("[IMP:10][firewall][apply] ufw not available: %s", exc)
            return False
        if result.returncode != 0:
            logger.error("[IMP:10][firewall][apply] ufw command failed: %s %s", " ".join(cmd), result.stderr.strip())
            return False
    logger.info("[IMP:9][firewall][apply] Incremental ufw policy applied (no disable/reset window)")
    return True


# endregion FUNC_apply_rules_subprocess


# region FUNC_run
## @purpose  Полный прогон: placement → validate → build (incremental + peer) → stale-reconcile →
##           apply → verify. 142 W6: tor_enabled=None → os.environ TOR_ENABLED (φ1-процесс имеет env;
##           параметр для тестируемости чистых функций). 162 W2-4: zabbix_monitoring=True по умолчанию.
##           DevPlan 010 T2.3: placement_path — node-configs/CONTEXT/placement.yaml; отсутствует →
##           single-node no-op (build_peer_rules → [], verify peer_ips=None → легаси-строгие проверки).
## @io       ⇥ extra_ports: list[str], source_ip: str|None, tor_enabled: bool|None,
##           zabbix_monitoring: bool = True, run_cmd: Callable|None = None,
##           placement_path: str|None = None → ⎋ bool
## @complexity O(R + L + N² × S × K)
def run(
    extra_ports: list[str],
    source_ip: str | None = None,
    tor_enabled: bool | None = None,
    zabbix_monitoring: bool = True,
    run_cmd: Callable[..., object] | None = None,
    placement_path: str | None = None,
) -> bool:
    """Full firewall pipeline: validate ports, build incremental + peer rules, reconcile stale, apply, verify.

    DI (W-H DevPlan 163): run_cmd=None → subprocess.run (канон); тесты передают fake-канал.
    """
    if tor_enabled is None:
        tor_enabled = os.environ.get("TOR_ENABLED", "false").lower() == "true"
    # ── Peer-scoped rules из placement.yaml (DevPlan 010 T2.3) ──
    # placement отсутствует/невалиден → fail-fast (single-node no-op — []); валидный → peer-правила
    # + известные пиры для verify + peer-порты вне baseline-reconcile (не stale).
    peer_rules: list[list[str]] = []
    peer_ips: set[str] = set()
    peer_ports: set[int] | None = None
    if placement_path:
        try:
            placement = load_placement(placement_path)
        except ConfigValidationError as exc:
            logger.error("[IMP:10][firewall][run] placement.yaml invalid: %s", exc)
            return False
        peer_rules = build_peer_rules(placement)
        if placement is not None:
            peer_ips = set(placement.nodes.values())
            peer_ports = _peer_publish_ports()
    try:
        ports = validate_ports(extra_ports)
        rules = build_rules(
            ports,
            source_ip,
            tor_enabled=tor_enabled,
            zabbix_monitoring=zabbix_monitoring,
            peer_rules=peer_rules,
        )
    except ConfigValidationError as exc:
        logger.error("[IMP:10][firewall][run] %s", exc)
        return False
    # Stale-reconcile (S-14): удалить platform-* allow-правила, вышедшие из желаемого набора —
    # идемпотентность без ufw reset. Читаем статус ДО apply (текущее состояние).
    # Peer-матричные порты (peer_ports) не stale, пока placement активен (T2.3).
    try:
        status_before = subprocess.run(["ufw", "status", "verbose"], capture_output=True, text=True, check=False)
        before_text = status_before.stdout if status_before.returncode == 0 else ""
    except OSError:
        before_text = ""
    desired_allow = set(BASELINE_PORTS) | set(ports)
    if tor_enabled:
        desired_allow.add(TOR_PRIVOXY_PORT)
    if zabbix_monitoring:
        desired_allow.add(ZABBIX_PORT)
    rules.extend(collect_stale_platform_rules(before_text, desired_allow, peer_ports=peer_ports))
    if not _apply_rules_subprocess(rules, run_cmd=run_cmd):
        return False
    try:
        status = subprocess.run(["ufw", "status", "verbose"], capture_output=True, text=True, check=False)
        status_text = status.stdout if status.returncode == 0 else ""
    except OSError:
        status_text = ""
    return verify_firewall(status_text, tor_enabled=tor_enabled, zabbix_monitoring=zabbix_monitoring, peer_ips=peer_ips)


# endregion FUNC_run


# region FUNC_main
def main(
    argv: list[str] | None = None,
    *,
    euid_fn: Callable[[], int] | None = None,
    run_cmd: Callable[..., object] | None = None,
) -> int:
    """CLI entry: `python3 -m core.internal.bootstrap.firewall [--source-ip <ip>] [extra_ports...]`.

    ▶ ┌argv extra_ports (space-separated)┐ → ○ run() → ⎋ exit 0|1
    ▶ ┌--apply-docker-user┐ → ◇ root? → ○ apply_docker_user_policy() (идемпотентно) → ⎋ exit 0|1

    DI (W-H DevPlan 163): euid_fn=None → os.geteuid; run_cmd=None → subprocess.run.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Incremental ufw firewall (DevPlan 118 E3 + 136 W10)")
    parser.add_argument(
        "--source-ip",
        default=None,
        help="Source IP for extra_ports allow rules (S-8: extra_ports никогда не 0.0.0.0/Anywhere)",
    )
    parser.add_argument(
        "--apply-docker-user",
        action="store_true",
        help="Apply DOCKER-USER ingress policy (DevPlan 162 W2-3) — systemd ExecStartPost docker.service; "
        "root required; idempotent (-C guard); exit 0|1",
    )
    parser.add_argument(
        "--no-zabbix-monitoring",
        action="store_true",
        help="Disable zabbix-monitoring allow rules (162 W2-4: default ON — Timeweb provider 10050/tcp)",
    )
    parser.add_argument(
        "--placement",
        default=None,
        help="Path to node-configs/CONTEXT/placement.yaml (DevPlan 010 T2.3) — peer-scoped allow "
        "rules from multi-node topology; отсутствует файл → single-node no-op",
    )
    parser.add_argument("extra_ports", nargs="*", help="Extra ports to allow from --source-ip (space-separated)")

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3)."""

        def __init__(self) -> None:
            super().__init__()
            self.source_ip: str | None
            self.apply_docker_user: bool
            self.no_zabbix_monitoring: bool
            self.placement: str | None
            self.extra_ports: list[str] | None

    args = parser.parse_args(argv, namespace=_CliArgs())
    if args.apply_docker_user:
        # W2-3: DOCKER-USER — отдельный режим (iptables, не ufw); root-check fail-fast
        # (как security_posture.py канон). iptables без root молча не сработает — честный отказ.
        if (os.geteuid if euid_fn is None else euid_fn)() != 0:
            logger.error("[IMP:10][firewall][docker-user] --apply-docker-user требует root (iptables)")
            return 1
        return 0 if apply_docker_user_policy(run_cmd=run_cmd) else 1
    # 142 W6: TOR_ENABLED читается из env внутри run() (None → env); CLI не принимает флаг —
    # фасад firewall.sh вызывается из φ1, где TOR_ENABLED уже в окружении.
    return (
        0
        if run(
            args.extra_ports or [],  # W11-G3: nargs="*" без аргументов → None; [] сохраняет falsy-семантику run()
            source_ip=args.source_ip,
            zabbix_monitoring=not args.no_zabbix_monitoring,
            run_cmd=run_cmd,
            placement_path=args.placement,
        )
        else 1
    )


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
