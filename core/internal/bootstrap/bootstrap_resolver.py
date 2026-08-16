#!/usr/bin/env python3
# GREP_SUMMARY: bootstrap-resolver, resolve, node.yaml, --get-many, batch-extraction, context-fallback, tab-parse, masked-age-key, bootstrap.sh, exit-0-1-2, W9-F1
# STRUCTURE: ▶ resolve → ∋ node_yaml --get-many (7 полей) → ○ parse_batch_output (alias<TAB>value) → ◇ context? → ⊕ CONTEXT←CONTEXT0 → ◇ owner_key? → ⎋ key=value|json (exit 0|1|2) ── ▶ mask --key → ⎋ <AGE_KEY:xxxx...> ── ◇ CLI dispatch
# region MODULE_CONTRACT
## @purpose  Python-резолвер параметров bootstrap (DevPlan 170 W9-F1): извлекает из node.yaml
##           поля, которые bootstrap.sh ранее парсил в shell (tab-парсинг --get-many + fallback
##           CONTEXT←CONTEXT0, строки 72-99) + masked-age-key для dry-run-вывода. Один CLI-вызов
##           `resolve` закрывает: резолв node.yaml (3-path) → batch-extract 7 полей → fallback →
##           валидацию owner_key → host. Таб-парсинг вынесен из shell в тестируемый Python.
## @scope    Вызывается только из core/entrypoints/bootstrap.sh через
##           `python3 -m core.internal.bootstrap.bootstrap_resolver resolve|mask`. Чистые функции
##           (parse_batch_output, mask_age_key, resolve_fields) — native-тестируемы (DI get_many_runner).
##           Траверс dotted-ключей (contexts.0.name) НЕ дублируется — делегируется node_yaml
##           --get-many CLI (single source of truth, DevPlan 116 B3 T5); здесь — только парсинг.
## @invariants
##   - Exit: 0 = ok; 1 = FATAL (node.yaml not found / parse error / owner_key missing /
##     node_yaml --get-many rc≠0); 2 = invalid input (битая tab-строка, unknown alias,
##     argparse usage error) — R5-negative тест
##   - stdout resolve: РОВНО key=value-строки (по умолчанию) или JSON — shell-readable,
##     НИКАКИХ логов в stdout (log-канал — stderr, LDD-ребандлинг как node_resolver.py)
##   - CONTEXT fallback сохраняет семантику bootstrap.sh:99: top-level `context` > `contexts.0.name`
##     (НЕ NodeYaml.get_context() — тот читает ТОЛЬКО contexts[0].name, другой контракт)
##   - owner_key отсутствует → exit 1 (bootstrap.sh:101 контракт перенесён в Python)
##   - mask: ключ маскируется первыми 8 символами `<AGE_KEY:xxxx....>`, пустой ключ → ""
##   - main() -> int канон (core/AGENTS.md): sys.exit только в __main__
##   - subprocess ВСЕГДА без shell (языковая политика, ruff S603); runner — DI-сeam для тестов
## @rationale Q: Почему subprocess к node_yaml, а не прямой импорт NodeYaml + node.get()?
##            A: 1) dotted-траверс с list-index (contexts.0.name) живёт ТОЛЬКО в
##            node_yaml/cli.py:_traverse_dotted_list_aware (приватный, cross-module-import запрещён
##            гейтом; 170 W10-B: node_yaml_cli.py → node_yaml/cli.py); 2) семантика "missing key →
##            пустое значение (exit 0)" — контракт
##            --get-many, bootstrap.sh уже вызывал его; дублирование траверса = drift-риск.
##            Диспетчер-шаблон: bootstrap.sh → python3 -m resolver (парсинг/fallback) →
##            python3 -m node_yaml (траверс) — каждый слой отвечает за одно.
## @changes  2026-08-15 | DevPlan 170 W9-F1 — Created (извлечение tab-парсинга bootstrap.sh:88-99
##           + masked-age-key; снимает TRAP[DEBT] 2026-08-14 в bootstrap.sh)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Callable

from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
from core.internal.shared.node_resolver import resolve_node_yaml

logger = logging.getLogger(__name__)

