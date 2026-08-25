#!/usr/bin/env python3
# GREP_SUMMARY: gate workflow-sha-pins supply-chain uses-full-sha mutable-tag raw-interpolation quoted run-block REF-0012 gitleaks-checksum
# STRUCTURE: ▶ collect(*.yml|*.yaml ∈ workflows/actions/templates-workflows) → ◇ (a) uses: SHA-form(40hex)+version-comment → ◇ (b) run:-блоки: ${{ }} только внутри двойных кавычек → ⊕ R5-негативы (mutable-tag / raw / single-quote / local-exempt) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  CI supply-chain structural gate (REF-0012, meta-refactoring W0):
##           (a) КАЖДЫЙ внешний `uses:` во всех workflow/composite-action файлах запинен
##           на full commit SHA (40 hex) с версией в комментарии (`owner/action@<sha> # vX.Y.Z`);
##           (b) внутри `run:`-блоков нет сырых `${{ }}` вне двойных кавычек
##           (actionlint-style quoted-interpolation floor).
## @scope    *.yml и *.yaml под: .github/workflows/, .github/actions/,
##           templates/*/.github/workflows/. Локальные ссылки (`./...`) exempt
##           (in-repo composite/reusable actions не пинятся по определению).
## @invariants
##   - Внешний ref без `@<40hex>` → RED (mutable tag = пере-point тега upstream крадёт секреты)
##   - Комментарий с версией (`# v…`) обязателен у каждого SHA-пина (human-readable rev)
##   - Шаблонные пины (`{{ORG_NAME}}/...`) требуют комментария-снапшота (формат `# main snapshot …`)
##   - `${{ }}` в run:-строке вне двойных кавычек → RED (word-splitting/injection floor;
##     полная защита — env-indirect, гейт закрепляет минимальный барьер)
##   - Строки-комментарии (# …) в run:-блоках не сканируются (TRAP-доки с примерами легальны)
##   - Детекторы чистые (path/content → violations) — R5-негативы через tmp_path probe
##
## 🧐 TRAP[DECISION] · 2026-08-24 · — · SHA-pin распространён на шаблонные workflows проектов ·
## Rejected: «tags допустимы в project-repos» · Reason: канал build&push (REF-0001) создаётся этой
## же неделей — tag-pin в шаблонах оставил бы ту же аудит-дыру, что закрывает REF-0012, в самом
## новом канале; пин в шаблоне достаётся всем новым проектам бесплатно (adopted-легаси вне скоупа
## гейта) · Rev: если бамп actions в проектных репо станет операционной болью — пересмотреть.
##
## 🧐 TRAP[DECISION] · 2026-08-24 · — · Гейт покрывает *.yml И *.yaml ·
## Rejected: литеральный скоуп «*.yml» из карточки · Reason: .github/actions/static-artifacts/action.yaml
## (.yaml!) содержит actions/upload-artifact — расширительная дыра скоупа; supply-chain не зависит
## от расширения · Rev: если появятся генерируемые *.yaml под скан-корнями с легитимными тегами —
## добавить явный allowlist.
## @rationale Единственный вектор компрометации, не требующий доступа к репо: re-point плавающего
##            тега upstream'ом = кража deploy-ключей; trojaned scanner отключает leak-детект;
##            hostile PR печатает LITELLM_MASTER_KEY сырой интерполяцией. Digest-pin политика
##            канона применена к образам (инвариант DevOps), но не к actions/binary — гейт
##            закрепляет вторую половину.
## @changes 2026-08-24 | REF-0012 — Created (uses-SHA-form + raw-interpolation + R5 negatives)
##           2026-08-25 | QA C2/G6 (DevPlan 14 T1.1) — freshness-критерий деплой-канала:
##                      каждый literal deploy-project.yml@<40hex> (templates ×2, adopter-source,
##                      channel_pin.SoT) обязан содержать last-touch commit workflow'а
##                      (git merge-base --is-ancestor) и нести честную дату снапшота ≥ даты
##                      последнего изменения; негативы stale-pin / ложная дата / отсутствие даты
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib
import re
import subprocess

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()

# ── Scan roots (REF-0012 §Files) ─────────────────────────────────────────────

_SCAN_DIRS: tuple[pathlib.Path, ...] = (
    ROOT / ".github" / "workflows",
    ROOT / ".github" / "actions",
)
_TEMPLATE_WF_GLOBS: tuple[str, ...] = (
    "templates/*/.github/workflows/*.yml",
    "templates/*/.github/workflows/*.yaml",
)

# ── Patterns ─────────────────────────────────────────────────────────────────

