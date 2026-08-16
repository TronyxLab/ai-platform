#!/usr/bin/env python3
# GREP_SUMMARY: security-posture S5 S8 S9 docker daemon live-restore iptables image-freshness digest-drift manifest-inspect staleness docker-proxy listening-ports
# STRUCTURE: ▶ S5: read daemon.json + ss -tlnp → ◇ live-restore/iptables/API-порты → ⎋ CheckResult ┤
#            ○ S8: docker ps → inspect (ref|digest) → ○ _inspect_image (manifest inspect --verbose) → ◇ _classify_staleness (pure) → WARN/PASS ┤
#            ○ S9: ss -tlnp → ◇ docker-proxy 0.0.0.0:[::] на внутреннем порту (MODULE_PORTS_DENY) → FAIL/PASS
# region MODULE_CONTRACT
## @purpose  S5/S8/S9 docker-сектор security-постуры ноды (DevPlan 134 L2): docker-демон (S5 —
##           live-restore/iptables/API-порты 2375/2376), image freshness (S8 — digest-drift
##           локального RepoDigests vs registry manifest inspect, DevPlan 134 L4) и реальный
##           LISTEN-кросс-чек docker-proxy (S9, W10 T10.2/S-7). Извлечено из монолита
##           security_posture.py (план 170 W6-D1): god check_image_freshness (73 LOC/CC18)
##           → _inspect_image (per-image DI-проба) + _classify_staleness (PURE).
## @scope    Вызывается run_all_checks (run.py) и напрямую (DI-тесты). Импортирует _shared,
##           shared/docker_ops (гейт docker_sole_path), shared/timeouts, firewall (реестры
##           портов — SoT platform-infra.yaml) — циклических зависимостей нет.
## @invariants
##   - S5: live-restore=true обязателен (иначе контейнеры умирают при рестарте демона);
##     iptables=false → FAIL; Docker API порты 2375/2376 не слушают (ss -tlnp)
##   - S8: ТОЛЬКО WARN по дрейфу (digest-pin — осознанная политика, гейт image_tag_form):
##     дрейф = «пора обновлять», не «сломано»; registry недоступен/timeout/rate-limit → WARN;
##     локально-собранные (пустой RepoDigests) → skip; локальные имена (manifest unknown) → skip;
##     docker ps/inspect падают → FAIL (честный отказ, cannot assess)
##   - S9: docker-proxy на 0.0.0.0/[::] для внутреннего порта модуля (MODULE_PORTS_DENY +
##     DENY_PORT из firewall.py — единый SoT с ufw-политикой) → FAIL; строки без docker-proxy
##     пропускаются (sshd/systemd-resolve вне скоупа); nginx 80/443 и user-проекты — by-design,
##     вне реестра, не флагаются
## @rationale Разделение по бизнес-домену (AI-First): docker-постура — отдельный модуль
##            (демон + образы + docker-proxy — один инфраструктурный домен, общие таймауты
##            DOCKER_CMD_TIMEOUT и реестры firewall). S8 не тянет слои (manifest inspect —
##            дешёвый запрос): CVE-точность — CI-скан L3, digest-drift ловит апстрим-фиксы.
## @changes 2026-08-15 | план 170 W6-D1 — извлечено из security_posture.py (S5/S8/S9, 1:1 тела);
##            check_image_freshness CC18 → _inspect_image + _classify_staleness (pure)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

from core.internal.bootstrap.firewall import FORBIDDEN_PORTS
from core.internal.shared import docker_ops  # W1: docker ps/inspect/manifest примитивы (гейт docker_sole_path)
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT

from ._shared import STATUS_FAIL, STATUS_PASS, STATUS_WARN, CheckResult
from ._shared import probe as _probe

logger = logging.getLogger(__name__)

# Пути — модульные константы (тесты переопределяют через paths= DI-параметр)
DOCKER_DAEMON_JSON = "/etc/docker/daemon.json"
# S9: returncode docker manifest inspect --verbose при таймауте (timeout-семантика subprocess)
_DOCKER_INSPECT_TIMEOUT_RC: int = 124


