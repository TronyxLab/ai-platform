# GREP_SUMMARY: test-docker-user-policy DOCKER-USER peer-accept post-dnat drop-last legacy-identical iptables-save verify stale-comment platform-du-peer data-plane DevPlan-16-T1A
# STRUCTURE: ▶ fixtures (iptables-save text) → ○ desired_docker_user_rules (peer до DROP / None=легаси) → ○ apply passthrough (-A порядок) → ○ verify_docker_user_rules (DROP-last, missing/stale peer, канонизация /32+-m tcp) → ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты iptables-домена docker_user_policy (DevPlan 162 W2-3 + DevPlan 16 T1.A):
##           DOCKER-USER peer-source семантика для кросс-нодового DNAT'ed data-plane (P0-1) и
##           верификация против факта iptables-save (стейл/отсутствие peer-ACCEPT).
## @scope    tests/unit: чистые функции desired_docker_user_rules / apply_docker_user_policy /
##           verify_docker_user_rules через firewall re-export; без subprocess/root.
## @invariants
##   - peer_rules=None → список байт-в-байт идентичен легаси (регрессия обратной совместимости
##     systemd ExecStartPost раннего бута)
##   - DROP — всегда последний во всех сценариях
##   - iptables-save канонизация (-m tcp, /32) учитывается верификатором (семантическое сравнение)
##   - R5-негативы: отсутствие ожидаемого peer-ACCEPT и стейл-комментарий → verify FAIL
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.bootstrap import docker_user_policy, firewall

# ── Фикстуры iptables-save ────────────────────────────────────────────────────
_SAVE_HEADER = "*filter\n:INPUT ACCEPT [0:0]\n:DOCKER-USER - [0:0]\n"
_SAVE_BASE_RULES = (
    "-A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT\n"
    "-A DOCKER-USER -p tcp -m tcp --dport 80 -j ACCEPT\n"
    "-A DOCKER-USER -p tcp -m tcp --dport 443 -j ACCEPT\n"
    "-A DOCKER-USER -s 172.16.0.0/12 -j ACCEPT\n"
    "-A DOCKER-USER -s 10.32.0.0/16 -j ACCEPT\n"
)
_SAVE_FOOTER = "COMMIT\n"

# Каноническая форма peer-правила билдера (аргументы БЕЗ -A/-C)
_PEER_APPS_6432 = [
    "-s",
    "10.8.0.13",
    "-p",
    "tcp",
    "--dport",
    "6432",
    "-j",
    "ACCEPT",
    "-m",
    "comment",
    "--comment",
    "platform-du-peer-6432-apps-1",
]
_PEER_AGENT_3000 = [
    "-s",
    "10.8.0.13",
    "-p",
    "tcp",
    "--dport",
    "3000",
    "-j",
    "ACCEPT",
    "-m",
    "comment",
    "--comment",
    "platform-du-peer-3000-agent-1",
]

# iptables-save канонизирует: -s <ip>/32 и разворачивает -p tcp в -p tcp -m tcp
_PEER_SAVE_LINE_6432 = (
    "-A DOCKER-USER -s 10.8.0.13/32 -p tcp -m tcp --dport 6432 -j ACCEPT "
    "-m comment --comment platform-du-peer-6432-apps-1\n"
)


def _save_text(*rule_lines: str) -> str:
    """Собрать текст iptables-save из заголовка, базовых правил и хвостовых."""
    return _SAVE_HEADER + _SAVE_BASE_RULES + "".join(rule_lines) + "-A DOCKER-USER -j DROP\n" + _SAVE_FOOTER


# region TEST_desired_rules
def test_no_peers_legacy_identical() -> None:
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 16 T1.A · peers=None → список идентичен легаси
    # · Scenario: обратная совместимость systemd ExecStartPost (ранний бут без placement):
    #   established + 80 + 443 + 2 моста + DROP, ровно 6 правил, тот же порядок
    # · Last fail: N/A — новый кейс T1.A (guard от непреднамеренного изменения базовой политики)
    # · Remove if: базовая политика DOCKER-USER пересмотрена (TRAP[DECISION] W2-3 отменён)
    rules = docker_user_policy.desired_docker_user_rules()
    assert len(rules) == 6
    assert rules[0] == ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"]
    assert rules[1] == ["-p", "tcp", "--dport", "80", "-j", "ACCEPT"]
    assert rules[2] == ["-p", "tcp", "--dport", "443", "-j", "ACCEPT"]
    assert rules[-1] == ["-j", "DROP"], "DROP обязан быть последним"
    # None vs дефолт — идентичны (контракт kwarg)
    assert docker_user_policy.desired_docker_user_rules(None) == rules
    assert firewall.desired_docker_user_rules() == rules  # re-export стабилен


def test_peer_accept_before_drop() -> None:
    # 🧪 TRAP[TEST] · SCENARIO · DevPlan 16 T1.A P0-1 · peer-ACCEPT строго между bridge и DROP
    # · Scenario: два peer-правила вставляются после bridge-ACCEPT и ДО catch-all DROP — иначе
    #   правила мертвы (после DROP) или открывают лишнее (до established-группы не критично,
    #   но контракт порядка фиксируем)
    # · Last fail: аудит 15 P0-1 — весь data-plane (6432/9000/…) молча DROPался при зелёном verify
    # · Remove if: DOCKER-USER peer-семантика отменена
    rules = docker_user_policy.desired_docker_user_rules([_PEER_APPS_6432, _PEER_AGENT_3000])
    assert len(rules) == 8, f"6 легаси + 2 peer: {rules}"
    assert rules[5] == _PEER_APPS_6432, "первый peer-ACCEPT сразу после bridge-правил"
    assert rules[6] == _PEER_AGENT_3000
    assert rules[-1] == ["-j", "DROP"], "DROP остаётся ПОСЛЕДНИМ при наличии peer-правил"


