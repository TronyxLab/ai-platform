#!/usr/bin/env python3
# GREP_SUMMARY: test-compose-validator validate-compose-networks try-parse-compose analyze-proxy-net proxy-net external cascade best-effort no-mutation
# STRUCTURE: fixtures(compose factory) → ◇ validate_compose_networks ┌no domain → valid=True skip┐ → ◇ try_parse_compose cascade (docker compose config → PyYAML → best-effort None) → ◇ analyze_proxy_net (external:true + ≥1 service) → ◇ no-mutation check → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for scaffold/compose_validator.py (DevPlan 139 W4.2 — закрытие blind spot
##            compose_validator, 219 LOC, НОВЫЙ). Покрывает 3-method cascade, proxy-net валидацию,
##            skip-без-domain и инвариант «compose-файлы не мутируются».
## @scope    validate_compose_networks (skip без domain; valid proxy-net; invalid; best-effort skip),
##           try_parse_compose (docker compose config → PyYAML → None), analyze_proxy_net (external:true
##           bool/dict-form, service networks dict/list-form, 0 services, отсутствие networks).
## @invariants
##   - Validation only: compose-файлы НЕ мутируются (byte-level check)
##   - Нет domain → skip (valid=True) БЕЗ парсинга compose
##   - 3-method cascade: docker compose config (mock) → PyYAML → best-effort skip (valid=True)
##   - proxy-net обязателен с external:true + минимум 1 service подключён
##   - tmp_path-изоляция (xdist), 0 subprocess реального docker (shutil.which мокается)
##   - Test Honesty R1-R5: negative-тесты (0 services, не-external, битый yaml, отсутствующий файл)
##   - LDD: каждый тест — IMP:9-траектория (ldd_trajectory)
## @rationale W4 (139): 219 LOC production без тестов — M4 gate adopt-project (step 6). Поведенческие
##            инварианты из MODULE_CONTRACT compose_validator — в исполняемые проверки.
## @changes  2026-08-05 | Created (DevPlan 139 W4.2)
##            2026-08-11 | DevPlan 145 W3 D-I2 — try_parse_compose FileNotFoundError
##                       теперь ловится → best-effort None (контракт «best-effort skip» соблюдён);
##                       test_try_parse_missing_file_returns_none вместо test_try_parse_missing_file_raises
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path
from unittest import mock

from core.internal.scaffold.compose_validator import (
    analyze_proxy_net,
    try_parse_compose,
    validate_compose_networks,
)
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

_PROXY_NET_COMPOSE = """
services:
  app:
    image: nginx:alpine
    networks:
      proxy-net:
        aliases:
          - app
networks:
  proxy-net:
    name: proxy-net
    external: true
"""


# region FUNC__write_compose
## @purpose  Записать compose-контент в tmp файл и вернуть путь.
## @io       ⇥ tmp_path, content: str, name: str → ⎋ Path
## @complexity O(1)
def _write_compose(tmp_path: Path, content: str = _PROXY_NET_COMPOSE, name: str = "compose.yaml") -> Path:
    """Write a compose file into tmp_path and return its path."""
    compose_path = tmp_path / name
    compose_path.write_text(content)
    return compose_path


# endregion FUNC__write_compose


# ═══════════════════════════════════════════════════════════════════════════
# validate_compose_networks
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_validate_no_domain_skips_without_parse
## @purpose  Нет domain → valid=True (skip) БЕЗ попытки парсинга compose (try_parse НЕ вызывается).
# 🧪 TRAP[TEST] · validate_no_domain_skips_without_parse · Contract (D4) · Regression: skip без domain парсит compose
# · Scenario: domain="" → ValidationResult(valid=True, "No domain"); try_parse_compose mocked → assert NOT called
# · Last fail: N/A (новый тест W4.2)
# · Remove if: контракт «без domain → skip» меняется
@ldd_trajectory
def test_validate_no_domain_skips_without_parse(tmp_path, monkeypatch, caplog) -> None:
    """Без domain → valid=True, try_parse НЕ вызывается."""
    compose_path = _write_compose(tmp_path)

    parse_mock = mock.MagicMock()
    monkeypatch.setattr("core.internal.scaffold.compose_validator.try_parse_compose", parse_mock)

    result = validate_compose_networks(compose_path, domain="", compose_profiles="")

    assert result.valid is True, "Без domain → valid=True"
    assert "No domain" in result.message
    parse_mock.assert_not_called(), "Skip не должен парсить compose"
    logger.info("[IMP:9][test] validate_compose_networks: без domain → valid=True, парсинг не запущен ✓")


