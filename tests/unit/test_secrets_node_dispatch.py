# 🧪 TRAP[TEST] · REF-0013 · NODE-dispatch + no-silent-glob-fallback
# GREP_SUMMARY: test-secrets-node-dispatch, resolve-enc-path, node-name, alphabetical-first, glob-fallback, ci-mk, secrets-unlock, stray-arg
# STRUCTURE: ▶ resolve_enc_path → ◇ exact path wins → ◇ bare NODE → <dir>/<NODE>.enc.yaml ⎋ | ✗ ⚡FileNotFoundError → ◇ explicit missing path ⚡ (никакой подмены чужой нодой) → ◇ empty → sorted glob → ▶ ci.mk structural check → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for REF-0013 NODE-dispatch in decrypt_secrets.resolve_enc_path +
##           structural check of makefiles/ci.mk secrets-unlock NODE-validation.
##           Исходный баг: `make secrets-unlock NODE=X` передавал имя ноды как позиционный
##           path-аргумент; файл не находился → молча срабатывал glob fallback → расшифровывалась
##           alphabetically-FIRST ЧУЖАЯ нода без предупреждения.
## @scope    Pure unit tests (tmp_path DI для secrets_dir) + текстовая проверка ci.mk.
## @invariants
##   - bare NODE name → <secrets_dir>/<NODE>.enc.yaml при существовании файла
##   - bare NODE без файла → FileNotFoundError С именем ноды в сообщении
##   - явный путь-подобный аргумент без файла → FileNotFoundError БЕЗ glob-подмены (R5-негатив
##     исходного бага: в каталоге есть другой enc-файл — он НЕ должен подставляться)
##   - пустой вход → прежний канон: sorted glob первый match (single-node)
## @rationale Многонодовая операционная ловушка unlock (REF-0013): оператор получал секреты
##            чужой ноды молча.
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.secrets.decrypt_secrets import resolve_enc_path
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _make_secrets_dir(tmp_path: Path) -> Path:
    """Create a secrets dir with two nodes' enc files (alphabetical order matters for the bug)."""
    secrets_dir = tmp_path / "opt" / "node-configs" / "secrets"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "alpha-node.enc.yaml").write_text("a: encrypted\n", encoding="utf-8")
    (secrets_dir / "zeta-node.enc.yaml").write_text("z: encrypted\n", encoding="utf-8")
    return secrets_dir


# region FUNC_test_bare_node_name_dispatches_to_its_file
## @purpose  Bare имя ноды диспетчится в <secrets_dir>/<NODE>.enc.yaml — даже когда в каталоге
##           есть alphabetically-более ранний чужой файл.
## @io       ⇥ tmp_path → ⎋ None (asserts)
@ldd_trajectory
def test_bare_node_name_dispatches_to_its_file(tmp_path: Path) -> None:
    """NODE=zeta-node resolves to zeta-node.enc.yaml, NOT the alphabetically first file."""
    secrets_dir = _make_secrets_dir(tmp_path)

    resolved = resolve_enc_path("zeta-node", secrets_dir=str(secrets_dir))

    assert resolved.endswith("zeta-node.enc.yaml"), f"NODE dispatch broken: got {resolved}"
    logger.info("[IMP:9][test_bare_node_name_dispatches_to_its_file] PASS: zeta-node → zeta-node.enc.yaml")


# endregion FUNC_test_bare_node_name_dispatches_to_its_file


# region FUNC_test_missing_node_name_raises_without_fallback
## @purpose  R5-негатив исходного бага REF-0013: несуществующее имя ноды → FileNotFoundError
##           с именем в сообщении; ЧУЖИЕ enc-файлы из каталога НЕ подставляются.
## @io       ⇥ tmp_path → ⎋ None (asserts)
@ldd_trajectory
def test_missing_node_name_raises_without_fallback(tmp_path: Path) -> None:
    """Missing bare NODE raises instead of silently decrypting another node's file."""
    secrets_dir = _make_secrets_dir(tmp_path)

    with pytest.raises(FileNotFoundError, match="ghost-node"):
        resolve_enc_path("ghost-node", secrets_dir=str(secrets_dir))

    logger.info(
        "[IMP:9][test_missing_node_name_raises_without_fallback] PASS: ghost-node → loud error, no substitution"
    )


