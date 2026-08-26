# GREP_SUMMARY: test-templates template-validation ai-platform.yaml glob schema parametrize LDD IMP caplog
# STRUCTURE: pathlib(templates/*/ai-platform.yaml) → parametrize → load_schema → load_yaml → validate → ⊕ result → ⎋ PASS/FAIL

# region MODULE_CONTRACT
## @purpose  Параметризованная валидация всех шаблонов проекта против JSON Schema.
##           Автоматическое обнаружение новых шаблонов через glob — без хардкода списков.
## @scope    tests/test_templates.py — параметризованный pytest тест.
## @invariants
##   - Все шаблоны в templates/*/ai-platform.yaml валидируются автоматически
##   - Список шаблонов не хардкодится — glob находит все существующие
##   - При добавлении нового шаблона тест подхватывает его без изменений
##   - At least one IMP:9 log per §TESTING
## @rationale Q: Почему paramtrize вместо хардкода? A: Устраняет рассинхронизацию
##            при добавлении новых шаблонов — не нужно обновлять список тестов.
## @changes — CREATED: 2026-07-02 | Wave 2 TASK-5 — parameterized template validation


# endregion MODULE_CONTRACT

import json
import logging
import re

import pytest
import yaml
from jsonschema import ValidationError, validate

logger = logging.getLogger(__name__)

import pathlib

from tests.helpers.gate_helpers import repo_root

# --- [IMP:7] Типы шаблонов платформы (DevPlan 141: ai-platform.yaml генерируется
# gen_ai_platform_yaml при scaffold — runtime SoT, шаблоны не хранят манифест).
# ids сохранены (template-backend/template-frontend) — стабильные nodeid для inventory.
TEMPLATE_TYPES = ("backend", "frontend")
SCHEMA_PATH = str(repo_root() / "core/schemas/ai-platform.schema.json")

# Плейсхолдеры, используемые в шаблонах — заменяются add-project.sh
_PLACEHOLDER_RE = re.compile(r"^\$[A-Z_]+$")


def _replace_placeholders(obj):
    """Рекурсивно заменить placeholder'ы ($VAR) на валидные тестовые значения.

    ## @purpose — Позволяет валидировать структуру шаблонов (required поля,
    ##            типы, доп. свойства) несмотря на placeholder'ы, которые
    ##            заменяются add-project.sh при создании проекта.
    ## @io — ⇥ obj: dict|list|str|any — узел YAML → ⎋ скопированный узел с заменами
    ## @complexity — O(N) где N = количество узлов в YAML
    ## @invariants
    ##   - Строки вида $VAR заменяются на тестовые значения
    ##   - Все остальные значения копируются без изменений
    ##   - Исходный объект не мутируется
    """
    if isinstance(obj, dict):
        return {k: _replace_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_placeholders(v) for v in obj]
    if isinstance(obj, str) and _PLACEHOLDER_RE.match(obj):
        # Карта замен: специфичные плейсхолдеры → валидные по схеме значения
        REPLACEMENTS = {
            "$PROJECT_NAME": "testproject",
            "$NODE_NAME": "node1",
            "$DOMAIN": "example.com",
            "$DB_NAME": "testdb",
        }
        return REPLACEMENTS.get(obj, "testvalue")
    return obj


# ⚠️ TRAP[BUG] · 2026-07-15 · MED · Параметризационные ID зависели от абсолютного пути
# · Symptom: ids=p.split("/")[1] → второй сегмент пути: Users0..2 (macOS), private0..2 (worktree
# ·   в /private/var), home0..2 (CI ubuntu) — inventory-gate ловил фантомные removals между окружениями.
# · Fix: стабильный ID = имя каталога шаблона (template-backend, ...) — не зависит от корня checkout.
@pytest.fixture(scope="module")
def runtime_ai_platform_yamls(tmp_path_factory) -> dict[str, str]:
    """Сгенерировать ai-platform.yaml для каждого типа шаблона через gen_ai_platform_yaml.

    ## @purpose — Runtime SoT (DevPlan 141 W1): шаблоны не хранят ai-platform.yaml —
    ##            генератор единственный источник. Гейт валидирует output генератора
    ##            против JSON Schema для каждого типа шаблона (backend/frontend).
    ## @io — ⎋ dict[ptype → str path к сгенерированному ai-platform.yaml]
    ## @complexity — O(N) где N = типы шаблонов
    """
    from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml

    root = tmp_path_factory.mktemp("runtime-template-yamls")
    paths: dict[str, str] = {}
    for ptype in TEMPLATE_TYPES:
        out = root / f"template-{ptype}" / "ai-platform.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        gen_ai_platform_yaml(
            name=f"test-{ptype}",
            ptype=ptype,
            node="test-node",
            domain="example.com",
            database="",
            mode="",
            output_path=str(out),
        )
        paths[ptype] = str(out)
        logger.info("[IMP:8][test_templates] Generated runtime ai-platform.yaml: %s", out)
    return paths


