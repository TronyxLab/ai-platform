# Direction 10 — False Confidence Mechanisms

Агент: adversarial-аудит направления «false confidence» · Дата: 2026-08-22

Итог направления: suite **структурно честен на уровне листьев, но самореферентен на уровне швов** — MEDIUM false-confidence риск в целом. Реальные сильные стороны: R5-пробы исполняют настоящие сканеры и громко падают при поломке детекторов (TEST-092), golden gate-step списки написаны руками, а не выведены парсером, main-CI ноги пинят honesty=fail, кэш корректно хеширует собственный executor-код. Четыре laundering-канала кластеризуются там, где верификация замыкается на артефакт, который сама верифицирует, или на enumeration: (1) generated-file parity судится самим генератором, дешёвый git-diff гейт вакуумен на CI, два file-списка дрейфуют; (2) fingerprint key слеп к toolchain и environment — make check может заверить вчерашний вердикт toolchain'а на сегодняшнем сломанном дереве; (3) honesty enforcement — enumerated allowlist на два workflow позади реальности; skip-as-default в одном забытом env var от зелёного; (4) девять различных rc=5/skip-to-zero каналов означают, что целый docker tier может перестать исполняться, не покраснев нигде. Каждый канал имеет конкретный closure-тест; инфраструктура не требуется. Наибольший leverage: deny-by-default workflow honesty gate, toolchain-aware cache key, per-suite collection floors.

---

### TEST-091: Freshness gate ничего не верифицирует о генерации; реальная parity — generator-self-consistent
- Test: tests/gates/test_gate_manifests_up_to_date.py:70-77; core/internal/scripts/manifest_driver.py:131-151,187-201; golden parity tests/gates/test_gate_check_suite_consistency.py:53-95
- Production code: Generated-manifest контракт (инвариант 11) — secrets-manifest.yaml, platform-env.yaml, smoke_env_generated.py, env_defaults_generated.py, entrypoint-manifest.yaml, AGENTS.md, .env.example, litellm-config.yml
- Claimed guarantee: «все generated files up to date»; CI блокирует дивергенцию authoritative sources и generated артефактов
- Actual guarantee: три разрозненных механизма разной силы. (1) pytest-гейт гоняет голый `git diff --exit-code` (worktree vs index) по 6 файлам — он НИКОГДА не вызывает генератор. На CI (свежий checkout, pre-commit уже прошёл) worktree==index тривиально → вакуумный PASS даже если ЗАКОММИЧЕННЫЕ манифесты stale. Ловит только незакоммиченный локальный дрейф. (2) Реальная parity = manifest_driver.py check, перезапускающий G1-G6 с --check — сравнение закоммиченных файлов с выходом, посчитанным ТЕМ ЖЕ генератором. Детерминированный баг генератора (потерянное поле, неверная нормализация) воспроизводится идентично с обеих сторон → перманентно зелёный; независимого expected-content oracle нет. (3) Golden parity (_GOLDEN_FAST/_GOLDEN_FULL) — рукописные константы, снятые до порта — структурно здравый anti-drift, но вручную рестампятся при каждом легитимном изменении манифестов, деградируя в rubber-stamp зеркало; test_manifest_checks_valid зовёт ТОТ ЖЕ validate_manifest, что и executor (shared-acceptance blind spot). Плюс file-списки расходятся: _GENERATED_FILES pytest-гейта (6) ≠ driver's _GENERATED_PATHS (7) ≠ фактические G-outputs (~8); синхронизация enforce'ится только комментарием («MUST stay synced», :11)
- Blind spot: push-gate.yml (все ветки) гоняет только make gate MODE=fast → gates suite → только git-diff гейт. Коммит правок source + stale generated output проходит все не-main ноги; regeneration-проверка живёт исключительно в platform-test.yml:161-162. Баги генератора/парсера нефальсифицируемы by design механизма (2)
- Possible production bug: правка core/secret-definitions.yaml с добавлением секрета, коммит без make generate-manifests → push-gate зелёный; L1 secrets-manifest недоотчитывает inventory до main. Хуже: баг generate_secrets_manifest.py, молча пропускающий класс source=sops → parity зелёная вечно, секреты деплоятся без manifest-трекинга
- Recommended test: независимый semantic validator, читающий SOURCE yaml (каждый id из secret-definitions.yaml обязан присутствовать в secrets-manifest.yaml) — cross-file, никогда не импортирующий internals генератора; плюс один структурный тест `_GENERATED_FILES == manifest_driver._GENERATED_PATHS` (tracked intersection), убивающий list drift
- Existing test to remove/merge: git-diff вариант test_manifests_up_to_date (CI value ≈ 0; локальная ценность уже покрыта check-manifests tier make check) — или слить оба в один driver-list-driven freshness тест
- Confidence: HIGH