# region FUNC_check_docker
## @purpose  S5: docker-демон — live-restore=true (переживает рестарты/ребуты), iptables НЕ отключён
##           (daemon.json "iptables": false = FAIL), Docker API порты 2375/2376 не слушают.
## @io       ⇥ probe: Callable | None (lazy default _probe), paths: Mapping | None
##              (override DOCKER_DAEMON_JSON — E3 DI) → ⎋ CheckResult
## @complexity O(2) — daemon.json read + ss probe
def check_docker(
    *,
    probe: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    paths: Mapping[str, str] | None = None,
) -> CheckResult:
    """S5: docker daemon hardening — live-restore, iptables enabled, no exposed API."""
    probe = probe or _probe
    paths_ = paths or {}
    daemon_json_str = paths_.get("DOCKER_DAEMON_JSON") or DOCKER_DAEMON_JSON
    daemon = Path(daemon_json_str)
    problems: list[str] = []
    if not daemon.is_file():
        problems.append(f"{daemon_json_str} missing — live-restore unconfirmed")
    else:
        try:
            config = cast(
                "dict[str, object]", json.loads(daemon.read_text(encoding="utf-8"))
            )  # W11-G3: json.loads → Any; JSON-граница daemon.json
        except (json.JSONDecodeError, OSError) as e:
            problems.append(f"daemon.json unparseable: {e}")
            config = {}
        if config.get("live-restore") is not True:
            problems.append("live-restore != true (containers die on daemon restart)")
        if config.get("iptables") is False:
            problems.append("iptables disabled in daemon.json")
    ss = probe(["ss", "-tlnp"], timeout=DOCKER_CMD_TIMEOUT)
    if ss.returncode == 0:
        ss_out = str(getattr(ss, "stdout", ""))
        problems.extend(
            f"Docker API port {port} LISTENING" for port in FORBIDDEN_PORTS if re.search(rf":{port}\s", ss_out)
        )
    if problems:
        return CheckResult("S5", STATUS_FAIL, "; ".join(problems))
    logger.info("[IMP:9][posture][S5] Docker daemon hardened")
    return CheckResult("S5", STATUS_PASS, "live-restore on, iptables on, no Docker API exposed")


# endregion FUNC_check_docker


# region FUNC__collect_manifest_digests
## @purpose  Извлечь набор digest'ов из `docker manifest inspect --verbose` (один манифест ИЛИ
##           multi-arch список — Descriptor.digest в обоих формах).
## @io       ⇥ data: object (json.loads результат) → ⎋ set[str] — sha256:... digest'ы
## @complexity O(n) — n = число Descriptor'ов
## @invariants  Пустой результат → registry-digest не определён (WARN, не ложный FAIL)
def _collect_manifest_digests(data: object) -> set[str]:
    """Collect digest values from manifest inspect --verbose (dict or list form)."""
    result: set[str] = set()
    # W11-G3: JSON-граница (json.loads) — касты dict[Unknown, Unknown] → dict[str, object]
    if isinstance(data, dict):
        desc = cast("dict[str, object]", data).get("Descriptor")
        if isinstance(desc, dict):
            digest = cast("dict[str, object]", desc).get("digest")
            if digest:
                result.add(str(digest))
    elif isinstance(data, list):
        for item in cast("list[object]", data):  # W11-G3: JSON-граница (list[Unknown] → list[object])
            if not isinstance(item, dict):
                continue
            desc = cast("dict[str, object]", item).get("Descriptor")
            if isinstance(desc, dict):
                digest = cast("dict[str, object]", desc).get("digest")
                if digest:
                    result.add(str(digest))
    return result


# endregion FUNC__collect_manifest_digests


# region FUNC__classify_staleness
## @purpose  PURE: классификация digest-дрейфа одного образа — локальный digest против набора
##           registry-digest'ов тега. Digest-pinned ref (tag + «at» + sha256-суффикс): tag_ref — часть до суффикса,
##           сообщение о необходимости обновить пин; иначе — пересборка L2.
## @io       ⇥ local_digest: str (RepoDigests[0]), registry_digests: set[str],
##              ref: str (Config.Image из docker inspect) → ⎋ str | None (stale-сообщение)
## @complexity O(1) — членство в set
## @invariants  digest входит в registry-набор → None (актуален); отклонение → stale-строка:
##              pin-форма («обновите пин в compose + node-update») при "@" in ref, иначе
##              tag-форма («пересобрать L2 (make hermes-build-context) и задеплоить»)
def _classify_staleness(local_digest: str, registry_digests: set[str], ref: str) -> str | None:
    """Classify digest drift for one image → stale message or None (current)."""
    if local_digest in registry_digests:
        return None
    if "@" in ref:
        return (
            f"{ref}: pin устарел — registry выдаёт другой digest (апстрим опубликовал новый образ, "
            "вероятно с security-фиксами); обновите пин в compose + node-update"
        )
    return (
        f"{ref}: в registry более свежий образ (digest отличен) — пересобрать L2 "
        "(make hermes-build-context) и задеплоить"
    )


