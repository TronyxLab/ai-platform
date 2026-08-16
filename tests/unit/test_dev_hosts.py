# GREP_SUMMARY: dev-hosts, etc-hosts, block-diff, apply, idempotent, sudo, atomic, R5-negative, caplog, unit-tests, DevPlan-136
# STRUCTURE: ▶ collect_hosts fixtures (node.yaml + SAN stubs) → ◇ block_diff (append/replace/stale/malformed) →
#            ▶ apply (write/no-op/ConfigNotFound) → ◇ _atomic_write sudo-branch → ▶ main CLI (dry-run/apply/print/exit 2) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты dev_hosts (DevPlan 136 W4, T4.4): collect_hosts (server_names + dev-cert
##           SAN base), block_diff (маркер-блок merge — чужие строки сохраняются, stale-removal,
##           malformed → ConfigParseError), apply (идемпотентность, атомарность, mock sudo),
##           main CLI (dry-run exit 1 при diff / apply / print / exit 2 ConfigNotFound).
## @scope    Native pytest — прямые вызовы функций, tmp_path (Zero Hardcode Rule), monkeypatch
##           для get_cert_sans (SAN-стабы) и _sudo_move (mock sudo). НЕ трогает реальный /etc/hosts.
## @invariants  Никаких hardcoded путей (tmp_path); никаких реальных subprocess; caplog LDD
##              IMP:9 в успешных сценариях (Anti-Illusion Rule); R5-negative на malformed-блок.
## @rationale DevPlan 136 AC W4: dry-run/apply идемпотентны; diff/merge сохраняют чужие строки;
##            R5 anti-survivorship (Test Honesty) — negative-кейс на точный вход (одиночный маркер),
##            который при неверной обработке молча испортил бы /etc/hosts.
## @changes 2026-08-05 | DevPlan 136 W4 (T4.4) — Created
# endregion MODULE_CONTRACT

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from core.internal import dev_hosts
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

DEV_SUFFIX = "ai-platform.local"

# SAN-стабы (формат вывода dev_cert_generator.get_cert_sans)
SAN_STUB = ["DNS:*.ai-platform.local", "DNS:localhost", "IP:127.0.0.1", "DNS:test.local"]


def _write_node_yaml(tmp_path: Path, projects: list[dict]) -> Path:
    """Фикстура: node.yaml с проектами в tmp_path/configs/<node>/node.yaml."""
    configs = tmp_path / "configs"
    node_dir = configs / "test-node"
    node_dir.mkdir(parents=True)
    lines = ["node:", "  name: test-node", "projects:"]
    for p in projects:
        lines.append(f"  - name: {p['name']}")
        lines.append(f"    domain: {p['domain']}")
    node_yaml = node_dir / "node.yaml"
    node_yaml.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return node_yaml


def _assert_imp9(caplog, needle: str) -> None:
    """Anti-Illusion: в успешном сценарии должна быть IMP:9 траектория (паттерн W3)."""
    found = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9 and needle in record.message:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found, f"Critical LDD Error: No IMP:9 log containing '{needle}' found"


