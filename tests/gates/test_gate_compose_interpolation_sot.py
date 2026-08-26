# GREP_SUMMARY: gate compose-interpolation sot parity VAR:? secret-definitions env_defaults plan-012 T4 F-014 ZAI
# STRUCTURE: ┌scan core/modules/*/base.yml + root compose for ${VAR:?}┐ → ◇ union SoT {secret-definitions#name ∪ tier=generated ∪ platform-infra env_defaults} → ◇ unknown VAR → RED with repair hint · ⊕ R5-negative inline fixture (F-014 class) → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Парити-гейт: каждая строгая интерполяция ${VAR:?...} в compose-файлах обязана
##           быть зарегистрирована в SoT-реестрах (plan 012 T4, AC3). Ловит класс ошибок
##           «ключ есть в compose, отсутствует в матрице» (прецедент ZAI/DEEPSEEK, F-014)
##           ДО доставки на ноду.
## @scope    Read-only скан: root docker-compose.yml + core/modules/*/docker-compose.base.yml;
##           SoT: core/secret-definitions.yaml (имена + tier=generated) и
##           core/platform-infra.yaml (env_defaults). Коммент-строки не сканируются
##           (${VAR:?} в комментариях документации — легитимен).
## @invariants
##   - Каждый ${VAR:?} ∈ {definitions#name} ∪ {tier=generated} ∪ {env_defaults keys}
##   - Нарушение → FAIL с file:line + repair-подсказкой (в какой SoT регистрировать)
##   - R5-negative: инлайн-фикстура с неизвестным VAR детектируется (сценарий класса F-014)
##   - Probe-файлы _gate_probe_* исключаются из сканов по префиксу (конвенция gates/AGENTS.md)
## @rationale D3: автоматизация (T3 auto-inject) убирает существующий класс ошибок,
##            гейт ловит будущее; dry-run φ8 (T10) защищает на ноде, гейт — в репозитории.
## @changes   CREATED 2026-08-26 | DevPlan 012 T4 — interpolation parity gate
# endregion MODULE_CONTRACT

import re
from pathlib import Path

import pytest
import yaml

from core.internal.shared.yaml_loader import load_secret_definitions

pytestmark = pytest.mark.gate

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SECRET_DEFINITIONS = PROJECT_ROOT / "core" / "secret-definitions.yaml"
PLATFORM_INFRA = PROJECT_ROOT / "core" / "platform-infra.yaml"
MODULES_DIR = PROJECT_ROOT / "core" / "modules"

# Строгая интерполяция ${VAR:?...} — только эта форма гейтится (DD3 reversed);
# ${VAR:-default} имеет дефолт и в реестре не нуждается.
_STRICT_INTERP_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?")
_COMMENT_LINE_RE = re.compile(r"^\s*#")
_PROBE_PREFIX = "_gate_probe_"