# endregion FUNC__classify_staleness


# region PROTOCOL_DockerOps
class DockerOps(Protocol):
    """DI-контракт ops-namespace (E3): docker_manifest_inspect_raw — структурно реализуется
    docker_ops-модулем и fake-объектами тестов (W11-G3: замена Any).

    ## @complexity — O(1) — декларация протокола
    """

    def docker_manifest_inspect_raw(
        self,
        image_ref: str,
        timeout: int = ...,
        flags: list[str] | None = ...,
        runner: object | None = ...,
    ) -> subprocess.CompletedProcess[str]: ...


# endregion PROTOCOL_DockerOps


# region FUNC__inspect_image
## @purpose  Registry-проба ОДНОГО образа (DI-канал ops): manifest inspect --verbose тега →
##           набор digest'ов → _classify_staleness. Извлечение из god-цикла check_image_freshness.
## @io       ⇥ ops: Any (docker_ops-совместимый, lazy-default контракт E3), ref: str,
##              local_digest: str → ⎋ tuple[str, str] — (outcome, message);
##              outcome ∈ {"current", "stale", "skipped"}
## @complexity O(1) — один registry-запрос + parse
## @invariants  Таймаут (rc=124) → stale «registry query timed out»; rc!=0 с no-such-manifest
##              токенами → skipped (локальное имя — registry его не знает, не дрейф);
##              rc!=0 прочее → stale «registry query failed: <первая строка stderr|rc>»;
##              непарсируемый/пустой digest-набор → stale «registry digest not parseable»
def _inspect_image(ops: DockerOps, ref: str, local_digest: str) -> tuple[str, str]:
    """Probe one image against its registry tag → (outcome, message)."""
    tag_ref = ref.split("@", 1)[0]
    if not tag_ref:
        return "skipped", ""
    registry = ops.docker_manifest_inspect_raw(tag_ref, timeout=DOCKER_CMD_TIMEOUT, flags=["--verbose"])
    if registry.returncode == _DOCKER_INSPECT_TIMEOUT_RC:
        return "stale", f"{ref} (registry query timed out)"
    if registry.returncode != 0:
        stderr = str(getattr(registry, "stderr", "")).strip()
        if any(token in stderr.lower() for token in ("no such manifest", "manifest unknown", "not found")):
            return "skipped", ""  # локальное имя — registry его не знает
        return (
            "stale",
            f"{ref} (registry query failed: {stderr.splitlines()[0] if stderr else f'rc={registry.returncode}'})",
        )
    try:
        registry_digests = _collect_manifest_digests(
            cast(
                "object", json.loads(str(getattr(registry, "stdout", "")))
            )  # W11-G3: json.loads → Any; граница JSON-разбора
        )
    except json.JSONDecodeError:
        registry_digests: set[str] = set()  # W11-G3: set() → set[Unknown]; аннотация фиксирует контракт
    if not registry_digests:
        return "stale", f"{ref} (registry digest not parseable)"
    stale_msg = _classify_staleness(local_digest, registry_digests, ref)
    if stale_msg is not None:
        return "stale", stale_msg
    return "current", ""


# endregion FUNC__inspect_image


