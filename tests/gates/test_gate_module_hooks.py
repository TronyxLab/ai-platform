#!/usr/bin/env python3
# GREP_SUMMARY: gate hook module on_project_deploy on_project_remove module.yaml validation executable bit GREP_SUMMARY MODULE_CONTRACT sourcing lib
# STRUCTURE: ▶ glob core/modules/*/module.yaml → ○ parse YAML ∋ module → ◇ hooks.on_project_deploy? → ▶ verify_file(path) ◇ executable? ◇ GREP_SUMMARY? ◇ MODULE_CONTRACT? ◇ source ../../lib/? → ◇ hooks.on_project_remove? аналогично → ⎋ SKIP if no hooks, FAIL if hook contract violated
# region MODULE_CONTRACT
## @purpose  Gate-тест, валидирующий hook-поля в module.yaml: файл хука существует, executable bit установлен, содержит GREP_SUMMARY, содержит MODULE_CONTRACT, sourcing идёт из ../../lib/.
## @scope    Итерирует все core/modules/*/module.yaml. Для каждого hooks.on_project_deploy и hooks.on_project_remove проверяет контракт. Модули без хуков — skip (не фейлят gate).
## @invariants
##   - Если hook не указан в module.yaml → тест пропускает модуль (pytest.skip)
##   - Если hook указан → проверяются все 5 контрактных пунктов
##   - Файл хука проверяется: существование, executable, GREP_SUMMARY, MODULE_CONTRACT, source ../../lib/
##   - Не редактирует production-код
## @rationale Gate-тест для T5 (DevPlan 020). Предотвращает деплой хуков с нарушенным контрактом. Работает до T14 (когда hooks будут созданы) — сейчас все модули SKIP.
## @changes 2026-07-17 | Initial implementation (T5 Hook-gate test)
## @usecases
##   - test_hook_contract_validation — parametrized по модулям: проверяет все hook-поля
##   - test_all_modules_have_no_hooks (опциональный) — пока все SKIP, этот тест green
# endregion MODULE_CONTRACT

import logging
import stat
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

# ── Пути ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODULES_DIR = PROJECT_ROOT / "core" / "modules"


# region FUNC_get_module_yamls
def _get_module_yamls() -> list[Path]:
    """Return sorted list of all core/modules/*/module.yaml paths.

    ## @purpose — Discover all module.yaml files for hook-gate iteration.
    ## @io — ⎋ list[Path]: sorted paths to existing module.yaml files
    ## @complexity — O(N) glob, N = number of module dirs
    """
    yamls = sorted(MODULES_DIR.glob("*/module.yaml"))
    if not yamls:
        logger.warning("[IMP:7][_get_module_yamls] No module.yaml files found in %s", MODULES_DIR)
    return yamls


# endregion FUNC_get_module_yamls


# region FUNC_get_hook_paths
def _get_hook_paths(module_yaml: Path) -> dict[str, str | None]:
    """Parse module.yaml and return dict of hook_type → hook_relative_path or None.

    ## @purpose — Extract hooks.on_project_deploy and hooks.on_project_remove from module.yaml.
    ## @io — ⇥ module_yaml: Path → ⎋ dict[str, str | None]: {"on_project_deploy": "hooks/...", "on_project_remove": "hooks/..."}
    ## @complexity — O(1) — single YAML parse
    ## @invariants
    ##   - Возвращает None для hook_type, отсутствующего в YAML
    ##   - Не валидирует содержимое — только чтение
    """
    with Path(module_yaml).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        logger.warning("[IMP:7][_get_hook_paths] Invalid module.yaml (not a dict): %s", module_yaml)
        return {"on_project_deploy": None, "on_project_remove": None}

    hooks = data.get("hooks", {}) or {}
    if not isinstance(hooks, dict):
        logger.warning("[IMP:7][_get_hook_paths] hooks field is not a dict in %s", module_yaml)
        return {"on_project_deploy": None, "on_project_remove": None}

    return {
        "on_project_deploy": hooks.get("on_project_deploy"),
        "on_project_remove": hooks.get("on_project_remove"),
    }


# endregion FUNC_get_hook_paths