def test_apply_passes_peer_rules_to_add_phase(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · SCENARIO · DevPlan 16 T1.A · apply добавляет peer-правила в -A фазе
    # · Scenario: свежая нода с placement — -C rc=1 на все 8 правил, -A rc=0; peer-правила
    #   применяются в порядке до DROP
    # · Last fail: N/A — новый кейс T1.A
    # · Remove if: apply перестанет принимать peer_rules
    caplog.set_level(logging.INFO)
    calls: list[list[str]] = []

    class FakeResult:
        def __init__(self, rc: int) -> None:
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    def fake_run(cmd: list[str], **kwargs: object) -> FakeResult:
        calls.append(list(cmd))
        return FakeResult(1 if cmd[1] == "-C" else 0)

    assert firewall.apply_docker_user_policy(run_cmd=fake_run, peer_rules=[_PEER_APPS_6432]) is True
    adds = [c for c in calls if c[1] == "-A"]
    assert len(adds) == 7, f"6 легаси + 1 peer: {adds}"
    assert adds[5][3:] == _PEER_APPS_6432, "peer-правило применено после bridge-ACCEPT ([0]=chain, [1]=-A)"
    assert adds[-1][-2:] == ["-j", "DROP"]
    assert any("[IMP:9]" in r.message for r in caplog.records)


# endregion TEST_desired_rules


# region TEST_verify_facts
def test_verify_structural_drop_last_ok() -> None:
    # 🧪 TRAP[TEST] · REGRESSION · 162 W2-3 + 16 T1.A п.4 · структурный verify (peer_rules=None)
    # · Scenario: корректный save-вывод → PASS; DROP не последним → FAIL (мёртвые ACCEPT)
    # · Last fail: N/A — новый кейс
    # · Remove if: verify_docker_user_rules удалён
    assert firewall.verify_docker_user_rules(_save_text()) is True
    broken = _save_text().replace("-A DOCKER-USER -j DROP\nCOMMIT\n", "")
    broken += "-A DOCKER-USER -j DROP\n-A DOCKER-USER -p tcp -m tcp --dport 9999 -j ACCEPT\nCOMMIT\n"
    assert firewall.verify_docker_user_rules(broken) is False, "ACCEPT после DROP = FAIL"


def test_verify_empty_chain_fails() -> None:
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T1.A · пустая/отсутствующая цепочка → FAIL
    # · Scenario: ExecStartPost не отработал/docker рестартовал цепочку — «зелёный» статус
    #   обязан падать, а не молчать о мёртвом data-plane
    # · Last fail: аудит 15 P0-1 (silent DROP data-plane)
    # · Remove if: DOCKER-USER гарантии обеспечиваются иначе
    empty_save = "*filter\n:DOCKER-USER - [0:0]\nCOMMIT\n"
    assert firewall.verify_docker_user_rules(empty_save) is False


def test_verify_missing_peer_accept_fails() -> None:
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T1.A · simulated ruleset БЕЗ peer-ACCEPT → FAIL
    # · Scenario: expected содержит (10.8.0.13, 6432, comment), факт — только базовые правила:
    #   детектор обязан поймать отсутствие (red→green: без verify-функции кейс невозможен)
    # · Last fail: аудит 15 P0-1 — DNAT'ed трафик молча DROPался
    # · Remove if: peer-детект перенесён в иной механизм
    save = _save_text()
    expected = [_PEER_APPS_6432]
    assert firewall.verify_docker_user_rules(save, expected) is False, (
        "отсутствие peer-ACCEPT обязано ронять verify (P0-1)"
    )
    # Тот же факт С нужным правилом → PASS (green)
    save_green = _save_text(_PEER_SAVE_LINE_6432)
    assert firewall.verify_docker_user_rules(save_green, expected) is True


def test_verify_stale_peer_accept_detected() -> None:
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T1.A/P1-12 · стейл peer-комментарий → FAIL
    # · Scenario: пир исчез из placement, правило осталось — verify отличает актуальный набор
    #   от факта по комментариям platform-du-peer-*
    # · Last fail: N/A — новый кейс (ранее peer-порты исключались из reconcile целиком)
    # · Remove if: стейл-детект переносится в реконсиляцию другого уровня
    save = _save_text(
        _PEER_SAVE_LINE_6432,
        "-A DOCKER-USER -s 10.8.0.99/32 -p tcp -m tcp --dport 6379 -j ACCEPT "
        "-m comment --comment platform-du-peer-6379-gone-peer\n",
    )
    assert firewall.verify_docker_user_rules(save, [_PEER_APPS_6432]) is False, "стейл peer-правило = FAIL"
    # Без ожиданий (fallback-режим) структурные проверки проходят
    assert firewall.verify_docker_user_rules(save) is True


def test_verify_canonicalized_save_matches() -> None:
    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 16 T1.A · канонизация iptables-save учитывается
    # · Scenario: save пишет `-s <ip>/32` и добавляет `-m tcp` — семантическое сравнение
    #   (src, dport, comment) матчит аргументную форму билдера (строковое сравнение сломалось бы)
    # · Last fail: N/A — новый кейс
    # · Remove if: формат вывода iptables-save изменён (маловероятно)
    save = _save_text(_PEER_SAVE_LINE_6432)
    assert firewall.verify_docker_user_rules(save, [_PEER_APPS_6432]) is True


# endregion TEST_verify_facts
