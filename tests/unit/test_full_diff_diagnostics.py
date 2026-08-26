# GREP_SUMMARY: full-diff-diagnostics check-generated divergence-beyond-line20 AI-0063 generators migration static-source
# STRUCTURE: ▶ synthetic drift >20 строк → ◇ check_generated печатает ПОЛНЫЙ diff → ⎋ последняя строка видна │ ▶ source-scan 7 сайтов → нет diff_lines[:20]
# region MODULE_CONTRACT
## @purpose  AI-0063 (DevPlan 17 T2.3): единый хелпер generated_check.check_generated —
##           расхождение >20 строк печатается ЦЕЛИКОМ во всех сайтах генераторов;
##           срезы diff_lines[:20] устранены.
## @scope    tests/unit: поведенческий тест хелпера + статический скан сайтов; без subprocess.
## @invariants
##   - Дрейф 30+ строк → stderr содержит первую И последнюю строку diff
##   - Ни один сайт генераторов не режет diff до 20 строк
# endregion MODULE_CONTRACT

import io
import logging
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from core.internal.scripts.generated_check import check_generated

logger = logging.getLogger(__name__)

# 7 сайтов-потребителей канона (файл, минимальное число вызовов check_generated)
_SITES: list[tuple[str, int]] = [
    ("core/internal/scripts/generate_entrypoint_manifest.py", 1),
    ("core/internal/scripts/generate_platform_env.py", 1),
    ("core/internal/scripts/generate_secrets_manifest.py", 1),
    ("core/internal/scripts/sync_env_defaults.py", 1),
    ("core/internal/scripts/sync_requirements.py", 1),
    ("core/internal/scripts/generate_agents_md.py", 2),
]


def _drifted_content(base_line: str, n_lines: int) -> str:
    """Контент из n_lines строк — гарантированный дрейф >20 строк."""
    return "".join(f"{base_line} variant {i}\n" for i in range(n_lines))


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · дрейф >20 строк печатается полностью (AI-0063)
# · Regression: 6 из 7 генераторов резали unified_diff до diff_lines[:20] — источник
#   divergence в RED check-manifests был невидим (P-14 применён только в entrypoint)
# · Scenario: файл с 5 строками vs generated 35 строк → rc=1, stderr содержит
#   и первую variant-строку, и последнюю (полный diff); match → rc=0 без вывода
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0063)
# · Remove if: diff-диагностика переезжает в отдельный CI-инструмент с полной печатью по умолчанию
def test_divergence_shown_beyond_line20(tmp_path: Path) -> None:
    """Полная печать диффа при дрейфе >20 строк + чистый rc=0 на совпадении."""
    target = tmp_path / "artifact.yaml"
    target.write_text(_drifted_content("stale", 5), encoding="utf-8")
    generated = _drifted_content("fresh", 35)

    err = io.StringIO()
    with redirect_stderr(err):
        rc = check_generated(target, generated)

    assert rc == 1, "дрейф обязан давать rc=1"
    captured = err.getvalue()
    assert "variant 34" in captured, "ПОСЛЕДНЯЯ строка diff обязана присутствовать (>20 строк)"
    assert "variant 0" in captured, "первая строка diff обязана присутствовать"
    assert "truncated" not in captured.lower(), "обрезка запрещена (P-14)"
    logger.info("[IMP:8][test] full diff shown for %d-line divergence", 35)

    # match → rc=0, stderr чистый (файл обновляется до сгенерированного контента)
    target.write_text(generated, encoding="utf-8")
    err2 = io.StringIO()
    with redirect_stderr(err2):
        assert check_generated(target, generated) == 0
    assert not err2.getvalue(), "match не должен печатать diff"

    logger.critical("[IMP:9][test] divergence beyond line20 fully shown — OK (AI-0063)")


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · все сайты генераторов делегируют в канон
# · Regression: миграция 7 сайтов на generated_check.check_generated неполная →
#   локальные [:20]-срезы возвращаются при следующем рефакторинге
# · Scenario: source-scan каждого файла: есть вызов check_generated, нет 'diff_lines[:20]'
# · Last fail: охранник миграции T2.3 (DevPlan 17)
# · Remove if: список сайтов вынесен в манифест генераторов
@pytest.mark.parametrize("site,min_calls", _SITES)
def test_generator_sites_delegate_to_canon(site: str, min_calls: int) -> None:
    src = Path(site).read_text(encoding="utf-8")
    assert "check_generated(" in src, f"{site} обязан вызывать канон check_generated"
    assert src.count("check_generated(") - src.count("def check_generated") >= min_calls, (
        f"{site}: ожидается >= {min_calls} вызов(ов) канона"
    )
    assert "diff_lines[:20]" not in src, f"{site}: срез diff_lines[:20] запрещён (AI-0063)"
