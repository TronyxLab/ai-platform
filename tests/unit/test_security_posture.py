#!/usr/bin/env python3
# GREP_SUMMARY: security-posture unit-tests S1-S7 positive-negative R5 aggregation exit-codes json root-check LDD IMP:9
# STRUCTURE: ▶ fixture fake_probe (subprocess mock) → ◇ S1-S7 positive/negative → ◇ aggregation 0/1/2 → ◇ json/render → ◇ main (root-check, exit) → ⎋ LDD IMP:9 asserts
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/security_posture.py (DevPlan 134 W2, L2).
##           Каждая проверка S1-S7 — positive + negative кейс (R5 Test Honesty), агрегация
##           exit 0/1/2, --json-форма, root-check, LDD IMP:9 траектории (Anti-Illusion Rule).
## @scope    Native pytest — прямые вызовы функций, tmp_path (Zero Hardcode Rule), monkeypatch
##           для subprocess-проб (fake probe) и файловых путей. НЕ запускает реальные бинари.
## @rationale DevPlan 134 AC(2): каждая проверка покрыта positive+negative.
## @changes 2026-08-04 | DevPlan 134 W2 — Created
# endregion MODULE_CONTRACT

import json
import logging

import pytest

from core.internal.bootstrap import firewall, security_posture