# endregion FUNC_test_validate_no_domain_skips_without_parse


# region FUNC_test_validate_valid_proxy_net
## @purpose  domain задан + корректный proxy-net (external:true + 1 service) → valid=True с message.
# 🧪 TRAP[TEST] · validate_valid_proxy_net · Contract (M4 gate) · Regression: валидный proxy-net отклоняется
# · Scenario: try_parse mocked → dict с proxy-net external:true + 1 service → valid=True,
# ·   message "proxy-net valid with 1 service(s)"
# · Last fail: N/A (новый тест W4.2)
# · Remove if: критерий proxy-net валидации меняется
@ldd_trajectory
def test_validate_valid_proxy_net(tmp_path, monkeypatch, caplog) -> None:
    """Корректный proxy-net (external:true + 1 service) → valid=True."""
    compose_path = _write_compose(tmp_path)
    import yaml

    parsed = yaml.safe_load(_PROXY_NET_COMPOSE)
    monkeypatch.setattr("core.internal.scaffold.compose_validator.try_parse_compose", lambda p, **kw: parsed)

    result = validate_compose_networks(compose_path, domain="example.com", compose_profiles="")

    assert result.valid is True, "Валидный proxy-net обязан пройти"
    assert "1 service" in result.message
    logger.info("[IMP:9][test] validate_compose_networks: valid proxy-net → valid=True (%s) ✓", result.message)


# endregion FUNC_test_validate_valid_proxy_net


# region FUNC_test_validate_invalid_no_service
## @purpose  proxy-net external:true, но 0 services подключено → valid=False (M4 gate блокирует adopt).
# 🧪 TRAP[TEST] · validate_invalid_no_service · NEGATIVE (R5) · Regression: proxy-net без service проходит gate
# · Scenario: proxy-net external:true, services без networks → valid=False, message "no service is connected"
# · Last fail: N/A (новый negative-тест W4.2)
# · Remove if: критерий «≥1 service подключён» меняется
@ldd_trajectory
def test_validate_invalid_no_service(tmp_path, monkeypatch, caplog) -> None:
    """proxy-net external, но 0 services → valid=False."""
    compose_path = _write_compose(tmp_path)
    data = {
        "services": {"app": {"image": "nginx:alpine"}},
        "networks": {"proxy-net": {"external": True}},
    }
    monkeypatch.setattr("core.internal.scaffold.compose_validator.try_parse_compose", lambda p, **kw: data)

    result = validate_compose_networks(compose_path, domain="example.com", compose_profiles="")

    assert result.valid is False, "0 services на proxy-net → invalid"
    assert "no service is connected" in result.message
    logger.info("[IMP:9][test] validate_compose_networks: 0 services → valid=False ✓")


# endregion FUNC_test_validate_invalid_no_service


# region FUNC_test_validate_best_effort_skip_when_parse_unavailable
## @purpose  Парсер недоступен (try_parse → None) → WARN + valid=True (best-effort skip).
# 🧪 TRAP[TEST] · validate_best_effort_skip · Contract (3-method cascade) · Regression: отсутствие парсера фейлит gate
# · Scenario: try_parse mocked → None → valid=True, message "Parse unavailable — best-effort skip"; IMP:8 WARN
# · Last fail: N/A (новый тест W4.2)
# · Remove if: best-effort skip контракт меняется
@ldd_trajectory
def test_validate_best_effort_skip_when_parse_unavailable(tmp_path, monkeypatch, caplog) -> None:
    """try_parse → None → valid=True (best-effort skip)."""
    compose_path = _write_compose(tmp_path)
    monkeypatch.setattr("core.internal.scaffold.compose_validator.try_parse_compose", lambda p, **kw: None)

    result = validate_compose_networks(compose_path, domain="example.com", compose_profiles="")

    assert result.valid is True, "Best-effort: парсер недоступен → valid=True"
    assert "best-effort" in result.message
    warns = [r.message for r in caplog.records if "WARN: skipping proxy-net validation" in r.message]
    assert warns, "Ожидался WARN-лог best-effort skip"
    logger.info("[IMP:9][test] validate_compose_networks: парсер недоступен → best-effort valid=True ✓")


