# GREP_SUMMARY: test-build-cache content-hash sha256 dockerfile dockerignore cache-hit cache-miss invalidation keys race-free
# STRUCTURE: fixtures(tmp_path module factory) → ◇ compute_source_hash (детерминизм, missing Dockerfile → "", .dockerignore, always-exclude) → ◇ check_build_needed (no cache → True, hit → False, miss → True, corrupt → True) → ◇ save_build_hash (cache dir create, key per module) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for bootstrap/deploy/build_cache.py (DevPlan 139 W4.3 — закрытие blind spot
##            build_cache, 280 LOC, НОВЫЙ). Покрывает cache hit/miss, инвалидацию, ключи
##            (module_name.hash) и xdist-безопасность (tmp_path-изоляция, отсутствие гонок).
## @scope    compute_source_hash (детерминизм, missing Dockerfile → "", .dockerignore, _ALWAYS_EXCLUDE),
##           check_build_needed (no cache/hit/miss/corrupt), save_build_hash (создание cache_dir,
##           имя ключа по basename module_dir), изоляция ключей между модулями.
## @invariants
##   - SHA256 детерминирован: одинаковые файлы → одинаковый хеш; порядок walk не влияет (sort)
##   - .dockerignore уважается; _ALWAYS_EXCLUDE (*.md, .git, __pycache__...) исключаются
##   - Missing Dockerfile → "" (build needed); missing cache → build needed; corrupt cache → build needed
##   - Permission errors на cache write → fail-open (build proceeds)
##   - tmp_path-изоляция каждого теста (xdist: 0 общих ресурсов, 0 гонок)
##   - Test Honesty R1-R5: negative-тесты (missing Dockerfile, no cache, corrupt cache, .dockerignore-исключение)
##   - LDD: каждый тест — IMP:9-траектория (ldd_trajectory)
## @rationale W4 (139): 280 LOC production без тестов — content-hash skip в deploy_docker_module
##            (status-page, backup-cron). Инварианты MODULE_CONTRACT build_cache — в исполняемые проверки.
## @changes  2026-08-05 | Created (DevPlan 139 W4.3)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.deploy.build_cache import (
    _load_dockerignore,
    _should_include,
    check_build_needed,
    compute_source_hash,
    save_build_hash,
)
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region FUNC__make_module
## @purpose  Создать tmp module dir с Dockerfile и произвольными файлами.
## @io       ⇥ tmp_path, name: str, files: dict[str, str], dockerignore: str | None → ⎋ Path
## @complexity O(N) где N = файлы
def _make_module(
    tmp_path: Path,
    name: str = "status-page",
    files: dict[str, str] | None = None,
    dockerignore: str | None = None,
) -> Path:
    """Create a module dir with Dockerfile + files (+ optional .dockerignore) under tmp_path."""
    module_dir = tmp_path / name
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "Dockerfile").write_text("FROM nginx:alpine\n")
    for rel, content in (files or {}).items():
        p = module_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    if dockerignore is not None:
        (module_dir / ".dockerignore").write_text(dockerignore)
    return module_dir


# endregion FUNC__make_module


# ═══════════════════════════════════════════════════════════════════════════
# compute_source_hash
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_compute_hash_deterministic
## @purpose  SHA256 детерминирован: два вызова на одном дереве → одинаковый хеш (64 hex).
# 🧪 TRAP[TEST] · compute_hash_deterministic · Contract · Regression: хеш нестабилен между запусками
# · Scenario: module dir с Dockerfile+app.py → hash1 == hash2; len == 64; hex
# · Last fail: N/A (новый тест W4.3)
# · Remove if: алгоритм хеширования меняется
@ldd_trajectory
def test_compute_hash_deterministic(tmp_path, caplog) -> None:
    """Одинаковое дерево → одинаковый SHA256 (детерминизм)."""
    module_dir = _make_module(tmp_path, files={"app.py": "print('x')\n", "conf/nginx.conf": "server {};\n"})

    h1 = compute_source_hash(str(module_dir))
    h2 = compute_source_hash(str(module_dir))

    assert h1 == h2, "Детерминизм: повторный вызов даёт тот же хеш"
    assert len(h1) == 64, f"SHA256 hex = 64 символа, got {len(h1)}"
    int(h1, 16), "Хеш обязан быть hex"
    logger.info("[IMP:9][test] compute_source_hash: детерминизм OK (hash=%s...) ✓", h1[:12])


