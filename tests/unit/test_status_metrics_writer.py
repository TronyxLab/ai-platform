# GREP_SUMMARY: status-metrics-writer torn-read-impossible os.replace json-writer atomic AI-0009
# STRUCTURE: ▶ valid JSON target → ◇ failed publish (crash before replace) → ⎋ старый валидный цел │ ▶ success → полный новый, temp удалён
# region MODULE_CONTRACT
## @purpose  AI-0009 (DevPlan 17 T3.2): status-metrics.json публикуется через temp + os.replace —
##           конкурентный читатель НИКОГДА не видит частичный JSON; неудачная запись не рушит
##           прежний валидный файл.
## @scope    tests/unit: json_writer.atomic_write с monkeypatched Path.replace; без Docker.
## @invariants
##   - Crash до replace → target содержит СТАРЫЙ валидный JSON (torn невозможен)
##   - Успех → target целиком новый валидный JSON, temp-файл удалён
# endregion MODULE_CONTRACT

import json
import logging
from pathlib import Path

import pytest

from core.internal.healthcheck.metrics.json_writer import SCHEMA_VERSION, atomic_write

logger = logging.getLogger(__name__)


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · конкурентный читатель не видит частичный JSON (AI-0009)
# · Regression: truncate-in-place оставлял окно torn-read — статус-страница парсила половину
#   файла; комментарий «never sees partial file» был ложью
# · Scenario: (1) crash между записью temp и replace → старый валидный JSON цел;
#   (2) успех → новый JSON целиком, schema_version инъектирован, temp-мусор удалён
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0009)
# · Remove if: writer переезжает на другой механизм публикации (например, dir-swap)
def test_torn_read_impossible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """temp+replace: читатель видит либо старый валидный, либо полный новый JSON."""
    target = tmp_path / "status-metrics.json"
    old_data = {"containers": [{"name": "nginx", "running": True}], "generated_at": "old"}
    target.write_text(json.dumps(old_data), encoding="utf-8")

    # ── 1. Crash ДО публикации: temp записан, replace упал → старый файл цел ──
    real_replace = Path.replace

    def _failing_replace(self: Path, _target: Path) -> Path:  # type: ignore[override]
        err = "simulated crash before publish"
        raise OSError(err)

    monkeypatch.setattr(Path, "replace", _failing_replace)
    with pytest.raises(OSError, match="simulated crash"):
        atomic_write({"status": "new"}, str(target))
    monkeypatch.setattr(Path, "replace", real_replace)

    observed = _read_json(target)  # читатель в любой момент: валидный JSON
    assert observed == old_data, "после неудачной публикации обязан остаться прежний валидный JSON"
    logger.info("[IMP:8][test] failed publish keeps old valid JSON — torn-read impossible")

    # ── 2. Успешная публикация: полный новый JSON + temp убран ──
    new_data = {"status": "ok", "items": list(range(50))}
    atomic_write(new_data, str(target))

    published = _read_json(target)
    assert published["schema_version"] == SCHEMA_VERSION, "schema_version инъектируется писателем"
    assert published["status"] == "ok"
    assert len(published["items"]) == 50, "файл заменён ЦЕЛИКОМ (не частично)"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, f"temp-файлы обязаны удаляться после публикации: {leftovers}"
    logger.critical("[IMP:9][test] atomic publish verified end-to-end — OK (AI-0009)")