# endregion FUNC_test_validate_best_effort_skip_when_parse_unavailable


# ═══════════════════════════════════════════════════════════════════════════
# try_parse_compose — 3-method cascade
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_try_parse_docker_config_cascade
## @purpose  Method 1: docker доступен (shutil.which truthy) + shared docker compose config rc=0 +
##            YAML-вывод → возвращается dict; shared-вызов с compose_args/-f и env_override COMPOSE_PROFILES.
# 🧪 TRAP[TEST] · try_parse_docker_config_cascade · Contract (B5 T3 sole path) · Regression: docker-ветка не парсит
# · Scenario: which→"/usr/bin/docker"; shared docker_compose_config mocked rc=0 stdout=YAML →
# ·   dict возвращается; assert env_override={"COMPOSE_PROFILES": ...} и -f путь
# · Last fail: N/A (новый тест W4.2)
# · Remove if: каскад парсинга меняется (docker-ветка удаляется)
@ldd_trajectory
def test_try_parse_docker_config_cascade(tmp_path, monkeypatch, caplog) -> None:
    """docker compose config (shared sole path) → dict; env_override COMPOSE_PROFILES пробрасывается."""
    compose_path = _write_compose(tmp_path)

    monkeypatch.setattr("core.internal.scaffold.compose_validator.shutil.which", lambda name: "/usr/bin/docker")
    shared_mock = mock.MagicMock(
        return_value=subprocess.CompletedProcess(
            ["docker", "compose", "config"], 0, stdout=_PROXY_NET_COMPOSE, stderr=""
        )
    )
    monkeypatch.setattr("core.internal.scaffold.compose_validator._shared_docker_compose_config", shared_mock)

    data = try_parse_compose(compose_path, compose_profiles="full")

    assert isinstance(data, dict), "docker-ветка обязана вернуть dict"
    assert "services" in data
    shared_mock.assert_called_once()
    call_kwargs = shared_mock.call_args.kwargs
    assert call_kwargs["compose_args"] == ["-f", str(compose_path)], "compose_args -f пробрасывается"
    assert call_kwargs["env_override"] == {"COMPOSE_PROFILES": "full"}, "COMPOSE_PROFILES пробрасывается"
    logger.info("[IMP:9][test] try_parse_compose: docker compose config cascade → dict (COMPOSE_PROFILES=full) ✓")


# endregion FUNC_test_try_parse_docker_config_cascade


# region FUNC_test_try_parse_pyyaml_fallback
## @purpose  Method 2: docker недоступен → PyYAML чтение файла напрямую → dict.
# 🧪 TRAP[TEST] · try_parse_pyyaml_fallback · Contract (3-method cascade) · Regression: PyYAML fallback сломан
# · Scenario: which → None; реальный compose-файл → dict; shared docker НЕ вызывается
# · Last fail: N/A (новый тест W4.2)
# · Remove if: PyYAML fallback удаляется из каскада
@ldd_trajectory
def test_try_parse_pyyaml_fallback(tmp_path, monkeypatch, caplog) -> None:
    """docker недоступен → PyYAML чтение → dict."""
    compose_path = _write_compose(tmp_path)

    monkeypatch.setattr("core.internal.scaffold.compose_validator.shutil.which", lambda name: None)
    shared_mock = mock.MagicMock()
    monkeypatch.setattr("core.internal.scaffold.compose_validator._shared_docker_compose_config", shared_mock)

    data = try_parse_compose(compose_path, compose_profiles="")

    assert isinstance(data, dict), "PyYAML fallback обязан вернуть dict"
    assert "networks" in data
    shared_mock.assert_not_called(), "Без docker shared-вызов не исполняется"
    logger.info("[IMP:9][test] try_parse_compose: PyYAML fallback (без docker) → dict ✓")