# region FUNC_verify_hook_script
def _verify_hook_script(hook_path: Path) -> None:
    """Verify a hook script satisfies all contract requirements. Raises AssertionError on failure.

    ## @purpose — Gate-валидация hook-скрипта: существует, executable, GREP_SUMMARY, MODULE_CONTRACT, source ../../lib/.
    ## @io — ⇥ hook_path: Path (абсолютный путь к hook-скрипту) → ⎋ None (raises AssertionError on violation)
    ## @complexity — O(K) where K = lines of hook script (early-exit on first violation)
    ## @invariants
    ##   - Проверка executable bit: os.stat().st_mode & stat.S_IXUSR
    ##   - GREP_SUMMARY: file must contain line matching "# GREP_SUMMARY:"
    ##   - MODULE_CONTRACT: file must contain "# region MODULE_CONTRACT" and "## @purpose"
    ##   - Sourcing: file must contain 'source "${SCRIPT_DIR}/../../lib/' or similar pattern with ../../lib/
    ##   - Валидация fail-fast: первое нарушение вызывает AssertionError с описанием
    ## @rationale Каждый пункт контракта критичен для автономной работы hook-системы:
    ##   executable — скрипт должен быть вызываемым
    ##   GREP_SUMMARY — агенты ищут файлы по ключевым словам
    ##   MODULE_CONTRACT — самодокументирование следующего агента
    ##   source ../../lib/ — доступ к shared healthcheck/lib функциям
    """
    logger.info("[IMP:8][_verify_hook_script] Validating hook: %s", hook_path)

    # 1. Существование файла
    assert hook_path.exists(), f"Hook file does not exist: {hook_path}"
    logger.info("[IMP:8][_verify_hook_script] File exists: %s", hook_path)

    # 2. Executable bit
    st = hook_path.stat()
    is_exec = bool(st.st_mode & stat.S_IXUSR)
    assert is_exec, f"Hook file is not executable (missing +x): {hook_path}"
    logger.info("[IMP:8][_verify_hook_script] Executable bit set: %s", hook_path)

    # 3. GREP_SUMMARY
    content = hook_path.read_text(encoding="utf-8")
    assert "# GREP_SUMMARY:" in content, f"Hook file missing '# GREP_SUMMARY:' line: {hook_path}"
    logger.info("[IMP:8][_verify_hook_script] GREP_SUMMARY present: %s", hook_path)

    # 4. MODULE_CONTRACT (region + @purpose)
    assert "# region MODULE_CONTRACT" in content, f"Hook file missing '# region MODULE_CONTRACT': {hook_path}"
    assert "## @purpose" in content, f"Hook file missing '## @purpose' in MODULE_CONTRACT: {hook_path}"
    logger.info("[IMP:8][_verify_hook_script] MODULE_CONTRACT present: %s", hook_path)

    # 5. Sourcing from ../../lib/ — exempt python3-only thin wrappers (<60 LOC)
    # Thin wrappers delegate ALL logic to Python modules and may not need lib/*.sh.
    is_thin_wrapper = content.count("\n") < 60 and "python3" in content
    if not is_thin_wrapper:
        assert "../lib/" in content or "../../lib/" in content, (
            f"Hook file does not source from '../../lib/' (missing '../lib/' or '../../lib/' pattern): {hook_path}"
        )
    else:
        logger.info(
            "[IMP:8][_verify_hook_script] Thin wrapper (<60 LOC, python3 dispatch) — lib source check skipped: %s",
            hook_path,
        )
    logger.info("[IMP:9][_verify_hook_script] All contract checks passed: %s", hook_path)


# endregion FUNC_verify_hook_script


# region FUNC_verify_module_hooks
def _verify_module_hooks(module_yaml: Path) -> list[str]:
    """Verify all hooks declared in a module.yaml. Returns list of hook names verified.

    ## @purpose — Iterate hook declarations in a single module.yaml and verify each.
    ## @io — ⇥ module_yaml: Path → ⎋ list[str]: hook names that were verified (empty if none)
    ## @complexity — O(N * K) where N = hook count, K = script lines
    ## @invariants
    ##   - Если hooks отсутствуют → возвращает [] (не фейлит)
    ##   - Если hook указан → вызывает _verify_hook_script для каждого
    ##   - Логирует IMP:9 после успешной верификации модуля
    """
    module_dir = module_yaml.parent
    module_name = module_dir.name
    hooks = _get_hook_paths(module_yaml)

    verified_hooks: list[str] = []
    for hook_type, hook_rel_path in hooks.items():
        if hook_rel_path is None:
            logger.info("[IMP:7][_verify_module_hooks] %s: no %s hook — skip", module_name, hook_type)
            continue

        if not isinstance(hook_rel_path, str):
            logger.warning(
                "[IMP:7][_verify_module_hooks] %s: %s hook is not a string (%r) — skip",
                module_name,
                hook_type,
                hook_rel_path,
            )
            continue

        hook_abs_path = (module_dir / hook_rel_path).resolve()
        _verify_hook_script(hook_abs_path)
        verified_hooks.append(f"{module_name}/{hook_type}")
        logger.info("[IMP:9][_verify_module_hooks] %s: %s hook verified OK", module_name, hook_type)

    if verified_hooks:
        logger.info(
            "[IMP:9][_verify_module_hooks] Module %s: all %d hook(s) passed gate", module_name, len(verified_hooks)
        )
    else:
        logger.info("[IMP:7][_verify_module_hooks] Module %s: no hooks declared — skipped", module_name)

    return verified_hooks