# region Tests: collect_hosts (T4.1)
class TestCollectHosts:
    # 🧪 TRAP[TEST] · Scenario · server_names → dev FQDN <name>.<suffix> (vhost_renderer dev-mode parity)
    # · Regression: dev-hosts должен давать те же FQDN, что render_vhost в dev-режиме
    # · Last fail: N/A (new module, DevPlan 136 W4)
    # · Remove if: dev FQDN-схема изменена в vhost_renderer
    def test_collect_hosts_server_names_dev_fqdn(self, caplog, tmp_path) -> None:
        caplog.set_level(logging.INFO)
        _write_node_yaml(
            tmp_path,
            [
                {"name": "tronyx-site", "domain": "tronyx.ru"},  # prod-домен в node.yaml — dev-rewrite обязателен
                {"name": "dance-site", "domain": "dance-site.ai-platform.local"},
            ],
        )
        hosts = dev_hosts.collect_hosts(
            node_configs_dir=str(tmp_path / "configs"),
            node_name="test-node",
            dev_suffix=DEV_SUFFIX,
            dev_certs_dir=str(tmp_path / "certs"),  # cert отсутствует — SAN-вклад пуст
        )
        assert hosts == {"tronyx-site.ai-platform.local", "dance-site.ai-platform.local"}
        _assert_imp9(caplog, "collect_hosts] Collected")

    # 🧪 TRAP[TEST] · Scenario · missing node.yaml → graceful empty server_names (не ошибка)
    # · Regression: fresh-клон без node-configs не должен ломать dev-hosts
    # · Last fail: N/A (new module)
    # · Remove if: node.yaml становится обязательным для dev-hosts
    def test_collect_hosts_missing_node_yaml_degrades_gracefully(self, caplog, tmp_path) -> None:
        caplog.set_level(logging.INFO)
        hosts = dev_hosts.collect_hosts(
            node_configs_dir=str(tmp_path / "configs"),
            node_name="test-node",
            dev_suffix=DEV_SUFFIX,
            dev_certs_dir=str(tmp_path / "certs"),
        )
        assert hosts == set()
        _assert_imp9(caplog, "collect_hosts] Collected")

    # 🧪 TRAP[TEST] · Scenario · SAN wildcard → base domain; concrete → itself; localhost/IP skip
    # · Regression: *.ai-platform.local должен давать ai-platform.local (wildcard не валиден в hosts(5))
    # · Last fail: N/A (new module)
    # · Remove if: SAN-контрибьюция dev-hosts изменена
    def test_collect_hosts_dev_cert_sans_base_domains(self, caplog, tmp_path) -> None:
        caplog.set_level(logging.INFO)
        certs = tmp_path / "certs"
        certs.mkdir()
        (certs / "fullchain.pem").write_text("stub", encoding="utf-8")
        hosts = dev_hosts.collect_hosts(
            node_configs_dir=str(tmp_path / "configs"),
            node_name="test-node",
            dev_suffix=DEV_SUFFIX,
            dev_certs_dir=str(certs),
            get_cert_sans_fn=lambda _p: SAN_STUB,  # DI (167 D3) — SAN-стаб вместо monkeypatch
        )
        # *.ai-platform.local → ai-platform.local · *.test.local → test.local ·
        # localhost (skip) · IP:127.0.0.1 (skip)
        assert hosts == {"ai-platform.local", "test.local"}
        _assert_imp9(caplog, "collect_hosts] Collected")

    # 🧪 TRAP[TEST] · Regression · REAL get_cert_sans (без monkeypatch) — str→Path контракт
    # · Scenario: smoke-тест поймал AttributeError 'str' object has no attribute 'is_file' —
    # ·   _sans_to_hosts передавал str в get_cert_sans(cert_file: Path) (dev_cert_generator
    # ·   контракт); юнит-тесты с monkeypatch этого не видели. Фикстура — реальный
    # ·   self-signed cert через openssl (fixture-генерация, не бизнес-логика)
    # · Last fail: 2026-08-05 — smoke CLI --dry-run rc=1 AttributeError
    # · Remove if: get_cert_sans перестаёт требовать Path (или переходит на str)
    def test_sans_to_hosts_real_get_cert_sans(self, tmp_path) -> None:
        if shutil.which("openssl") is None:
            pytest.skip("openssl недоступен — инфраструктурная недоступность (не маскировка бага)")
        certs = tmp_path / "certs"
        certs.mkdir()
        cert_file = certs / "fullchain.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-keyout",
                str(certs / "key.pem"),
                "-out",
                str(cert_file),
                "-subj",
                "/CN=dev-hosts-test",
                "-addext",
                "subjectAltName=DNS:*.ai-platform.local,DNS:localhost,IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
        )
        hosts = dev_hosts._sans_to_hosts(cert_file)
        assert hosts == {"ai-platform.local"}  # *.ai-platform.local → base; localhost/IP skip

    # 🧪 TRAP[TEST] · Scenario · missing dev cert → graceful empty SAN contribution
    # · Regression: без сгенерированного dev-сертификата (fresh clone) dev-hosts работает по server_names
    # · Last fail: N/A (new module)
    # · Remove if: dev-сертификат становится обязательным для dev-hosts
    def test_collect_hosts_missing_cert_degrades_gracefully(self, caplog, tmp_path) -> None:
        caplog.set_level(logging.INFO)
        _write_node_yaml(tmp_path, [{"name": "botanika", "domain": "botanika.ai-platform.local"}])
        hosts = dev_hosts.collect_hosts(
            node_configs_dir=str(tmp_path / "configs"),
            node_name="test-node",
            dev_suffix=DEV_SUFFIX,
            dev_certs_dir=str(tmp_path / "certs"),  # нет fullchain.pem
        )
        assert hosts == {"botanika.ai-platform.local"}
        _assert_imp9(caplog, "collect_hosts] Collected")