class FakeResult:
    """Graceful CompletedProcess stand-in (rc + stdout + stderr)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture()
def fake_probe(monkeypatch):
    """Подменяет _probe: registry[name] → FakeResult. Default rc=0 stdout=''."""
    registry: dict[str, FakeResult] = {}

    def _probe(cmd, timeout):
        return registry.get(cmd[0], FakeResult())

    monkeypatch.setattr(security_posture, "_probe", _probe)
    return registry


@pytest.fixture()
def patch_paths(monkeypatch, tmp_path):
    """Redirect /etc и /opt пути в tmp_path."""
    auto_file = tmp_path / "20auto-upgrades"
    unattended_file = tmp_path / "50unattended-upgrades"
    daemon_json = tmp_path / "daemon.json"
    platform_base = tmp_path / "platform"
    keys_file = tmp_path / "authorized_keys"
    monkeypatch.setattr(security_posture, "AUTO_UPDATES_FILE", str(auto_file))
    monkeypatch.setattr(security_posture, "UNATTENDED_FILE", str(unattended_file))
    monkeypatch.setattr(security_posture, "DOCKER_DAEMON_JSON", str(daemon_json))
    monkeypatch.setattr(security_posture, "PLATFORM_BASE", str(platform_base))
    monkeypatch.setattr(security_posture, "CI_DEPLOY_AUTHORIZED_KEYS", str(keys_file))
    return {
        "auto_file": auto_file,
        "unattended_file": unattended_file,
        "daemon_json": daemon_json,
        "platform_base": platform_base,
        "keys_file": keys_file,
    }


def _write_uu_policy(patch_paths):
    """Пишет валидную unattended-upgrades политику (канон security_updates.py W1)."""
    patch_paths["auto_file"].write_text('APT::Periodic::Unattended-Upgrade "1";\n')
    patch_paths["unattended_file"].write_text(
        'Unattended-Upgrade::Origins-Pattern {\n "origin=Ubuntu,archive=${distro_codename}-security";\n};\n'
    )


# region Tests: S1 — unattended-upgrades active
class TestS1:
    def test_positive_active(self, patch_paths, fake_probe):
        fake_probe["dpkg"] = FakeResult(0, "")
        _write_uu_policy(patch_paths)
        result = security_posture.check_unattended_upgrades()
        assert result.status == security_posture.STATUS_PASS
        assert "active" in result.message

    def test_negative_package_missing(self, patch_paths, fake_probe):
        fake_probe["dpkg"] = FakeResult(1, "package not installed")
        _write_uu_policy(patch_paths)
        result = security_posture.check_unattended_upgrades()
        assert result.status == security_posture.STATUS_FAIL
        assert "NOT installed" in result.message

    def test_negative_config_disabled(self, patch_paths, fake_probe):
        """R5 negative (original form): 20auto-upgrades с Unattended-Upgrade "0" → FAIL."""
        fake_probe["dpkg"] = FakeResult(0, "")
        patch_paths["auto_file"].write_text('APT::Periodic::Unattended-Upgrade "0";\n')
        patch_paths["unattended_file"].write_text("security\n")
        result = security_posture.check_unattended_upgrades()
        assert result.status == security_posture.STATUS_FAIL
        assert "Unattended-Upgrade disabled" in result.message

    def test_negative_no_security_origins(self, patch_paths, fake_probe):
        fake_probe["dpkg"] = FakeResult(0, "")
        patch_paths["auto_file"].write_text('APT::Periodic::Unattended-Upgrade "1";\n')
        patch_paths["unattended_file"].write_text("no security here\n")
        result = security_posture.check_unattended_upgrades()
        assert result.status == security_posture.STATUS_FAIL
        assert "no security origins" in result.message


# endregion Tests: S1


# region Tests: S2 — pending security updates
class TestS2:
    def test_positive_none_pending(self, fake_probe):
        fake_probe["/usr/lib/update-notifier/apt-check"] = FakeResult(
            0, "0 updates can be applied immediately.\n0 of these updates are security updates.\n"
        )
        result = security_posture.check_pending_security_updates()
        assert result.status == security_posture.STATUS_PASS

    def test_warn_security_pending(self, fake_probe):
        """>0 security updates → WARN (норма между daily-кронами, алерт оператору)."""
        fake_probe["/usr/lib/update-notifier/apt-check"] = FakeResult(
            0, "3 updates can be applied immediately.\n2 of these updates are security updates.\n"
        )
        result = security_posture.check_pending_security_updates()
        assert result.status == security_posture.STATUS_WARN
        assert "2 security updates pending" in result.message

    def test_warn_apt_check_unavailable(self, fake_probe):
        fake_probe["/usr/lib/update-notifier/apt-check"] = FakeResult(127, "not found")
        result = security_posture.check_pending_security_updates()
        assert result.status == security_posture.STATUS_WARN
        assert "apt-check unavailable" in result.message


# endregion Tests: S2


# region Tests: S3 — ufw
class TestS3:
    UFW_OK = (
        "Status: active\n"
        "To                         Action      From\n"
        "22/tcp                     ALLOW       Anywhere\n"
        "80/tcp                     ALLOW       Anywhere\n"
        "443/tcp                    ALLOW       Anywhere\n"
        "5432/tcp                   DENY        Anywhere\n"
    )

    def test_positive_active_with_baseline(self, fake_probe):
        fake_probe["ufw"] = FakeResult(0, self.UFW_OK)
        result = security_posture.check_ufw()
        assert result.status == security_posture.STATUS_PASS
        assert "5432 DENY" in result.message

    def test_negative_inactive(self, fake_probe):
        fake_probe["ufw"] = FakeResult(0, "Status: inactive\n")
        result = security_posture.check_ufw()
        assert result.status == security_posture.STATUS_FAIL
        assert "NOT active" in result.message

    def test_negative_docker_api_port_open(self, fake_probe):
        """R5 negative: 2375 открыт — Docker API exposed (firewall.py FORBIDDEN_PORTS)."""
        fake_probe["ufw"] = FakeResult(0, self.UFW_OK + "2375/tcp                     ALLOW       Anywhere\n")
        result = security_posture.check_ufw()
        assert result.status == security_posture.STATUS_FAIL
        assert "2375" in result.message

    def test_negative_5432_not_denied(self, fake_probe):
        lines = [ln.replace("DENY", "ALLOW") if "5432/tcp" in ln else ln for ln in self.UFW_OK.splitlines()]
        fake_probe["ufw"] = FakeResult(0, "\n".join(lines) + "\n")
        result = security_posture.check_ufw()
        assert result.status == security_posture.STATUS_FAIL
        assert "5432" in result.message

    def test_uses_firewall_parse_no_duplication(self):
        """parse_ufw_status приходит из firewall.py (0 дублирования, DevPlan D5)."""
        assert security_posture.parse_ufw_status is firewall.parse_ufw_status


# endregion Tests: S3


# region Tests: S4 — sshd
class TestS4:
    SSHD_OK = "permitrootlogin prohibit-password\npasswordauthentication no\npubkeyauthentication yes\n"

    def test_positive_hardened(self, fake_probe):
        fake_probe["sshd"] = FakeResult(0, self.SSHD_OK)
        result = security_posture.check_sshd()
        assert result.status == security_posture.STATUS_PASS

    def test_negative_password_auth_enabled(self, fake_probe):
        fake_probe["sshd"] = FakeResult(
            0, self.SSHD_OK.replace("passwordauthentication no", "passwordauthentication yes")
        )
        result = security_posture.check_sshd()
        assert result.status == security_posture.STATUS_FAIL
        assert "PasswordAuthentication" in result.message

    def test_negative_root_login_permitted(self, fake_probe):
        fake_probe["sshd"] = FakeResult(0, self.SSHD_OK.replace("prohibit-password", "yes"))
        result = security_posture.check_sshd()
        assert result.status == security_posture.STATUS_FAIL
        assert "PermitRootLogin" in result.message


# endregion Tests: S4


# region Tests: S5 — docker daemon
class TestS5:
    def test_positive_hardened(self, patch_paths, fake_probe):
        patch_paths["daemon_json"].write_text(json.dumps({"live-restore": True, "iptables": True}))
        fake_probe["ss"] = FakeResult(0, "State: LISTEN\n")
        result = security_posture.check_docker()
        assert result.status == security_posture.STATUS_PASS

    def test_negative_live_restore_missing(self, patch_paths, fake_probe):
        patch_paths["daemon_json"].write_text(json.dumps({}))
        fake_probe["ss"] = FakeResult(0, "")
        result = security_posture.check_docker()
        assert result.status == security_posture.STATUS_FAIL
        assert "live-restore" in result.message

    def test_negative_api_port_listening(self, patch_paths, fake_probe):
        """R5 negative: Docker API 2376 слушает → FAIL (S5)."""
        patch_paths["daemon_json"].write_text(json.dumps({"live-restore": True}))
        fake_probe["ss"] = FakeResult(0, 'LISTEN 0 128 *:2376 *:* users:(("dockerd"))\n')
        result = security_posture.check_docker()
        assert result.status == security_posture.STATUS_FAIL
        assert "2376" in result.message


# endregion Tests: S5


# region Tests: S6 — file perms
class TestS6:
    def test_positive_clean(self, patch_paths, fake_probe):
        patch_paths["platform_base"].mkdir()
        (patch_paths["platform_base"] / "secrets").mkdir()
        fake_probe["find"] = FakeResult(0, "")
        result = security_posture.check_file_perms()
        assert result.status == security_posture.STATUS_PASS

    def test_warn_platform_missing(self, patch_paths):
        result = security_posture.check_file_perms()
        assert result.status == security_posture.STATUS_WARN
        assert "not deployed" in result.message

    def test_negative_world_writable(self, patch_paths, fake_probe):
        patch_paths["platform_base"].mkdir()
        fake_probe["find"] = FakeResult(0, f"{patch_paths['platform_base']}/evil.sh\n")
        result = security_posture.check_file_perms()
        assert result.status == security_posture.STATUS_FAIL
        assert "world-writable" in result.message

    def test_negative_world_readable_secrets(self, patch_paths, fake_probe):
        patch_paths["platform_base"].mkdir()
        secrets = patch_paths["platform_base"] / "secrets"
        secrets.mkdir()
        fake_probe["find"] = FakeResult(0, f"{secrets}/age.key\n")
        result = security_posture.check_file_perms()
        assert result.status == security_posture.STATUS_FAIL
        assert "world-readable secrets" in result.message


# endregion Tests: S6


# region Tests: S7 — forced-command
class TestS7:
    FORCED_OK = 'command="cd /opt/platform && PYTHONPATH=/opt/platform python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict ssh-ed25519 AAAA key'

    def test_positive_dispatch_intact(self, patch_paths):
        patch_paths["keys_file"].write_text(self.FORCED_OK + "\n")
        result = security_posture.check_forced_command()
        assert result.status == security_posture.STATUS_PASS
        assert "orchestrator_cli dispatch" in result.message

    def test_negative_missing_forced_command(self, patch_paths):
        """R5 negative: ключ БЕЗ command= (открытый канал — потеря restrict) → FAIL."""
        patch_paths["keys_file"].write_text("ssh-ed25519 AAAA plain-key\n")
        result = security_posture.check_forced_command()
        assert result.status == security_posture.STATUS_FAIL
        assert "no forced-command" in result.message

    def test_negative_missing_keys_file(self, patch_paths):
        result = security_posture.check_forced_command()
        assert result.status == security_posture.STATUS_FAIL
        assert "missing" in result.message


# endregion Tests: S7


# region Tests: S8 — image freshness (DevPlan 134 L4)
class TestS8:
    """docker digest-drift: локальный RepoDigests vs registry manifest inspect."""

    @pytest.fixture()
    def docker_probe(self, monkeypatch):
        """Диспечер по полной команде docker: registry[key] -> FakeResult. Default rc=0 stdout=''."""
        registry: dict[str, FakeResult] = {}

        def _probe(cmd, timeout):
            key = " ".join(cmd)
            return registry.get(key, FakeResult())

        monkeypatch.setattr(security_posture, "_probe", _probe)
        return registry

    PS_OK = "abc123\ndef456\n"

    @staticmethod
    def _ps(ids: str) -> FakeResult:
        return FakeResult(0, ids)

    @staticmethod
    def _inspect(*lines: str) -> FakeResult:
        return FakeResult(0, "\n".join(lines) + ("\n" if lines else ""))

    @staticmethod
    def _manifest(payload: str, rc: int = 0, stderr: str = "") -> FakeResult:
        return FakeResult(rc, payload if rc == 0 else "", stderr=stderr)

    def test_positive_no_containers(self, docker_probe):
        docker_probe["docker ps --format {{.ID}}"] = self._ps("")
        result = security_posture.check_image_freshness()
        assert result.status == security_posture.STATUS_PASS
        assert "no running containers" in result.message

    def test_positive_all_current_pinned(self, docker_probe, caplog):
        """Digest-pinned образ актуален: registry digest совпадает с локальным (multi-arch set)."""
        docker_probe["docker ps --format {{.ID}}"] = self._ps("abc123")
        docker_probe[
            "docker inspect -f {{.Config.Image}}|{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}} abc123"
        ] = self._inspect(
            "postgres:16@sha256:1111111111111111111111111111111111111111111111111111111111111111|sha256:1111111111111111111111111111111111111111111111111111111111111111"
        )
        docker_probe["docker manifest inspect --verbose postgres:16"] = self._manifest(
            json.dumps(
                {"Descriptor": {"digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111"}}
            )
        )
        with caplog.at_level(logging.INFO):
            result = security_posture.check_image_freshness()
        assert result.status == security_posture.STATUS_PASS
        assert "current" in result.message
        assert any("[IMP:9]" in r.message for r in caplog.records), "LDD: IMP:9 не найдено"

    def test_positive_multiarch_list_local_digest_in_set(self, docker_probe):
        """Multi-arch manifest list: локальный digest входит в набор platform-digest'ов → PASS."""
        docker_probe["docker ps --format {{.ID}}"] = self._ps("abc123")
        docker_probe[
            "docker inspect -f {{.Config.Image}}|{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}} abc123"
        ] = self._inspect(
            "ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1|sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        docker_probe["docker manifest inspect --verbose ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1"] = (
            self._manifest(
                json.dumps(
                    [
                        {
                            "Descriptor": {
                                "digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                            }
                        },
                        {
                            "Descriptor": {
                                "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                            }
                        },
                    ]
                )
            )
        )
        result = security_posture.check_image_freshness()
        assert result.status == security_posture.STATUS_PASS

    def test_warn_pinned_stale(self, docker_probe):
        """R5 negative (original form): апстрим опубликовал новый digest для pinned-тега → WARN."""
        docker_probe["docker ps --format {{.ID}}"] = self._ps("abc123")
        docker_probe[
            "docker inspect -f {{.Config.Image}}|{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}} abc123"
        ] = self._inspect(
            "postgres:16@sha256:1111111111111111111111111111111111111111111111111111111111111111|sha256:1111111111111111111111111111111111111111111111111111111111111111"
        )
        docker_probe["docker manifest inspect --verbose postgres:16"] = self._manifest(
            json.dumps(
                {"Descriptor": {"digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222"}}
            )
        )
        result = security_posture.check_image_freshness()
        assert result.status == security_posture.STATUS_WARN
        assert "pin устарел" in result.message
        assert "postgres:16" in result.message

    def test_warn_tag_based_newer(self, docker_probe):
        """Tag-based L2: локальный digest отличен от registry → WARN с рекомендацией пересборки."""
        docker_probe["docker ps --format {{.ID}}"] = self._ps("abc123")
        docker_probe[
            "docker inspect -f {{.Config.Image}}|{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}} abc123"
        ] = self._inspect(
            "ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1|sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        docker_probe["docker manifest inspect --verbose ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1"] = (
            self._manifest(
                json.dumps(
                    {
                        "Descriptor": {
                            "digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                        }
                    }
                )
            )
        )
        result = security_posture.check_image_freshness()
        assert result.status == security_posture.STATUS_WARN
        assert "hermes-build-context" in result.message

    def test_positive_local_only_image_skipped(self, docker_probe):
        """Локально-собранный образ (manifest unknown) → skip, PASS."""
        docker_probe["docker ps --format {{.ID}}"] = self._ps("abc123")
        docker_probe[
            "docker inspect -f {{.Config.Image}}|{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}} abc123"
        ] = self._inspect("status-page:latest|sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc")
        docker_probe["docker manifest inspect --verbose status-page:latest"] = self._manifest(
            "", rc=1, stderr="Error: no such manifest: status-page:latest"
        )
        result = security_posture.check_image_freshness()
        assert result.status == security_posture.STATUS_PASS

    def test_warn_registry_unreachable(self, docker_probe):
        """Registry недоступен (сеть/auth) → WARN graceful (как apt-check в S2)."""
        docker_probe["docker ps --format {{.ID}}"] = self._ps("abc123")
        docker_probe[
            "docker inspect -f {{.Config.Image}}|{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}} abc123"
        ] = self._inspect(
            "postgres:16@sha256:1111111111111111111111111111111111111111111111111111111111111111|sha256:1111111111111111111111111111111111111111111111111111111111111111"
        )
        docker_probe["docker manifest inspect --verbose postgres:16"] = self._manifest(
            "", rc=1, stderr='Error response from daemon: Get "https://registry-1.docker.io/v2/": dial tcp: i/o timeout'
        )
        result = security_posture.check_image_freshness()
        assert result.status == security_posture.STATUS_WARN
        assert "registry query failed" in result.message

    def test_fail_docker_unavailable(self, docker_probe):
        """docker ps падает (демон не запущен) → FAIL (нельзя оценить образы)."""
        docker_probe["docker ps --format {{.ID}}"] = FakeResult(1, "", stderr="Cannot connect to the Docker daemon")
        result = security_posture.check_image_freshness()
        assert result.status == security_posture.STATUS_FAIL

    def test_positive_local_built_no_repo_digests_skipped(self, docker_probe):
        """Локально-собранный (пустой RepoDigests) → skip, PASS."""
        docker_probe["docker ps --format {{.ID}}"] = self._ps("abc123")
        docker_probe[
            "docker inspect -f {{.Config.Image}}|{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}} abc123"
        ] = self._inspect("status-page:latest|")
        result = security_posture.check_image_freshness()
        assert result.status == security_posture.STATUS_PASS


# endregion Tests: S8


# region Tests: aggregation + report + main
class TestAggregation:
    def test_all_pass_exit_0(self):
        results = [security_posture.CheckResult("S1", "PASS", ""), security_posture.CheckResult("S2", "PASS", "")]
        assert security_posture.aggregate_exit_code(results) == 0

    def test_warn_exit_1(self):
        results = [security_posture.CheckResult("S1", "PASS", ""), security_posture.CheckResult("S2", "WARN", "")]
        assert security_posture.aggregate_exit_code(results) == 1

    def test_fail_wins_exit_2(self):
        results = [
            security_posture.CheckResult("S1", "WARN", ""),
            security_posture.CheckResult("S3", "FAIL", ""),
        ]
        assert security_posture.aggregate_exit_code(results) == 2

    def test_main_root_check_fail_fast(self, monkeypatch):
        monkeypatch.setattr(security_posture.os, "geteuid", lambda: 1000)
        assert security_posture.main(["--json"]) == 2

    def test_main_json_structure(self, monkeypatch, capsys, patch_paths, fake_probe):
        """JSON-форма: node/exit_code/checks[{id,status,message}] — фундамент L5 (D6)."""
        monkeypatch.setattr(security_posture.os, "geteuid", lambda: 0)
        fake_probe["dpkg"] = FakeResult(1, "")
        assert security_posture.main(["--node", "n1", "--json"]) == 2
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["node"] == "n1"
        assert payload["exit_code"] == 2
        ids = [c["id"] for c in payload["checks"]]
        assert ids == ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
        assert any(c["status"] == "FAIL" for c in payload["checks"])

    def test_full_run_logs_imp9(self, monkeypatch, patch_paths, fake_probe, caplog):
        """LDD: полный прогон healthy → минимум один [IMP:9] лог (Anti-Illusion Rule)."""
        monkeypatch.setattr(security_posture.os, "geteuid", lambda: 0)
        fake_probe["dpkg"] = FakeResult(0, "")
        fake_probe["/usr/lib/update-notifier/apt-check"] = FakeResult(
            0, "0 updates can be applied immediately.\n0 of these updates are security updates.\n"
        )
        fake_probe["ufw"] = FakeResult(0, TestS3.UFW_OK)
        fake_probe["sshd"] = FakeResult(0, TestS4.SSHD_OK)
        fake_probe["ss"] = FakeResult(0, "")
        patch_paths["platform_base"].mkdir()
        (patch_paths["platform_base"] / "secrets").mkdir()
        patch_paths["daemon_json"].write_text(json.dumps({"live-restore": True}))
        patch_paths["keys_file"].write_text(TestS7.FORCED_OK + "\n")
        _write_uu_policy(patch_paths)

        with caplog.at_level(logging.INFO):
            assert security_posture.main(["--node", "n1"]) == 0
        imp9 = [r.message for r in caplog.records if "[IMP:9]" in r.message]
        assert imp9, "LDD: ни одного IMP:9 лога в успешном прогоне"
        assert any("S7" in m for m in imp9)


# endregion Tests: aggregation + report + main