@pytest.mark.parametrize("ptype", TEMPLATE_TYPES, ids=["template-backend", "template-frontend"])
def test_template_validates_against_schema(ptype: str, runtime_ai_platform_yamls: dict[str, str]) -> None:
    """Валидация сгенерированного ai-platform.yaml (gen_ai_platform_yaml) против JSON Schema.

    ## @purpose — Гарантирует, что конфиг, генерируемый для каждого типа шаблона,
    ##           соответствует схеме (DevPlan 141: манифест — runtime, не статика).
    ## @io — ⇥ ptype: str — тип шаблона → ⎋ None (pytest assert)
    ## @complexity — O(N) — single schema validation per call
    ## @invariants
    ##   - Сгенерированный YAML должен быть валидным
    ##   - Содержимое должно соответствовать схеме (любое несоответствие — падение)
    """
    template_path = runtime_ai_platform_yamls[ptype]
    logger.info("[IMP:7][test_template_validates_against_schema] Validating %s (runtime)", template_path)

    # --- [IMP:8] Загрузка схемы
    with pathlib.Path(SCHEMA_PATH).open(encoding="utf-8") as f:
        schema = json.load(f)
    logger.info("[IMP:8] Schema loaded from %s", SCHEMA_PATH)

    # --- [IMP:8] Загрузка шаблона с заменой {{VAR}} placeholder'ов перед YAML-парсингом
    # Шаблоны теперь используют {{VAR}} синтаксис template engine, который не является
    # валидным YAML ({{}} интерпретируется как flow mapping). Заменяем на текстовые
    # значения ДО safe_load, чтобы YAML-парсер не падал.
    TEMPLATE_VAR_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")
    VAR_REPLACEMENTS = {
        "{{PROJECT_NAME}}": "testproject",
        "{{ORG_NAME}}": "testorg",
        "{{DOMAIN}}": "example.com",
        "{{NODE_NAME}}": "node1",
        "{{PLATFORM_DOMAIN}}": "platform.example.com",
        "{{CONTEXT}}": "testctx",
    }

    with pathlib.Path(template_path).open(encoding="utf-8") as f:
        raw_text = f.read()

    def _replace_var(m: re.Match) -> str:
        return VAR_REPLACEMENTS.get(m.group(0), "testvalue")

    clean_text = TEMPLATE_VAR_RE.sub(_replace_var, raw_text)
    manifest = yaml.safe_load(clean_text)
    logger.info("[IMP:8] {{VAR}} placeholders replaced for %s", template_path)

    # --- [IMP:8] Old-style ($VAR) placeholder replacement (for backward compat)
    manifest_clean = _replace_placeholders(manifest)
    logger.info("[IMP:8] $VAR placeholders (if any) replaced for %s", template_path)

    # --- [IMP:9] Валидация
    try:
        validate(instance=manifest_clean, schema=schema)
        logger.info("[IMP:9] VALIDATION PASSED: %s conforms to schema", template_path)
    except ValidationError as e:
        logger.error("[IMP:9] VALIDATION FAILED: %s - %s", template_path, e.message)
        raise


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D19/D20 — vhost template output safety (DevPlan 136 W1 T1.9)
# · Scenario: generate_vhost_body (nginx vhost «template») с /health →
# ·   НЕТ `proxy_pass $var/URI` (D19: invalid URL prefix → 500), `set $upstream` В location /health (D20)
# · Last fail: 2026-08-04 — /health proxy_pass $upstream_my_app$request_uri + set вне location → 500
# · Remove if: vhost прокси-шаблон (generate_vhost_body) меняется
def test_vhost_template_health_location_safe() -> None:
    """D19/D20: rendered vhost body (nginx template) — /health без proxy_pass $var/URI, set в location."""
    from core.internal.scaffold.vhost_renderer import generate_vhost_body

    logger.info("[IMP:7][test_templates] Rendering vhost body with /health (D19/D20 guard)")

    body = generate_vhost_body("app.example.com", "my-app", "example.com")
    health_block = body[body.index("location /health {") :]

    # D19: нет proxy_pass с переменной + URI/хвостом (nginx «invalid URL prefix»)
    assert re.search(r"proxy_pass\s+\$upstream_[A-Za-z0-9_]+[^;]*[/$]", body) is None, (
        "D19 regression: proxy_pass $var/URI в vhost template"
    )
    # D20: set $upstream определён В location /health (location-scope)
    assert "set $upstream_my_app http://my-app:80;" in health_block, (
        "D20 regression: set $upstream отсутствует в location /health"
    )
    logger.info("[IMP:9][test_templates] vhost /health template safe (D19/D20) — OK")