# endregion Tests: collect_hosts (T4.1)


# region Tests: block_diff (T4.1)
class TestBlockDiff:
    # 🧪 TRAP[TEST] · Scenario · append block at end, foreign lines preserved verbatim
    # · Regression: managed block не должен трогать пользовательские записи /etc/hosts
    # · Last fail: N/A (new module)
    # · Remove if: маркер-блок ownership изменён
    def test_block_diff_appends_preserving_foreign_lines(self) -> None:
        etc_hosts = "127.0.0.1 localhost\n255.255.255.255 broadcasthost\n"
        new_content, changed = dev_hosts.block_diff(etc_hosts, {"a.local", "b.local"})
        assert changed is True
        assert "127.0.0.1 localhost" in new_content
        assert "255.255.255.255 broadcasthost" in new_content
        assert dev_hosts.BEGIN_MARKER in new_content
        assert dev_hosts.END_MARKER in new_content
        assert "127.0.0.1 a.local b.local" in new_content

    # 🧪 TRAP[TEST] · Scenario · existing block replaced in place, foreign prefix/suffix preserved
    # · Regression: повторный merge не должен дублировать блок
    # · Last fail: N/A (new module)
    # · Remove if: маркер-блок ownership изменён
    def test_block_diff_replaces_existing_block_in_place(self) -> None:
        etc_hosts = (
            "127.0.0.1 localhost\n"
            "# BEGIN ai-platform dev-hosts\n127.0.0.1 old.local\n# END ai-platform dev-hosts\n"
            "255.255.255.255 broadcasthost\n"
        )
        new_content, changed = dev_hosts.block_diff(etc_hosts, {"new.local"})
        assert changed is True
        assert new_content.count(dev_hosts.BEGIN_MARKER) == 1
        assert "old.local" not in new_content
        assert "127.0.0.1 new.local" in new_content
        assert "127.0.0.1 localhost" in new_content
        assert "255.255.255.255 broadcasthost" in new_content

    # 🧪 TRAP[TEST] · Scenario · idempotent merge — повторный merge = byte-level no-op
    # · Regression: AC W4 «повторный --apply no-op»
    # · Last fail: N/A (new module)
    # · Remove if: идемпотентность dev-hosts отменена
    def test_block_diff_idempotent_no_change_after_merge(self) -> None:
        etc_hosts = "127.0.0.1 localhost\n"
        merged, changed1 = dev_hosts.block_diff(etc_hosts, {"a.local", "b.local"})
        assert changed1 is True
        merged2, changed2 = dev_hosts.block_diff(merged, {"a.local", "b.local"})
        assert changed2 is False
        assert merged2 == merged

    # 🧪 TRAP[TEST] · Scenario · empty host set removes stale block (stale cleanup), foreign kept
    # · Regression: удаление последнего проекта должно чистить /etc/hosts, а не оставлять мусор
    # · Last fail: N/A (new module)
    # · Remove if: stale-cleanup семантика изменена
    def test_block_diff_empty_hosts_removes_stale_block(self) -> None:
        etc_hosts = (
            "127.0.0.1 localhost\n"
            "# BEGIN ai-platform dev-hosts\n127.0.0.1 old.local\n# END ai-platform dev-hosts\n"
            "255.255.255.255 broadcasthost\n"
        )
        new_content, changed = dev_hosts.block_diff(etc_hosts, set())
        assert changed is True
        assert dev_hosts.BEGIN_MARKER not in new_content
        assert "old.local" not in new_content
        assert "127.0.0.1 localhost" in new_content
        assert "255.255.255.255 broadcasthost" in new_content

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · malformed block — только BEGIN без END → ConfigParseError
    # · Scenario: точный вход бага — «одиночный маркер»: если бы merge молча дописал блок,
    # ·   за orphan-BEGIN появился бы второй блок → два managed-блока в /etc/hosts (порча)
    # · Last fail: N/A (new module — negative фиксирует контракт с первого дня)
    # · Remove if: обработка malformed-блока изменена на auto-repair
    def test_block_diff_malformed_single_marker_negative_raises(self) -> None:
        etc_hosts = "127.0.0.1 localhost\n# BEGIN ai-platform dev-hosts\n"
        with pytest.raises(ConfigParseError):
            dev_hosts.block_diff(etc_hosts, {"a.local"})

    # 🧪 TRAP[TEST] · Scenario · empty hosts + empty file → no-op (не создаёт пустой блок)
    # · Regression: пустой деплой не должен писать пустой маркер-блок
    # · Last fail: N/A (new module)
    # · Remove if: I6 (no empty block) отменена
    def test_block_diff_empty_hosts_empty_file_noop(self) -> None:
        new_content, changed = dev_hosts.block_diff("", set())
        assert changed is False
        assert not new_content