# endregion FUNC_test_try_parse_pyyaml_fallback


# region FUNC_test_try_parse_none_when_yaml_malformed
## @purpose  docker недоступен + битый YAML → None (парсер недоступен) — вход в best-effort skip.
# 🧪 TRAP[TEST] · try_parse_none_when_yaml_malformed · NEGATIVE (R5) · Regression: битый yaml роняет парсинг
# · Scenario: which → None; файл с malformed YAML ("a: [unclosed") → None (YAMLError → caught)
# · Last fail: N/A (новый negative-тест W4.2)
# · Remove if: семантика битого YAML меняется (начинает raise наружу)
@ldd_trajectory
def test_try_parse_none_when_yaml_malformed(tmp_path, monkeypatch, caplog) -> None:
    """docker недоступен + malformed YAML → None (best-effort вход)."""
    compose_path = _write_compose(tmp_path, content="services:\n  app:\n    image: [unclosed", name="bad.yaml")

    monkeypatch.setattr("core.internal.scaffold.compose_validator.shutil.which", lambda name: None)

    data = try_parse_compose(compose_path, compose_profiles="")

    assert data is None, "Malformed YAML → None (парсер недоступен)"
    logger.info("[IMP:9][test] try_parse_compose: malformed YAML → None (graceful) ✓")


# endregion FUNC_test_try_parse_none_when_yaml_malformed


# region FUNC_test_try_parse_missing_file_returns_none
## @purpose  D-I2 закрыт (DevPlan 145 W3): docker недоступен + файл отсутствует → FileNotFoundError
##            ловится → best-effort None (контракт «best-effort skip» соблюдён, drift устранён).
# 🧪 TRAP[TEST] · try_parse_missing_file_returns_none · NEGATIVE (R5) · Regression: missing-file роняет парсинг
# · Scenario: which → None; compose_path не существует → None (best-effort skip, контракт соблюдён)
# · Last fail: 2026-08-05 — drift контракта (FileNotFoundError пробрасывался); закрыт D-I2
# · Remove if: try_parse_compose снова начинает пробрасывать missing-file (контракт нарушен)
@ldd_trajectory
def test_try_parse_missing_file_returns_none(tmp_path, monkeypatch, caplog) -> None:
    """docker недоступен + файл отсутствует → None (best-effort skip, D-I2 закрыт)."""
    missing = tmp_path / "does-not-exist" / "compose.yaml"

    monkeypatch.setattr("core.internal.scaffold.compose_validator.shutil.which", lambda name: None)

    data = try_parse_compose(missing, compose_profiles="")

    assert data is None, "Missing-file → None (best-effort skip, контракт соблюдён после D-I2)"
    logger.info("[IMP:9][test] try_parse_compose: missing-file → None (D-I2 закрыт, best-effort) ✓")


# endregion FUNC_test_try_parse_missing_file_returns_none


# ═══════════════════════════════════════════════════════════════════════════
# analyze_proxy_net — core logic
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_analyze_proxy_net_valid_dict_and_list_forms
## @purpose  external:true в dict-форме ("external: {name: proxy-net}") + service networks в list-форме
##            (["proxy-net"]) → valid (True, count 1).
# 🧪 TRAP[TEST] · analyze_proxy_net_dict_list_forms · Contract · Regression: dict/list формы networks не распознаются
# · Scenario: external dict-form + networks list-form → (True, 1)
# · Last fail: N/A (новый тест W4.2)
# · Remove if: формы networks/external меняются
@ldd_trajectory
def test_analyze_proxy_net_valid_dict_and_list_forms(caplog) -> None:
    """external dict-form + service list-form networks → valid, 1 service."""
    data = {
        "services": {"app": {"networks": ["proxy-net"]}},
        "networks": {"proxy-net": {"external": {"name": "proxy-net"}}},
    }
    valid, svc_count, msg = analyze_proxy_net(data)
    assert valid is True, f"dict-form external должен быть валиден: {msg}"
    assert svc_count == 1
    logger.info("[IMP:9][test] analyze_proxy_net: external dict-form + list networks → valid (count=1) ✓")