# ═══════════════════════════════════════════════════════════════════════
# REF-0001 (meta-refactoring W2): build&push канал в шаблонных workflows
# ═══════════════════════════════════════════════════════════════════════

# Полный SHA (40 hex) — форма пина внешних actions (гейт test_gate_workflow_sha_pins)
_SHA40_RE = re.compile(r"[0-9a-f]{40}")


def _template_deploy_wf_path(ptype: str) -> pathlib.Path:
    """Path к шаблонному workflow проекта для заданного типа шаблона."""
    return repo_root() / "templates" / f"template-{ptype}" / ".github" / "workflows" / "deploy.yml"


def _job_block(text: str, job_name: str) -> str:
    """Extract top-level job block ('  <name>:' до следующего ключа той же глубины).

    ## @purpose — Текстовый экстрактор job-блока: сырой шаблон НЕ валидный YAML
    ##            ({{ORG_NAME}} парсится как flow mapping), поэтому сканируем текст.
    ## @io — ⇥ text workflow, job_name → ⎋ текст блока или ''
    """
    m = re.search(rf"(?ms)^  {re.escape(job_name)}:[^\S\n]*\n.*?(?=^  \S|\Z)", text)
    return m.group(0) if m else ""


# region FUNC__build_job_violations
def _build_job_violations(job: str) -> list[str]:
    """Контракт содержимого job'а build-and-push (шаги/auth/tags/permissions).

    ## @io — ⇥ job: str текст блока → ⎋ violations
    """
    violations: list[str] = []
    for marker, what in (
        ("actions/checkout@", "checkout step"),
        ("docker/setup-buildx-action@", "setup-buildx step"),
        ("docker/login-action@", "registry-login step"),
        ("docker/build-push-action@", "build-push step"),
    ):
        if marker not in job:
            violations.append(f"build-and-push: нет {what} ({marker})")
    if "ghcr.io" not in job or "secrets.GITHUB_TOKEN" not in job:
        violations.append("build-and-push: registry-auth неполон (ghcr.io + secrets.GITHUB_TOKEN)")
    if "${{ github.sha }}" not in job or ":latest" not in job:
        violations.append("build-and-push: tags без '${{ github.sha }}' и/или ':latest' — receive тянет строго :<sha>")
    if "packages: write" not in job:
        violations.append("build-and-push: нет permissions packages:write")
    return violations


# endregion FUNC__build_job_violations


# region FUNC__scan_uses_sha_lines
def _scan_uses_sha_lines(text: str) -> list[str]:
    """SHA-form всех внешних uses — построчно (version-комментарий теряется при YAML-парсе;
    полная проверка формы с комментариями — scan_uses_sha_pins из гейта REF-0012).

    ## @io — ⇥ text workflow → ⎋ violations
    """
    violations: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("uses:"):
            continue
        ref = stripped[len("uses:") :].strip().split("#", 1)[0].strip()
        if ref.startswith("./"):
            continue
        _, _, tag = ref.rpartition("@")
        if tag and not _SHA40_RE.fullmatch(tag) and "{{ORG_NAME}}" not in ref:
            violations.append(f"uses не SHA-pinned (строка {lineno}): {ref}")
    return violations


# endregion FUNC__scan_uses_sha_lines


# region FUNC__scan_build_channel
def _scan_build_channel(text: str) -> list[str]:
    """Detector REF-0001: контракт build&push-канала шаблонного deploy.yml.

    ▶ ┌text┐ → ◇ job build-and-push ∋ checkout/buildx/login/build-push steps →
    ◇ registry-auth (ghcr.io + GITHUB_TOKEN) → ◇ tags ∋ <sha>+latest →
    ◇ packages:write → ◇ deploy.needs ∋ build-and-push → ⎋ list[str] violations

    ## @purpose — Детектор канала сборки образов проектов: engine на ноде тянет строго
    ##            ghcr.io/<org>/<proj>:<sha>; без этого job первый деплой умирает
    ##            (5 неудачных pull → PlatformFatalError, карточка REF-0001).
    ## @io — ⇥ text workflow → ⎋ violations (пустой = PASS)
    ## @complexity — O(L) строк; декомпозирован на _build_job_violations +
    ##              _scan_uses_sha_lines (C901 ≤10, agent-check)
    ## @invariants
    ##   - Все 4 шага канала обязательны (checkout/buildx/login/build-push)
    ##   - Тег ${{ github.sha }} обязателен: receive передаёт github.sha, compose читает IMAGE_TAG
    ##   - packages:write только в build-job (минимум прав, REF-0012 hygiene)
    """
    job = _job_block(text, "build-and-push")
    if not job:
        return ["нет job 'build-and-push' — канал сборки образов отсутствует (REF-0001)"]
    violations = _build_job_violations(job)
    deploy_job = _job_block(text, "deploy")
    if not deploy_job:
        violations.append("нет job 'deploy' — делегация в reusable workflow потеряна")
    elif "build-and-push" not in deploy_job.split("uses:", 1)[0]:
        violations.append("deploy: needs не содержит build-and-push (деплой до пуша образа)")
    violations.extend(_scan_uses_sha_lines(text))
    return violations


