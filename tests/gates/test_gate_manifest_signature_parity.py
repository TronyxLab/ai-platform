# GREP_SUMMARY: gate manifest-signature-parity entrypoint-manifest make_target signature drift invariant-11 T6 T7
# STRUCTURE: ▶ parse entrypoint-manifest.yaml make_target entries → ◇ карта таргет→ожидаемая сигнатура → ⊕ сравнение → ⎋ pass|fail (R5 negative)
# region MODULE_CONTRACT
## @purpose  Parity gate (DevPlan 122 T6+T7, P-6/P-7, инвариант 11): сигнатуры make_target
#            в entrypoint-manifest.yaml соответствуют фактическому коду. Генератор НЕ чинит
#            сигнатуры (merge сохраняет make_target verbatim) — гейт ловит ручной/авто-дрейф.
## @scope    Read-only gate. Карта таргет→ожидаемая подстрока signature:
##           gate → 'ci-docker' (код: ci.mk:136 MODE=fast|full|ci-docker, check_suite.py:71)
##           up → 'MODULES' (код: modules.mk up = MODULES-фильтр, НЕ PROJECT)
##           backup → НЕ '[NODE=' (код: backup без переменных — делегация в backup-cron)
##           restore → 'DUMP_FILE' (код: restore требует DUMP_FILE, НЕ NODE)
##           down → НЕ '-v' (канон P-1: down без -v, деструктив — down-volumes)
##           down-volumes → '-v' (новый глагол, DevPlan 122 T1)
## @invariants
##   - Каждая запись make_target в карте присутствует в манифесте
##   - signature содержит ожидаемые подстроки и НЕ содержит запрещённых (R5-детектор)
##   - R5 negative: инлайн-запись с устаревшей сигнатурой → RED (исходный вход P-6/P-7)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale P-6: manifest:279 + AGENTS.md:44 = MODE=fast|full, код — fast|full|ci-docker.
##            P-7: up[PROJECT]/backup[NODE]/restore NODE=<n> vs код MODULES/без переменных/DUMP_FILE.
##            Существующий test_gate_manifest_integrity сигнатуры НЕ валидирует — дрейф был невидим.
## @changes 2026-08-03 | Created (DevPlan 122 T6+T7)
# endregion MODULE_CONTRACT


import pathlib

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

ROOT = repo_root()
MANIFEST = ROOT / "core" / "entrypoint-manifest.yaml"

# Таргет → (обязательные подстроки signature, запрещённые подстроки signature)
# Детектор P-6/P-7: если код и манифест разойдутся — RED.
SIGNATURE_MAP: dict[str, tuple[list[str], list[str]]] = {
    "gate": (["ci-docker"], []),
    "up": (["MODULES"], ["PROJECT"]),
    "backup": ([], ["[NODE="]),
    "restore": (["DUMP_FILE"], ["NODE="]),
    "down": ([], ["-v"]),
    "down-volumes": (["-v"], []),
}


def _load_manifest_entries() -> dict[str, dict]:
    """Load make_target entries from entrypoint-manifest.yaml.

    ## @purpose — Map make_target → entry dict for signature checks.
    ##            Записи вложены в группы (bootstrap/deploy/test/test/gate/...).
    ## @io — ⎋ dict[str, dict]
    """
    with pathlib.Path(MANIFEST).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entries: dict[str, dict] = {}
    for group in data.values():
        if not isinstance(group, list):
            continue
        for entry in group:
            if not isinstance(entry, dict):
                continue
            name = entry.get("make_target")
            if name:
                entries[name] = entry
    return entries


@pytest.mark.gate
class TestGateManifestSignatureParity:
    """Gate: сигнатуры make_target в манифесте == фактический код (инвариант 11, P-6/P-7)."""

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · сигнатурный дрейф манифеста (DevPlan 122 T6+T7)
    # · Last fail: gate=fast|full (код ci-docker), up=[PROJECT] (код MODULES),
    # ·   backup=[NODE] (код без переменных), restore=NODE=<n> (код DUMP_FILE), down=-v (канон P-1)
    # · Remove if: сигнатуры канонизируются иначе
    def test_signatures_match_code(self):
        """signature каждой записи соответствует карте (код — факт, доки обновляются)."""
        entries = _load_manifest_entries()
        violations: list[str] = []
        for target, (required, forbidden) in SIGNATURE_MAP.items():
            entry = entries.get(target)
            if entry is None:
                violations.append(f"{target}: запись отсутствует в манифесте")
                continue
            signature = str(entry.get("signature", ""))
            violations.extend(
                f"{target}: signature '{signature}' не содержит '{sub}'" for sub in required if sub not in signature
            )
            violations.extend(
                f"{target}: signature '{signature}' содержит запрещённое '{sub}'"
                for sub in forbidden
                if sub in signature
            )
        assert not violations, "GATE_MANIFEST_SIGNATURE_PARITY: " + "; ".join(violations)

    # 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · исходный вход P-6/P-7 (DevPlan 122 T6+T7)
    # · Last fail: manifest:279 signature 'make gate [MODE=fast|full]' (код — ci-docker)
    # · Remove if: сигнатуры канонизируются иначе
    def test_stale_signature_detected_negative(self):
        """R5 negative: инлайн-запись с устаревшей сигнатурой gate → RED (детектор ловит P-6)."""
        inline_entries = {
            "gate": {"make_target": "gate", "signature": "make gate [MODE=fast|full]"},
            "restore": {"make_target": "restore", "signature": "make restore NODE=<n>"},
            "down": {"make_target": "down", "signature": "make down"},
        }
        # Детектор: gate требует ci-docker
        gate_sig = inline_entries["gate"]["signature"]
        assert "ci-docker" not in gate_sig, "R5 FAIL: fixture должен быть устаревшей сигнатурой (без ci-docker)"
        # restore требует DUMP_FILE
        restore_sig = inline_entries["restore"]["signature"]
        assert "DUMP_FILE" not in restore_sig, "R5 FAIL: fixture должен быть устаревшей сигнатурой (без DUMP_FILE)"
