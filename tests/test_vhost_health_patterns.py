#!/usr/bin/env python3
# GREP_SUMMARY: test-vhost-renderer vhost /health proxy-pass set-upstream D19 D20 nginx-500 regression DevPlan-136
# STRUCTURE: ▶ generate_vhost_body → ◇ test_no_proxy_pass_var_uri (D19) → ◇ test_set_upstream_in_health_location (D20) →
#            ◇ test_old_pattern_detector (R5 negative) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  D19/D20 regression tests (DevPlan 136 W1 T1.9) for the nginx vhost template
##           (core/internal/scaffold/vhost_renderer.py::generate_vhost_body):
##           - D19 (643df6d): `proxy_pass $var/URI` → nginx 500 «invalid URL prefix» —
##             в /health location proxy_pass обязан быть БЕЗ URI-суффикса (только переменная)
##           - D20 (c87d24c): `set $upstream_*` обязан быть ОПРЕДЕЛЁН ВНУТРИ location /health
##             (location-scope — set из / не виден → пустая переменная → 500)
## @scope    Pure unit — generate_vhost_body вызывается напрямую, без docker/nginx/файлов.
##           R5 negative: детектор старого паттерна обязан ловить синтетический старый body.
## @invariants
##   - В /health: proxy_pass $upstream_<safe>; — без URI после переменной (D19)
##   - В /health: set $upstream_<safe> http://<project>:80; ПРИСУТСТВУЕТ (D20)
##   - Старый паттерн `proxy_pass $upstream_...$request_uri` детектируется (R5 negative)
##   - LDD: IMP:9 лог в каждом успешном сценарии
## @rationale DevPlan 136 W1 T1.9: D19/D20-фиксы 135 (643df6d, c87d24c) без регресс-тестов —
##            R5 anti-survivorship: тест на ТОЧНЫЙ вход (vhost с /health → 500).
##            Расхождение DevPlan↔код: DevPlan-путь tests/unit/test_vhost_renderer.py занят
##            полным юнит-набором renderer'а + duplicate-basename ломает pytest-коллекцию
##            (import file mismatch) — D19/D20-тесты в tests/test_vhost_health_patterns.py
##            (задокументировано в coverage-matrix-d1-d23.md).
## @changes  2026-08-05 | DevPlan 136 W1 T1.9 — Created (D19/D20 regression tests)
# endregion MODULE_CONTRACT

import logging
import re

import pytest

from core.internal.scaffold.vhost_renderer import generate_vhost_body

logger = logging.getLogger(__name__)

# Старый баг-паттерн (до 643df6d): proxy_pass с переменной + URI-суффиксом
_OLD_PROXY_PASS_VAR_URI = r"proxy_pass\s+\$upstream_[A-Za-z0-9_]+[^;]*[/$]"


def _has_old_proxy_pass_var_uri(body: str) -> bool:
    """Детектор D19: proxy_pass $var + URI/переменная-суффикс (nginx «invalid URL prefix» → 500).

    ## @purpose — Флаговый детектор старого паттерна: proxy_pass ссылается на переменную И
    ##            содержит URI/переменную-хвост. Канон: `proxy_pass $upstream_x;` (голый var).
    ## @io — ⇥ body: str → ⎋ bool (True = старый паттерн присутствует)
    ## @complexity — O(N) — regex scan
    """
    return re.search(_OLD_PROXY_PASS_VAR_URI, body) is not None


def _extract_location_health(body: str) -> str:
    """Извлечь блок location /health из rendered body (для location-scope asserts D20).

    ## @purpose — Вырезает блок от 'location /health {' до закрывающей '}' — проверка
    ##            set $upstream именно В ЭТОМ location (scope, не глобально).
    ## @io — ⇥ body: str → ⎋ str (текст location /health блока)
    ## @complexity — O(N)
    """
    start = body.index("location /health {")
    end = body.index("\n    }", start)
    return body[start : end + 6]


# region FUNC_test_vhost_health_no_proxy_pass_var_uri
## @purpose — D19: rendered body НЕ содержит proxy_pass $var/URI; /health proxy_pass — голый var.
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D19 — proxy_pass $var/URI → 500 (643df6d)
# · Scenario: generate_vhost_body(...) с /health → НЕТ 'proxy_pass $var/URI' во всём body
# · Last fail: 2026-08-04 — /health proxy_pass $upstream_my_app$request_uri → nginx 500 (invalid URL prefix)
# · Remove if: vhost /health прокси-шаблон меняется
def test_vhost_health_no_proxy_pass_var_uri(caplog: pytest.LogCaptureFixture) -> None:
    """D19: нет `proxy_pass $var/URI` в rendered vhost (проверка всего body + /health блока)."""
    caplog.set_level(logging.INFO)

    body = generate_vhost_body("app.example.com", "my-app", "example.com")
    health_block = _extract_location_health(body)

    assert not _has_old_proxy_pass_var_uri(body), f"D19 regression: старый паттерн proxy_pass $var/URI: {body}"
    assert "proxy_pass $upstream_my_app$request_uri" not in body, "D19: exact старый паттерн запрещён"
    assert "proxy_pass $upstream_my_app;" in health_block, (
        f"D19: /health proxy_pass обязан быть голым var без URI: {health_block}"
    )
    assert "$request_uri" not in health_block, "D19: $request_uri не должен попадать в /health proxy_pass"
    logger.critical("[IMP:9][test] D19 PASS: proxy_pass без URI-суффикса в /health")