### TEST-092: R5 probes исполняют реальное поведение — но только слой детектора, никогда assertion-wiring позитивного гейта
- Test: tests/gates/test_gate_no_unregistered_entrypoint.py:326-348; tests/gates/test_gate_subprocess_io_sole.py:105-135; канон tests/gates/AGENTS.md §R5 (119H/129W2)
- Production code: entrypoint-registration сканеры (_collect_sh_files + manifest path extraction); single-canon run_subprocess AST/line detector
- Claimed guarantee: anti-survivorship — каждый детектор доказанно ловит точный input своего исходного бага (164-W3-1 unregistered entrypoint; B4 второй subprocess_io)
- Actual guarantee: оба probe-сайта подлинно behavioral, а не grep-фикстура: реальный файл пишется в сканируемое дерево (core/entrypoints/_gate_probe_<uuid>.sh :337; core/_gate_probe_subprocess_io_<hex8>.py :121), НАСТОЯЩАЯ функция сканера исполняется против него, detection ассертится (`assert probe_rel in collected`:341; `assert hits`:132). Если collector молча вернёт [] или запись probe упадёт — тест RED; гейт не может пройти с инертной пробой. Однако негативы упражняют только collection/detection: subprocess-io негатив сканирует с include_probes=True, минуя exclusion `_gate_probe_` + сравнение _CANON_FILE (:87) позитивного пути полностью; entrypoint-негатив ассертит collection+registration, но никогда не водит violation-reporting ветку test_all_makefile_targets_in_allowed_verbs
- Blind spot: assertion-level rot позитивных гейтов (allowlist creep, инвертированные сравнения, проглоченные offenders) держит все R5-негативы зелёными. Смежный dead-weight: fixed-name probe константы захардкожены в exclusion sets ≥6 позитивных сканеров (test_gate_no_simulators.py:79, test_gate_grep_summary.py:56-75, test_gate_r1_no_pass_tests.py:44, test_gate_bootstrap_no_duplicate_steps.py:84-85) — чистые grep-satisfying fixture-имена; если _gate_probe_marker_tmp перестанет создаваться test_gate_marker_location.py:163, каждое exclusion продолжает проходить вечно, ничего не защищая
- Possible production bug: кто-то добавляет exception clause («skip vendored dirs») в _find_run_subprocess_implementations, глотающую и реальные дубликаты — негатив всё равно зелёный (его scan path не затронут), sole-canon гарантия молча потеряна
- Recommended test: провести probe через ПОЗИТИВНЫЙ scan path и ассертить, что предикат позитивного теста фейлится (offenders непуст при presence probe, canon исключён) — закрыть gap детектор→ассерт; плюс мета-тест: каждая строка _gate_probe_*/probe-dir в exclusion константах соответствует probe, которую реально создаёт живой тест
- Existing test to remove/merge: none; расширять, не удалять
- Confidence: HIGH