# endregion FUNC__scan_build_channel


# 🧪 TRAP[TEST] · 2026-08-24 · Regression · build&push channel (REF-0001, FAIL-0801)
# · Scenario: scaffolded проект без build-job — первый деплой гарантированно умирает
#   (engine тянет несуществующий ghcr.io/<org>/<proj>:<sha>)
# · Last fail: N/A (канал создаётся этим REF; до него шаблон содержал только delegating job)
# · Remove if: модель доставки образов проектов меняется (не push-в-GHCR)
@pytest.mark.parametrize("ptype", TEMPLATE_TYPES, ids=["template-backend", "template-frontend"])
def test_template_deploy_wf_build_and_push_channel(ptype: str, caplog: pytest.LogCaptureFixture) -> None:
    """Оба шаблона: build&push job полный, все actions SHA-pinned, deploy needs build."""
    caplog.set_level(logging.INFO)
    wf = _template_deploy_wf_path(ptype)
    assert wf.exists(), f"{wf} отсутствует"
    text = wf.read_text(encoding="utf-8")
    logger.info("[IMP:7][test_templates][REF-0001] scanning %s", wf)

    # Форма SHA-pins с version-комментариями — переиспользуем детектор гейта REF-0012
    from tests.gates.test_gate_workflow_sha_pins import scan_uses_sha_pins

    sha_violations = scan_uses_sha_pins(wf)
    channel_violations = _scan_build_channel(text)

    logger.info(
        "[IMP:8][test_templates][REF-0001] %s: sha_violations=%d channel_violations=%d",
        ptype,
        len(sha_violations),
        len(channel_violations),
    )
    assert not sha_violations, f"SHA-pin violations в {wf}: {sha_violations}"
    assert not channel_violations, f"REF-0001 violations в {wf}:\n" + "\n".join(channel_violations)
    logger.info("[IMP:9][test_templates][REF-0001] %s: build&push канал полный (4 шага, SHA-pins, needs)", ptype)


# 🧪 TRAP[TEST] · 2026-08-24 · NEGATIVE (R5) · build-channel detector — исходное состояние до REF-0001
# · Last fail: templates/*/deploy.yml до REF-0001 — только delegating job, build-and-push отсутствовал
# · Remove if: детектор _scan_build_channel удалён вместе с позитивным тестом
@pytest.mark.parametrize("ptype", TEMPLATE_TYPES, ids=["template-backend", "template-frontend"])
def test_template_deploy_wf_build_channel_negative(ptype: str, caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: детектор ловит отсутствие build-job и обрыв needs-цепочки."""
    caplog.set_level(logging.INFO)
    text = _template_deploy_wf_path(ptype).read_text(encoding="utf-8")

    # (a) Исходное состояние до REF-0001: build-and-push вырезан → детектируется
    stripped_text = text.replace(_job_block(text, "build-and-push"), "")
    violations = _scan_build_channel(stripped_text)
    logger.info("[IMP:8][test_templates][REF-0001][negative] no-build-job violations=%d", len(violations))
    assert any("нет job 'build-and-push'" in v for v in violations), (
        f"R5 FAIL: удаление build-job не детектировано: {violations!r}"
    )

    # (b) Обрыв цепочки: build есть, но deploy не ждёт его (деплой до пуша образа)
    broken_needs = text.replace("needs: [build-and-push]", "needs: []")
    violations_b = _scan_build_channel(broken_needs)
    logger.info("[IMP:8][test_templates][REF-0001][negative] broken-needs violations=%d", len(violations_b))
    assert any("needs не содержит build-and-push" in v for v in violations_b), (
        f"R5 FAIL: обрыв needs-цепочки не детектирован: {violations_b!r}"
    )
    logger.info("[IMP:9][test_templates][REF-0001][negative] PASS: оба регресса детектируются")