# endregion FUNC_test_missing_node_name_raises_without_fallback


# region FUNC_test_explicit_missing_path_never_globs_other_node
## @purpose  Явный путь-подобный аргумент (с '/' или суффиксом .yaml), которого нет на диске,
##           → FileNotFoundError БЕЗ glob-fallback — точный вход исходного бага
##           (alphabetically-first alpha-node.enc.yaml НЕ должен подставляться).
## @io       ⇥ tmp_path → ⎋ None (asserts)
@pytest.mark.parametrize(
    "missing_input",
    ["/nonexistent/path/to/node.enc.yaml", "relative/missing.enc.yaml"],
    ids=["absolute-path", "relative-path"],
)
@ldd_trajectory
def test_explicit_missing_path_never_globs_other_node(tmp_path: Path, missing_input: str) -> None:
    """Explicit missing path is never silently replaced by another node's file."""
    secrets_dir = _make_secrets_dir(tmp_path)

    with pytest.raises(FileNotFoundError):
        resolve_enc_path(missing_input, secrets_dir=str(secrets_dir))

    logger.info("[IMP:9][test_explicit_missing_path_never_globs_other_node] PASS: %s rejected loudly", missing_input)


# endregion FUNC_test_explicit_missing_path_never_globs_other_node


# region FUNC_test_empty_input_glob_and_exact_path_preserved
## @purpose  Обратная совместимость: пустой вход → прежний канон sorted-glob первый;
##           точный существующий путь возвращается как есть (DevPlan 173 W1.3 контракт).
## @io       ⇥ tmp_path → ⎋ None (asserts)
@ldd_trajectory
def test_empty_input_glob_and_exact_path_preserved(tmp_path: Path) -> None:
    """Empty input keeps sorted-glob canon; exact existing path returned unchanged."""
    secrets_dir = _make_secrets_dir(tmp_path)

    resolved_none = resolve_enc_path(None, secrets_dir=str(secrets_dir))
    assert resolved_none.endswith("alpha-node.enc.yaml"), f"Empty input must keep sorted-glob canon: {resolved_none}"

    exact = secrets_dir / "zeta-node.enc.yaml"
    resolved_exact = resolve_enc_path(str(exact), secrets_dir=str(secrets_dir))
    assert resolved_exact == str(exact), f"Exact existing path must be returned as-is: {resolved_exact}"
    logger.info("[IMP:9][test_empty_input_glob_and_exact_path_preserved] PASS: glob canon + exact path intact")


# endregion FUNC_test_empty_input_glob_and_exact_path_preserved


# region FUNC_test_ci_mk_node_validation_structural
## @purpose  Структурный контроль makefiles/ci.mk: таргет secrets-unlock валидирует формат
##           NODE (reject путей/пробелов) и пробрасывает $(NODE) в entrypoint.
## @io       ⇥ None (reads repo makefiles/ci.mk) → ⎋ None (asserts)
@ldd_trajectory
def test_ci_mk_node_validation_structural() -> None:
    """ci.mk secrets-unlock validates NODE format before delegating."""
    ci_mk = Path(__file__).resolve().parent.parent.parent / "makefiles" / "ci.mk"
    content = ci_mk.read_text(encoding="utf-8")

    assert "grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]*$$'" in content, (
        "ci.mk secrets-unlock must validate NODE as a bare node name (no paths/spaces)"
    )
    assert "secrets.sh $(NODE)" in content, "ci.mk must delegate $(NODE) to core/entrypoints/secrets.sh"
    unlock_region = content.split("## secrets-unlock:", 1)[1]
    assert "SECRETS_FILE" in unlock_region, (
        "ci.mk secrets-unlock docs/error hint must point operators at SECRETS_FILE for explicit paths"
    )
    logger.info("[IMP:9][test_ci_mk_node_validation_structural] PASS: ci.mk NODE validation present")


# endregion FUNC_test_ci_mk_node_validation_structural
