"""Unit-тесты security-префикса T2.0a DevPlan 010: redis auth пропагирован потребителям."""
# GREP_SUMMARY: test_security_prefix redis requirepass REDIS_PASSWORD secret-definitions hermes healthcheck REDISCLI_AUTH
# STRUCTURE: ▶ read configs → ◇ secret-def autogen → ⊕ compose env+requirepass+authed-healthcheck → ∑ hermes passthrough → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 010 T2.0a (security-префикс Волны 2): redis требует пароль ДО публикации
##           порта пирам — REDIS_PASSWORD autogen в SoT секретов, requirepass в compose,
##           аутентифицированный healthcheck, passthrough потребителю (hermes-agent)
## @scope    core/secret-definitions.yaml; core/modules/redis/docker-compose.base.yml;
##           core/modules/hermes-agent/docker-compose.base.yml
## @invariants
##   - REDIS_PASSWORD зарегистрирован как autogen-секрет (tier generated)
##   - redis compose: environment ${REDIS_PASSWORD:?required} + --requirepass из shell-env
##   - healthcheck аутентифицирован через REDISCLI_AUTH (без ps-экспозиции)
##   - hermes-agent получает REDIS_PASSWORD passthrough для /ready dependency-check
## @rationale Test honesty R1: содержательные assert'ы на фактические файлы; LDD-траектория
##           печатается перед assert'ами.
# endregion MODULE_CONTRACT

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRET_DEFS = REPO / "core" / "secret-definitions.yaml"
REDIS_COMPOSE = REPO / "core" / "modules" / "redis" / "docker-compose.base.yml"
HERMES_COMPOSE = REPO / "core" / "modules" / "hermes-agent" / "docker-compose.base.yml"


# region FUNC_test_redis_password_autogen_secret
def test_redis_password_autogen_secret() -> None:
    """REDIS_PASSWORD зарегистрирован в secret-definitions как autogen с ci_default."""
    text = SECRET_DEFS.read_text(encoding="utf-8")
    print("--- LDD TRAJECTORY ---")
    for line in text.splitlines():
        if "REDIS_PASSWORD" in line:
            print(line)
    print("--- END LDD TRAJECTORY ---")
    # [IMP:9] бизнес-инвариант: секрет в SoT (grep-гейт «секрет в голове = RED» закрыт)
    assert "- name: REDIS_PASSWORD" in text
    assert "ci-test-redis-password" in text


# endregion FUNC_test_redis_password_autogen_secret


# region FUNC_test_redis_compose_requires_and_sets_password
def test_redis_compose_requires_and_sets_password() -> None:
    """redis compose: fail-fast env + requirepass из shell-env + аутентифицированный healthcheck."""
    text = REDIS_COMPOSE.read_text(encoding="utf-8")
    print("--- LDD TRAJECTORY ---")
    for i, line in enumerate(text.splitlines(), 1):
        if any(t in line for t in ("REDIS_PASSWORD", "--requirepass", "REDISCLI_AUTH")):
            print(f"{i}: {line}")
    print("--- END LDD TRAJECTORY ---")
    # [IMP:9] fail-fast: пароль обязателен (канон ${VAR:?}, DD3 reversed)
    assert "${REDIS_PASSWORD:?REDIS_PASSWORD_REQUIRED}" in text
    # [IMP:9] пароль раскрывается ВНУТРИ контейнера (не в argv compose)
    assert '--requirepass "$REDIS_PASSWORD"' in text
    # [IMP:8] healthcheck аутентифицирован без ps-экспозиции ($$ — экранирование compose)
    assert "REDISCLI_AUTH=$$REDIS_PASSWORD" in text


# endregion FUNC_test_redis_compose_requires_and_sets_password


# region FUNC_test_hermes_receives_redis_password
def test_hermes_receives_redis_password() -> None:
    """hermes-agent compose содержит passthrough REDIS_PASSWORD для /ready dependency-check."""
    text = HERMES_COMPOSE.read_text(encoding="utf-8")
    print("--- LDD TRAJECTORY ---")
    for line in text.splitlines():
        if "REDIS_PASSWORD" in line:
            print(line)
    print("--- END LDD TRAJECTORY ---")
    # [IMP:9] потребитель пропагирован (T2.0a acceptance W2-s)
    assert 'REDIS_PASSWORD: "${REDIS_PASSWORD:-}"' in text


# endregion FUNC_test_hermes_receives_redis_password