# endregion Tests: block_diff (T4.1)


# region Tests: apply (T4.1)
class TestApply:
    # 🧪 TRAP[TEST] · Scenario · apply пишет файл; повторный apply = no-op (AC W4 идемпотентность)
    # · Regression: второй --apply не должен трогать файл (byte-level)
    # · Last fail: N/A (new module)
    # · Remove if: идемпотентность apply отменена
    def test_apply_writes_then_idempotent_noop(self, caplog, tmp_path) -> None:
        caplog.set_level(logging.INFO)
        hosts_path = tmp_path / "hosts"
        hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")

        applied1 = dev_hosts.apply(str(hosts_path), {"a.local"})
        assert applied1 is True
        content_after_first = hosts_path.read_text(encoding="utf-8")
        assert "127.0.0.1 a.local" in content_after_first

        applied2 = dev_hosts.apply(str(hosts_path), {"a.local"})
        assert applied2 is False
        assert hosts_path.read_text(encoding="utf-8") == content_after_first
        _assert_imp9(caplog, "apply] Applied")

    # 🧪 TRAP[TEST] · Scenario · apply сохраняет чужие строки /etc/hosts verbatim
    # · Regression: managed-блок не должен поглощать пользовательские записи
    # · Last fail: N/A (new module)
    # · Remove if: маркер-блок ownership изменён
    def test_apply_preserves_foreign_lines(self, tmp_path) -> None:
        hosts_path = tmp_path / "hosts"
        hosts_path.write_text("127.0.0.1 localhost\n# user note\n", encoding="utf-8")
        dev_hosts.apply(str(hosts_path), {"a.local"})
        content = hosts_path.read_text(encoding="utf-8")
        assert "127.0.0.1 localhost" in content
        assert "# user note" in content
        assert content.index("127.0.0.1 localhost") < content.index(dev_hosts.BEGIN_MARKER)

    # 🧪 TRAP[TEST] · Scenario · отсутствующий hosts-файл → ConfigNotFoundError (exit 2)
    # · Regression: /etc/hosts отсутствует (поломанная система) — fail loud, не создавать молча
    # · Last fail: N/A (new module)
    # · Remove if: контракт exit 2 изменён
    def test_apply_missing_file_raises_config_not_found(self, tmp_path) -> None:
        with pytest.raises(ConfigNotFoundError):
            dev_hosts.apply(str(tmp_path / "missing-hosts"), {"a.local"})


# endregion Tests: apply (T4.1)


