"""
# GREP_SUMMARY: test-docker-daemon daemon.json live-restore merge atomic docker install
# STRUCTURE: ⚡ tmp_path → write daemon.json → call merge_live_restore → assert live-restore + atomic
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/docker_daemon.py (Strangler-порт install-docker.sh inline)
## @scope    merge_live_restore: существующий daemon.json → live-restore:true без потери ключей;
##           не-JSON содержимое → False; отсутствующий файл → False
## @invariants
##   - tmp_path isolated — zero hardcoded paths
##   - Direct function calls (native pytest, no subprocess)
## @changes  2026-07-31 | Created (debt S-2)
# endregion MODULE_CONTRACT
"""

import json
import logging

from core.internal.bootstrap.docker_daemon import merge_live_restore

logger = logging.getLogger("test_docker_daemon")


def _print_trajectory(caplog):
    """Print IMP:7-10 lines before assertions (LDD protocol)."""
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        msg = getattr(record, "message", "")
        if "[IMP:" in str(msg):
            imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(msg)
    print("--- END LDD TRAJECTORY ---")


def test_merge_live_restore_preserves_existing_keys(tmp_path, caplog) -> None:
    """Существующие ключи сохраняются, live-restore включается."""
    caplog.set_level(logging.INFO)
    daemon_json = tmp_path / "daemon.json"
    daemon_json.write_text(json.dumps({"iptables": True, "log-driver": "json-file"}))

    ok = merge_live_restore(str(daemon_json))
    _print_trajectory(caplog)

    assert ok is True
    config = json.loads(daemon_json.read_text())
    assert config["live-restore"] is True
    assert config["iptables"] is True
    assert config["log-driver"] == "json-file"
    logger.critical("[IMP:9][test] merge_preserves: ok=%s — OK", ok)


def test_merge_live_restore_invalid_json(tmp_path, caplog) -> None:
    """Не-JSON содержимое → False (без перезаписи файла)."""
    caplog.set_level(logging.INFO)
    daemon_json = tmp_path / "daemon.json"
    daemon_json.write_text("not json {{{")

    ok = merge_live_restore(str(daemon_json))
    _print_trajectory(caplog)

    assert ok is False
    assert daemon_json.read_text() == "not json {{{"
    logger.critical("[IMP:9][test] merge_invalid: ok=%s — OK", ok)


def test_merge_live_restore_missing_file(tmp_path, caplog) -> None:
    """Отсутствующий файл → False (install-docker создаёт файл heredoc'ом отдельно)."""
    caplog.set_level(logging.INFO)
    ok = merge_live_restore(str(tmp_path / "nonexistent.json"))
    _print_trajectory(caplog)

    assert ok is False
    logger.critical("[IMP:9][test] merge_missing: ok=%s — OK", ok)


def test_merge_live_restore_idempotent(tmp_path, caplog) -> None:
    """Повторный merge — no-op (live-restore уже true, ключи не дублируются)."""
    caplog.set_level(logging.INFO)
    daemon_json = tmp_path / "daemon.json"
    daemon_json.write_text(json.dumps({"live-restore": True, "iptables": True}))

    ok = merge_live_restore(str(daemon_json))
    assert ok is True
    config = json.loads(daemon_json.read_text())
    assert config["live-restore"] is True
    assert len(config) == 2
    logger.critical("[IMP:9][test] merge_idempotent: ok=%s — OK", ok)
