#!/usr/bin/env python3
# GREP_SUMMARY: dockerfile, base-image, from-parsing, digest-pin, multi-stage, stage-alias, prebuild-pull, cold-bootstrap, shared
# STRUCTURE: ▶ ┌dockerfile text┐ → ○ FROM-строки (проход 1: алиасы `AS Y`) → ○ refs (проход 2: токен ∉ алиасы)
#           → ⊕ dedupe (order-preserving) → ◇ @sha256? → ⚠️ IMP:5 unpinned (включается) → ⎋ list[str]
#           ▶ module_base_images ┌module_dir┐ → ◇ Dockerfile|build/Dockerfile? → ⎋ parse | []
# region MODULE_CONTRACT
## @purpose  Извлечение ВНЕШНИХ базовых образов из Dockerfile-строк `FROM` (F-03, 017-launch-validation):
##           единственная реализация парсинга баз build-модулей платформы (status-page, backup-cron,
##           hermes-agent) для детерминированного pre-pull перед `docker compose build`. BuildKit НЕ
##           ретраит pull внутри сборки — первый массовый пул с docker.io на голой ноде транзиентно
##           падает (троттлинг); pre-pull пинненных баз с ретраями убирает класс P0 cold-bootstrap.
## @scope    Pure parsing layer (by DDD — Infrastructure): НЕ выполняет docker-операций, НЕ знает
##           о композе/модулях платформы. Потребитель: shared/docker_compose.docker_prebuild_pull
##           (и будущие потребители digest-pin-политики — гейт test_gate_image_tag_form, hermes_images).
## @invariants
##   1. Двухпроходная семантика stage-алиасов: проход 1 собирает множество `FROM X AS Y` → Y;
##      проход 2 возвращает FROM-токены, НЕ входящие в это множество (`FROM base` → skip).
##   2. Ref = точный текст первого токена после `FROM` (и после ведущих `--flag`-токенов) до
##      разделителя пробел/конец — сохранение name[:tag][@sha256:...] байт-в-байт.
##   3. Digest-pin политика репо (DevPlan 170 W12 C1): ref без `@sha256:` → warning [IMP:5],
##      НО возвращается (pre-pull безвреден — docker pull непинненного тега кешируется).
##   4. Дубликаты (один базовый образ в нескольких стадиях) схлопываются с сохранением порядка.
##   5. module_base_images ищет первый существующий из [Dockerfile, build/Dockerfile]; ни одного →
##      [] (не ошибка — модуль без собственного образа).
##   6. Регистронезависимое распознавание инструкции FROM (Dockerfile instructions case-insensitive).
##   7. Вне скоупа: ARG-индирекция (`FROM ${BASE}`), `FROM scratch` (резервированное имя Docker) —
##      токен возвращается как есть, решение о пулле — у потребителя.
## @rationale Q: почему отдельный shared-модуль? A: парсинг Dockerfile — чистая переиспользуемая
##            утилита (потенциальные потребители: digest-pin гейт, hermes_images, CI); класть его
##            внутрь docker_prebuild_pull — смешение парсинга и I/O. Критерий shared/ — дедупликация
##            будущих реализаций парсинга FROM (DRY), инвентарь shared/AGENTS.md обновлён.
## @changes 2026-08-27 | F-03 (017-launch-validation P0) — Created (pre-pull пинненных баз build-модулей)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


# region FUNC__iter_from_refs
def _iter_from_refs(text: str) -> Iterator[tuple[str, str | None]]:
    """Разобрать FROM-строки Dockerfile: yield (ref, alias) на каждую инструкцию FROM.

    ▶ ┌text┐ → ○ for line: ◇ startswith "from " (case-insensitive) → ○ tokens → ○ skip `--`-флаги
      → ○ ref = первый не-флаг токен → ○ alias = токен после `AS` (если есть) → ⊕ yield (ref, alias)

    ## @purpose  Лексический разбор инструкций `FROM <ref> [AS <alias>]` (включая `--platform`-флаги):
    ##            извлекает ref (точный текст) и опциональный stage-алиас для двухпроходной фильтрации.
    ## @io       ⇥ text: str (содержимое Dockerfile) → ⎋ Iterator[(ref: str, alias: str | None)]
    ## @complexity O(L * T) — строки × токены
    ## @invariants
    ##   - Инструкция распознаётся по префиксу "from " в нижнем регистре (FROM/From/from)
    ##   - Ведущие `--`-токены (--platform=..., --keep-git-dir=...) пропускаются до первого ref
    ##   - alias = первый токен сразу после `AS` (регистронезависимо); отсутствует → None
    """
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.lower().startswith("from "):
            continue
        tokens = line.split()
        # tokens[0] == "FROM" (регистр любой) — далее пропускаем build-флаги (--platform и пр.)
        idx = 1
        while idx < len(tokens) and tokens[idx].startswith("--"):
            idx += 1
        if idx >= len(tokens):
            continue
        ref = tokens[idx]
        alias: str | None = None
        for j in range(idx + 1, len(tokens)):
            if tokens[j].upper() == "AS" and j + 1 < len(tokens):
                alias = tokens[j + 1]
                break
        yield ref, alias