_USES_LINE_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*([^\s#]+)\s*(#.*)?$")
_SHA40_RE = re.compile(r"[0-9a-f]{40}")
_RUN_KEY_RE = re.compile(r"^(\s*)(?:-\s+)?run:\s*(.*)$")


# region FUNC_iter_ci_yaml_files
def iter_ci_yaml_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Collect unique CI YAML files from all scan roots.

    ▶ ┌root┐ → ○ rglob(.github/{workflows,actions}) ∪ glob(templates/*/workflows) → ⊕ sorted unique → ⎋ list[Path]

    ## @purpose — Единая выборка файлов для обоих детекторов (workflows + composite actions +
    ##            шаблонные workflows проектов).
    ## @io — ⇥ root: pathlib.Path корня репо → ⎋ отсортированный список *.yml/*.yaml
    ## @complexity — O(F) где F = число файлов под скан-корнями
    ## @invariants — Дубликаты между glob-паттернами устраняются (set по resolved path).
    """
    files: set[pathlib.Path] = set()
    for scan_dir in _SCAN_DIRS:
        if scan_dir.is_dir():
            for ext in ("*.yml", "*.yaml"):
                files.update(p.resolve() for p in scan_dir.rglob(ext))
    for pattern in _TEMPLATE_WF_GLOBS:
        files.update(p.resolve() for p in root.glob(pattern))
    return sorted(files)


# endregion FUNC_iter_ci_yaml_files


# region FUNC_scan_uses_sha_pins
def scan_uses_sha_pins(path: pathlib.Path) -> list[str]:
    """Detect external `uses:` refs NOT pinned to a full commit SHA with version comment.

    ▶ ┌file┐ → ○ line-scan uses: → ◇ ./… или docker:// → skip | ◇ @<40hex>+comment → ok | иначе violation → ⎋ list[str]

    ## @purpose — Детектор (a): все внешние actions/reusable-workflows — full-SHA pinned,
    ##            комментарий с версией обязателен (REF-0012 SEC-0038).
    ## @io — ⇥ path файла → ⎋ список человекочитаемых violations (пустой = PASS)
    ## @complexity — O(L) строк на файл
    ## @invariants
    ##   - Локальные ссылки `./…` exempt (in-repo composite — контент контролируется гейтами репо)
    ##   - `docker://…` exempt (образные ссылки — digest-pin политика образов, другой домен)
    ##   - Шаблонный placeholder-ref (`{{ORG_NAME}}/…`) требует любой непустой комментарий-снапшот
    ##   - Обычный внешний пин требует `# v<digit>` в той же строке
    """
    violations: list[str] = []
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    try:
        lines = path.read_text(errors="replace", encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"{rel}: unreadable ({exc})"]
    for lineno, line in enumerate(lines, 1):
        match = _USES_LINE_RE.match(line)
        if not match:
            continue
        ref, comment = match.group(1), (match.group(2) or "").strip()
        if ref.startswith(("./", "docker://")):
            continue  # локальные composite/in-repo reusable — не пинятся
        if "@" not in ref:
            violations.append(f"{rel}:{lineno}: uses без '@ref': {ref!r}")
            continue
        owner_part, _, tag = ref.rpartition("@")
        if not _SHA40_RE.fullmatch(tag):
            violations.append(
                f"{rel}:{lineno}: mutable tag/ref '{tag}' — pin на full commit SHA "
                f"(owner/action@<40-hex> # vX.Y.Z): {ref!r}"
            )
            continue
        if "{{" in owner_part:
            # Шаблонный пин (scaffold-placeholder): версия = branch/tag в комментарии-снапшоте
            if not comment:
                violations.append(f"{rel}:{lineno}: шаблонный SHA-pin без комментария-снапшота: {ref!r}")
        elif not re.search(r"#\s*v\d", comment):
            violations.append(f"{rel}:{lineno}: SHA-pin без версии в комментарии ('# vX.Y.Z' обязателен): {ref!r}")
    return violations


# endregion FUNC_scan_uses_sha_pins


# region FUNC_check_script_line
def check_script_line(text: str) -> int:
    """Count raw `${{ }}` occurrences outside double quotes in a shell script fragment.

    ▶ ┌line┐ → ○ char-scan: toggle \" (не \\-escaped) → ◇ ${{ при outside → count++ → ⎋ int

    ## @purpose — Ядро детектора (b): GitHub расширяет выражения ТЕКСТУАЛЬНО до bash-парсинга;
    ##            вне двойных кавычек expansion подвергается word-splitting/globbing и может
    ##            содержать shell-метасимволы из inputs/secrets (injection floor, REF-0012).
    ## @io — ⇥ text фрагмента run-скрипта → ⎋ число нарушений
    ## @complexity — O(len(text))
    ## @invariants
    ##   - Одинарные кавычки НЕ считаются защитой: `'…${{ x }}…'` = violation (значение с '
    ##     вырывается из single-quote контекста; гейт нормализует стиль на double-quote)
    ##   - Экранированные \\" не переключают состояние
    """
    violations = 0
    in_double_quote = False
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_double_quote = not in_double_quote
        elif ch == "$" and text.startswith("${{", i) and not in_double_quote:
            violations += 1
            i += 3
            continue
        i += 1
    return violations


# endregion FUNC_check_script_line


# region FUNC_collect_line_hits
def collect_line_hits(text: str, rel: str, lineno: int, label: str, violations: list[str]) -> None:
    """Append a violation when `text` contains raw `${{ }}` outside double quotes.

    ▶ ┌text,label┐ → ◇ check_script_line>0? → ⊕ violation-строка в violations → ⎋ void

    ## @purpose — Общая точка эмиссии violations для inline- и block-форм run (C901-декомпозиция).
    ## @io — ⇥ text/rel/lineno/label + мутабельный violations → ⎋ None (side-effect: append)
    ## @complexity — O(len(text))
    """
    hits = check_script_line(text)
    if hits:
        violations.append(f"{rel}:{lineno}: raw ${{{{ }}}} вне двойных кавычек {label} ({hits})")


# endregion FUNC_collect_line_hits


# region FUNC_scan_raw_interpolation
def scan_raw_interpolation(path: pathlib.Path) -> list[str]:
    """Detect raw `${{ }}` in run:-blocks outside double quotes (state machine over lines).

    ▶ ┌file┐ → ◇ run:|/> → block-mode(indent) | ◇ run:inline → check(value) | ○ block-line → check → ⎋ list[str]

    ## @purpose — Детектор (b): actionlint-style проверка отсутствия raw `${{ }}` внутри `run:`
    ##            вне двойных кавычек (Tests-required REF-0012).
    ## @io — ⇥ path файла → ⎋ список violations (пустой = PASS)
    ## @complexity — O(L × len(line))
    ## @invariants
    ##   - Блочные скаляры (| > с чампингом) отслеживаются по indentation (> indent ключа)
    ##   - Inline-форма `run: <text>` проверяется целиком
    ##   - Комментарий-строки (# …) пропускаются (примеры в док-комментариях легальны)
    ##   - Ограничение (documented floor): trailing-комментарии после кода сканируются как код
    """
    violations: list[str] = []
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    try:
        lines = path.read_text(errors="replace", encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"{rel}: unreadable ({exc})"]
    in_block = False
    block_indent = 0
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        run_match = _RUN_KEY_RE.match(line)
        if run_match:
            value = run_match.group(2).strip()
            if value.startswith(("|", ">")):
                in_block = True
                block_indent = len(run_match.group(1))
            else:
                in_block = False
                collect_line_hits(value, rel, lineno, "в run:", violations)
            continue
        if not in_block:
            continue
        if not stripped:
            continue
        if len(line) - len(line.lstrip()) <= block_indent:
            in_block = False  # dedent — блок закончился; строка вне блока
            continue
        if stripped.startswith("#"):
            continue  # док-комментарии с примерами легальны
        collect_line_hits(stripped, rel, lineno, "в run-блоке", violations)
    return violations


# endregion FUNC_scan_raw_interpolation


# region TESTS_POSITIVE


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · uses-SHA-form (REF-0012, SEC-0038)
# · Scenario: любой `uses:` внешнего action на плавающем теге → re-point тега upstream =
#   кража VPS_SSH_KEY/CI_DEPLOY_KEY/AGE_SECRET_KEY без доступа к репо
# · Last fail: 2026-08-24 — 25 внешних refs на mutable tags (checkout@v7 ×8, trivy@v0.36.0,
#   codeql@v4, docker/* ×7, cache/upload-artifact/setup-*); шаблоны проектов @main
# · Remove if: платформа мигрирует на OIDC/fork-pinned registry экшенов (обновить инвариант)
def test_all_external_actions_pinned_full_sha(caplog) -> None:
    """Каждый внешний `uses:` — full commit SHA (40 hex) + версия в комментарии."""
    files = iter_ci_yaml_files(ROOT)
    assert files, "Не найдено ни одного CI YAML под скан-корнями — сломана выборка гейта"
    logger.info("[IMP:8][sha-pins][collect] scanned %d CI yaml files", len(files))

    violations: list[str] = []
    for path in files:
        violations.extend(scan_uses_sha_pins(path))
    if violations:
        for v in violations:
            logger.error("[IMP:10][sha-pins] %s", v)
        pytest.fail(
            "[GATE:FAIL][id:workflow-sha-pins][class:L2]\n"
            f"External actions НЕ на full commit SHA ({len(violations)}):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nPin format: `owner/action@<full-sha> # vX.Y.Z` (dependabot обновляет; "
            "SHA резолвится `gh api repos/<owner>/<repo>/commits/<tag>`)."
        )
    logger.info("[IMP:9][sha-pins] PASS: все внешние uses: — full-SHA pins с версией в комментарии")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · quoted interpolation в run: (REF-0012, AI-0022/A-13)
# · Scenario: hostile PR печатает LITELLM_MASTER_KEY сырой ${{ secrets.* }} интерполяцией;
#   inputs.node/host с shell-метасимволами ломают SSH-команду deploy-project.yml
# · Last fail: 2026-08-24 — core-deploy/mirror/hermes-nightly/deploy-project: ~15 raw сайтов
#   в run:-блоках (включая secrets.VPS_HOST и secrets.AGE_SECRET_KEY)
# · Remove if: все run:-шаги переведены на env-indirect + валидацию входов (гейту нечего ловить)
def test_run_blocks_quoted_interpolation_only(caplog) -> None:
    """В run:-блоках нет сырых `${{ }}` вне двойных кавычек."""
    files = iter_ci_yaml_files(ROOT)
    violations: list[str] = []
    for path in files:
        violations.extend(scan_raw_interpolation(path))
    if violations:
        for v in violations:
            logger.error("[IMP:10][quoted-interp] %s", v)
        pytest.fail(
            "[GATE:FAIL][id:workflow-sha-pins][class:L2]\n"
            f"Raw ${{{{ }}}} вне двойных кавычек в run:-блоках ({len(violations)}):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nFix: двойные кавычки вокруг каждого ${{ … }} (или env-indirect: значение "
            'в step-level env:, использование "$VAR" в скрипте).'
        )
    logger.info("[IMP:9][quoted-interp] PASS: 0 raw ${{ }} вне двойных кавычек во всех run:-блоках")


# endregion TESTS_POSITIVE


# region TESTS_NEGATIVE_R5


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-24 · NEGATIVE (R5) · sha-pins — исходное состояние до REF-0012
# · Last fail: `actions/checkout@v7` (исходный вход, пойманный аудитом SEC-0038)
# · Remove if: детектор SHA-пинов удалён вместе с позитивным тестом
def test_negative_mutable_tag_detected(tmp_path, caplog) -> None:
    """R5 negative: mutable tag (`@v7`) детектируется как violation."""
    probe = tmp_path / "probe.yml"
    probe.write_text(
        "- name: Checkout repository\n  uses: actions/checkout@v7\n",
        encoding="utf-8",
    )
    violations = scan_uses_sha_pins(probe)
    logger.info("[IMP:8][sha-pins][negative] mutable-tag violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: mutable tag не детектирован: {violations!r}"
    assert "mutable tag" in violations[0], f"R5 FAIL: неверная категория: {violations!r}"
    logger.info("[IMP:9][sha-pins][negative] PASS: mutable tag детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-24 · NEGATIVE (R5) · sha-pins — SHA-pin без комментария версии
# · Last fail: гипотетический `actions/checkout@<sha>` без `# vX.Y.Z` (rev неизвестен человеку)
# · Remove if: требование version-комментария отменено планом
def test_negative_sha_pin_without_version_comment_detected(tmp_path, caplog) -> None:
    """R5 negative: full-SHA pin БЕЗ `# vX.Y.Z` комментария детектируется."""
    probe = tmp_path / "probe.yml"
    probe.write_text(
        "- name: Checkout repository\n  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n",
        encoding="utf-8",
    )
    violations = scan_uses_sha_pins(probe)
    logger.info("[IMP:8][sha-pins][negative] no-comment violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: отсутствие версии в комментарии не детектировано: {violations!r}"
    logger.info("[IMP:9][sha-pins][negative] PASS: pin без версии в комментарии детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-24 · NEGATIVE (R5) · sha-pins — local refs exempt
# · Regression: ужесточение детектора до RED на `./…` сломало бы все 12 composite actions репо
# · Last fail: N/A (preventive — контракт exemption зафиксирован в карточке REF-0012)
# · Remove if: локальные composite actions начинают пиниться (изменение модели поставки)
def test_negative_local_action_refs_exempt(tmp_path, caplog) -> None:
    """Локальные `./…` и `docker://` refs — exempt (0 violations на корректном файле)."""
    probe = tmp_path / "probe.yml"
    probe.write_text(
        "steps:\n"
        "  - uses: ./.github/actions/setup-gitleaks\n"
        "  - uses: docker://ghcr.io/example/image@sha256:"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "  - uses: {{ORG_NAME}}/ai-platform/.github/workflows/deploy-project.yml@"
        "4425ce0835665b1d0a9ef3f1e2cbf702663645ed # main snapshot 2026-08-24\n",
        encoding="utf-8",
    )
    violations = scan_uses_sha_pins(probe)
    logger.info("[IMP:8][sha-pins][negative] local-exempt violations=%d", len(violations))
    assert violations == [], f"R5 FAIL: ложное срабатывание на exempt-формах: {violations!r}"
    logger.info("[IMP:9][sha-pins][negative] PASS: local/docker/template-пины не дают violations")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-24 · NEGATIVE (R5) · quoted-interp — сырая интерполяция секрета
# · Last fail: core-deploy.yml (до REF-0012): `AGE_SECRET_KEY=\"${{ secrets.AGE_SECRET_KEY }}\"`
#   и `$({{ secrets.VPS_HOST }})`-стиль user@host в ssh-командах — исходные входы аудита AI-0022
# · Remove if: детектор raw-interpolation удалён вместе с позитивным тестом
def test_negative_raw_interpolation_outside_quotes_detected(tmp_path, caplog) -> None:
    """R5 negative: raw ${{ }} вне кавычек в run-блоке детектируется."""
    probe = tmp_path / "probe.yml"
    probe.write_text(
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - run: |\n"
        '          ssh ${{ steps.ssh_opts.outputs.opts }} admin@${{ secrets.VPS_HOST }} "echo OK"\n',
        encoding="utf-8",
    )
    violations = scan_raw_interpolation(probe)
    logger.info("[IMP:8][quoted-interp][negative] raw violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: raw интерполяция не детектирована: {violations!r}"
    logger.info("[IMP:9][quoted-interp][negative] PASS: raw интерполяция вне кавычек детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-24 · NEGATIVE (R5) · quoted-interp — одинарные кавычки не защита
# · Last fail: deploy-project.yml:151 (до REF-0012): EXPECTED_NAME='${{ inputs.project_name }}'
#   — значение с одинарной кавычкой вырывается из single-quote контекста
# · Remove if: семантика гейта расширена до env-indirect-only (одинарные станут невалидными там же)
def test_negative_single_quoted_interpolation_detected(tmp_path, caplog) -> None:
    """R5 negative: `'${{ … }}'` (single quotes) детектируется — защита только double-quote."""
    probe = tmp_path / "probe.yml"
    probe.write_text(
        "jobs:\n  j:\n    steps:\n      - run: |\n          EXPECTED_NAME='${{ inputs.project_name }}'\n",
        encoding="utf-8",
    )
    violations = scan_raw_interpolation(probe)
    logger.info("[IMP:8][quoted-interp][negative] single-quote violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: single-quote форма не детектирована: {violations!r}"
    logger.info("[IMP:9][quoted-interp][negative] PASS: single-quote интерполяция детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-24 · NEGATIVE (R5) · quoted-interp — double-quote форма проходит
# · Regression: ложное срабатывание на канонической форме `"${{ … }}"` заблокировало бы
#   легитимные правки (friction > gain, правило allowlist-гейтов)
# · Last fail: N/A (preventive)
# · Remove if: гейт мигрирует на AST-парсер bash (проверки станут структурными)
def test_negative_double_quoted_form_passes(tmp_path, caplog) -> None:
    """Корректная double-quote форма (`"${{ … }}"`) и комментарии не дают violations."""
    probe = tmp_path / "probe.yml"
    probe.write_text(
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - run: |\n"
        '          # пример: git checkout "${{ steps.sha.outputs.sha }}"\n'
        '          git checkout "${{ steps.sha.outputs.sha }}"\n'
        '          echo "[IMP:9][x] host=${{ env.ssh_host }} verified"\n',
        encoding="utf-8",
    )
    violations = scan_raw_interpolation(probe)
    logger.info("[IMP:8][quoted-interp][negative] false-positive violations=%d", len(violations))
    assert violations == [], f"R5 FAIL: ложное срабатывание на double-quote форме: {violations!r}"
    logger.info("[IMP:9][quoted-interp][negative] PASS: double-quote форма и комментарии проходят")


# endregion TESTS_NEGATIVE_R5


# region CHANNEL_FRESHNESS_G6 (QA C2/G6 — DevPlan 14 T1.1)

# Пин reusable деплой-канала: `deploy-project.yml@<40hex> # <comment>`
_DEPLOY_CHANNEL_USES_RE = re.compile(r"deploy-project\.yml@([0-9a-f]{40})[ \t]*(?:#[ \t]*(.*))?")
_SNAPSHOT_DATE_RE = re.compile(r"main snapshot (\d{4}-\d{2}-\d{2})")
_PLATFORM_WF_RELPATH = ".github/workflows/deploy-project.yml"
_ADOPTER_RELPATH = "core/internal/scaffold/project_adopter.py"
_CHANNEL_PIN_RELPATH = "core/internal/scaffold/channel_pin.py"


# region FUNC_collect_deploy_channel_pins
def collect_deploy_channel_pins(root: pathlib.Path) -> list[tuple[str, str, str]]:
    """Collect (label, sha, comment) for every literal deploy-channel pin + the channel_pin SoT value.

    ▶ ┌root┐ → ○ scan templates/*/workflows + adopter source literals → ⊕ append SoT DEPLOY_CHANNEL_PIN → ⎋ list[(label, sha, comment)]

    ## @purpose — Единая выборка всех трёх мест пина канала (гейт-equalizer triple-literal):
    ##            шаблоны ×2, остаточные литералы в исходнике adopter'а, значение SoT-модуля.
    ## @io — ⇥ root репо → ⎋ список (label, sha, comment); label — relpath источника
    ## @complexity — O(T + A), T/A — размеры шаблонов и project_adopter.py
    ## @invariants — SoT-значение добавляется ВСЕГДА (даже если нигде больше не найдено).
    """
    pins: list[tuple[str, str, str]] = []
    for pattern in _TEMPLATE_WF_GLOBS:
        pins.extend(
            (path.relative_to(root).as_posix(), m.group(1), m.group(2) or "")
            for path in sorted(root.glob(pattern))
            for m in _DEPLOY_CHANNEL_USES_RE.finditer(path.read_text(encoding="utf-8", errors="replace"))
        )
    adopter_src = root / _ADOPTER_RELPATH
    if adopter_src.exists():
        pins.extend(
            (f"{_ADOPTER_RELPATH}:literal", m.group(1), m.group(2) or "")
            for m in _DEPLOY_CHANNEL_USES_RE.finditer(adopter_src.read_text(encoding="utf-8", errors="replace"))
        )
    # Lazy import: tests/conftest добавляет repo-root в sys.path
    from core.internal.scaffold.channel_pin import DEPLOY_CHANNEL_PIN, PIN_COMMENT

    pins.append((_CHANNEL_PIN_RELPATH, DEPLOY_CHANNEL_PIN, PIN_COMMENT))
    return pins


# endregion FUNC_collect_deploy_channel_pins


# region FUNC_check_channel_pin_freshness
def check_channel_pin_freshness(root: pathlib.Path, pin: str, comment: str) -> list[str]:
    """Offline freshness check of one deploy-channel pin against git history.

    ▶ ┌root,pin,comment┐ → ◇ last-touch(workflow) → ◇ merge-base --is-ancestor(last-touch, pin)? → ◇ snapshot-date ≥ last-touch-date? → ⎋ list[str]

    ## @purpose — G6 freshness: пин обязан содержать последнее изменение deploy-project.yml,
    ##            а комментарий-снапшот — честную дату ≥ даты того изменения (fixture QA C2:
    ##            «main snapshot 2026-08-24» при пине от 2026-08-18 = ложь).
    ## @io — ⇥ root/pin/comment → ⎋ violations ([] = fresh)
    ## @complexity — O(1) git-запросов (2 вызова)
    ## @invariants — Работает офлайн на локальной git-истории; отсутствие истории → violation.
    """
    violations: list[str] = []
    log_proc = subprocess.run(
        ["git", "log", "-1", "--format=%H %cI", "--", _PLATFORM_WF_RELPATH],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if log_proc.returncode != 0 or not log_proc.stdout.strip():
        return [f"{_PLATFORM_WF_RELPATH}: cannot resolve last-touch commit ({log_proc.stderr.strip()[:120]})"]
    parts = log_proc.stdout.strip().split(maxsplit=1)
    last_sha, last_date = parts[0], parts[1][:10]
    anc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", last_sha, pin],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if anc.returncode != 0:
        violations.append(
            f"stale pin {pin[:12]}…: {_PLATFORM_WF_RELPATH} последний раз менялся в "
            f"{last_sha[:12]}… ({last_date}) — пин НЕ содержит это изменение "
            "(git merge-base --is-ancestor != 0)"
        )
    date_match = _SNAPSHOT_DATE_RE.search(comment or "")
    if not date_match:
        violations.append(
            f"pin {pin[:12]}…: комментарий без даты снапшота ('main snapshot YYYY-MM-DD' обязателен): {comment!r}"
        )
    elif date_match.group(1) < last_date:
        violations.append(
            f"pin {pin[:12]}…: ложная дата комментария {date_match.group(1)} < даты последнего "
            f"изменения {_PLATFORM_WF_RELPATH} ({last_date})"
        )
    return violations


# endregion FUNC_check_channel_pin_freshness


def _init_probe_channel_repo(tmp_path: pathlib.Path) -> tuple[str, str]:
    """Build a minimal git repo with a 2-commit workflow history → (stale_sha, fresh_sha).

    ▶ ┌tmp_path┐ → ○ git init+config → ⚡ commit c1 (base) → commit c2 (hardening) → ⎋ (c1, c2)

    ## @purpose — Изолированная git-история для R5-негативов freshness-критерия (без записи в рабочее репо).
    ## @io — ⇥ tmp_path → ⎋ (sha первого коммита, sha второго)
    """

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["git", *args], cwd=str(tmp_path), capture_output=True, text=True, timeout=30, check=False
        )
        assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
        return proc

    run("init", "-q")
    run("config", "user.email", "gate-probe@example.local")
    run("config", "user.name", "gate-probe")
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "deploy-project.yml"
    wf.write_text("name: Deploy Project (Reusable)\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "c1 base workflow")
    stale_sha = run("rev-parse", "HEAD").stdout.strip()
    wf.write_text("name: Deploy Project (Reusable)\n# REF-0011/0012 hardening (+74/-12)\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "c2 hardening")
    fresh_sha = run("rev-parse", "HEAD").stdout.strip()
    return stale_sha, fresh_sha


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · freshness деплой-канала (QA C2/G6, DevPlan 14 T1.1)
# · Scenario: шаблонные и adopted проекты получают канал от stale-pin 4425ce0 (2026-08-18),
#   который НЕ содержит +74/−12 харденинга REF-0011/0012 — новые проекты рождаются без защиты
# · Last fail: 2026-08-25 — оба шаблона + adopter генерировали @4425ce0…/@main с ложной датой
#   комментария «2026-08-24»
# · Remove if: деплой-канал мигрирует на механизм, где freshness гарантирован платформой
#   (например, org-level pinned release channel)
def test_channel_pins_fresh_and_consistent(caplog) -> None:
    """Все literal пины канала (шаблоны ×2 + SoT) свежи и байт-идентичны channel_pin.py."""
    pins = collect_deploy_channel_pins(ROOT)
    assert len(pins) >= 3, f"ожидались пины из 2 шаблонов + SoT channel_pin.py, найдено: {pins!r}"
    logger.info("[IMP:8][channel-freshness][collect] %d pin site(s): %s", len(pins), [p[0] for p in pins])

    from core.internal.scaffold.channel_pin import DEPLOY_CHANNEL_PIN, PIN_COMMENT

    template_pins = {(sha, cmt) for label, sha, cmt in pins if label.startswith("templates/")}
    assert template_pins == {(DEPLOY_CHANNEL_PIN, PIN_COMMENT)}, (
        f"Шаблонные пины расходятся с SoT channel_pin.py {(DEPLOY_CHANNEL_PIN, PIN_COMMENT)!r}: {template_pins!r}"
    )

    violations: list[str] = []
    for label, sha, cmt in pins:
        violations.extend(f"{label}: {v}" for v in check_channel_pin_freshness(ROOT, sha, cmt))
    if violations:
        for v in violations:
            logger.error("[IMP:10][channel-freshness] %s", v)
        pytest.fail(
            "[GATE:FAIL][id:workflow-sha-pins][class:L2]\n"
            f"Deploy-channel pin stale/false-comment ({len(violations)}):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nFix: перепинить templates/*/deploy.yml + core/internal/scaffold/channel_pin.py "
            "на HEAD и поставить ЧЕСТНУЮ дату снапшота."
        )
    logger.info("[IMP:9][channel-freshness] PASS: все %d пинов канала свежи и консистентны с SoT", len(pins))


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · freshness — stale-pin (исходный вход QA C2)
# · Last fail: пин 4425ce0 (родитель last-touch коммита с харденингом) — ровно этот вход
# · Remove if: freshness-детектор удалён вместе с позитивным тестом
def test_negative_stale_channel_pin_detected(tmp_path, caplog) -> None:
    """R5 negative: пин-родитель last-touch коммита (не содержит изменение) → RED."""
    stale_sha, _fresh_sha = _init_probe_channel_repo(tmp_path)
    violations = check_channel_pin_freshness(tmp_path, stale_sha, "main snapshot 2099-01-01")
    logger.info("[IMP:8][channel-freshness][negative] stale violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: stale-pin не детектирован: {violations!r}"
    assert any("stale pin" in v for v in violations), f"R5 FAIL: неверная категория: {violations!r}"
    logger.info("[IMP:9][channel-freshness][negative] PASS: stale-pin детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · freshness — ложная дата комментария (QA C2 fixture)
# · Last fail: комментарий «main snapshot 2026-08-24» при пине от 2026-08-18 — ложь в комментарии
#   прошла бы незамеченной без дата-критерия
# · Remove if: дата-критерий отменён планом
def test_negative_false_snapshot_date_detected(tmp_path, caplog) -> None:
    """R5 negative: дата комментария РАНЬШЕ даты последнего изменения файла → RED."""
    _stale_sha, fresh_sha = _init_probe_channel_repo(tmp_path)
    violations = check_channel_pin_freshness(tmp_path, fresh_sha, "main snapshot 2020-01-01")
    logger.info("[IMP:8][channel-freshness][negative] false-date violations=%d", len(violations))
    assert any("ложная дата" in v for v in violations), f"R5 FAIL: ложная дата не детектирована: {violations!r}"
    logger.info("[IMP:9][channel-freshness][negative] PASS: ложная дата комментария детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · freshness — комментарий без даты снапшота
# · Last fail: N/A (preventive — формат 'main snapshot YYYY-MM-DD' контракт гейта)
# · Remove if: формат комментария отменён
def test_negative_missing_snapshot_date_detected(tmp_path, caplog) -> None:
    """R5 negative: пин без 'main snapshot YYYY-MM-DD' в комментарии → RED."""
    _stale_sha, fresh_sha = _init_probe_channel_repo(tmp_path)
    violations = check_channel_pin_freshness(tmp_path, fresh_sha, "some unrelated note")
    logger.info("[IMP:8][channel-freshness][negative] no-date violations=%d", len(violations))
    assert any("без даты снапшота" in v for v in violations), (
        f"R5 FAIL: отсутствие даты не детектировано: {violations!r}"
    )
    logger.info("[IMP:9][channel-freshness][negative] PASS: отсутствие даты снапшота детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · freshness — свежий пин с честной датой проходит
# · Regression: защита от false-positive — легитимный bump пина не должен ломать CI
# · Last fail: N/A (preventive)
# · Remove if: вместе с детектором
def test_positive_fresh_probe_passes(tmp_path, caplog) -> None:
    """Свежий пин (= last-touch commit) с датой ≥ даты коммита → 0 violations."""
    _stale_sha, fresh_sha = _init_probe_channel_repo(tmp_path)
    date_proc = subprocess.run(
        ["git", "log", "-1", "--format=%cI", "--", ".github/workflows/deploy-project.yml"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    honest_date = date_proc.stdout.strip()[:10]
    violations = check_channel_pin_freshness(tmp_path, fresh_sha, f"main snapshot {honest_date}")
    logger.info("[IMP:8][channel-freshness][positive-probe] violations=%d", len(violations))
    assert violations == [], f"R5 FAIL: ложное срабатывание на свежем пине: {violations!r}"
    logger.info("[IMP:9][channel-freshness][positive-probe] PASS: свежий пин с честной датой проходит")


# endregion CHANNEL_FRESHNESS_G6
# ═══════════════════════════════════════════════════════════════════
# region TESTS_FETCH_DEPTH (DevPlan 16 T1.G / P0-4)
# ═══════════════════════════════════════════════════════════════════

# 🧐 TRAP[DECISION] · 2026-08-25 · DevPlan 16 T1.G · shallow checkout = конфигурационная
# ошибка, fallback в гейте не предусмотрен · Rejected: date-fallback oracle внутри
# test_gate_workflow_sha_pins (сопоставление дат при depth=1) · Reason: дешёвый upstream-фикс
# (fetch-depth: 0) против второй кодовой ветки сопоставления со своей поверхностью ложных
# срабатываний — YAGNI до появления evidence · Rev: если full-history checkout станет
# невозможен (форк-ограничения GitHub) — вернуться к fallback-оракулу

_FETCH_DEPTH_WORKFLOWS: tuple[str, ...] = (
    ".github/workflows/push-gate.yml",
    ".github/workflows/platform-gate-fast.yml",
)


def _workflow_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · DevPlan 16 T1.G P0-4 · fetch-depth: 0 в гейт-workflows
# · Regression: без полной истории git log/merge-base freshness-гейта резолвит last-touch в
#   граничный коммит shallow-checkout → ложный stale-pin RED на первом же пушу после CI
# · Scenario: оба workflow содержат `fetch-depth: 0` под actions/checkout
# · Last fail: аудит 15 P0-4 — grep: единственный fetch-depth: 0 был в mirror.yml
# · Remove if: гейт перестанет использовать merge-base/git-log (oracle изменён)
def test_workflows_fetch_depth_zero(caplog) -> None:
    for rel in _FETCH_DEPTH_WORKFLOWS:
        text = _workflow_text(rel)
        assert re.search(
            r"actions/checkout@[0-9a-f]{40}[^\n]*\n\s+with:\n(?:\s*#[^\n]*\n)*\s*fetch-depth:\s*0\b", text
        ), f"{rel}: checkout обязан нести fetch-depth: 0 (полная история для merge-base, P0-4)"
    logger.info("[IMP:9][fetch-depth][ok] %d workflow(s) с fetch-depth: 0", len(_FETCH_DEPTH_WORKFLOWS))


@pytest.mark.gate
# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T1.G · удаление fetch-depth детектируется
# · Scenario: probe-fixture без fetch-depth (воспроизведение исходного дефекта) не проходит
#   паттерн-детектор
# · Remove if: вместе с test_workflows_fetch_depth_zero
def test_negative_missing_fetch_depth_detected(tmp_path) -> None:
    probe = tmp_path / "probe.yml"
    # Исходная форма (дефект): checkout БЕЗ with/fetch-depth
    probe.write_text(
        "steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7\n      - name: next\n",
        encoding="utf-8",
    )
    pattern = r"actions/checkout@[0-9a-f]{40}[^\n]*\n\s+with:\n(?:\s*#[^\n]*\n)*\s*fetch-depth:\s*0\b"
    assert not re.search(pattern, probe.read_text(encoding="utf-8")), (
        "R5 FAIL: fixture воспроизводит исходный дефект, но паттерн сматчился"
    )
    logger.info("[IMP:9][fetch-depth][negative] PASS: отсутствие fetch-depth детектируется")


# endregion TESTS_FETCH_DEPTH