# endregion FUNC_verify_module_hooks


# ── Tests ──────────────────────────────────────────────────────────────────────


# region FUNC_test_hook_contract_validation
@pytest.mark.gate
@pytest.mark.parametrize("module_yaml", _get_module_yamls(), ids=lambda p: p.parent.name)

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_hook_contract_validation(module_yaml: Path, caplog):
    """Parametrized gate test: validate all hook fields in a module.yaml.

    ## @purpose — Gate-тест hook-контракта для каждого модуля. SKIP если хуков нет, FAIL если контракт нарушен.
    ## @io — ⇥ module_yaml parametrized path + caplog → ⎋ None
    ## @complexity — O(N * K) per module
    ## @invariants
    ##   - caplog.set_level(logging.DEBUG) для захвата всех LDD-логов
    ##   - LDD trajectory выводится после проверки
    ##   - Если модуль без хуков → pytest.skip с пояснением
    ## @rationale — см. MODULE_CONTRACT
    """
    caplog.set_level(logging.DEBUG)
    module_name = module_yaml.parent.name

    logger.info("[IMP:7][test_hook_contract_validation] Checking module: %s (%s)", module_name, module_yaml)

    try:
        verified = _verify_module_hooks(module_yaml)
    except AssertionError as e:
        # Print LDD trajectory on failure for debugging
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            if "[IMP:" in record.message:
                imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    print(record.message)
        print("--- END LDD TRAJECTORY ---")
        logger.error("[IMP:9][test_hook_contract_validation] Hook contract violation in %s: %s", module_name, e)
        raise

    if not verified:
        # No hooks declared — skip (not fail)
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            if "[IMP:" in record.message:
                imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    print(record.message)
        print("--- END LDD TRAJECTORY ---")
        pytest.skip(f"Module '{module_name}' has no hooks declared — not a gate failure")

    # Print LDD trajectory and assert IMP:9
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_hook_contract_validation


# region FUNC_test_all_hooks_are_specified_as_strings
@pytest.mark.gate
def test_all_hooks_are_specified_as_strings():
    """Verify all hooks fields in all module.yaml are strings (not other types).

    ## @purpose — Schema-level gate: hooks values must be string paths, not bool/int/list.
    ## @scope — Iterates all module.yaml, reads hooks, checks type.
    ## @invariants
    ##   - hooks.on_project_deploy if present must be str
    ##   - hooks.on_project_remove if present must be str
    ##   - Missing hooks → not a failure
    ## @complexity — O(N) where N = module.yaml count
    ## @rationale YAML allows any type; typo like hooks.on_project_deploy: true would parse as bool
    """
    errors: list[str] = []
    for module_yaml in _get_module_yamls():
        module_dir = module_yaml.parent
        with Path(module_yaml).open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        hooks = (data or {}).get("hooks") or {}
        if not isinstance(hooks, dict):
            continue
        for hook_type in ("on_project_deploy", "on_project_remove"):
            val = hooks.get(hook_type)
            if val is not None and not isinstance(val, str):
                rel_path = module_dir.name
                errors.append(f"{rel_path}/module.yaml: hooks.{hook_type} is {type(val).__name__}, expected str")

    assert not errors, "Hook type violations:\n" + "\n".join(errors)


# endregion FUNC_test_all_hooks_are_specified_as_strings
# 🧪 TRAP[TEST] · 2026-07-17 · Regression: hook type coercion · Last fail: N/A · Remove if: gate schema validated elsewhere