# endregion FUNC_test_analyze_proxy_net_valid_dict_and_list_forms


# region FUNC_test_analyze_proxy_net_no_networks_section
## @purpose  Нет секции networks → invalid (False, 0).
# 🧪 TRAP[TEST] · analyze_proxy_net_no_networks · NEGATIVE · Regression: отсутствие networks валидно
# · Scenario: data без networks → (False, 0, "No networks section found")
# · Last fail: N/A (новый negative-тест W4.2)
# · Remove if: требование секции networks меняется
@ldd_trajectory
def test_analyze_proxy_net_no_networks_section(caplog) -> None:
    """networks — не dict (list) → invalid ("No networks section found")."""
    valid, _svc_count, msg = analyze_proxy_net({"services": {}, "networks": []})
    assert valid is False, "networks не-dict → invalid"
    assert "No networks section" in msg
    logger.info("[IMP:9][test] analyze_proxy_net: networks не-dict → invalid ✓")


# endregion FUNC_test_analyze_proxy_net_no_networks_section


# region FUNC_test_analyze_proxy_net_not_external
## @purpose  proxy-net присутствует, но external:false → invalid c диагностическим сообщением.
# 🧪 TRAP[TEST] · analyze_proxy_net_not_external · NEGATIVE (R5) · Regression: proxy-net без external:true валиден
# · Scenario: external:false → (False, 0) c message "does not declare ... external:true"
# · Last fail: N/A (новый negative-тест W4.2)
# · Remove if: требование external:true меняется
@ldd_trajectory
def test_analyze_proxy_net_not_external(caplog) -> None:
    """proxy-net external:false → invalid."""
    data = {"services": {"app": {"networks": ["proxy-net"]}}, "networks": {"proxy-net": {"external": False}}}
    valid, _svc_count, msg = analyze_proxy_net(data)
    assert valid is False, "proxy-net без external:true → invalid"
    assert "external:true" in msg
    logger.info("[IMP:9][test] analyze_proxy_net: external:false → invalid ✓")


# endregion FUNC_test_analyze_proxy_net_not_external


# ═══════════════════════════════════════════════════════════════════════════
# No-mutation invariant
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_validate_compose_not_mutated
## @purpose  Валидация НЕ мутирует compose-файл: содержимое byte-identical до/после
##            validate_compose_networks (docker недоступен → PyYAML путь).
# 🧪 TRAP[TEST] · validate_compose_not_mutated · Contract (invariant) · Regression: валидация правит compose
# · Scenario: реальный файл; docker недоступен (which → None); valid proxy-net → valid=True;
# ·   содержимое файла байт-в-байт не изменилось
# · Last fail: N/A (новый тест W4.2)
# · Remove if: инвариант «validation only» меняется (валидатор начинает мутировать compose)
@ldd_trajectory
def test_validate_compose_not_mutated(tmp_path, monkeypatch, caplog) -> None:
    """validate_compose_networks не мутирует compose-файл (byte-level check)."""
    compose_path = _write_compose(tmp_path)
    before = compose_path.read_bytes()

    monkeypatch.setattr("core.internal.scaffold.compose_validator.shutil.which", lambda name: None)

    result = validate_compose_networks(compose_path, domain="example.com", compose_profiles="")

    assert result.valid is True, "Реальный valid compose (PyYAML путь) должен пройти"
    assert compose_path.read_bytes() == before, "Compose-файл не должен мутироваться"
    logger.info("[IMP:9][test] validate_compose_networks: файл byte-identical после валидации ✓")


# endregion FUNC_test_validate_compose_not_mutated
