#!/usr/bin/env python3
# GREP_SUMMARY: webnames-protocol dnsapi api-key injection shred-secrets webnames.ru acme dns-01 inject shred-protocol tmpfs key-rotation DI runner-param DevPlan-170-W6-D2
# STRUCTURE: ▶ ┌API_KEY + dnsapi-script content┐ → ○ inject_webnames (API_KEY= sed-канон) → ⊕ shred_secrets (shred -u + unlink fallback, DI runner)
#            → ⎋ str/None; leaf-протокол webnames-инъекции+shred — НЕ зависит от acme-оркестрации
# region MODULE_CONTRACT
## @purpose  webnames-протокол безопасности (DevPlan 170 W6-D2, research-A §5): инъекция
##           WEBNAMES_API_KEY в dnsapi-скрипт (sed "s|^API_KEY=.*|API_KEY=\"...\"|" канон) +
##           shred-уничтожение ключевых файлов из ВСЕХ on-disk-локаций. Вынесен из issue_cert.py
##           (инъекция+shred были инлайновыми в webnames DNS-01 ветке) — протокол консолидирован
##           в leaf-модуль, оркестрация (issue_cert) импортирует готовые функции.
## @scope    core/internal/bootstrap: вызывается ТОЛЬКО из issue_cert.py (_issue_acme_webnames:
##           inject + shred после acme.sh, включая провал — ключ не остаётся на диске).
##           Прямых shell-вызовов нет (модуль — библиотека, не CLI).
## @invariants
##   - inject_webnames заменяет ПЕРВУЮ строку `API_KEY=...` на `API_KEY="<key>"` (re.MULTILINE ^),
##     НЕ логирует ключ (чистая функция)
##   - shred_secrets: shred -u graceful (rc!=0/not-found → не блокирует); остаток файла → unlink;
##     вызывается ВСЕГДА после acme.sh (включая провал — ключ не остаётся на диске)
##   - DI (W-H DevPlan 163): runner — обязательный параметр shred_secrets (CommandRunner;
##     тесты передают fake, 0 monkeypatch)
##   - 8118/PRIVOXY не имеет отношения к модулю (только webnames DNS-01 протокол)
## @rationale Q: Почему отдельный модуль, а не функция в issue_cert?
##            A: research-A §5: issue_cert run 128/CC20 + webnames inject+shred — разные
##            ответственности (оркестрация acme.sh vs протокол работы с секретом).
##            Шred-протокол — переиспользуемый security-контракт (provider_registry mode=inject);
##            отдельный leaf-модуль тестируется изолированно и не растягивает issue_cert.
## @changes  2026-08-15 | DevPlan 170 W6-D2 — создан: inject_webnames_key/_shred_paths/_API_KEY_LINE_RE
##                      из issue_cert.py консолидированы (имена inject_webnames/shred_secrets)
## @see      core/internal/bootstrap/issue_cert.py (единственный потребитель),
##           core/internal/bootstrap/provider_registry.py (mode: inject — реестр DNS-провайдеров)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from pathlib import Path

# W1-A1 (план 170): timeout=10 литерал (shred) → канон SoT DOCKER_CMD_TIMEOUT (10,
# короткая команда) — AMBER-зачистка research-D §D1 (перенесён из issue_cert.py W6-D2).
from core.internal.shared.subprocess_io import CommandRunner
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)

# ── Канонические константы webnames-протокола (совпадают с прежними литералами issue_cert.py) ──
# Имя DNS-плагина acme.sh (--dns dns_webnames) — короткое имя из dnsapi/ (P2 acme.sh basename TRAP)
DNSAPI_PLUGIN_NAME: str = "dns_webnames"
# Имя внешнего скрипта dnsapi-расширения (dnsapi_ext/), мишень инъекции API_KEY
WEBNAMES_EXT_SCRIPT: str = "dns_webnames.sh"
# Инъекция API_KEY в dnsapi-скрипт (sed "s|^API_KEY=.*|API_KEY=\"...\"|" канон)
_API_KEY_LINE_RE = re.compile(r"^API_KEY=.*$", re.MULTILINE)


# region FUNC_inject_webnames
## @purpose  Чистая инъекция API_KEY в содержимое dnsapi-скрипта (sed "s|^API_KEY=.*|...|" канон).
## @io       ⇥ content: str, api_key: str → ⎋ str (модифицированное содержимое)
## @complexity O(L) — L = строк скрипта
## @invariants
##   - Заменяет ПЕРВУЮ строку `API_KEY=...` на `API_KEY="<key>"` (re.MULTILINE ^)
##   - Не логирует ключ (чистая функция; вызов не печатает)
def inject_webnames(content: str, api_key: str) -> str:
    """Replace the API_KEY= line in a webnames dnsapi script with the provided key (sed-канон)."""
    return _API_KEY_LINE_RE.sub(f'API_KEY="{api_key}"', content, count=1)


# endregion FUNC_inject_webnames


# region FUNC_shred_secrets
## @purpose  Shred-протокол: уничтожить файлы с API-ключом (shred -u с rm -f fallback).
##           DI runner — единственный I/O-канал (тесты передают fake, 0 monkeypatch).
## @io       ⇥ paths: list[Path], runner: CommandRunner → ⎋ None
## @complexity O(P) — P = файлов
## @invariants
##   - shred -u graceful (rc!=0/not-found → не блокирует); остаток файла → unlink
##   - Вызывается ВСЕГДА после acme.sh (включая провал — ключ не остаётся на диске)
def shred_secrets(paths: list[Path], runner: CommandRunner) -> None:
    """Securely remove key-bearing files: shred -u with rm -f fallback (webnames shred protocol)."""
    for path in paths:
        if not path.exists():
            continue
        runner.run(["shred", "-u", str(path)], timeout=DOCKER_CMD_TIMEOUT, check=False)
        if path.exists():
            path.unlink(missing_ok=True)
    logger.info("[IMP:9][webnames][shred] API key shredded from %d on-disk location(s)", len(paths))


# endregion FUNC_shred_secrets