# endregion FUNC_test_vhost_health_no_proxy_pass_var_uri


# region FUNC_test_vhost_health_set_upstream_in_location
## @purpose — D20: set $upstream_* ОПРЕДЕЛЁН ВНУТРИ location /health (не только в /).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D20 — set $upstream В location /health (c87d24c)
# · Scenario: location /health блок содержит 'set $upstream_my_app http://my-app:80;' ПЕРЕД proxy_pass
# · Last fail: 2026-08-04 — set только в / → /health proxy_pass "" → 500 (empty variable)
# · Remove if: vhost /health прокси-шаблон меняется
def test_vhost_health_set_upstream_in_location(caplog: pytest.LogCaptureFixture) -> None:
    """D20: set $upstream находится ВНУТРИ location /health (location-scope), перед proxy_pass."""
    caplog.set_level(logging.INFO)

    body = generate_vhost_body("app.example.com", "my-app", "example.com")
    health_block = _extract_location_health(body)

    assert "set $upstream_my_app http://my-app:80;" in health_block, (
        f"D20 regression: set $upstream отсутствует в /health location: {health_block}"
    )
    # set идёт ДО proxy_pass внутри блока (порядок фикса)
    assert health_block.index("set $upstream_my_app") < health_block.index("proxy_pass $upstream_my_app"), (
        f"D20: set обязан идти до proxy_pass в /health: {health_block}"
    )
    logger.critical("[IMP:9][test] D20 PASS: set $upstream определён в location /health")


# endregion FUNC_test_vhost_health_set_upstream_in_location


# region FUNC_test_vhost_health_old_pattern_detector_negative
## @purpose — R5 negative (D19/D20): детектор старого паттерна обязан ЛОВИТЬ синтетический старый body
##            (proxy_pass $var+URI и set вне /health) — без детектора тест был бы pass-test (R1).
# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · D19/D20 — старый паттерн детектируется
# · Scenario: synthetic old body (proxy_pass $upstream_my_app$request_uri) → _has_old_proxy_pass_var_uri True
# · Last fail: 2026-08-04 — реальный body с этим паттерном давал 500 на ноде
# · Remove if: vhost прокси-шаблон меняется (обновить и детектор)
def test_vhost_health_old_pattern_detector_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: синтетический старый паттерн детектируется (иначе детектор мёртв, R1)."""
    caplog.set_level(logging.INFO)

    current_body = generate_vhost_body("app.example.com", "my-app", "example.com")
    # Текущий output — ЧИСТ (D19/D20 фиксы)
    assert not _has_old_proxy_pass_var_uri(current_body), "текущий body обязан быть чистым от D19-паттерна"
    assert current_body.count("set $upstream_my_app") >= 2, (
        "D20: set $upstream обязан быть минимум в 2 locations (/, /health)"
    )

    # Synthetic OLD body (вход, вызывавший баг) — детектор обязан поймать
    old_body = """location /health {
        set $upstream_my_app http://my-app:80;
        proxy_pass $upstream_my_app$request_uri;
    }"""
    assert _has_old_proxy_pass_var_uri(old_body), "R5: детектор обязан ловить старый proxy_pass $var+URI"
    logger.critical("[IMP:9][test] D19/D20 R5 PASS: старый паттерн детектируется, текущий body чист")


# endregion FUNC_test_vhost_health_old_pattern_detector_negative


# region FUNC_test_vhost_health_all_locations_have_set
## @purpose — D20 companion: КАЖДЫЙ location с proxy_pass $upstream имеет собственный set (location-scope
##            инвариант: set из / не виден в /health — переменная обязана определяться в каждом location).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D20 companion — каждый proxy_pass location имеет set
# · Scenario: все location-блоки с 'proxy_pass $upstream_my_app' содержат 'set $upstream_my_app' выше
# · Last fail: 2026-08-04 — /health не имел set → пустая переменная → 500
# · Remove if: vhost прокси-шаблон меняется
def test_vhost_health_all_locations_have_set(caplog: pytest.LogCaptureFixture) -> None:
    """D20 companion: в каждом location с proxy_pass $upstream присутствует set (scope-инвариант)."""
    caplog.set_level(logging.INFO)

    body = generate_vhost_body("app.example.com", "my-app", "example.com")
    # Разбить на location-блоки и проверить каждый proxy_pass $upstream блок
    locations = re.findall(r"location [^{]+\{[^}]*\}", body)
    proxy_locations = [loc for loc in locations if "proxy_pass $upstream_my_app;" in loc]
    assert proxy_locations, "ожидались location-блоки с proxy_pass $upstream_my_app"
    for loc in proxy_locations:
        assert "set $upstream_my_app http://my-app:80;" in loc, (
            f"D20: location без set $upstream (location-scope!): {loc}"
        )
    logger.critical("[IMP:9][test] D20 PASS: каждый proxy_pass location имеет собственный set")


# endregion FUNC_test_vhost_health_all_locations_have_set
