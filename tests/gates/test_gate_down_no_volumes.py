# GREP_SUMMARY: gate down-no-volumes down-volumes modules.mk -v data-loss modules lifecycle
# STRUCTURE: ▶ parse makefiles/modules.mk down: recipe → ◇ 'down:' line без '-v' → ⊕ 'down-volumes:' line с '-v' → ⎋ pass|fail (R5 negative inline fixture)
# region MODULE_CONTRACT
## @purpose  Parity gate (DevPlan 122 T1, P-1 HIGH): `make down` — НЕ деструктивный
##           (docker compose down БЕЗ -v, данные сохраняются); деструктивный снос — только
##           явный `make down-volumes` (down -v). Фиксирует канон безопасности P-1:
##           «docs обещают без -v, код выполнял down -v» — потеря volumes при доверии докам.
## @scope    Read-only gate. Парсит makefiles/modules.mk рецепты down/down-volumes.
## @invariants
##   - Рецепт `down:` НЕ содержит `-v`
##   - Таргет `down-volumes:` СУЩЕСТВУЕТ и содержит `-v`
##   - R5 negative: inline-фикстура `down:` = `down -v` → RED (исходный вход P-1)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale P-1 (Problem Registry 121): modules.mk:47 `down -v` vs доки «docker compose down».
##            Эталон безопасности — scaffold.mk:8 remove-project (compose down без -v).
## @changes 2026-08-03 | Created (DevPlan 122 T1)
# endregion MODULE_CONTRACT

import re

import pytest

from tests.helpers.gate_helpers import repo_root

ROOT = repo_root()
MODULES_MK = ROOT / "makefiles" / "modules.mk"


def _read_modules_mk() -> str:
    """Read modules.mk content."""
    return MODULES_MK.read_text()


def _extract_recipe(text: str, target: str) -> str | None:
    """Extract the recipe lines for a make target.

    ## @purpose — Find `target:` block and return subsequent indented recipe lines.
    ## @io — text : str, target : str → ⎋ str | None (recipe or None if target missing)
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^{target}:", line.strip()) or line.strip() == f"{target}:":
            recipe: list[str] = []
            for rl in lines[i + 1 :]:
                if rl.startswith(("\t", "  ", "    ")):
                    recipe.append(rl)
                elif not rl.strip():
                    continue
                else:
                    break
            return "\n".join(recipe)
    return None


@pytest.mark.gate
class TestGateDownNoVolumes:
    """Gate: `make down` preserves volumes; destructive teardown requires `down-volumes` (P-1)."""

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · down -v data-loss (DevPlan 122 T1, P-1)
    # · Last fail: modules.mk:47 `docker compose ... down -v` — комментарий «remove volumes»
    # ·   при доках «docker compose down» (AGENTS.md:69, manifest:561)
    # · Remove if: down-семантика канонизируется иначе
    def test_down_recipe_has_no_volumes_flag(self):
        """`down:` recipe must NOT contain -v (data preserved)."""
        text = _read_modules_mk()
        recipe = _extract_recipe(text, "down")
        assert recipe is not None, "GATE_DOWN_NO_VOLUMES: `down:` target not found in makefiles/modules.mk"
        assert "-v" not in recipe, (
            "GATE_DOWN_NO_VOLUMES: `make down` recipe contains `-v` — data loss on documented "
            "non-destructive command (P-1). Destructive teardown belongs to `down-volumes` only."
        )

    # 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · down-volumes exists with -v (DevPlan 122 T1)
    # · Last fail: no explicit destructive target existed
    # · Remove if: down-volumes renamed
    def test_down_volumes_target_exists_with_flag(self):
        """`down-volumes:` target must exist and contain -v (explicit destructive)."""
        text = _read_modules_mk()
        recipe = _extract_recipe(text, "down-volumes")
        assert recipe is not None, (
            "GATE_DOWN_NO_VOLUMES: `down-volumes:` target not found — no explicit destructive teardown path (P-1)"
        )
        assert "-v" in recipe, "GATE_DOWN_NO_VOLUMES: `down-volumes:` recipe must contain `-v`"

    # 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · исходный вход P-1 (DevPlan 122 T1)
    # · Last fail: modules.mk:47 `down -v` (реальный вход, поймавший P-1)
    # · Remove if: down-семантика канонизируется иначе
    def test_down_with_volumes_negative(self):
        """R5 negative: inline-фикстура `down:` = `down -v` → RED (детектор ловит P-1)."""
        inline_modules_mk = """
down:
	@docker compose -f docker-compose.yml down -v
	@echo "stopped"
"""
        recipe = _extract_recipe(inline_modules_mk, "down")
        assert recipe is not None, "Inline fixture must contain a down: recipe"
        assert "-v" in recipe, "R5 FAIL: inline fixture must reproduce original bug input (down -v)"