# region Tests: _atomic_write sudo branch (mock sudo)
class TestAtomicWriteSudo:
    # 🧪 TRAP[TEST] · Scenario · unwritable parent → sudo mv ветка (DI _sudo_move, I4)
    # · Regression: /etc/hosts (не-writable parent) обязан идти через sudo mv, а не падать
    # · Last fail: N/A (new module)
    # · Remove if: sudo-граница (I4) изменена
    def test_atomic_write_sudo_branch_uses_sudo_move(self, tmp_path) -> None:
        dest_dir = tmp_path / "protected"
        dest_dir.mkdir()
        dest = dest_dir / "hosts"
        dest_dir.chmod(0o555)  # read+exec — не writable для владельца → sudo-ветка

        calls: list[tuple[str, str]] = []

        def fake_sudo_move(src: Path, target: Path) -> None:
            calls.append((str(src), str(target)))
            # Эмуляция root: sudo mv игнорирует write-биты каталога (root bypass)
            target.parent.chmod(0o755)
            Path(src).replace(target)  # эмулируем sudo mv
            target.parent.chmod(0o555)

        try:
            # DI (167 D3): sudo_move_fn вместо monkeypatch _sudo_move
            dev_hosts._atomic_write(dest, "managed content\n", sudo_move_fn=fake_sudo_move)
        finally:
            dest_dir.chmod(0o755)  # cleanup для teardown tmp_path

        assert dest.read_text(encoding="utf-8") == "managed content\n"
        assert len(calls) == 1
        assert calls[0][1] == str(dest)


# endregion Tests: _atomic_write sudo branch (mock sudo)


# region Tests: main CLI (T4.1)
class TestMain:
    def _base_args(self, tmp_path: Path) -> list[str]:
        """Минимальный набор флагов — изолирует main от реальных /etc/hosts и .env."""
        return [
            "--node-configs-dir",
            str(tmp_path / "configs"),
            "--node",
            "test-node",
            "--dev-suffix",
            DEV_SUFFIX,
            "--dev-certs-dir",
            str(tmp_path / "certs"),
        ]

    # 🧪 TRAP[TEST] · Scenario · dry-run exit 1 при diff, затем (после apply) exit 0
    # · Regression: AC W4 «make dev-hosts выдаёт diff» + идемпотентность
    # · Last fail: N/A (new module)
    # · Remove if: exit-контракт dry-run изменён
    def test_main_dry_run_exit_1_on_diff_then_0(self, caplog, tmp_path) -> None:
        caplog.set_level(logging.INFO)
        _write_node_yaml(tmp_path, [{"name": "tronyx-site", "domain": "tronyx.ru"}])
        hosts_path = tmp_path / "hosts"
        hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")

        rc_diff = dev_hosts.main([*self._base_args(tmp_path), "--etc-hosts", str(hosts_path), "--dry-run"])
        assert rc_diff == 1
        _assert_imp9(caplog, "DIFF detected")

        rc_apply = dev_hosts.main([*self._base_args(tmp_path), "--etc-hosts", str(hosts_path), "--apply"])
        assert rc_apply == 0

        rc_sync = dev_hosts.main([*self._base_args(tmp_path), "--etc-hosts", str(hosts_path)])
        assert rc_sync == 0
        assert "tronyx-site.ai-platform.local" in hosts_path.read_text(encoding="utf-8")
        _assert_imp9(caplog, "no diff")

    # 🧪 TRAP[TEST] · Scenario · --print выводит собранные hostname (sorted), exit 0
    # · Regression: AC W4 «dry-run/print/apply идемпотентен»
    # · Last fail: N/A (new module)
    # · Remove if: --print семантика изменена
    def test_main_print_outputs_sorted_hosts(self, caplog, tmp_path, capsys) -> None:
        caplog.set_level(logging.INFO)
        _write_node_yaml(
            tmp_path,
            [{"name": "b", "domain": "b.ai-platform.local"}, {"name": "a", "domain": "a.ai-platform.local"}],
        )
        rc = dev_hosts.main([*self._base_args(tmp_path), "--print"])
        out = capsys.readouterr().out
        assert rc == 0
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert lines == ["a.ai-platform.local", "b.ai-platform.local"]  # sorted
        _assert_imp9(caplog, "--print:")

    # 🧪 TRAP[TEST] · Scenario · отсутствующий hosts-файл → main exit 2 (ConfigNotFoundError)
    # · Regression: контракт exit-кодов 2 (core/AGENTS.md)
    # · Last fail: N/A (new module)
    # · Remove if: контракт exit 2 изменён
    def test_main_missing_hosts_file_exit_2(self, caplog, tmp_path) -> None:
        caplog.set_level(logging.INFO)
        rc = dev_hosts.main([*self._base_args(tmp_path), "--etc-hosts", str(tmp_path / "missing")])
        assert rc == 2


# endregion Tests: main CLI (T4.1)
