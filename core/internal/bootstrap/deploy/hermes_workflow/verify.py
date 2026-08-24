# GREP_SUMMARY: hermes-images, verify-images, image-exists, registry-check, will-build-locally, all-found
# STRUCTURE: ▶ verify_images_present ┌images × check_image_exists_fn┐ → ○ per-image check → ◇ missing? → WARN (will build locally) → ◇ all_found? → IMP:9 → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Верификация наличия hermes-образов в registry — per-image check через
##           check_image_exists_fn (docker manifest inspect, shared/docker_compose). Подмодуль
##           пакета hermes_workflow/ (T3.7): «verify»-ответственность split'а (верификация/health).
## @scope    Внутренний API пакета — вызывается deploy.handle_hermes_agent (impl). Чистая функция:
##           0 docker-вызовов, check-image fn передаётся аргументом (DI). Зависимость только stdlib.
## @invariants
##   1. missing-образ → all_found=False + IMP:5 WARN «Pre-built image not found ... will build locally»
##   2. ВСЕ найдены → IMP:9 all_found (Anti-Illusion: бизнес-лог в траектории)
##   3. Проверка не мутирует вход (images не изменяется)
## @rationale Q: Why verify.py отдельно? A: T3.7 целевая структура {images, deploy, verify} —
##   скорректирована по факту связности: presence-проверка образов — единственная «верификация»
##   workflow'а (health-проверка контейнеров живёт в healthcheck_runner.py — вне скоупа пакета);
##   вынос в чистую функцию даёт изолированное тестирование без моков на пакет.
## @changes  2026-08-22 | T3.7 simplify — извлечено из hermes_workflow.py (байт-в-байт LDD/логи)
# endregion MODULE_CONTRACT

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


# region FUNC_verify_images_present
## @purpose  Проверить наличие всех hermes-образов в registry (верификация pre-built).
## @io       ⇥ images: list[str], check_image_exists_fn: Callable[[str], bool]
##           ⎋ bool: True = все образы найдены (build не нужен), False = хотя бы один missing
## @complexity O(n) — n = число образов, по одному check_image_exists на образ
## @invariants
##   - missing → WARN с именем образа (fallback build path)
##   - все найдены → IMP:9 all_found
##   - all_found=False не прерывает цикл — логируются ВСЕ missing-образы (полная диагностика)
def verify_images_present(
    images: list[str],
    check_image_exists_fn: Callable[[str], bool],
) -> bool:
    all_found = True
    for img in images:
        if not check_image_exists_fn(img):
            all_found = False
            logger.warning(
                "[IMP:5][handle_hermes_agent][missing] Pre-built image not found: %s — will build locally", img
            )
    if all_found:
        logger.info("[IMP:9][handle_hermes_agent][all_found] All hermes-agent images found in registry")
    return all_found


# endregion FUNC_verify_images_present