# ── Константы контракта ──────────────────────────────────────────────────────
# Батч-спека bootstrap.sh:81 (B3 T5, U-52) + host (node.host) — перенесена в Python.
_GET_MANY_SPEC: str = (
    "owner_key:node.owner_key,ci_deploy_key:node.ci_deploy_key,ci_root_key:node.ci_root_key,"
    "platform_domain:domain,context:context,context0:contexts.0.name,host:node.host"
)
# Алиасы спеки — fail-fast против дрейфа формата вывода node_yaml.
_EXPECTED_ALIASES: frozenset[str] = frozenset({
    "owner_key",
    "ci_deploy_key",
    "ci_root_key",
    "platform_domain",
    "context",
    "context0",
    "host",
})
# Порядок key=value-вывода (стабильный, shell case-контракт).
_OUTPUT_ORDER: tuple[str, ...] = (
    "owner_key",
    "ci_deploy_key",
    "ci_root_key",
    "platform_domain",
    "context",
    "host",
    "node_yaml_path",
)
_KEY_MASK_LEN: int = 8  # сколько символов ключа показывать в маске (паритет bootstrap.sh cut -c1-8)

GetManyRunner = Callable[[str, str], str]


class ResolverError(Exception):
    """Generic FATAL (exit 1) — node.yaml не найден, parse error, node_yaml rc≠0."""


class ResolverParseError(ResolverError):
    """Invalid --get-many output (exit 2, R5-negative) — битая tab-строка / unknown alias."""


