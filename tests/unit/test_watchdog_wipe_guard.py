# GREP_SUMMARY: test-watchdog-wipe-guard, F8, wipe-signature, state-loss, boot-id, named-volumes, platform-marker, baseline, notify, TG, dry-run, watchdog
# STRUCTURE: ┌boot_id_fn DI + run_cmd (docker volume ls)┐ → ○ scenarios ∋ (first-run baseline / wipe-signature notify / reboot-rebaseline / cooldown-suppress / dry-run / docker-down skip) → ⊕ state assertions + LDD → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for watchdog.check_wipe_signature / notify_wipe (F8 prevention, DevPlan 031
##           T3): детектор потери docker-состояния ноды (named volumes + /opt/platform/core маркер
##           vs персистентный baseline). tronyx-vps терял ВСЁ состояние за ночь (2026-09-03,
##           provider-level reset: journald = 1 boot, volumes пересозданы оператором) — prevention:
##           TG watchdog.wipe при healthy baseline → пустое состояние.
## @scope    Pure Python — docker CLI замокан (run_cmd DI), /proc boot_id и /opt маркер — DI
##           (boot_id_fn), state-файл в tmp_path (Zero Hardcode Rule).
## @invariants
##   - 0 реальных docker/proc вызовов; boot_id инъектируется
##   - Первый прогон = baseline без алерта; wipe = здоровый baseline → volumes=0 ∧ marker absent
##   - dry-run: 0 мутаций (state не пишется, TG не вызывается)
##   - LDD: сценарии с действием assert IMP:9/IMP:10; R5 negative (cooldown: нет повторного алерта)
## @changes  2026-09-03 · DevPlan 031 T3 (F8 prevention) — создан
# endregion MODULE_CONTRACT

import json
import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.healthcheck import watchdog

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit


# region HELPERS


class FakeVolumeDocker:
    """run_cmd-fake: docker volume ls -q + notifications notify (как FakeDocker в test_watchdog)."""

    def __init__(self, volume_count: int, volume_rc: int = 0) -> None:
        self.volume_count = volume_count
        self.volume_rc = volume_rc
        self.notify_calls: list[list[str]] = []

    def __call__(self, cmd, timeout: int = 30, env=None) -> subprocess.CompletedProcess:  # ruff: ignore[ARG002]
        if cmd[:3] == ["docker", "volume", "ls"]:
            if self.volume_rc != 0:
                return subprocess.CompletedProcess(cmd, 1, "", "simulated volume ls failure")
            names = "\n".join(f"platform_vol-{i}" for i in range(self.volume_count))
            return subprocess.CompletedProcess(cmd, 0, names, "")
        if cmd[:3] == ["python3", "-m", "core.internal.shared.notifications"]:
            self.notify_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        msg = f"unexpected cmd: {cmd}"
        raise AssertionError(msg)


def _write_baseline(path: Path, *, boot_id: str, volumes: int, alerted_at: float = 0.0) -> None:
    path.write_text(
        json.dumps({"boot_id": boot_id, "volumes": volumes, "alerted_at": alerted_at}),
        encoding="utf-8",
    )


# endregion HELPERS


# region TESTS_WIPE_GUARD