# endregion FUNC_test_compute_hash_deterministic


# region FUNC_test_compute_hash_changes_with_content
## @purpose  Изменение содержимого файла → другой хеш (основа инвалидации кэша).
# 🧪 TRAP[TEST] · compute_hash_changes_with_content · Contract · Regression: смена контента не меняет хеш
# · Scenario: файл "v1" → hash A; файл "v2" → hash B; A != B
# · Last fail: N/A (новый тест W4.3)
# · Remove if: содержимое перестаёт влиять на хеш
@ldd_trajectory
def test_compute_hash_changes_with_content(tmp_path, caplog) -> None:
    """Изменение контента файла → другой хеш."""
    module_dir = _make_module(tmp_path, files={"app.py": "v1"})
    h1 = compute_source_hash(str(module_dir))
    (module_dir / "app.py").write_text("v2")
    h2 = compute_source_hash(str(module_dir))
    assert h1 != h2, "Изменение контента обязано менять хеш"
    logger.info("[IMP:9][test] compute_source_hash: контент меняет хеш (инвалидация работает) ✓")


# endregion FUNC_test_compute_hash_changes_with_content


# region FUNC_test_compute_hash_missing_dockerfile_empty
## @purpose  Missing Dockerfile → пустой хеш "" (build needed).
# 🧪 TRAP[TEST] · compute_hash_missing_dockerfile · NEGATIVE (R5) · Regression: отсутствие Dockerfile даёт «ложный» хеш
# · Scenario: dir без Dockerfile → "" (не hex, не None) → check_build_needed True
# · Last fail: N/A (новый negative-тест W4.3)
# · Remove if: семантика «missing Dockerfile → build needed» меняется
@ldd_trajectory
def test_compute_hash_missing_dockerfile_empty(tmp_path, caplog) -> None:
    """Без Dockerfile → пустой хеш (build needed)."""
    module_dir = tmp_path / "no-dockerfile"
    module_dir.mkdir()
    (module_dir / "app.py").write_text("x")

    h = compute_source_hash(str(module_dir))
    assert not h, "Без Dockerfile хеш пустой"
    logger.info("[IMP:9][test] compute_source_hash: missing Dockerfile → '' (build needed) ✓")


# endregion FUNC_test_compute_hash_missing_dockerfile_empty


# region FUNC_test_compute_hash_respects_dockerignore
## @purpose  .dockerignore исключает файлы из хеша: дерево с secret.txt и без него → одинаковый хеш.
# 🧪 TRAP[TEST] · compute_hash_respects_dockerignore · Contract · Regression: .dockerignore игнорируется
# · Scenario: .dockerignore="secret.txt"; dir A с secret.txt, dir B без → hA == hB
# · Last fail: N/A (новый тест W4.3)
# · Remove if: обработка .dockerignore меняется
@ldd_trajectory
def test_compute_hash_respects_dockerignore(tmp_path, caplog) -> None:
    """.dockerignore-паттерн исключает файл из хеша."""
    common = {"app.py": "same"}
    dir_a = _make_module(tmp_path, name="mod-a", files={**common, "secret.txt": "S3CRET"}, dockerignore="secret.txt")
    dir_b = _make_module(tmp_path, name="mod-b", files=common, dockerignore="secret.txt")

    h_a = compute_source_hash(str(dir_a))
    h_b = compute_source_hash(str(dir_b))

    assert h_a == h_b, ".dockerignore-исключённый файл не должен влиять на хеш"
    logger.info("[IMP:9][test] compute_source_hash: .dockerignore исключает secret.txt ✓")


# endregion FUNC_test_compute_hash_respects_dockerignore


