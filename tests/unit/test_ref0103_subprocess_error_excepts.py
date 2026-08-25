# GREP_SUMMARY: test-ref0103 subprocess-error except-table timeout-expired non-fatal context-deployer llm-provision
# STRUCTURE: ▶ таблица 5 except-сайтов (карточка REF-0103) → ○ парс n-го except-кортежа в теле якоря → ◇ "subprocess.SubprocessError" ∈ кортеж? → ⎋ 6×assert + IMP:9
# region MODULE_CONTRACT
## @purpose  Except-таблица тест REF-0103: 5 non-fatal except-кортежей (context_deployer ×3 +
##           llm_provision ×2) обязаны содержать subprocess.SubprocessError — базовый класс
##           TimeoutExpired/CalledProcessError. До фикса TimeoutExpired вне кортежей ронял
##           deploy-context после N деплоев вместо WARN (карточка: «crash после N деплоев»).
## @scope    Статический скан исходников (без импорта модулей — freeze-safe); таблица сайтов
##           = авторитетный перечень из карточки REF-0103 / DevPlan meta-refactoring В3.
## @invariants
##   - Каждый сайт идентифицируется (файл, якорь-def, № except в теле) и парсится до закрывающей )
##   - R5: тест красный, если хоть один сайт теряет SubprocessError (регрессия crash-класса)
##   - Test Honesty R1: реальные asserts на свойстве кортежей
## @rationale REF-0103 Tests required: «except-таблица тест SubprocessError».
## @changes 2026-08-25 | REF-0103 — created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

import pytest

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _nth_except_tuple_in_function(source: str, func_def: str, except_index: int) -> str | None:
    """Извлечь except_index-й (с 1) except-кортеж в теле функции до следующего top-level def.

    ## @purpose — Парсер «except (…):» внутри тела якорной функции (статик-скан, freeze-safe).
    """
    start = source.find(func_def)
    if start == -1:
        return None
    body_end = source.find("\ndef ", start + len(func_def))
    body = source[start : body_end if body_end != -1 else len(source)]
    found: str | None = None
    pos = 0
    for _ in range(except_index):
        tuple_idx = body.find("except (", pos)
        if tuple_idx == -1:
            return None
        close_idx = body.find(")", tuple_idx)
        if close_idx == -1:
            return None
        found = body[tuple_idx + len("except (") : close_idx]
        pos = close_idx
    return found


# Таблица 5 сайтов (карточка REF-0103): (файл, якорь-def, № except в теле функции)
_SUBPROCESS_ERROR_SITES: list[tuple[str, str, int]] = [
    ("core/internal/bootstrap/deploy/context_deployer.py", "def _step_vhosts(", 1),
    ("core/internal/bootstrap/deploy/context_deployer.py", "def _step_nginx_reload(", 1),
    ("core/internal/bootstrap/deploy/context_deployer.py", "def _step_verify(", 1),
    ("core/internal/bootstrap/deploy/llm_provision.py", "def render_and_provision_llm(", 1),
    ("core/internal/bootstrap/deploy/llm_provision.py", "def render_and_provision_llm(", 2),
]


@pytest.mark.parametrize(
    ("rel_path", "anchor", "except_index"),
    _SUBPROCESS_ERROR_SITES,
    ids=[
        "context_deployer._step_vhosts",
        "context_deployer._step_nginx_reload",
        "context_deployer._step_verify",
        "llm_provision.render_and_provision_llm.step1-render",
        "llm_provision.render_and_provision_llm.step2-provision",
    ],
)
def test_subprocess_error_in_except_tuple(rel_path: str, anchor: str, except_index: int) -> None:
    """R5/REF-0103: except-кортеж сайта содержит subprocess.SubprocessError (ловит TimeoutExpired)."""
    source = (_PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
    tuple_body = _nth_except_tuple_in_function(source, anchor, except_index)

    assert tuple_body is not None, f"{rel_path}:{anchor}#except{except_index} — except-кортеж не найден"
    assert "subprocess.SubprocessError" in tuple_body, (
        f"REF-0103 FAIL: {rel_path}:{anchor}#except{except_index} ({tuple_body}) без "
        f"subprocess.SubprocessError — TimeoutExpired снова роняет поток вместо non-fatal WARN"
    )
    logger.critical(
        "[IMP:9][test][REF-0103] %s:%s#except%d — SubprocessError OK (%s)",
        rel_path,
        anchor,
        except_index,
        tuple_body.strip()[:60],
    )


def test_except_table_covers_exactly_five_sites() -> None:
    """Таблица сайтов = ровно 5 позиций карточки REF-0103 (не расползается молча)."""
    assert len(_SUBPROCESS_ERROR_SITES) == 5, (
        "Карточка REF-0103 перечисляет РОВНО 5 except-сайтов; изменение таблицы — синхронная правка карточки/DevPlan"
    )
    logger.critical("[IMP:9][test][REF-0103] except-table size=5 OK")
