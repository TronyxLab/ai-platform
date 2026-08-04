# GREP_SUMMARY: reload-safe, importlib-reload, sys.modules, test-canon, reload-race, monkeypatch-globals, xdist
# STRUCTURE: ┌reload_module(name)┐ → ◇ importlib.import_module → ◇ assert expected_file → ⊕ importlib.reload (тот же объект) → ⎋ ModuleType || ┌reload_guard(env_patch)┐ → ○ env → reload → ⎋ restore
# region MODULE_CONTRACT
## @purpose  Единый канон reload-безопасности для тестов (DevPlan 129 W4): модули НЕ удаляются
##           из sys.modules; при необходимости перечитать module-level константы — importlib.reload
##           того же объекта модуля + патч ПОСЛЕ reload. Устраняет reload-гонку monkeypatch/sys.modules:
##           `del sys.modules[name]` создаёт НОВЫЙ объект модуля при следующем импорте, а существующие
##           ссылки (__globals__ других модулей, захваченные классы в _deliver/_receive) остаются на
##           СТАРЫЙ объект → monkeypatch патчит новый, вызов идёт по старому → реальный SSH/Docker
##           поллинг вместо заглушки (флейк test_deploy_mk_chain.py:128, зависание 1276.8s
##           test_orchestrator_receive_version.py — test-env-leak-and-flakes.md Rev 2026-08-09).
## @scope    Все тест-хелперы, которым нужен reload модуля с новым env (test_status_page,
##           test_platform_export_metrics, unit/*, интеграционные). НЕ содержит тестов (пакет _conftest).
## @invariants
##   - НИКОГДА не удаляет модули из sys.modules (del sys.modules / sys.modules.pop — запрещены)
##   - reload_module(): importlib.import_module → проверка __file__ → importlib.reload того же объекта
##   - reload_guard(): контекст-менеджер для env-зависимого reload (env patch → reload → auto-restore)
##   - Патчи (monkeypatch.setattr) применяются ТОЛЬКО ПОСЛЕ reload — к актуальному объекту модуля
##   - Фальсифицируемость: reload_module с несовпадающим __file__ падает с понятным сообщением
## @rationale DevPlan 129 W4 (AC-5): reload-гонка monkeypatch/sys.modules исследована и устранена —
##            канон «НЕ удалять модули из sys.modules; importlib.reload + патч ПОСЛЕ» задокументирован
##            в едином хелпере. Тест-хелперы не дублируют паттерн (DRY); следующий агент видит канон.
## @changes 2026-08-04 · Created (DevPlan 129 W4, test-env-leak-and-flakes.md Rev 2026-08-09)
# endregion MODULE_CONTRACT

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

# ⚠️ Канон (DevPlan 129 W4): удаление модуля из sys.modules запрещено в тест-хелперах.
# · del sys.modules[name] → следующий import создаёт НОВЫЙ объект; старые ссылки
# · (__globals__ других модулей) остаются на старый → monkeypatch-заглушка не применяется.
_RELOAD_UNSAFE = ("del sys.modules", "sys.modules.pop")


def reload_module(module_name: str, expected_file_substring: str | None = None) -> ModuleType:
    """Перечитать модуль через importlib.reload (тот же объект) — канон reload-безопасности.

    ▶ ┌module_name┐ → ◇ importlib.import_module → ◇ __file__ проверка → ⊕ importlib.reload → ⎋ ModuleType
    ## @purpose — Перезагрузка модуля для подхвата новых module-level констант (env) БЕЗ удаления
    ##            из sys.modules. importlib.reload обновляет ТОТ ЖЕ объект — существующие ссылки
    ##            в __globals__ других модулей остаются валидными (нет reload-гонки).
    ## @io — ⇥ module_name: str, expected_file_substring: str|None (подстрока __file__ — защита от
    ##       reload не того модуля при кэшировании одноимённого имени из другого пути)
    ##       → ⎋ ModuleType (перезагруженный модуль)
    ## @complexity O(1) — import + reload
    ## @invariants
    ##   - Модуль НЕ удаляется из sys.modules (канон W4)
    ##   - expected_file_substring задан и не совпал с __file__ → ValueError (fail-fast, R1: не тихий pass)
    ##   - reload возвращает тот же объект (идентичность sys.modules[name] до/после)
    """
    module = importlib.import_module(module_name)
    file_path = getattr(module, "__file__", None) or ""
    if expected_file_substring and expected_file_substring not in file_path:
        raise ValueError(
            f"[IMP:10][reload_safe] Module '{module_name}' loaded from unexpected path: {file_path!r} — "
            f"expected file containing {expected_file_substring!r}. reload_module не перезагрузит чужой "
            f"модуль (канон DevPlan 129 W4). Проверьте sys.path/кэш одноимённого модуля."
        )
    reloaded = importlib.reload(module)
    # Идентичность объекта — гарантия, что старые ссылки (__globals__) остались валидными
    assert sys.modules.get(module_name) is reloaded, (
        f"[IMP:10][reload_safe] reload изменил объект в sys.modules — канон W4 нарушен: {module_name}"
    )
    return reloaded