# 🧪 TRAP[TEST] · SCENARIO · F8/DevPlan 031 T3 · первый прогон → baseline без алерта
# · Scenario: watchdog впервые видит здоровую ноду (volumes=6, platform core ok) → baseline
#   сохраняется, TG НЕ вызывается (ложный алерт на первом прогоне исключён)
# · Last fail: N/A (new — F8 prevention)
# · Remove if: wipe-guard удалён/заменён
def test_wipe_guard_first_run_saves_baseline(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    state_file = tmp_path / "wipe-guard-state.json"
    fake = FakeVolumeDocker(volume_count=6)

    rc = watchdog.check_wipe_signature(
        state_file=str(state_file),
        now=1000.0,
        run_cmd=fake,
        boot_id_fn=lambda: "boot-A",
    )

    assert rc == 0
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert raw["volumes"] == 6, f"baseline volumes должен быть 6, got {raw}"
    assert raw["boot_id"] == "boot-A"
    assert fake.notify_calls == [], "первый прогон НЕ должен алертить"
    assert any("baseline saved" in r.message for r in caplog.records), "нужен IMP:8 baseline-лог"
    logger.critical("[IMP:9][test][wipe] первый прогон: baseline сохранён без алерта")


# 🧪 TRAP[TEST] · REGRESSION (R5) · F8/DevPlan 031 T3 · wipe-сигнатура → TG watchdog.wipe
# · Scenario: здоровый baseline (volumes=6, marker ok, boot-A) → СЛЕДУЮЩИЙ прогон volumes=0 и
#   platform core отсутствует (docker state + /opt сброшены) → алерт watchdog.wipe + baseline
#   перезаписан пустым (без повтора каждые 5 мин). Оригинальный вход: tronyx-vps ночь 2026-09-03.
# · Last fail: 2026-09-03 — tronyx потерял volumes+payload+platform+known_hosts+journald за ночь
# · Remove if: wipe-guard удалён/заменён
def test_wipe_guard_wipe_signature_notifies(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    state_file = tmp_path / "wipe-guard-state.json"
    _write_baseline(state_file, boot_id="boot-A", volumes=6)
    fake = FakeVolumeDocker(volume_count=0)

    rc = watchdog.check_wipe_signature(
        state_file=str(state_file),
        now=1_000_000.0,  # > WIPE_ALERT_COOLDOWN_SEC (21600) от baseline alerted_at=0
        run_cmd=fake,
        boot_id_fn=lambda: "boot-A",
    )

    assert rc == 0
    assert len(fake.notify_calls) == 1, "wipe-сигнатура → ровно 1 TG алерт"
    assert "--event" in fake.notify_calls[0]
    assert fake.notify_calls[0][fake.notify_calls[0].index("--event") + 1] == "watchdog.wipe"
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert raw["volumes"] == 0, "baseline перезаписан пустым состоянием"
    assert raw["alerted_at"] == 1_000_000.0, "alerted_at штампуется после успешного алерта"
    assert any("WIPE SIGNATURE" in r.message for r in caplog.records), "нужен IMP:10 wipe-лог"
    logger.critical("[IMP:9][test][wipe] wipe-сигнатура детектирована → TG watchdog.wipe")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · F8/DevPlan 031 T3 · cooldown — повторный алерт подавлен
# · Scenario: wipe уже заалерчен (alerted_at в baseline) → следующий прогон в окне cooldown НЕ
#   шлёт второй TG (иначе — TG-спам каждые 5 минут на wiped-ноде)
# · Last fail: N/A (preventive — suppress-окно notify)
# · Remove if: cooldown-семантика watchdog.wipe изменена
def test_wipe_guard_cooldown_suppresses_repeat_alert(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    state_file = tmp_path / "wipe-guard-state.json"
    # baseline уже пустой + alerted_at недавно (после первого wipe-алерта)
    _write_baseline(state_file, boot_id="boot-A", volumes=0, alerted_at=1950.0)
    fake = FakeVolumeDocker(volume_count=0)

    rc = watchdog.check_wipe_signature(
        state_file=str(state_file),
        now=2000.0,
        run_cmd=fake,
        boot_id_fn=lambda: "boot-A",
    )

    assert rc == 0
    assert fake.notify_calls == [], "повторный wipe-алерт в cooldown-окне подавлен"
    logger.critical("[IMP:9][test][wipe] cooldown: повторный алерт подавлен")


# 🧪 TRAP[TEST] · SCENARIO · F8/DevPlan 031 T3 · перезагрузка с восстановленным состоянием
# · Scenario: boot сменился (A → B), но volumes/marker на месте (штатная перезагрузка ноды) →
#   re-baseline БЕЗ алерта (wipe ≠ reboot)
# · Last fail: N/A (preventive — reboot ≠ wipe)
# · Remove if: re-baseline-семантика изменена
def test_wipe_guard_reboot_with_state_rebaselines(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    state_file = tmp_path / "wipe-guard-state.json"
    _write_baseline(state_file, boot_id="boot-A", volumes=6)
    fake = FakeVolumeDocker(volume_count=6)

    rc = watchdog.check_wipe_signature(
        state_file=str(state_file),
        now=3000.0,
        run_cmd=fake,
        boot_id_fn=lambda: "boot-B",
    )

    assert rc == 0
    assert fake.notify_calls == [], "штатная перезагрузка НЕ алертит"
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert raw["boot_id"] == "boot-B", "baseline перезаписан новым boot_id"
    logger.critical("[IMP:9][test][wipe] reboot с состоянием → re-baseline без алерта")


# 🧪 TRAP[TEST] · SCENARIO · F8/DevPlan 031 T3 · dry-run — 0 мутаций
# · Scenario: --dry-run wipe-прогон → state НЕ пишется, TG НЕ вызывается (mode-контракт)
# · Last fail: N/A (preventive)
# · Remove if: dry-run-семантика изменена
def test_wipe_guard_dry_run_no_mutation(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    state_file = tmp_path / "wipe-guard-state.json"
    fake = FakeVolumeDocker(volume_count=0)

    rc = watchdog.check_wipe_signature(
        state_file=str(state_file),
        now=4000.0,
        dry_run=True,
        run_cmd=fake,
        boot_id_fn=lambda: "boot-A",
    )

    assert rc == 0
    assert not state_file.exists(), "dry-run НЕ должен писать state"
    assert fake.notify_calls == [], "dry-run НЕ должен слать TG"
    logger.critical("[IMP:9][test][wipe] dry-run: 0 мутаций")


# 🧪 TRAP[TEST] · SCENARIO · F8/DevPlan 031 T3 · docker недоступен → skip (не ложный алерт)
# · Scenario: docker volume ls rc≠0 (daemon down/флак) → guard skip, state НЕ трогается
# · Last fail: N/A (preventive — graceful degradation)
# · Remove if: graceful-skip семантика изменена
def test_wipe_guard_docker_down_skips(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    state_file = tmp_path / "wipe-guard-state.json"
    _write_baseline(state_file, boot_id="boot-A", volumes=6)
    fake = FakeVolumeDocker(volume_count=0, volume_rc=1)

    rc = watchdog.check_wipe_signature(
        state_file=str(state_file),
        now=5000.0,
        run_cmd=fake,
        boot_id_fn=lambda: "boot-A",
    )

    assert rc == 0
    assert fake.notify_calls == [], "docker-down флак НЕ должен алертить как wipe"
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert raw["volumes"] == 6, "docker-down → baseline НЕ перезаписывается"
    logger.critical("[IMP:9][test][wipe] docker down → guard skip, baseline цел")


# endregion TESTS_WIPE_GUARD
