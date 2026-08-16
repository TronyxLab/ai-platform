#!/usr/bin/env python3
# GREP_SUMMARY: env-facts, EnvironmentFacts, SystemEnvironmentFacts, is-root, which, path-isfile, DI, geteuid, shutil, testability, W4b
# STRUCTURE: ▶ EnvironmentFacts Protocol ┌is_root/which/path_isfile┐ → ○ SystemEnvironmentFacts (os.geteuid/shutil.which/os.path.isfile) → ⊕ default_env_facts() → ⎋ lazy-default для 7 модулей
# region MODULE_CONTRACT
## @purpose  Абстракция системных фактов окружения (DevPlan 160 W4b T4.2): is_root()
##           (os.geteuid==0), which(bin) (shutil.which), path_isfile(p) (os.path.isfile).
##           Параметр `facts: EnvironmentFacts | None = None` с ленивым default
##           (default_env_facts() = SystemEnvironmentFacts) делает модули, читающие
##           ОС-факты напрямую, тестируемыми без monkeypatch os/shutil.
## @scope    Потребляется: bootstrap/install_tor_proxy.py (root-guard),
##           bootstrap/security_posture.py (root-guard), healthcheck/watchdog.py
##           (which docker), bootstrap/tor_transport.py (which transport-bin),
##           validate/validate_orchestrator.py (which ajv), deploy/verify_contracts.py
##           (which docker), practices/check_project.py (which gitleaks/docker/ruff/...).
##           Минимальное изменение: только DI-параметры, дефолты = реальные вызовы.
## @invariants
##   1. EnvironmentFacts — Protocol (структурная типизация): is_root/which/path_isfile
##   2. SystemEnvironmentFacts.is_root() вызывает os.geteuid() — тот же модуль os, что
##      патчат существующие тесты monkeypatch.setattr(<mod>.os, "geteuid", ...) (os — singleton)
##   3. default_env_facts() — фабрика SystemEnvironmentFacts (ленивый default, без кэша)
##   4. Никаких env-чтений на import-time (импорт модуля не имеет побочных эффектов)
##   5. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
## @rationale DevPlan 160 W4b (AF-2, D2): 7 модулей читали os.geteuid()/shutil.which()/
##            os.path.isfile() напрямую → unit-тесты патчили os/shutil на уровне модуля.
##            DI параметр facts=... (fake в тестах) убирает патчи и делает проверку
##            «root/which» явной частью контракта функции.
## @changes  2026-08-13 | DevPlan 160 W4b — created (T4.2 EnvironmentFacts)
## @usecases
##   - install_tor_proxy.main([], facts=_FakeFacts(is_root=False)) — тест root-guard
##   - watchdog.run_watchdog(facts=_FakeFacts(which=lambda _: None)) — docker unavailable
## @see      core/internal/shared/subprocess_io.py (CommandRunner — парный DI-протокол)
# endregion MODULE_CONTRACT

from __future__ import annotations

import os
import pathlib
import shutil
from typing import Protocol, runtime_checkable

logger = None  # модуль без логгера — чистые фасады ОС-фактов (O(1), без I/O логики)


# region PROTOCOL_EnvironmentFacts
@runtime_checkable
class EnvironmentFacts(Protocol):
    """Структурный контракт системных фактов (fake-реализация в тестах).

    ## @purpose — Абстракция ОС-фактов для DI: тесты передают fake (is_root/which/
    ##            path_isfile с ассертами) вместо monkeypatch os/shutil.
    ## @io — ⇥ is_root() → bool; which(bin) → str | None; path_isfile(p) → bool
    ## @complexity — O(1) — прямые системные вызовы
    ## @invariants
    ##   - is_root(): True если euid == 0 (root-проверки bootstrap-фаз)
    ##   - which(bin): shutil.which-семантика — путь бинарника или None
    ##   - path_isfile(p): os.path.isfile-семантика
    """

    def is_root(self) -> bool:
        """True если процесс запущен от root (geteuid() == 0)."""
        ...

    def which(self, binary: str) -> str | None:
        """Путь бинарника в PATH (shutil.which) или None."""
        ...

    def path_isfile(self, path: str | os.PathLike[str]) -> bool:
        """True если path — существующий файл (os.path.isfile)."""
        ...


# endregion PROTOCOL_EnvironmentFacts


# region CLASS_SystemEnvironmentFacts
class SystemEnvironmentFacts:
    """Реальная реализация EnvironmentFacts — прямые вызовы os/shutil.

    ## @purpose — Ленивый default для facts-параметров: реальные системные факты.
    ##            is_root() через os.geteuid() — os singleton, поэтому существующие
    ##            monkeypatch.setattr(<mod>.os, "geteuid", ...) продолжают работать.
    ## @io — ⇥ — → ⎋ реализация протокола
    ## @complexity — O(1) — каждый вызов — один системный вызов
    ## @invariants
    ##   - НЕ кэширует результат (факты могут меняться между вызовами в prod)
    ##   - path_isfile принимает str | PathLike (передача Path безопасна)
    """

    @staticmethod
    def is_root() -> bool:
        return os.geteuid() == 0

    @staticmethod
    def which(binary: str) -> str | None:
        return shutil.which(binary)

    @staticmethod
    def path_isfile(path: str | os.PathLike[str]) -> bool:
        return pathlib.Path(path).is_file()


# endregion CLASS_SystemEnvironmentFacts


# region FUNC_default_env_facts
def default_env_facts() -> EnvironmentFacts:
    """Фабрика реальных системных фактов (ленивый default для facts-параметров).

    ▶ ┌None┐ → ⊕ SystemEnvironmentFacts() → ⎋ EnvironmentFacts

    ## @purpose — Единая точка создания default-фактов: `facts = facts or default_env_facts()`.
    ## @io — ⇥ None → ⎋ EnvironmentFacts (SystemEnvironmentFacts)
    ## @complexity — O(1)
    ## @invariants
    ##   - Без кэширования/синглтона — каждый вызов новый лёгкий объект (O(1))
    """
    return SystemEnvironmentFacts()


# endregion FUNC_default_env_facts