# region FUNC_test_compute_hash_always_excludes_docs
## @purpose  _ALWAYS_EXCLUDE: *.md и .git/ исключаются всегда (README.md не влияет на хеш).
# 🧪 TRAP[TEST] · compute_hash_always_excludes_docs · Contract (_ALWAYS_EXCLUDE) · Regression: docs меняют хеш
# · Scenario: dir A с README.md, dir B без → hA == hB (при идентичных прочих файлах)
# · Last fail: N/A (новый тест W4.3)
# · Remove if: _ALWAYS_EXCLUDE набор меняется
@ldd_trajectory
def test_compute_hash_always_excludes_docs(tmp_path, caplog) -> None:
    """*.md и .git всегда исключаются из хеша."""
    common = {"app.py": "same"}
    dir_a = _make_module(tmp_path, name="mod-a", files={**common, "README.md": "docs"})
    dir_b = _make_module(tmp_path, name="mod-b", files=common)
    # .git dir только в dir_a (исключается по _ALWAYS_EXCLUDE)
    (dir_a / ".git").mkdir(parents=True)
    (dir_a / ".git" / "config").write_text("[core]\n")

    h_a = compute_source_hash(str(dir_a))
    h_b = compute_source_hash(str(dir_b))

    assert h_a == h_b, "README.md/.git не должны влиять на хеш (_ALWAYS_EXCLUDE)"
    logger.info("[IMP:9][test] compute_source_hash: *.md/.git исключены (_ALWAYS_EXCLUDE) ✓")


# endregion FUNC_test_compute_hash_always_excludes_docs


# region FUNC_test_should_include_helpers
## @purpose  _should_include: явные include/exclude решения (always-exclude + user-pattern + dir-prefix).
# 🧪 TRAP[TEST] · should_include_helpers · Contract · Regression: фильтр include ломается
# · Scenario: ".env"→False; "app.py"→True; ignore={"data/"}; "data/x"→False; "datax"→True
# · Last fail: N/A (новый тест W4.3)
# · Remove if: логика _should_include меняется
@ldd_trajectory
def test_should_include_helpers(caplog) -> None:
    """_should_include: always-exclude, user-pattern, dir-prefix."""
    assert _should_include(".env", set()) is False, ".env — always exclude"
    assert _should_include("app.py", set()) is True, "обычный файл включается"
    assert _should_include("data/x", {"data/"}) is False, "dir-prefix паттерн исключает"
    assert _should_include("datax", {"data/"}) is True, "не-подпуть не исключается"
    logger.info("[IMP:9][test] _should_include: always-exclude + user-pattern + dir-prefix ✓")


# endregion FUNC_test_should_include_helpers


# region FUNC_test_load_dockerignore
## @purpose  _load_dockerignore: паттерны загружаются, комментарии/пустые строки отбрасываются,
##            отсутствующий файл → пустое множество.
# 🧪 TRAP[TEST] · load_dockerignore · Contract · Regression: парсинг .dockerignore ломается
# · Scenario: .dockerignore="# c\n\n*.log\nsecret/" → {"*.log", "secret/"}; без файла → set()
# · Last fail: N/A (новый тест W4.3)
# · Remove if: формат .dockerignore меняется
@ldd_trajectory
def test_load_dockerignore(tmp_path, caplog) -> None:
    """.dockerignore: паттерны загружены, комментарии/пустые строки отброшены."""
    module_dir = _make_module(tmp_path, dockerignore="# comment\n\n*.log\nsecret/")

    patterns = _load_dockerignore(str(module_dir))

    assert "*.log" in patterns and "secret/" in patterns
    assert "# comment" not in patterns, "Комментарии не паттерны"
    assert "" not in patterns

    empty = _load_dockerignore(str(tmp_path / "no-ignore"))
    assert empty == set(), "Отсутствующий .dockerignore → пустое множество"
    logger.info("[IMP:9][test] _load_dockerignore: паттерны загружены, комментарии отброшены ✓")


# endregion FUNC_test_load_dockerignore


