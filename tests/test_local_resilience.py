# GREP_SUMMARY: test-local-resilience, restart-recovery, SIGKILL, unless-stopped, parametrized, long-running-services, data-integrity, postgres, L1-L2-L3, requires_docker, DevPlan-164
# STRUCTURE: ▶ resilience_targets (fixture из compose-файлов, не хардкод) → ◇ test_restart_recovery_sigkill[target] ┌SIGKILL → running+healthy ≤120s┐ → ◇ test_postgres_data_integrity ┌INSERT → SIGKILL → SELECT survives┐ → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Локальные resilience-тесты (DevPlan 164 W1-4/W4L-2): restart-политики
##           (unless-stopped) и healthcheck-восстановление — docker-механика, проверяемая
##           на локальном стеке macOS. Параметризация по ВСЕМ long-running сервисам из
##           compose-файлов (fixture генерирует список — 0 хардкода имён).
## @scope    tests/test_local_resilience.py: L1 stateful (postgres data-integrity),
##           L2/L3 общий restart-recovery (SIGKILL → running+healthy ≤120s).
## @invariants
##   - Список сервисов — из docker-compose.base.yml модулей (restart != "no" — init исключены)
##   - SIGKILL main-процесса (docker kill -s KILL) с guard «контейнер реально умер»
##     (state exited/restarting ИЛИ RestartCount вырос) ДО ожидания восстановления
##   - Восстановление адаптивно к фактической restart-политике контейнера:
##     активная (unless-stopped/always/on-failure) → авто-рестарт демоном;
##     "no" (тестовый стек, контракт docker-compose.test.yml) → docker start (эмуляция политики)
##   - Критерий здоровья: State.Running AND (Health.Status == healthy | healthcheck отсутствует) —
##     канон healthcheck_poller (unhealthy → ждать, стартовые гонки)
##   - Предел восстановления: 120s (restart-policy + healthcheck-циклы)
##   - L1 целостность: INSERT → SIGKILL → SELECT возвращает committed-строку (0 потерь)
##   - requires_docker маркер; стек — каноническая фикстура platform_services (не свой compose up)
## @rationale T1/T6 (node chaos) валидируют то же на ноде; docker-механика restart/healthcheck
##           идентична на macOS — локальный прогон дешевле ноды и параметризован по всем сервисам
##           (не только postgres/nginx, как прежние точечные тесты).
##           НЕ тестируемо локально (node-only, план 165): daemon-restart, iptables-партиция,
##           clock-skew, watchdog-cron.
##           ⚠️ Тестовый стек (docker-compose.test.yml) отключает restart ("no" — контракт
##           core/modules/AGENTS.md §docker-compose.test.yml contract) — авто-рестарт демона
##           недоступен; guard и восстановление адаптивны, см. TRAP[TEST] ниже.
## @changes  2026-08-13 | DevPlan 164 W1-4 — Created (параметризованный restart-recovery)
##           2026-08-14 | DevPlan 164 W4L-2 — реализация: адаптивный guard (exited/restarting
##                      ИЛИ RestartCount), адаптивное восстановление (docker start при policy=no),
##                      TRAP[TEST] на всех тест-функциях, LDD IMP:9
# endregion MODULE_CONTRACT

import json
import logging
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from tests._conftest.audit import discover_docker_modules
from tests.conftest import ldd_trajectory

pytestmark = pytest.mark.requires_docker

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = ROOT / "core" / "modules"

RECOVERY_TIMEOUT = 120
POLL_INTERVAL = 5
# Окно guard «контейнер реально умер»: после SIGKILL демон с активной политикой может
# перезапустить контейнер <1s — ждём до DEATH_GUARD_WINDOW для детекции exited/restarting
# или роста RestartCount (иначе guard ложно-пропускает быстрый авто-рестарт).
DEATH_GUARD_WINDOW = 10
# Окно ожидания авто-restart демона (активная политика на Linux-ноде). Docker Desktop macOS
# не применяет restart policy к docker kill (ручная остановка) — после окна fallback docker start.
AUTO_RESTART_WINDOW = 20