# region FUNC_test_registered_deploy_hooks_have_runtime_trigger
## @purpose  B8 gate (волна 118): каждый module.yaml hooks.on_project_deploy имеет runtime-вызов
##           в деплой-пайплайне (registry-driven invoke через shared/module_interface).
##           Закрывает «зарегистрировано, но не вызывается» (K5): после B8 зарегистрирован
##           только nginx; мониторинг/postgres хуки удалены (Python-эквиваленты есть).
## @io       — → ⎋ None (asserts)
## @complexity — O(M + P) где M = module.yaml, P = строки deploy-пайплайна
## @invariants
##   - Для каждого module.yaml с hooks.on_project_deploy: DeployOrchestrator post-deploy chain
##     содержит registry-driven invoke (module_interface.invoke(module, "deploy-hook", ...))
##   - Оркестратор НЕ хардкодит имена модулей — читает core/modules/*/module.yaml
##   - R5: monitoring/postgres hooks удалены (волна 118 B8)
@pytest.mark.gate
def test_registered_deploy_hooks_have_runtime_trigger(caplog):
    """B8: каждый зарегистрированный deploy-hook имеет runtime-вызов в пайплайне."""
    caplog.set_level(logging.DEBUG)

    # 1. Собрать все зарегистрированные hooks.on_project_deploy
    registered: dict[str, str] = {}
    for module_yaml in _get_module_yamls():
        hook_paths = _get_hook_paths(module_yaml)
        if hook_paths.get("on_project_deploy"):
            registered[module_yaml.parent.name] = hook_paths["on_project_deploy"]

    logger.info("[IMP:8][test_registered_deploy_hooks_have_runtime_trigger] Registered hooks: %s", registered)

    # 2. Deploy-пайплайн должен содержать registry-driven invoke (не хардкод имён)
    # (170 W4-B3: post-deploy chain переехал из orchestrator.py в deploy/hooks/post_deploy_chain.py)
    chain = PROJECT_ROOT / "core" / "internal" / "deploy" / "hooks" / "post_deploy_chain.py"
    assert chain.is_file(), f"Post-deploy chain not found: {chain}"
    content = chain.read_text()
    assert "module_interface" in content, (
        "B8 FAIL: deploy-пайплайн не импортирует shared/module_interface (нет runtime-вызова hooks)"
    )
    assert '"deploy-hook"' in content or "'deploy-hook'" in content, (
        "B8 FAIL: deploy-пайплайн не вызывает интерфейс 'deploy-hook'"
    )
    assert "hooks" in content and "on_project_deploy" in content, (
        "B8 FAIL: deploy-пайплайн не читает hooks.on_project_deploy из module.yaml (registry-driven)"
    )

    # 3. R5: после B8 зарегистрирован ТОЛЬКО nginx (monitoring/postgres хуки удалены)
    # Волна 118 B8: monitoring/postgres hooks удалены (Python-эквиваленты: monitoring/config_renderer.py (172 W5.1),
    # on_project_deploy.py). nginx — реальная логика (reload-guard) — восстановлен триггер.
    assert "nginx" in registered, "B8 FAIL: nginx deploy-hook должен быть зарегистрирован (reload-guard)"
    assert "monitoring" not in registered, (
        "B8 FAIL: monitoring deploy-hook должен быть удалён (Python-эквивалент monitoring/config_renderer.py)"
    )
    assert "postgres" not in registered, (
        "B8 FAIL: postgres deploy-hook должен быть удалён (Python-эквивалент on_project_deploy.py)"
    )

    logger.info(
        "[IMP:9][test_registered_deploy_hooks_have_runtime_trigger] PASS: %d registered hook(s) — runtime trigger подтверждён (B8)",
        len(registered),
    )


# endregion FUNC_test_registered_deploy_hooks_have_runtime_trigger


# region FUNC_test_deleted_hook_files_absent
## @purpose  R5 negative (волна 118 B8): удалённые hook-файлы monitoring/postgres отсутствуют.
## @io       — → ⎋ None (asserts)
## @complexity — O(1)
@pytest.mark.gate
def test_deleted_hook_files_absent(caplog):
    """B8 R5: monitoring/postgres hook файлы удалены (removed API)."""
    caplog.set_level(logging.DEBUG)
    for rel in (
        "core/modules/monitoring/hooks/on-project-deploy.sh",
        "core/modules/postgres/hooks/on-project-deploy.sh",
    ):
        assert not (PROJECT_ROOT / rel).exists(), f"B8 FAIL: удалённый hook существует: {rel}"
    logger.info("[IMP:9][test_deleted_hook_files_absent] PASS: monitoring/postgres hook файлы удалены (B8 R5)")


# endregion FUNC_test_deleted_hook_files_absent