# ═══════════════════════════════════════════════════════════════════════════
# check_build_needed / save_build_hash — cache lifecycle
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_check_build_needed_no_cache
## @purpose  Нет кэш-файла → build needed (True).
# 🧪 TRAP[TEST] · check_build_needed_no_cache · NEGATIVE (R5) · Regression: отсутствие кэша трактуется как skip
# · Scenario: свежий cache_dir → check → True
# · Last fail: N/A (новый negative-тест W4.3)
# · Remove if: семантика «no cache → build» меняется
@ldd_trajectory
def test_check_build_needed_no_cache(tmp_path, caplog) -> None:
    """Нет кэш-файла → build needed (True)."""
    module_dir = _make_module(tmp_path)
    cache_dir = tmp_path / "cache"

    needed = check_build_needed(str(module_dir), cache_dir=str(cache_dir))

    assert needed is True, "Без кэша → build needed"
    logger.info("[IMP:9][test] check_build_needed: no cache → True (build needed) ✓")


# endregion FUNC_test_check_build_needed_no_cache


# region FUNC_test_check_build_needed_cache_hit_skip
## @purpose  save → check: хеш совпадает → build SKIP (False).
# 🧪 TRAP[TEST] · check_build_needed_cache_hit_skip · Contract · Regression: hit не пропускает build
# · Scenario: save_build_hash → check_build_needed → False (skip); IMP:9 "Build skipped"
# · Last fail: N/A (новый тест W4.3)
# · Remove if: семантика cache-hit меняется
@ldd_trajectory
def test_check_build_needed_cache_hit_skip(tmp_path, caplog) -> None:
    """Сохранённый хеш == текущий → skip (False)."""
    module_dir = _make_module(tmp_path, name="status-page")
    cache_dir = tmp_path / "cache"
    h = compute_source_hash(str(module_dir))
    save_build_hash(str(module_dir), h, cache_dir=str(cache_dir))

    needed = check_build_needed(str(module_dir), cache_dir=str(cache_dir))

    assert needed is False, "Cache hit → build skip"
    skip_logs = [r.message for r in caplog.records if "Build skipped" in r.message]
    assert skip_logs, "Ожидался IMP:9 'Build skipped'"
    logger.info("[IMP:9][test] check_build_needed: cache hit → skip (False) ✓")


# endregion FUNC_test_check_build_needed_cache_hit_skip


# region FUNC_test_check_build_needed_invalidation_on_change
## @purpose  Инвалидация: после save изменён исходник → build needed (True).
# 🧪 TRAP[TEST] · check_build_needed_invalidation · Contract · Regression: изменение исходников не инвалидирует кэш
# · Scenario: save → модификация app.py → check → True (rebuild)
# · Last fail: N/A (новый тест W4.3)
# · Remove if: инвалидация кэша меняется
@ldd_trajectory
def test_check_build_needed_invalidation_on_change(tmp_path, caplog) -> None:
    """Изменение исходников после save → build needed (True)."""
    module_dir = _make_module(tmp_path, name="backup-cron", files={"app.py": "v1"})
    cache_dir = tmp_path / "cache"
    h = compute_source_hash(str(module_dir))
    save_build_hash(str(module_dir), h, cache_dir=str(cache_dir))

    (module_dir / "app.py").write_text("v2-changed")
    needed = check_build_needed(str(module_dir), cache_dir=str(cache_dir))

    assert needed is True, "Изменённые исходники → rebuild"
    # W5 T5.4: level-agnostic content check (не привязан к IMP:8 flow-уровню)
    changed_logs = [r.message for r in caplog.records if "Hash changed" in r.message]
    assert changed_logs, "Ожидался лог 'Hash changed' (rebuild-причина)"
    logger.info("[IMP:9][test] check_build_needed: инвалидация по изменению исходников → True ✓")


# endregion FUNC_test_check_build_needed_invalidation_on_change