# region FUNC_check_image_freshness
## @purpose  S8: docker image freshness — для каждого запущенного контейнера сравнить локальный
##           digest (RepoDigests) с текущим digest'ом тега в registry (docker manifest inspect —
##           использует существующий docker auth на ноде: ghcr φ6, Docker Hub φ3). Отклонение =
##           «образ устарел, апстрим опубликовал новый (вероятно, security-фиксы)» (DevPlan 134 L4).
##           Data-driven: per-image логика в _inspect_image, классификация — _classify_staleness (pure).
## @io       ⇥ ops: ModuleType | None (lazy default docker_ops — docker ps/inspect/manifest DI,
##              DevPlan 160 E3) → ⎋ CheckResult
## @complexity O(C + R) — C = контейнеры (2 docker-вызова), R = registry-запросов (1/образ)
## @invariants  Digest-pinned ref (tag + sha256-суффикс): tag_ref = часть до суффикса — сравнивается digest тега
##              Локально-собранные образы (пустой RepoDigests) → skip (не трекаются)
##              Локальные имена (manifest unknown) → PASS (registry их не знает — не дрейф)
##              Registry недоступен/timeout/rate-limit → WARN (graceful, как apt-check в S2)
##              Только WARN (никогда FAIL по дрейфу) — digest-pin — осознанная политика
##              (гейт image_tag_form): дрейф = «пора обновлять», не «сломано»
## @rationale L4-детекция (DevPlan 134): content-hash skip не подхватывает фиксы базовых образов;
##            digest-drift ловит любой опубликованный апстрим-фикс дешевле trivy на ноде
##            (CVE-точность — CI-скан L3). Docker manifest inspect не тянет слои — дешёвый запрос.
def check_image_freshness(
    *, ops: ModuleType | None = None
) -> CheckResult:  # DI: lazy default docker_ops (модуль) — E3 DevPlan 160
    """S8: image freshness — local digest vs registry digest (drift = update available)."""
    # W1 (DevPlan 128): docker ps/inspect/manifest — shared/docker_ops (non-fatal)
    # E3 (DevPlan 160): ops= override (lazy default docker_ops) — тест S8 без monkeypatch
    ops = ops if ops is not None else docker_ops
    ps = ops.docker_ps(format="{{.ID}}", timeout=DOCKER_CMD_TIMEOUT)
    if ps.returncode != 0:
        return CheckResult("S8", STATUS_FAIL, f"docker ps failed (rc={ps.returncode}) — cannot assess images")
    cids = [ln.strip() for ln in str(getattr(ps, "stdout", "")).splitlines() if ln.strip()]
    if not cids:
        logger.info("[IMP:9][posture][S8] No running containers — nothing to track")
        return CheckResult("S8", STATUS_PASS, "no running containers — nothing to track")

    # v1.0.1 TRAP[BUG] (Фаза 6, tronyx-vps): формат «{{.Config.Image}}|{{if .RepoDigests}}...»
    # ошибался на локально-собранных образах — docker-шаблонизатор (missingkey=error)
    # бросает «map has no entry for key RepoDigests» на ДОСТУПЕ в {{if}} → rc=1 на ВСЁМ
    # батче → S8 «docker inspect failed» даже при здоровых образах. JSON-формат терпит
    # отсутствие ключа (.get) — тот же семантический контракт «нет digest → skipped».
    inspect = ops.docker_inspect_many(
        cids,
        format="{{json .}}",
        timeout=DOCKER_CMD_TIMEOUT,
    )
    if inspect.returncode != 0:
        return CheckResult("S8", STATUS_FAIL, f"docker inspect failed (rc={inspect.returncode})")

    stale: list[str] = []
    skipped = 0
    checked = 0
    for line in str(getattr(inspect, "stdout", "")).splitlines():
        try:
            data = cast("dict[str, object]", json.loads(line))
        except ValueError:
            skipped += 1  # не-JSON строка (пустой/битый вывод) — консервативный skip
            continue
        config = cast("dict[str, object] | None", data.get("Config"))
        ref = str(config.get("Image", "")) if config else ""
        digests = cast("list[str] | None", data.get("RepoDigests")) or []
        local_digest = digests[0] if digests else ""
        if not ref or not local_digest:
            skipped += 1  # локально-собранный образ — registry-digest базиса нет
            continue
        outcome, message = _inspect_image(
            cast("DockerOps", cast("object", ops)), ref, local_digest
        )  # W11-G3: docker_ops-модуль → Protocol (структурный матчинг модуля не срабатывает; каст через object)
        if outcome == "skipped":
            skipped += 1
        elif outcome == "stale":
            stale.append(message)
        else:
            checked += 1

    if stale:
        logger.info("[IMP:8][posture][S8] %d stale image(s) of %d checked", len(stale), checked)
        return CheckResult("S8", STATUS_WARN, "; ".join(stale))
    logger.info("[IMP:9][posture][S8] All %d images current (skipped local-built: %d)", checked, skipped)
    return CheckResult("S8", STATUS_PASS, f"all {checked} tracked images current in registry")