# endregion FUNC__iter_from_refs


# region FUNC_parse_pinned_bases
def parse_pinned_bases(dockerfile_path: str | Path) -> list[str]:
    """Извлечь ВНЕШНИЕ базовые образы из FROM-строк Dockerfile (двухпроходный alias-фильтр).

    ▶ ┌dockerfile_path┐ → ○ read_text → ○ проход 1: алиасы (`FROM X AS Y` → Y) → ○ проход 2: refs ∉ алиасы
      → ⊕ dedupe → ◇ @sha256? → ⚠️ [IMP:5] unpinned (включается) → ⎋ list[ref]

    ## @purpose  Единственная реализация извлечения внешних баз build-модуля: возвращает ref'ы вида
    ##            name[:tag][@sha256:...] с точным сохранением текста; внутренние stage-алиасы
    ##            (`FROM base`) и дубликаты исключаются. Вход для docker_prebuild_pull.
    ## @io       ⇥ dockerfile_path: str | Path → ⎋ list[str] (порядок появления, без дублей)
    ## @complexity O(L * T) + O(R) — парсинг + фильтрация/dedupe
    ## @invariants
    ##   - Отсутствующий файл → FileNotFoundError (fail-fast; module_base_images гардирует is_file)
    ##   - Ref в множестве алиасов (internal stage) → пропускается молча (IMP:8 trace)
    ##   - Ref без "@sha256:" → warning [IMP:5] (digest-pin политика), НО включается в результат
    ##   - Дубликат ref → включается один раз (порядок первого вхождения)
    """
    path = Path(dockerfile_path)
    text = path.read_text(encoding="utf-8")
    logger.info("[IMP:8][parse_pinned_bases] Parsing FROM instructions in %s", path)

    # Проход 1: собрать множество stage-алиасов (`FROM X AS Y` → Y)
    aliases: set[str] = set()
    refs: list[str] = []
    for ref, alias in _iter_from_refs(text):
        if alias is not None:
            aliases.add(alias)
        refs.append(ref)

    # Проход 2: внешние базы = FROM-токены, не входящие в множество алиасов (dedupe, порядок сохранён)
    seen: set[str] = set()
    external: list[str] = []
    for ref in refs:
        if ref in aliases:
            logger.info("[IMP:8][parse_pinned_bases][internal_alias] Skipping internal stage alias: %s", ref)
            continue
        if ref in seen:
            continue
        seen.add(ref)
        external.append(ref)
        if "@sha256:" not in ref:
            # Digest-pin политика (DevPlan 170 W12 C1): tag@sha256 на всех внешних FROM.
            # Warning + включение — pre-pull непинненного тега безвреден (кешируется).
            logger.warning(
                "[IMP:5][parse_pinned_bases][unpinned] Base image without digest-pin (repo policy requires tag@sha256): %s",
                ref,
            )
    logger.info("[IMP:8][parse_pinned_bases] %d external base image(s): %s", len(external), external)
    return external


# endregion FUNC_parse_pinned_bases


# region FUNC_module_base_images
def module_base_images(module_dir: str | Path) -> list[str]:
    """Найти Dockerfile модуля (Dockerfile | build/Dockerfile) и делегировать parse_pinned_bases.

    ▶ ┌module_dir┐ → ◇ candidate ∈ [Dockerfile, build/Dockerfile]: is_file? → ⊕ parse_pinned_bases
      → ⎋ list[ref] | [] (нет Dockerfile)

    ## @purpose  Фасад «базы модуля»: резолвит путь Dockerfile по канону модульных структур платформы
    ##            (core/modules/<name>/Dockerfile и build/Dockerfile — hermes-agent build-дерево).
    ## @io       ⇥ module_dir: str | Path (директория модуля, напр. modules/status-page)
    ##           → ⎋ list[str] (внешние базы) | [] когда Dockerfile отсутствует
    ## @complexity O(1) — проверка 2 кандидатов + делегирование
    ## @invariants
    ##   - Порядок поиска: Dockerfile → build/Dockerfile (первый существующий)
    ##   - Ни одного Dockerfile → [] (не ошибка; docker_prebuild_pull трактует как no-op success)
    """
    base_dir = Path(module_dir)
    for candidate in ("Dockerfile", "build/Dockerfile"):
        candidate_path = base_dir / candidate
        if candidate_path.is_file():
            logger.info("[IMP:8][module_base_images] Dockerfile found: %s", candidate_path)
            return parse_pinned_bases(candidate_path)
    logger.info(
        "[IMP:7][module_base_images][no_dockerfile] No Dockerfile (Dockerfile|build/Dockerfile) in %s", base_dir
    )
    return []


# endregion FUNC_module_base_images