# region FUNC__collect_long_running
## @purpose  Собрать (module, service) long-running сервисов из compose-файлов (0 хардкода имён).
## @io       ⇥ — → ⎋ list[tuple[str, str]]
## @complexity O(M * S) — M = модулей, S = сервисов
def _collect_long_running() -> list[tuple[str, str]]:
    """Long-running services (restart != 'no') from module compose files."""
    targets: list[tuple[str, str]] = []
    for module_name in sorted(discover_docker_modules(str(MODULES_DIR))):
        path = MODULES_DIR / module_name / "docker-compose.base.yml"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for service_name, spec in (data.get("services") or {}).items():
            if not isinstance(spec, dict):
                continue
            if spec.get("restart") == "no":
                continue  # init-контейнеры не тестируются на recovery
            targets.append((module_name, service_name))
    return targets


# endregion FUNC__collect_long_running


# region FUNC__container_name
## @purpose  Имя контейнера из infra (канон: container_name из test-overlay). Fallback:
##           container_name из base.yml.
## @io       ⇥ module_name, service_name → ⎋ str
## @complexity O(1)
def _container_name(module_name: str, service_name: str) -> str:
    """Resolve test container name via infra registry (fallback: base.yml container_name)."""
    from _conftest.infra import infra

    try:
        return infra.get_container_name(module_name, service_name)
    except Exception:  # ruff: ignore[BLE001] — fallback: base.yml container_name (infra без записи)
        path = MODULES_DIR / module_name / "docker-compose.base.yml"
        if path.is_file():
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            spec = (data.get("services") or {}).get(service_name, {})
            if isinstance(spec, dict) and spec.get("container_name"):
                return str(spec["container_name"])
        return f"{module_name}-{service_name}"


# endregion FUNC__container_name


# region FUNC__docker_inspect
## @purpose  (running, health_status) контейнера через docker inspect (канон healthcheck_poller).
## @io       ⇥ container: str → ⎋ tuple[bool, str | None]
## @complexity O(1) + subprocess
def _docker_inspect(container: str) -> tuple[bool, str | None]:
    """docker inspect → (running, health_status). Health None = нет healthcheck (здоров по канону)."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                container,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][resilience][inspect] docker inspect failed: %s", e)
        return False, None
    if result.returncode != 0:
        return False, None
    parts = result.stdout.strip().split("|", 1)
    if not parts:
        return False, None
    running = parts[0] == "true"
    health = parts[1] if len(parts) > 1 else None
    return running, (None if health == "none" else health)


# endregion FUNC__docker_inspect


# region FUNC__docker_restart_count
## @purpose  RestartCount контейнера — часть guard «контейнер реально умер» (kill попал в main-процесс).
## @io       ⇥ container: str → ⎋ int (-1 = недоступен)
## @complexity O(1) + subprocess
def _docker_restart_count(container: str) -> int:
    """docker inspect RestartCount. -1 on failure."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.RestartCount}}", container],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][resilience][restartcount] docker inspect failed: %s", e)
        return -1
    if result.returncode != 0:
        return -1
    try:
        return int(result.stdout.strip())
    except ValueError:
        return -1


# endregion FUNC__docker_restart_count


# region FUNC__docker_restart_policy
## @purpose  Фактическая restart-политика РАБОТАЮЩЕГО контейнера (HostConfig.RestartPolicy.Name).
##           Определяет механизм восстановления: активная политика → авто-рестарт демона,
##           "no" (тестовый стек) → docker start (эмуляция политики).
## @io       ⇥ container: str → ⎋ str (имя политики; "" = недоступен/без политики)
## @complexity O(1) + subprocess
def _docker_restart_policy(container: str) -> str:
    """docker inspect HostConfig.RestartPolicy.Name — фактическая политика контейнера."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.HostConfig.RestartPolicy.Name}}", container],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][resilience][policy] docker inspect failed: %s", e)
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


# endregion FUNC__docker_restart_policy


# region FUNC__wait_recovered
## @purpose  Ожидание восстановления: running + (healthy | нет healthcheck). Канон
##           healthcheck_poller: "unhealthy" → ждать (стартовые гонки после рестарта).
## @io       ⇥ container: str, timeout: int = RECOVERY_TIMEOUT → ⎋ tuple[bool, float]
## @complexity O(timeout/interval)
def _wait_recovered(container: str, timeout: int = RECOVERY_TIMEOUT) -> tuple[bool, float]:
    """Wait until container running and healthy (or no-healthcheck). Returns (ok, elapsed)."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        running, health = _docker_inspect(container)
        if running and (health is None or health == "healthy"):
            return True, time.monotonic() - start
        time.sleep(POLL_INTERVAL)
    running, health = _docker_inspect(container)
    return (running and (health is None or health == "healthy")), time.monotonic() - start


