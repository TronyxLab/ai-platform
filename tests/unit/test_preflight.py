# GREP_SUMMARY: preflight unit-test ghcr-auth docker-login stdin token-injection DI-runner no-shell special-chars R5 caplog
# STRUCTURE: ▶ ┌token + FakeRunner(DI)┐ → ○ probe_ghcr_auth → ◇ rc==0? ok | warn | ◇ args-список 0-shell + input=token → ⎋ asserts
# region MODULE_CONTRACT
## @purpose  Прямой unit-тест core/internal/bootstrap/preflight.probe_ghcr_auth (дыра research-A §5:
##           прямого unit-теста preflight.py не было — только entrypoint-контракт).
##           План 170 W2-A1: probe_ghcr_auth переписан с bash-строковой инъекции на
##           subprocess-список + токен через stdin; тест доказывает: (a) структуру вызова
##           (список аргументов БЕЗ shell), (b) негатив на спецсимволы токена ('$', '\'', '"',
##           пробел, ';') — токен передаётся ОДНИМ аргументом через input= и НЕ интерполируется.
## @scope    Только probe_ghcr_auth (+ CheckResult classification). Без Docker, без сети, без
##           monkeypatch.setattr на production-модуль — DI через параметр runner= (DI-HYG, гвардрейл 5).
## @invariants
##   - 0 monkeypatch.setattr: раннер инжектируется параметром runner= (сигнатура probe_ghcr_auth,
##     аналогично s3_client в probe_s3_connectivity) — тест проверяет РЕАЛЬНЫЙ вызов-структуру
##   - Токен НЕ должен появляться в args списке раннера (только в kwargs["input"])
##   - "bash"/"shell" отсутствуют в структуре вызова — инъекция невозможна
##   - rc=0 → CheckResult(status="ok"); rc!=0 → status="warn" (graceful degradation); пустой
##     токен → warn БЕЗ вызова раннера; TimeoutExpired → warn (except-ветка)
##   - R5: негатив спецсимволов доказывает отсутствие регрессии к bash-строке (старый код
##     ломался бы на '`' / '"' / ';' — интерполяция bash)
## @rationale Research-A §5: preflight.py (636 LOC) имел только entrypoint-контракт —
##            probe-функции не были покрыты unit-тестами. W2-A1 (C3) закрыл уязвимость
##            bash-инъекции токена; тест фиксирует новый контракт вызова, чтобы регрессия
##            к `bash -c "echo '<token>' | docker login ..."` была невозможна.
## @changes  2026-08-14 | план 170 W2-A1 — Created (дыра research-A §5; C3-фикс probe_ghcr_auth)
# endregion MODULE_CONTRACT

import logging
import subprocess

from core.internal.bootstrap.preflight import probe_ghcr_auth
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# Каноническая структура вызова docker login (0 shell, токен через stdin)
EXPECTED_DOCKER_LOGIN_ARGS = [
    "docker",
    "login",
    "--username",
    "x-access-token",
    "--password-stdin",
    "ghcr.io",
]

# Токен со спецсимволами: '$', '\'', '"', пробел, ';', '`' — все интерпретируемые bash-ом.
SPECIAL_CHARS_TOKEN = "tok$en'with\"sp ace;and`tick"


# region FAKE_RUNNER