def _scan_compose_text(text: str) -> list[tuple[str, int]]:
    """Scan compose text for ${VAR:?} tokens outside comment lines → [(var, line_no)]."""
    found: list[tuple[str, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _COMMENT_LINE_RE.match(line):
            continue
        found.extend((match.group(1), line_no) for match in _STRICT_INTERP_RE.finditer(line))
    return found


def _scan_targets() -> list[Path]:
    """Compose files under parity gate: root compose + all module base.yml (probe-safe)."""
    targets = [PROJECT_ROOT / "docker-compose.yml"]
    if MODULES_DIR.is_dir():
        targets.extend(sorted(MODULES_DIR.glob("*/docker-compose.base.yml")))
    return [p for p in targets if p.is_file() and not p.name.startswith(_PROBE_PREFIX)]


def _load_sot_registry() -> set[str]:
    """Union SoT registry: definitions#name ∪ tier=generated names ∪ env_defaults keys."""
    registered: set[str] = set()

    for definition in load_secret_definitions(SECRET_DEFINITIONS):
        name = str(definition.get("name", ""))
        if name:
            registered.add(name)

    with PLATFORM_INFRA.open(encoding="utf-8") as fh:
        infra = yaml.safe_load(fh) or {}
    env_defaults = infra.get("env_defaults") or {}
    registered.update(str(key) for key in env_defaults)

    return registered


def _find_violations(registered: set[str], targets: list[Path]) -> list[str]:
    """Return actionable violation lines 'file:line: VAR not in any SoT registry'."""
    violations: list[str] = []
    for path in targets:
        # tmp_path-фикстуры (R5-negative) живут вне репозитория — показываем как есть
        try:
            display = str(path.relative_to(PROJECT_ROOT))
        except ValueError:
            display = str(path)
        for var, line_no in _scan_compose_text(path.read_text(encoding="utf-8")):
            if var not in registered:
                violations.append(
                    f"{display}:{line_no}: ${{{var}:?}} not registered in "
                    f"SoT (core/secret-definitions.yaml name/tier=generated OR "
                    f"core/platform-infra.yaml env_defaults) — repair: register {var} or drop :?"
                )
    return sorted(violations)


# ═══════════════════════════════════════════════════════════════════════════════
# Positive: every ${VAR:?} in real composes is registered
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.gate
def test_all_required_vars_registered() -> None:
    """Every ${VAR:?} token in composes resolves to a SoT-registered variable.

    ## @purpose — AC3: парити-гейт интерполяции; неизвестный VAR = будущий unsatisfied
    ##            ${VAR:?} на ноде → FAIL до доставки (класс F-014).
    ## @io — ⇥ composes + SoT YAML → ⎋ None (asserts zero violations)
    ## @complexity — O(files × lines)
    ## @scenario — AC3/T4: каждый ${VAR:?} ↔ SoT
    """
    # 🧪 TRAP[TEST] · 2026-08-26 · REGRESSION · F-014 class detector (plan 012 T4)
    # · Scenario: real tree scan — все ${VAR:?} зарегистрированы в SoT
    # · Last fail: F-014 — ZAI_API_KEY был в litellm compose, но отсутствовал в матрице
    #   ноды → bootstrap падал на интерполяции после ручной правки
    # · Remove if: строгая интерполяция ${VAR:?} выведена из употребления (DD3 revert)
    violations = _find_violations(_load_sot_registry(), _scan_targets())
    assert not violations, "[IMP:9][gate] Unregistered strict-interpolation vars:\n" + "\n".join(violations)
    print(f"[IMP:9][gate] All ${{VAR:?}} tokens registered across {len(_scan_targets())} compose files")


# ═══════════════════════════════════════════════════════════════════════════════
# Negative (R5): unknown VAR is detected (F-014 class reproduction)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.gate
def test_unknown_var_red(tmp_path: Path) -> None:
    """R5-negative: compose fixture with unregistered ${UNKNOWN_VAR:?} must be flagged RED.

    ## @purpose — Anti-survivorship: детектор обязан ловить исходный класс бага F-014
    ##            («ключ есть в compose, отсутствует во всех SoT»). Фикстура инлайн
    ##            (tmp_path) — общий код сканера переиспользуется, probe-исключения не нужны.
    ## @io — ⇥ tmp_path compose fixture → ⎋ None (asserts violation detected)
    ## @complexity — O(1)
    ## @scenario — R5/F-014: неизвестный VAR → RED
    """
    # 🧪 TRAP[TEST] · 2026-08-26 · NEGATIVE (R5) · F-014 unknown-var detector
    # · Scenario: fixture с ${UNKNOWN_VAR:?err} (аналог ZAI-прецедента) → violation
    # · Last fail: F-014 — compose-var вне матрицы не детектировался до деплоя на ноду
    # · Remove if: гейт перестаёт быть единственным репо-уровневым детектором класса
    fixture = tmp_path / "docker-compose.yml"
    fixture.write_text(
        "services:\n  dummy:\n    image: scratch\n    environment:\n"
        "      MYSTERY_KEY: ${UNKNOWN_F014_VAR:?must be registered}\n",
        encoding="utf-8",
    )

    violations = _find_violations(_load_sot_registry(), [fixture])
    assert any("UNKNOWN_F014_VAR" in v for v in violations), (
        f"R5 FAIL: detector missed original F-014 class input — violations={violations}"
    )
    assert all("repair" in v for v in violations), "Violation message must carry repair hint"
    print("[IMP:9][gate] R5-negative verified: unknown var flagged with repair hint")