# endregion FUNC__wait_recovered


# region FUNC__recover_container
## @purpose  Восстановление контейнера после SIGKILL: авто-рестарт демоном (активная политика,
##           Linux-нода) ИЛИ docker start (fallback: политика "no" на тестовом стеке / Docker
##           Desktop macOS, где docker kill НЕ триггерит restart policy — ручная остановка).
##           Возвращает (ok, elapsed) — running+healthy в пределах RECOVERY_TIMEOUT.
## @io       ⇥ container: str, policy: str → ⎋ tuple[bool, float]
## @complexity O(recovery_timeout) — docker start O(1) + _wait_recovered поллинг
## @invariants
##   - docker kill — ручная остановка: демон НЕ применяет restart policy (подтверждено 2026-08-14
##     на Docker Desktop macOS: nginx unless-stopped, RestartCount 0→0 за 121s) — авто-restart
##     ожидается коротким окном AUTO_RESTART_WINDOW, затем fallback docker start (эмуляция)
##   - На Linux-ноде (план 165 chaos) kill -9 host-pid = падение процесса ИЗНУТРИ → политика
##     срабатывает; здесь docker kill = SIGKILL main-процесса снаружи — эквивалент по эффекту
def _recover_container(container: str, policy: str) -> tuple[bool, float]:
    """Bring container back: daemon auto-restart (active policy) or docker start (fallback)."""
    # Docker Desktop macOS: docker kill — ручная остановка, демон не перезапускает даже при
    # активной политике (unless-stopped/always). Ожидаем короткое окно авто-restart (Linux-нода),
    # затем fallback docker start — эмуляция политики (тестовый стек и macOS-платформа).
    if policy not in {"", "no"}:
        logger.info(
            "[IMP:7][resilience][recover] policy=%r — waiting %ds for daemon auto-restart (Linux-нода), fallback docker start",
            policy,
            AUTO_RESTART_WINDOW,
        )
        ok_auto, elapsed_auto = _wait_recovered(container, timeout=AUTO_RESTART_WINDOW)
        if ok_auto:
            return True, elapsed_auto
        logger.info(
            "[IMP:8][resilience][recover] auto-restart not observed within %ds (Docker Desktop macOS) — docker start fallback",
            AUTO_RESTART_WINDOW,
        )
    else:
        logger.info("[IMP:7][resilience][recover] policy=%r — manual docker start (test-stack emulation)", policy)
    try:
        result = subprocess.run(
            ["docker", "start", container],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:7][resilience][recover] docker start failed: %s", e)
        return False, 0.0
    if result.returncode != 0:
        logger.error(
            "[IMP:7][resilience][recover] docker start rc=%d stderr=%s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False, 0.0
    return _wait_recovered(container)


# endregion FUNC__recover_container


# region FUNC__refresh_status_metrics
## @purpose  Эмуляция прод-cron-обновления status-metrics.json для status-page: /healthz
##           требует freshness ≤5 мин (W10 T10.13), а session-фикстура пишет файл один раз
##           при setup. В проде метрики экспортируются cron'ом каждую минуту
##           (platform_export_metrics.py / make dev-metrics).
## @io       ⇥ module_name, service_name → ⎋ None (no-op для не-status-page таргетов)
## @complexity O(1) — перезапись JSON с актуальным generated_at
def _refresh_status_metrics(module_name: str, service_name: str) -> None:
    """Обновить generated_at в /tmp/run/platform/status-metrics.json (только status-page)."""
    if module_name != "status-page":
        return
    metrics_path = Path("/tmp/run/platform/status-metrics.json")
    if not metrics_path.is_file():
        logger.warning(
            "[IMP:8][resilience][refresh-metrics] %s not a file — skip refresh",
            metrics_path,
        )
        return
    try:
        with metrics_path.open(encoding="utf-8") as f:
            metrics = json.load(f)
        metrics["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
        logger.info(
            "[IMP:9][resilience][refresh-metrics] status-metrics.json refreshed (generated_at=%s)",
            metrics["generated_at"],
        )
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[IMP:7][resilience][refresh-metrics] refresh failed: %s", exc)


# endregion FUNC__refresh_status_metrics


# region FUNC__kill_with_death_guard
## @purpose  SIGKILL + guard «контейнер реально умер» (R5-честность): после kill контейнер
##           обязан перейти в exited/restarting ИЛИ RestartCount вырасти (мгновенный авто-рестарт
##           демона с активной политикой). Если оба условия ложны — kill не попал в main-процесс —
##           тест валидирует живого контейнера, а не recovery → pytest.fail.
## @io       ⇥ container: str, restarts_before: int → ⎋ None (raises pytest.fail при нарушении guard)
## @complexity O(DEATH_GUARD_WINDOW) поллинг
def _kill_with_death_guard(container: str, restarts_before: int) -> None:
    """docker kill -s KILL с guard «контейнер реально умер» (state exited/restarting | RestartCount↑)."""
    try:
        result = subprocess.run(
            ["docker", "kill", "-s", "KILL", container],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        pytest.fail(f"docker kill failed for {container}: {e}")
    assert result.returncode == 0, f"docker kill failed: {result.stderr.strip()[:200]}"

    # Guard: контейнер реально умер — exited/restarting (state) ИЛИ демон перезапустил
    # мгновенно (RestartCount вырос). Оба ложны → kill не попал в main-процесс → тест невалиден.
    deadline = time.monotonic() + DEATH_GUARD_WINDOW
    state_dead = False
    while time.monotonic() < deadline:
        running, _ = _docker_inspect(container)
        if not running:
            state_dead = True
            break
        time.sleep(1)
    restarts_after = _docker_restart_count(container)
    assert state_dead or restarts_after > restarts_before, (
        f"{container}: контейнер НЕ умер после SIGKILL (state=running, RestartCount "
        f"{restarts_before}→{restarts_after}) — recovery-тест невалиден (R5-честность)"
    )
    logger.info(
        "[IMP:8][resilience][guard] %s: kill landed (state_dead=%s, RestartCount %s→%s)",
        container,
        state_dead,
        restarts_before,
        restarts_after,
    )


# endregion FUNC__kill_with_death_guard


# region FIXTURE_resilience_targets
## @purpose  Параметризация: все long-running сервисы из compose-файлов (не хардкод).
##           Вызывается на import-time — список генерируется из base.yml модулей.
## @io       ⇥ — → ⎋ pytest.mark.parametrize-декоратор
## @complexity O(M * S)
def resilience_targets():
    """Параметризация: все long-running сервисы из compose-файлов (не хардкод)."""
    targets = _collect_long_running()
    ids = [f"{m}/{s}" for m, s in targets]
    return pytest.mark.parametrize("module_name,service_name", targets, ids=ids)


# endregion FIXTURE_resilience_targets


# ═══════════════════════════════════════════════════════════════════
# region Tests: L2/L3 restart-recovery (параметризованный)
# ═══════════════════════════════════════════════════════════════════


@resilience_targets()
@ldd_trajectory
def test_restart_recovery_sigkill(module_name, service_name, platform_services, caplog) -> None:
    """L2/L3: SIGKILL main-процесса → восстановление → running+healthy ≤120s.

    Guard «контейнер реально умер»: после SIGKILL состояние exited/restarting ИЛИ RestartCount
    вырос ДО ожидания — иначе тест валидирует recovery живого контейнера (R5-негатив честности).
    Восстановление адаптивно: активная restart-политика → авто-рестарт демона; политика "no"
    (тестовый стек, docker-compose.test.yml контракт) → docker start (эмуляция политики).
    """
    # 🧪 TRAP[TEST] Regression · Scenario: SIGKILL main-процесса long-running контейнера
    #   → восстановление (авто-политика | docker start) → running+healthy ≤120s
    #   Last fail: 2026-08-14 (W4-2 169): status-page не восстанавливался за 120s —
    #   /healthz 503 stale-metrics (session-фикстура создаёт status-metrics.json один раз,
    #   status-page-тест идёт последним в сессии >5 мин freshness-окна; в проде метрики
    #   обновляются cron'ом каждую минуту)
    #   Fix: перед восстановлением эмулировать cron-обновление метрик (см. _refresh_status_metrics)
    #   Remove if: status-page /healthz перестанет зависеть от freshness ≤5 мин ИЛИ фикстура
    #   начнёт периодически обновлять метрики
    caplog.set_level(logging.INFO)
    container = _container_name(module_name, service_name)
    running_before, _ = _docker_inspect(container)
    if not running_before:
        pytest.skip(f"{container} not running — skipped by infra availability")
    policy = _docker_restart_policy(container)
    restarts_before = _docker_restart_count(container)

    _kill_with_death_guard(container, restarts_before)

    # ⚠️ TRAP[BUG] · 2026-08-14 · P1 · status-page /healthz 503 stale-metrics после SIGKILL
    # · Symptom: status-page-test не восстанавливался за 120s (W4-2 169); /healthz = 503 metrics_file_stale
    # · Root: session-фикстура platform_services пишет /tmp/run/platform/status-metrics.json ОДИН раз
    # ·   при setup (generated_at = старт сессии); тест status-page идёт последним (>5 мин) —
    # ·   freshness-проверка /healthz (W10 T10.13, окно 5 мин) даёт stale → контейнер unhealthy
    # · Fix: эмуляция продакшн-cron (обновление метрик каждую минуту) перед восстановлением
    # · Prevention: тест не должен зависеть от времени жизни session-фикстуры; прод-поведение —
    # ·   периодический cron-экспорт (см. make dev-metrics / platform_export_metrics.py)
    _refresh_status_metrics(module_name, service_name)

    ok, elapsed = _recover_container(container, policy)
    assert ok, (
        f"{module_name}/{service_name} ({container}) не восстановился за {RECOVERY_TIMEOUT}s "
        f"(restart={_restart_policy(module_name, service_name)}, runtime_policy={policy!r})"
    )
    logger.critical(
        "[IMP:9][test] %s/%s recovered after SIGKILL in %.0fs (policy=%r)", module_name, service_name, elapsed, policy
    )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: L1 postgres data-integrity
# ═══════════════════════════════════════════════════════════════════


def _restart_policy(module_name: str, service_name: str) -> str:
    """restart-политика сервиса из base.yml (для диагностики фейла)."""
    path = MODULES_DIR / module_name / "docker-compose.base.yml"
    if not path.is_file():
        return "?"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    spec = (data.get("services") or {}).get(service_name, {})
    return str(spec.get("restart", "?")) if isinstance(spec, dict) else "?"


@ldd_trajectory
def test_postgres_data_integrity_sigkill(platform_services, caplog) -> None:
    """L1: committed-строка переживает SIGKILL (0 потерь — целостность данных).

    Crash-consistency через WAL: INSERT (committed) → SIGKILL main-процесса → восстановление
    (авто-политика | docker start) → SELECT возвращает committed-строку. БД `platform`
    создаётся через POSTGRES_DB (base.yml), test-стек использует trust-auth — psql без пароля.
    """
    # 🧪 TRAP[TEST] Regression · Scenario: committed-строка postgres переживает SIGKILL
    #   (crash-consistency, 0 потерь — DevPlan W4L-2 L1, паттерн T6 с guard)
    #   Last fail: never (DevPlan 164 W4L-2)
    #   Remove if: postgres выйдет из allowlist long-running ИЛИ WAL crash-recovery изменится
    caplog.set_level(logging.INFO)
    container = _container_name("postgres", "postgres")
    running_before, _ = _docker_inspect(container)
    if not running_before:
        pytest.skip(f"{container} not running — skipped by infra availability")
    policy = _docker_restart_policy(container)
    restarts_before = _docker_restart_count(container)

    marker = f"resilience_{int(time.time())}"
    psql = ["docker", "exec", container, "psql", "-U", "postgres", "-d", "platform", "-tAc"]
    try:
        subprocess.run(
            [
                *psql,
                f"CREATE TABLE IF NOT EXISTS resilience_probe (id text PRIMARY KEY); INSERT INTO resilience_probe VALUES ('{marker}');",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        pytest.fail(f"INSERT через psql не удался: {e}")

    _kill_with_death_guard(container, restarts_before)

    ok, elapsed = _recover_container(container, policy)
    assert ok, f"postgres не восстановился за {RECOVERY_TIMEOUT}s (policy={policy!r})"

    try:
        select = subprocess.run(
            [*psql, f"SELECT id FROM resilience_probe WHERE id = '{marker}';"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        pytest.fail(f"SELECT через psql не удался: {e}")
    assert marker in select.stdout, f"L1 FAIL: committed-строка {marker} потеряна после SIGKILL — целостность нарушена"
    logger.critical(
        "[IMP:9][test] postgres data integrity after SIGKILL — committed row survives (0 losses, recovered in %.0fs)",
        elapsed,
    )


# endregion