# region FUNC__default_get_many_runner
def _default_get_many_runner(yaml_path: str, spec: str) -> str:
    """Production runner: node_yaml --get-many via subprocess (no shell).

    ▶ ┌yaml_path, spec┐ → subprocess [sys.executable, -m node_yaml] → ◇ rc≠0? → ⊕ raise ResolverError → ⎋ stdout

    ## @purpose  Единственная production-точка вызова node_yaml --get-many. Путь-инвариант:
    ##            sys.executable (тот же интерпретатор, что запустил резолвер — venv/консистентность),
    ##            список аргументов БЕЗ shell (S603). rc≠0 → ResolverError с усечённым stderr.
    ## @io — ⇥ yaml_path: str, spec: str → ⎋ str (alias<TAB>value-строки, как --get-many контракт)
    ## @complexity — O(N) — один процесс + захват вывода
    ## @invariants
    ##   - subprocess.run c shell=False (языковая политика — никогда shell=True)
    ##   - rc≠0 → ResolverError (exit 1 FATAL): --get-many возвращает 4 на битой спеке — это
    ##     конфигурационная ошибка (spec — внутренняя константа), НЕ вход пользователя
    ##   - stdout возвращается как есть (парсинг — ответственность parse_batch_output)
    """
    logger.info("[IMP:8][bootstrap_resolver][get-many] Invoking node_yaml --get-many (file=%s)", yaml_path)
    result = subprocess.run(
        [sys.executable, "-m", "core.internal.shared.node_yaml", "--file", yaml_path, "--get-many", spec],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        snippet = detail[0] if detail else f"rc={result.returncode}"
        msg = f"node_yaml --get-many failed: {snippet}"
        logger.error("[IMP:10][bootstrap_resolver][get-many] %s", msg)
        raise ResolverError(msg)
    logger.info("[IMP:9][bootstrap_resolver][get-many] --get-many batch extraction OK (%d bytes)", len(result.stdout))
    return result.stdout


# endregion FUNC__default_get_many_runner


# region FUNC_parse_batch_output
def parse_batch_output(text: str, expected_aliases: frozenset[str] = _EXPECTED_ALIASES) -> dict[str, str]:
    """Разбор alias<TAB>value-вывода node_yaml --get-many в dict (замена shell tab-парсинга).

    ▶ ┌text┐ → ∋ строки → ○ partition('\t') → ◇ нет TAB? / alias∉expected? → ⊕ raise ResolverParseError → ⊕ dict → ⎋ dict

    ## @purpose  Прямая замена `while IFS=$'\t' read -r alias value` (bootstrap.sh:89-98) —
    ##            хрупкий shell-парсинг перенесён в Python с fail-fast. Значения могут
    ##            содержать пробелы/`=` — разделитель ТОЛЬКО первый TAB (partition).
    ## @io — ⇥ text: str (вывод --get-many); expected_aliases: frozenset → ⎋ dict[str, str]
    ## @complexity — O(L) — L строк вывода
    ## @raises — ResolverParseError: строка без TAB (дрейф формата node_yaml) или unknown alias
    ## @invariants
    ##   - Строка без TAB → ResolverParseError (exit 2) — R5-negative: битая tab-строка НЕ
    ##     тихо деградирует (shell read молча давал пустой value — класс бага скрытого)
    ##   - Unknown alias (вне ожидаемой спеки) → ResolverParseError — дрейф вывода ловится сразу
    ##   - Пустой text → {} (owner_key-валидация в main() покрывает реальный сбой)
    ##   - Дубликат алиаса → последняя строка побеждает (node_yaml на фиксированной спеке не дублирует)
    ##   - Отсутствующий алиас-строка → ключа нет в dict → "" (семантика missing key = empty, exit 0)
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        alias, sep, value = line.partition("\t")
        if not sep:
            msg = f"Malformed --get-many line (no TAB separator): {line!r}"
            logger.error("[IMP:10][bootstrap_resolver][parse] %s", msg)
            raise ResolverParseError(msg)
        if alias not in expected_aliases:
            msg = f"Unexpected --get-many alias {alias!r} (expected one of {sorted(expected_aliases)})"
            logger.error("[IMP:10][bootstrap_resolver][parse] %s", msg)
            raise ResolverParseError(msg)
        fields[alias] = value
    logger.info("[IMP:9][bootstrap_resolver][parse] Parsed %d field(s) from --get-many output", len(fields))
    return fields


# endregion FUNC_parse_batch_output


# region FUNC_mask_age_key
def mask_age_key(key: str, visible_chars: int = _KEY_MASK_LEN) -> str:
    """Маска AGE-ключа для dry-run-вывода (замена bootstrap.sh:148 cut -c1-8 + конкатенации).

    ▶ ┌key┐ → ◇ пустой? → ⎋ "" → ○ prefix=key[:visible_chars] → ⎋ <AGE_KEY:prefix...>

    ## @purpose  Единая маска секрета: в dry-run REMOTE_CMD полный ключ заменяется на
    ##            `<AGE_KEY:xxxx....>` (первые 8 символов) — ключ не светится в логах/выводе.
    ## @io — ⇥ key: str, visible_chars: int → ⎋ str (маскированная форма или "" для пустого)
    ## @complexity — O(K) — K = длина ключа (слайс + f-string)
    ## @invariants
    ##   - Пустой ключ → "" (bootstrap.sh вызывает mask только при non-empty DETECTED_AGE_KEY)
    ##   - visible_chars=0 → `<AGE_KEY:...>` (0 символов префикса — валидно, но не используется)
    ##   - Никогда не содержит полный ключ — безопасно для stderr/stdout
    """
    if not key:
        return ""
    return f"<AGE_KEY:{key[:visible_chars]}...>"


# endregion FUNC_mask_age_key


# region FUNC_resolve_fields
def resolve_fields(
    yaml_path: str,
    *,
    get_many_runner: GetManyRunner | None = None,
) -> dict[str, str]:
    """Оркестрация: --get-many → parse → CONTEXT fallback (семантика bootstrap.sh:99).

    ▶ ┌yaml_path┐ → ○ runner(yaml_path, spec) → ○ parse_batch_output → ◇ context пуст? → ⊕ context←context0 → ⊕ pop context0 → ⎋ fields

    ## @purpose  Бизнес-резолв полей bootstrap из node.yaml. Fallback CONTEXT←CONTEXT0
    ##            сохранён байт-в-байт (bootstrap.sh:99: top-level context > contexts.0.name) —
    ##            намеренно НЕ NodeYaml.get_context() (тот читает только contexts[0].name).
    ## @io — ⇥ yaml_path: str; get_many_runner: DI-сeam (None → _default_get_many_runner) → ⎋ dict[str, str]
    ## @complexity — O(L + N) — L строк вывода, N полей
    ## @raises — ResolverError (runner rc≠0), ResolverParseError (битый вывод)
    ## @invariants
    ##   - Результат: owner_key/ci_deploy_key/ci_root_key/platform_domain/context/host (context0 удалён)
    ##   - context пуст → context = context0 (могут быть оба пусты — легитимно, exit 0)
    ##   - Отсутствующие поля → "" (missing key контракт --get-many, exit 0)
    ##   - Чистая функция: никаких побочных эффектов (вывод/файлы/env)
    """
    runner = get_many_runner or _default_get_many_runner
    raw = runner(yaml_path, _GET_MANY_SPEC)
    fields = parse_batch_output(raw)
    if not fields.get("context"):
        fields["context"] = fields.get("context0", "")
    fields.pop("context0", None)
    logger.info("[IMP:9][bootstrap_resolver][resolve] Resolved fields for %s: %s", yaml_path, sorted(fields))
    return fields


# endregion FUNC_resolve_fields


# region FUNC__build_parser
def _build_parser() -> argparse.ArgumentParser:
    """CLI-парсер: subcommands resolve | mask.

    ## @purpose  Централизованный argparse для тестируемости (паттерн node_detect._build_parser).
    ## @io — ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        prog="core.internal.bootstrap.bootstrap_resolver",
        description="Resolve bootstrap parameters from node.yaml (DevPlan 170 W9-F1).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_resolve = sub.add_parser("resolve", help="Resolve node.yaml path + fields (owner_key, context, host, ...)")
    p_resolve.add_argument("--node", help="Node name (3-path search via NodeYaml.resolve)")
    p_resolve.add_argument("--file", help="Direct node.yaml path (overrides --node)")
    p_resolve.add_argument(
        "--platform-root",
        default=None,
        help="Base config dir for 3-path search (default: env PLATFORM_ROOT → platform_remote_base)",
    )
    p_resolve.add_argument(
        "--format",
        choices=["key=value", "json"],
        default="key=value",
        help="Output format: 'key=value' lines (shell read-контракт, default) or JSON",
    )

    p_mask = sub.add_parser("mask", help="Mask an AGE key for dry-run display: <AGE_KEY:xxxxxxxx...>")
    p_mask.add_argument("--key", required=True, help="AGE secret key to mask (never printed in full)")
    return parser


# endregion FUNC__build_parser


# region CLASS_CliArgs
class _CliArgs(argparse.Namespace):
    """Типизированный argparse-Namespace (W11-G3): parse_args(namespace=...)."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str
        self.file: str | None
        self.node: str | None
        self.platform_root: str | None
        self.format: str
        self.key: str


# endregion CLASS_CliArgs


# region FUNC__collect_fields
def _collect_fields(args: _CliArgs, get_many_runner: GetManyRunner | None) -> dict[str, str]:
    """Сборка полей для resolve-команды (путь → resolve_fields → owner_key → node_yaml_path).

    ▶ ┌args┐ → ◇ --file? | --node? | ✗ parser.error → ○ resolve_fields → ◇ owner_key? → ✗ return → ⊕ node_yaml_path → ⎋ fields

    ## @purpose  Выделено из main() для малой try-зоны (TRY300/PLR-разбивка): все raise-пути
    ##            (ResolverParseError / ResolverError / Config*) собраны в одном вызове —
    ##            обработка исключений живёт в _cli_resolve (2 try-стейтмента).
    ## @io — ⇥ args: argparse.Namespace, get_many_runner: DI → ⎋ dict[str, str] (6 полей + node_yaml_path)
    ## @complexity — O(P + N)
    ## @raises — ResolverParseError (битый вывод → exit 2), ResolverError/Config* (FATAL → exit 1)
    ## @invariants
    ##   - resolve без --node и --file → parser.error (SystemExit 2 — invalid input)
    ##   - owner_key пуст → stderr IMP:10 + return-путь exit 1 (контракт bootstrap.sh:101)
    ##   - node_yaml_path — артефакт резолвера (нужен shell: --node-yaml, NODE_CONFIGS_DIR)
    """
    if args.file:
        yaml_path = args.file
    elif args.node:
        yaml_path = resolve_node_yaml(node_name=args.node, platform_root=args.platform_root)
    else:
        parser = _build_parser()
        parser.error("resolve requires --node (3-path search) or --file (direct path)")
        return {}  # unreachable (parser.error exits)

    logger.info("[IMP:8][bootstrap_resolver][resolve] Resolving fields from %s", yaml_path)
    fields = resolve_fields(yaml_path, get_many_runner=get_many_runner)
    if not fields.get("owner_key"):
        # FATAL-контракт bootstrap.sh:101 («owner_key not found») — single source в Python.
        msg = f"owner_key not found in {yaml_path}"
        logger.error("[IMP:10][bootstrap_resolver][resolve] %s", msg)
        raise ResolverError(msg)
    fields["node_yaml_path"] = yaml_path
    return fields


# endregion FUNC__collect_fields


# region FUNC__cli_resolve
def _cli_resolve(args: _CliArgs, get_many_runner: GetManyRunner | None) -> int:
    """resolve-команда: поля → key=value/JSON-вывод. Exit 0 | 2 (parse) | 1 (FATAL).

    ▶ ┌args┐ → ○ _collect_fields (try) → ◇ ✗ ResolverParseError → ⎋ 2 → ◇ ✗ Resolver/Config* → ⎋ 1 → ⊕ emit → ⎋ 0

    ## @purpose  Печать результата отделена от сбора полей — try-зона минимальна (TRY300:
    ##            return 0/print вне try), исключения мапятся на exit-контракт 0/1/2.
    ## @io — ⇥ args, get_many_runner → ⎋ int (0 ok / 2 invalid input / 1 FATAL)
    ## @complexity — O(L) — печать полей
    ## @invariants — stdout содержит ТОЛЬКО данные; _emit не может бросить (print безопасен)
    """
    try:
        fields = _collect_fields(args, get_many_runner)
    except ResolverParseError as exc:
        logger.error("[IMP:10][bootstrap_resolver][resolve] %s", exc)
        return 2
    except (ResolverError, ConfigNotFoundError, ConfigParseError) as exc:
        logger.error("[IMP:10][bootstrap_resolver][resolve] %s", exc)
        return 1

    if args.format == "json":
        print(json.dumps(fields, indent=2))
    else:
        for key in _OUTPUT_ORDER:
            print(f"{key}={fields.get(key, '')}")
    logger.info("[IMP:9][bootstrap_resolver][resolve] Resolve OK (owner_key present)")
    return 0


# endregion FUNC__cli_resolve


# region FUNC_main
def main(
    argv: list[str] | None = None,
    *,
    get_many_runner: GetManyRunner | None = None,
) -> int:
    """CLI entrypoint. sys.exit вызывается только в __main__ (канон core/AGENTS.md).

    ▶ ┌argv┐ → ◇ resolve? → _cli_resolve (0|1|2) → ◇ mask? → print mask_age_key → ⎋ 0 → ✗ parser.error → ⎋ 2

    ## @purpose  Диспетчер subcommands (паттерн node_resolver.main). Интерфейс для bootstrap.sh:
    ##            ОДИН вызов resolve закрывает резолв пути + полей + валидацию owner_key;
    ##            mask — маскирование AGE-ключа для dry-run. Exit-контракт 0/1/2.
    ## @io — ⇥ argv: list[str] | None; get_many_runner: DI-сeam (native-тесты без subprocess) → ⎋ int
    ## @complexity — O(1) dispatch
    ## @invariants
    ##   - LDD: module-logger ребандлится к ТЕКУЩЕМУ sys.stderr (паттерн node_resolver.main) —
    ##     IMP:8-10 видны в CLI; caplog-телеметрия в pytest сохранена (propagation)
    ##   - main() -> int канон (core/AGENTS.md): sys.exit только в __main__
    """
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    cli_handler = logging.StreamHandler(sys.stderr)
    cli_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(cli_handler)
    logger.setLevel(logging.INFO)

    parser = _build_parser()
    args = parser.parse_args(argv, namespace=_CliArgs())

    if args.action == "resolve":
        return _cli_resolve(args, get_many_runner)

    if args.action == "mask":
        print(mask_age_key(args.key))
        logger.info("[IMP:9][bootstrap_resolver][mask] Masked AGE key for dry-run display")
        return 0

    parser.error(f"Unknown action: {args.action}")
    return 1  # unreachable (parser.error exits)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