class FakeDockerLoginRunner:
    """Фейк subprocess-раннера (DI): записывает структуру вызова, возвращает заданный rc.

    ## @purpose — Замена subprocess.run через параметр runner= (DI-HYG: 0 monkeypatch.setattr
    ##            на production-модуль). Проверяет, ЧТО реально вызвала probe_ghcr_auth.
    ## @io — ⇥ returncode: int, stderr: str → ⎋ CompletedProcess с этими значениями
    ## @complexity — O(1)
    ## @invariants
    ##   - Каждый вызов сохраняется в self.calls (args-кортеж + kwargs-дикт)
    ##   - Имитирует subprocess.run: принимает любые args/**kwargs, возвращает CompletedProcess
    ##   - Может кинуть переданное исключение (raise_exc) — покрытие except-веток probe
    """

    def __init__(self, returncode: int = 0, stderr: str = "", raise_exc: BaseException | None = None):
        self.returncode = returncode
        self.stderr = stderr
        self.raise_exc = raise_exc
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args: object, **kwargs: object):
        self.calls.append((args, kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        cmd = list(args[0]) if args and isinstance(args[0], list) else []
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr=self.stderr)


# endregion FAKE_RUNNER


# region TESTS


# 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · успешный docker login → CheckResult ok
# · Scenario: DI-раннер возвращает rc=0; probe_ghcr_auth(token, runner=fake) обязан
# ·   вернуть status="ok" и деталь "ghcr.io authentication successful"
# · Last fail: N/A (новый unit — дыра research-A §5)
# · Remove if: probe_ghcr_auth перестанет использовать docker login (канал смены)
@ldd_trajectory
def test_probe_ghcr_auth_login_ok(caplog) -> None:
    """rc=0 → CheckResult ok + IMP:9 лог успеха."""
    fake = FakeDockerLoginRunner(returncode=0)
    result = probe_ghcr_auth("valid-token", runner=fake)

    assert result.status == "ok", f"[IMP:10][test] expected ok, got {result.status}: {result.detail}"
    assert result.detail == "ghcr.io authentication successful"
    assert fake.calls, "[IMP:10][test] runner не вызван"
    assert fake.calls[0][0][0] == EXPECTED_DOCKER_LOGIN_ARGS, (
        "[IMP:10][test] структура вызова docker login не канонична"
    )
    assert fake.calls[0][1].get("input") == "valid-token"
    assert "shell" not in fake.calls[0][1], "[IMP:10][test] shell= не должен передаваться"
    logger.critical("[IMP:9][test] probe_ghcr_auth ok (rc=0, args=6, stdin-токен) — структура вызова канонична")


# 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · rc!=0 → CheckResult warn (graceful degradation)
# · Scenario: DI-раннер возвращает rc=1 + stderr; probe обязан НЕ падать, а вернуть warn
# ·   с error=stderr (context_deployer перейдёт на build fallback)
# · Last fail: N/A (новый unit)
# · Remove if: политика graceful degradation ghcr-пробы изменится (WARN → FATAL)
@ldd_trajectory
def test_probe_ghcr_auth_login_failed_warns(caplog) -> None:
    """rc!=0 → CheckResult warn + error содержит stderr docker login."""
    fake = FakeDockerLoginRunner(returncode=1, stderr="error response from daemon: unauthorized")
    result = probe_ghcr_auth("bad-token", runner=fake)

    assert result.status == "warn", f"[IMP:10][test] expected warn, got {result.status}"
    assert "build fallback" in result.detail
    assert result.error is not None and "unauthorized" in result.error
    logger.critical("[IMP:9][test] probe_ghcr_auth warn (rc=1) — graceful degradation подтверждён")


# 🧪 TRAP[TEST] · 2026-08-14 · NEGATIVE (R5) · спецсимволы токена НЕ интерполируются в shell
# · Scenario: токен 'tok$en'with"sp ace;and`tick' (bash-метасимволы: $, ', ", пробел, ;, `).
# ·   Старый код (`bash -c "echo '<token>' | docker login ..."`) ломался бы / выполнял команды.
# ·   Новый код: args — СПИСОК литералов (0 bash, 0 shell=), токен — ровно ОДИН элемент
# ·   в kwargs["input"], ни один элемент списка не содержит спецсимволов токена.
# · Last fail: N/A (новый негатив; доказывает закрытие C3-уязвимости W2-A1)
# · Remove if: probe_ghcr_auth сменит канал передачи токена (docker_ops/credential-helper)
def test_probe_ghcr_auth_special_chars_no_shell(caplog) -> None:
    """Негатив: токен со спецсимволами передаётся как input=, НЕ в аргументах, 0 shell."""
    caplog.set_level(logging.DEBUG)
    fake = FakeDockerLoginRunner(returncode=0)
    result = probe_ghcr_auth(SPECIAL_CHARS_TOKEN, runner=fake)

    assert fake.calls, "[IMP:10][test] runner не вызван"
    args, kwargs = fake.calls[0]

    # (1) Список аргументов — канонические литералы, БЕЗ bash/shell и БЕЗ токена
    cmd: list[str] = list(args[0])
    assert cmd == EXPECTED_DOCKER_LOGIN_ARGS, f"[IMP:10][test] args содержат неожиданное: {cmd}"
    assert "bash" not in cmd and "sh" not in cmd, "[IMP:10][test] bash-обёртка недопустима"
    for literal in cmd:
        assert SPECIAL_CHARS_TOKEN not in literal, (
            f"[IMP:10][test] токен интерполирован в аргумент {literal!r} — shell-инъекция"
        )
        assert any(c in literal for c in "$\"' ;`") is False, (
            f"[IMP:10][test] аргумент {literal!r} содержит спецсимволы — токен вшит в литерал"
        )

    # (2) Токен — ОДИН аргумент через stdin (input=), без shell-интерпретации
    assert kwargs.get("input") == SPECIAL_CHARS_TOKEN, (
        "[IMP:10][test] токен должен передаваться целиком через input= (--password-stdin)"
    )
    assert "shell" not in kwargs, "[IMP:10][test] shell= не должен передаваться"

    # (3) Результат — ok: спецсимволы не сломали probe (старый код упал бы на синтаксисе bash)
    assert result.status == "ok", f"[IMP:10][test] expected ok, got {result.status}: {result.error}"

    # LDD траектория + бизнес-лог теста
    for record in list(caplog.records):
        if "[IMP:" in record.message and int(record.message.split("[IMP:")[1].split("]")[0]) >= 7:
            logger.info("%s", record.message)
    logger.critical("[IMP:9][test] негатив спецсимволов: 6 литералов + input= (0 shell) — C3-инъекция закрыта")


# 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · пустой токен → warn БЕЗ вызова раннера
# · Scenario: token="" (GHCR_PULL_TOKEN не установлен) → раннер НЕ вызывается вовсе,
# ·   результат warn с detail "GHCR_PULL_TOKEN not set — ... build fallback"
# · Last fail: N/A (новый unit)
# · Remove if: поведение при отсутствии токена изменится (GHCR станет обязательным)
@ldd_trajectory
def test_probe_ghcr_auth_empty_token_no_call(caplog) -> None:
    """Пустой токен → warn, subprocess НЕ запускается (0 вызовов раннера)."""
    fake = FakeDockerLoginRunner(returncode=0)
    result = probe_ghcr_auth("", runner=fake)

    assert result.status == "warn", f"[IMP:10][test] expected warn, got {result.status}"
    assert "GHCR_PULL_TOKEN not set" in result.detail
    assert fake.calls == [], "[IMP:10][test] раннер вызван при пустом токене — лишний docker login"
    logger.critical("[IMP:9][test] пустой токен → warn без docker login (0 вызовов) — ранний return работает")


# 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · TimeoutExpired → warn (except-ветка)
# · Scenario: DI-раннер кидает subprocess.TimeoutExpired (docker login завис >10s) →
# ·   probe обязан вернуть warn, а не упасть
# · Last fail: N/A (новый unit — покрытие except-ветки probe_ghcr_auth)
# · Remove if: обработка TimeoutExpired изменится
@ldd_trajectory
def test_probe_ghcr_auth_timeout_warns(caplog) -> None:
    """TimeoutExpired из раннера → CheckResult warn (не исключение)."""
    fake = FakeDockerLoginRunner(raise_exc=subprocess.TimeoutExpired(cmd=["docker", "login"], timeout=10))
    result = probe_ghcr_auth("slow-token", runner=fake)

    assert result.status == "warn", f"[IMP:10][test] expected warn, got {result.status}"
    assert "build fallback" in result.detail
    assert result.error is not None and "timed out" in result.error, f"[IMP:10][test] error={result.error!r}"
    logger.critical("[IMP:9][test] TimeoutExpired → warn — probe не роняет bootstrap")


# endregion TESTS