# region FUNC_test_check_build_needed_corrupt_cache
## @purpose  Коррумпированный кэш (пустой/мусорный хеш) → build needed (True, fail-open).
# 🧪 TRAP[TEST] · check_build_needed_corrupt_cache · NEGATIVE (R5) · Regression: corrupt cache трактуется как skip
# · Scenario: cache-файл содержит "garbage" → check → True
# · Last fail: N/A (новый negative-тест W4.3)
# · Remove if: семантика corrupt-cache меняется
@ldd_trajectory
def test_check_build_needed_corrupt_cache(tmp_path, caplog) -> None:
    """Мусорный кэш-файл → build needed (True)."""
    module_dir = _make_module(tmp_path, name="status-page")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "status-page.hash").write_text("garbage-not-a-hash")

    needed = check_build_needed(str(module_dir), cache_dir=str(cache_dir))

    assert needed is True, "Коррумпированный кэш → build needed"
    logger.info("[IMP:9][test] check_build_needed: corrupt cache → True (fail-open) ✓")


# endregion FUNC_test_check_build_needed_corrupt_cache


# region FUNC_test_save_build_hash_creates_cache_dir
## @purpose  save_build_hash создаёт cache_dir (вложенный путь) и пишет хеш + "\n".
# 🧪 TRAP[TEST] · save_build_hash_creates_cache_dir · Contract · Regression: cache dir не создаётся
# · Scenario: cache_dir = tmp/a/b/c (не существует) → save → файл `module`.hash содержит hash+"\n"
# · Last fail: N/A (новый тест W4.3)
# · Remove if: поведение создания cache_dir меняется
@ldd_trajectory
def test_save_build_hash_creates_cache_dir(tmp_path, caplog) -> None:
    """save_build_hash создаёт вложенный cache_dir и пишет hash+\"\\n\"."""
    module_dir = _make_module(tmp_path, name="status-page")
    cache_dir = tmp_path / "deep" / "nested" / "cache"

    save_build_hash(str(module_dir), "abc123", cache_dir=str(cache_dir))

    cache_file = cache_dir / "status-page.hash"
    assert cache_file.is_file(), "Кэш-файл создан"
    assert cache_file.read_text() == "abc123\n", "Хеш записан с новой строкой"
    logger.info("[IMP:9][test] save_build_hash: cache_dir создан, hash записан ✓")


# endregion FUNC_test_save_build_hash_creates_cache_dir


# region FUNC_test_cache_keys_isolated_per_module
## @purpose  Ключи изолированы по module_name: два модуля в одном cache_dir не влияют друг на друга
##            (отсутствие гонок/коллизий — xdist-безопасность).
# 🧪 TRAP[TEST] · cache_keys_isolated_per_module · Contract (keys) · Regression: ключи модулей коллизируют
# · Scenario: модуль A save; check(A) → skip; check(B) в том же cache_dir → True (B не имеет кэша);
# ·   файлы .hash разные (status-page.hash, backup-cron.hash)
# · Last fail: N/A (новый тест W4.3)
# · Remove if: формат ключа кэша меняется
@ldd_trajectory
def test_cache_keys_isolated_per_module(tmp_path, caplog) -> None:
    """Ключи по module_name: два модуля в одном cache_dir не пересекаются."""
    mod_a = _make_module(tmp_path, name="status-page")
    mod_b = _make_module(tmp_path, name="backup-cron")
    cache_dir = tmp_path / "cache"
    h_a = compute_source_hash(str(mod_a))
    save_build_hash(str(mod_a), h_a, cache_dir=str(cache_dir))

    assert check_build_needed(str(mod_a), cache_dir=str(cache_dir)) is False, "A: hit → skip"
    assert check_build_needed(str(mod_b), cache_dir=str(cache_dir)) is True, "B: нет своего кэша → build"

    files = sorted(p.name for p in cache_dir.iterdir())
    assert files == ["status-page.hash"], f"Кэш-ключи по module_name, got {files}"
    logger.info("[IMP:9][test] save_build_hash: ключи изолированы по module_name (0 гонок) ✓")


# endregion FUNC_test_cache_keys_isolated_per_module