# endregion FUNC_check_image_freshness


# region FUNC_check_listening_ports
## @purpose  S9 (W10 T10.2, S-7): реальный LISTEN-кросс-чек — docker-proxy НЕ должен слушать 0.0.0.0
##           на ВНУТРЕННИХ портах модулей (реестр MODULE_PORTS_DENY из firewall.py — SoT
##           platform-infra.yaml). Внутренние сервисы (postgres/minio/clickhouse/redis/...) обязаны
##           биндить 127.0.0.1 или не публиковаться (compose base.yml: NO ports / 127.0.0.1 bindings —
##           верификация W10 на test-VPS, ss -tlnp). docker-proxy 0.0.0.0 на внутреннем порту =
##           утечка сервиса наружу в обход loopback-контроля.
##           user-проекты публикуют web-порты (напр. test-project-web 0.0.0.0:8080) ПО ДИЗАЙНУ —
##           они НЕ флагаются (не входят в реестр внутренних портов; верификация test-VPS).
## @io       ⇥ probe: Callable | None (lazy default _probe) → ⎋ CheckResult
##           ✅ DevPlan 162 W2-2 (2026-08-13) ПОДТВЕРЖДЕНО: S9 = FAIL при нарушении — check возвращает
##           STATUS_FAIL, aggregate_exit_code (любой FAIL → 2). Отдельного ослабления/усиления не
##           требуется; compose-gate (tests/gates/test_gate_no_external_port_binding.py) закрывает
##           декларативную сторону, S9 — runtime-кросс-чек (обход ufw через Docker FORWARD).
## @complexity O(1) — один `ss -tlnp` probe + regex
## @invariants  Слушает только `ss -tlnp` (реальный LISTEN, не compose-декларация — кросс-чек S-7)
##              [::] wildcard эквивалентен 0.0.0.0 (IPv6-дубль) — тоже FAIL для внутренних портов
##              Строки без process docker-proxy пропускаются (sshd 22, systemd-resolve 53 — вне скоупа)
##              Реестр внутренних портов — MODULE_PORTS_DENY + DENY_PORT (firewall.py, SoT) —
##              единый источник с ufw-политикой (не дублировать литералы)
def check_listening_ports(*, probe: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> CheckResult:
    """S9: no docker-proxy listening on 0.0.0.0 for module-internal ports (real LISTEN cross-check)."""
    probe = probe or _probe
    # Импорт из firewall.py: тот же пакет core/internal/bootstrap, никакого цикла (firewall не
    # импортирует security). Публичные порты nginx 80/443 в реестр НЕ входят (by-design),
    # user-проекты публикуют произвольные порты — тоже вне реестра.
    from core.internal.bootstrap.firewall import DENY_PORT as _FIREWALL_DENY_PORT
    from core.internal.bootstrap.firewall import MODULE_PORTS_DENY as _MODULE_PORTS

    internal_ports = set(_MODULE_PORTS) | {_FIREWALL_DENY_PORT}
    ss = probe(["ss", "-tlnp"], timeout=DOCKER_CMD_TIMEOUT)
    if ss.returncode != 0:
        return CheckResult("S9", STATUS_FAIL, f"ss -tlnp failed (rc={ss.returncode}) — cannot assess listeners")
    ss_out = str(getattr(ss, "stdout", ""))
    violations: list[str] = []
    for line in ss_out.splitlines():
        if "docker-proxy" not in line:
            continue
        m = re.search(r"(0\.0\.0\.0|\[::\]):(\d+)", line)
        if not m:
            continue
        port = int(m.group(2))
        if port not in internal_ports:
            # user-проекты / прочие публичные порты — вне реестра внутренних сервисов
            continue
        violations.append(f"0.0.0.0:{port}")
    if violations:
        return CheckResult(
            "S9",
            STATUS_FAIL,
            "docker-proxy listening on 0.0.0.0 for module-internal port(s): "
            + ", ".join(sorted(set(violations)))
            + f" (registry: {', '.join(str(p) for p in sorted(internal_ports))})",
        )
    logger.info("[IMP:9][posture][S9] No docker-proxy on 0.0.0.0 for module-internal ports")
    return CheckResult(
        "S9", STATUS_PASS, "no docker-proxy on 0.0.0.0 for module-internal ports (internal services loopback-bound)"
    )


# endregion FUNC_check_listening_ports