### TEST-093: Honesty mode default = skip; CI pin enforceится перечислением 2 из 3 pinned workflow
- Test: tests/_conftest/honesty.py:39-46,71-80,100,117,140; enforcement gate tests/gates/test_gate_honesty_mode.py:31-34,100-114
- Production code: R4 (NO_SERVICE = FAIL) для Docker/scripts/env/TCP-зависимых тестов
- Claimed guarantee: на CI отсутствие сервиса = FAIL, никогда зелёный-via-mass-skip
- Actual guarantee: REQUIRE_HONESTY_MODE ∈ {marker(default)→pytest.skip, xfail→xfail(strict=False), fail→pytest.fail}. Пины есть в platform-test.yml:95, push-gate.yml:69, platform-gate-fast.yml:63. Но структурный гейт _WORKFLOWS tuple перечисляет ТОЛЬКО platform-gate-fast.yml + platform-test.yml — push-gate.yml пиннут, но неверифицирован, и любой будущий workflow leg, гоняющий pytest без env, молча регрессирует в marker mode (mass-skip → exit 0 → green). Пять workflow сегодня без пина (core-deploy, deploy-project, hermes-nightly, mirror, security-scan). xfail mode — sanctioned silent-green значение (non-strict xfail не может зафейлиться), достижимо одним env var
- Blind spot: да, есть leg-shaped дыра: platform-test.yml:439-442 — integration-live выходит 0 при отсутствии API keys («skipping» как успех, fork-PR путь); deploy-project.yml:216-220 толерирует pytest rc=5 и unknown project types исполняют ноль language checks (:200-202). Локальный make check (default marker, без Docker) кэширует greens, построенные частично на skip'ах — питая replay TEST-094
- Possible production bug: Docker daemon раннера ломается в ноге, забывшей пин (или рефакторинг переименовал env) → весь requires_docker tier skip'ается, job репортит green, docker-only регрессии уходят в main
- Recommended test: инвертировать в deny-by-default — glob ВСЕХ .github/workflows/*.yml и требовать REQUIRE_HONESTY_MODE: fail в каждом workflow, чьи run-steps зовут pytest/make gate/make check (пустой allowlist, по собственному канону репо «allowlist пуст = RED»); запретить значение xfail в CI контекстах
- Existing test to remove/merge: расширить test_ci_workflows_require_honesty_fail на месте
- Confidence: HIGH

### TEST-094: Fingerprint cache хеширует только байты дерева — toolchain и environment drift переигрывают stale greens
- Test: core/internal/check_suite/fingerprint.py:56-58,68,76-100,114-139; consumer core/internal/check_suite/diagnostic.py:107-113,304-308
- Production code: make check replay accelerator (<10s на неизменённом дереве); gate/CI намеренно cache-free
- Claimed guarantee: «байт-идентичное дерево → тот же fingerprint; любая правка/untracked → miss» — replay только когда дерево реально неизменено и прошлый прогон был зелёным
- Actual guarantee: hash = sha256 над rel-paths + полным CONTENT всех tracked+untracked non-ignored файлов (git ls-files -c -o --exclude-standard) + 3 extra configs (check-suite.yaml, .pre-commit-config.yaml, pyproject.toml). Content-based (mtimes игнорируются — хорошо); изменения executor-кода инвалидируют (tracked). НЕ в ключе: (a) всё под .venv/ (FINGERPRINT_EXCLUDE_PARTS :56) — т.е. версии инструментов; (b) каждая env var, консультируемая исполнителем (REQUIRE_HONESTY_MODE, TEST_NO_XDIST, CHECK_XDIST_MAX_WORKERS); (c) gitignored runtime inputs; edge: нечитаемые файлы дают name-only вклад (except OSError: continue :130-133)
- Blind spot (конкретные replay-a-broken-tree сценарии): (1) pip install -U ruff/basedpyright/deptry внутри .venv (после dependabot sync) — новый инструмент находит новые нарушения, байты дерева не изменились → make check переигрывает кэшированный зелёный, посчитанный старым инструментом. (2) Зелёный прогон с unset REQUIRE_HONESTY_MODE (docker-tier skip'ается), затем оператор экспортирует fail, ожидая честных падений → идентичный fingerprint → кэшированный зелёный вместо честного прогона. (3) chmod 000 падающего тест-файла → содержимое молча выпадает из хеша
- Possible production bug: агент следует каноническому циклу «фикс → make check до чистоты» после апгрейда venv; кэш говорит green; pre-push quick check (check-diff, cache-free) ловит — или не ловит для env-var классов, оставляя CI единственной сетью
- Recommended test: влить toolchain digest в ключ (hash ruff --version, basedpyright --version, deptry/vulture/import-linter версий, python --version) плюс salt из трёх env vars исполнителя; unit-тест, ассертящий различие fingerprint при изменении любого из них
- Existing test to remove/merge: none
- Confidence: HIGH

### TEST-095: Empty-collection-to-PASS каналы — полная карта
- Test: enforcement chain ниже; production code: контракт исполнения pytest suite
- Claimed guarantee: каждый конфигурированный suite исполняет реальные тесты в CI; 0-collected — исключительное, явно обоснованное состояние
- Actual guarantee: rc=5 (0 collected) маппится в успех через эти каналы:

| # | Канал | Локация |
|---|-------|---------|
| 1 | gates-docker allow_no_tests: true | core/check-suite.yaml:145 |
| 2 | predeploy-docker allow_no_tests: true | core/check-suite.yaml:191 |
| 3 | integration allow_no_tests: true | core/check-suite.yaml:243 |
| 4 | Executor: rc5→passed_no_tests→PASS | core/internal/check_suite/runner.py:64,354-356; трактуется как non-failure на gate.py:166 |
| 5 | Integration error-path: rc==5 → exit 0 | .github/workflows/platform-test.yml:422-427 |
| 6 | Integration live: rc==5 → exit 0 | .github/workflows/platform-test.yml:444-447 |
| 7 | Integration live: missing API keys → exit 0 (пустой прогон by construction) | platform-test.yml:439-442 |
| 8 | Project CI quality: pytest rc∈{0,5}→PASS | .github/workflows/deploy-project.yml:216-220 |
| 9 | Project CI: unknown type: → ноль checks, деплой продолжается | deploy-project.yml:200-202 |

  Ни один continue-on-error не оборачивает тестовые шаги в platform-test.yml (только pre-pull :227, diagnostics, sarif-upload в security-scan.yml:107); нет || true на тестовых вызовах (репо их явно убрал, C-2/C-4). Каналы 1-2 важнее всего, т.к. gates-docker едет на КАЖДЫЙ push через push-gate.yml:98 fast mode
- Blind spot: rot маркерных выражений превращает полный suite в rc=5 молча — точный near-miss записан на core/check-suite.yaml:224-228 (glob literal → rc=4, поймано только потому что rc≠5; rename, дающий rc=5, проходит насквозь). Переименованный requires_docker/component/integration маркер опустошает каналы 1, 2, 5-7 одновременно на всех ветках с зелёными бейджами; backstop остаются только smoke/component (без флага)
- Possible production bug: рефакторинг уносит последний @pytest.mark.requires_docker gate-тест из tests/gates/ → gates-docker собирает 0 → PASS везде → docker-tier гейт тихо перестаёт существовать, CI остаётся зелёным
- Recommended test: per-suite floor counts — мета-гейт, ассертящий --collect-only ≥1 тест для каждого suite id с историческим >0 объёмом (gates, gates-docker, contract, ai-instructions, static_audit, predeploy, predeploy-docker, smoke, component, integration); downgrade rc=5 из PASS в WARN-with-annotation, когда 30-дневная медиана collection >0
- Existing test to remove/merge: none — охраняющие floors не найдены (отсутствие и есть находка)
- Confidence: HIGH