class reload_guard:
    """Контекст-менеджер: env-patch → reload → автоматический restore env (finally-стиль).

    ## @purpose — Паттерн test_status_page._setup_app_env / test_platform_export_metrics:
    ##            env-переменные выставляются ТОЛЬКО на время reload (module-level чтения),
    ##            после reload восстанавливаются (snapshot/restore) — без ручного finally.
    ## @io — ⇥ env_updates: dict[str, str|None] (None = удалить ключ) → ⎛ (контекст-менеджер)
    ## @complexity O(1)
    ## @invariants
    ##   - Env восстанавливается в finally (независимо от исключения в теле)
    ##   - Не вызывает reload сам — тело контекста делает reload_module (патч ПОСЛЕ reload)
    ##   - Безопасен для вложенности (snapshot/restore парный)
    """

    def __init__(self, env_updates: dict[str, str | None]) -> None:
        self._updates = env_updates
        self._saved: dict[str, str | None] = {}

    def __enter__(self) -> "reload_guard":
        import os

        self._os = os
        for key in self._updates:
            self._saved[key] = os.environ.get(key)
        for key, value in self._updates.items():
            if value is None:
                self._os.environ.pop(key, None)
            else:
                self._os.environ[key] = value
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:  # type: ignore[no-untyped-def]
        for key, value in self._saved.items():
            if value is None:
                self._os.environ.pop(key, None)
            else:
                self._os.environ[key] = value
        return False  # не глотать исключения (CONSTITUTION-4: все ошибки видимы)


# region FUNC_assert_no_sys_modules_deletion
def assert_no_sys_modules_deletion(source: str, module_name: str) -> None:
    """Guard: тест-хелпер не должен удалять модуль из sys.modules (канон W4).

    ▶ ┌source code + module_name┐ → ◇ grep del sys.modules → ⊕ raise AssertionError → ⎋ None
    ## @purpose — Fail-fast детектор нарушения канона reload-безопасности. Если в тест-хелпере
    ##            появится del sys.modules[module_name] — тест падает с понятным сообщением
    ##            (анти-регрессия, R5: негативный детектор для reload-гонки).
    ## @io — ⇥ source: str (исходник хелпера), module_name: str → ⎋ None (raise при нарушении)
    ## @complexity O(N) — сканирование строк
    """
    for lineno, line in enumerate(source.splitlines(), 1):
        if "del sys.modules" in line or "sys.modules.pop" in line:
            raise AssertionError(
                f"[IMP:10][reload_safe] {module_name}: строка {lineno}: `{line.strip()}` — удаление модуля "
                f"из sys.modules запрещено каноном DevPlan 129 W4 (reload-гонка monkeypatch/globals). "
                f"Используйте reload_module() — importlib.reload того же объекта."
            )


# endregion FUNC_assert_no_sys_modules_deletion

# ── Self-check: файл сам не ВЫЗЫВАЕТ del sys.modules (канон применим и к себе) ──
# Детектируются только фактические вызовы (строка НАЧИНАЕТСЯ с запрещённого паттерна);
# упоминания в docstring/комментариях/STRUCTURE — документация, не нарушение.
_SELF_SOURCE = Path(__file__).read_text()
for _ln in _SELF_SOURCE.splitlines():
    _stripped = _ln.lstrip()
    if _stripped.startswith(("del sys.modules", "sys.modules.pop")):
        raise AssertionError(f"Запрещённый вызов в reload_safe.py: {_stripped} — канон W4 нарушен")


def _reload_doc_example() -> Iterator[ModuleType]:
    """Документационный пример использования (не тест, не исполняется).

    ## @purpose — Демонстрация канона для агентов: env-dependent reload без del sys.modules.
    ## @io — ⎋ Iterator (генератор-пример, никогда не вызывается)
    """
    with reload_guard({"NODE_NAME": "test-node", "NODE_YAML_PATH": None}):
        app = reload_module("app", expected_file_substring="status-page")
    # Патч ПОСЛЕ reload — к актуальному объекту модуля
    yield app
