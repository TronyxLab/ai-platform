# GREP_SUMMARY: unit-test, bootstrap-resolver, parse-batch-output, tab-parsing, --get-many, context-fallback, masked-age-key, CLI, exit-0-1-2, R5-negative, DevPlan-170-W9-F1
# STRUCTURE: ▶ TestParseBatchOutput×4 (happy→dict / broken-line→exit2 / unknown-alias→exit2 / empty→{}) → ▶ TestResolveFields×3 (fallback / runner-DI / clean) → ▶ TestMaskAgeKey×3 (mask / empty / short) → ▶ TestCLI×6 (resolve-key=value / resolve-json / missing-owner-exit1 / broken-tab-exit2 / runner-fatal-exit1 / mask) → ⎋ 16 pass
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/bootstrap_resolver.py (DevPlan 170 W9-F1) —
##           tab-парсинг bootstrap.sh → Python. Покрытие: parse_batch_output (замена shell
##           while-read с TAB-разделителем), CONTEXT fallback, mask_age_key, CLI resolve/mask с
##           exit-контрактом 0/1/2. R5-negative: битая tab-строка → exit 2 (test_detect...).
## @scope    Pure unit tests — native imports, no subprocess (DI get_many_runner инжектится),
##           no Docker. capsys для stdout CLI, tmp_path для файловых путей.
## @invariants
##   - CLI-тесты вызывают main(argv, get_many_runner=fake) — реальный subprocess НЕ запускается
##   - stdout CLI: ТОЛЬКО данные (key=value / JSON / mask); stderr — LDD-телеметрия
##   - Exit: 0 ok / 1 FATAL (owner_key missing, runner rc≠0) / 2 invalid input (битая строка, unknown alias)
##   - CONTEXT fallback: top-level context > contexts.0.name (семантика bootstrap.sh:99)
##   - Каждый тест — реальные asserts + TRAP[TEST] + IMP:9 LDD log (Test Honesty R1/R2)
## @rationale DevPlan 170 W9-F1 §W9: unit-тесты нового резолвера + R5-negative на битую
##           tab-строку (shell read молча давал пустой value — класс скрытого бага).
## @changes  2026-08-15 | DevPlan 170 W9-F1 — Created
# endregion MODULE_CONTRACT

import json
import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.bootstrap_resolver import (
    ResolverError,
    ResolverParseError,
    main,
    mask_age_key,
    parse_batch_output,
    resolve_fields,
)
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Helpers / fixtures
# ═══════════════════════════════════════════════════════════════════


def _fake_runner(
    *,
    owner: str = "ssh-rsa AAAA owner-key",
    ci_deploy: str = "ssh-rsa BBBB ci-deploy-key",
    ci_root: str = "ssh-rsa CCCC ci-root-key",
    domain: str = "tronyx.ru",
    context: str = "primary-ctx",
    context0: str = "contexts-zero-ctx",
    host: str = "192.168.1.100",
):
    """DI-раннер: возвращает корректный alias<TAB>value-вывод node_yaml --get-many (без subprocess).

    ## @purpose — Тестовый seam: get_many_runner инжектится в main()/resolve_fields() —
    ##            реальный вызов node_yaml (subprocess) НЕ происходит (marker unit: no subprocess).
    ## @io — ⇥ параметры полей (defaults — happy-path) → ⎋ Callable[[str, str], str]
    ## @complexity — O(1)
    """

    def runner(_yaml_path: str, _spec: str) -> str:
        return (
            f"owner_key\t{owner}\n"
            f"ci_deploy_key\t{ci_deploy}\n"
            f"ci_root_key\t{ci_root}\n"
            f"platform_domain\t{domain}\n"
            f"context\t{context}\n"
            f"context0\t{context0}\n"
            f"host\t{host}\n"
        )

    return runner


# ═══════════════════════════════════════════════════════════════════
# TestParseBatchOutput
# ═══════════════════════════════════════════════════════════════════


# region CLASS_TestParseBatchOutput
## @purpose  parse_batch_output — замена shell tab-парсинга bootstrap.sh:89-98.
## @scope    happy-path dict, битая строка (R5-negative exit 2), unknown alias (fail-fast), пустой текст.
## @invariants — partition(TAB) на ПЕРВОМ табе; значения с пробелами сохраняются; строка без TAB → raise
class TestParseBatchOutput:
    # region FUNC_test_parse_happy_path
    ## @purpose — Корректный alias<TAB>value-вывод → dict (значения с пробелами не режутся).
    ## @io — ⇥ caplog → ⎋ None (asserts dict)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · happy-path tab-парсинг (DevPlan 170 W9-F1)
    # · Scenario: корректные alias<TAB>value-строки → dict с 7 полями, value с пробелами цел
    # · Last fail: N/A (new test)
    # · Remove if: parse_batch_output меняется
    def test_parse_happy_path(self, caplog: pytest.LogCaptureFixture) -> None:
        """parse_batch_output: корректные TAB-строки → dict (значения с пробелами сохраняются)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing happy-path tab parsing")

        text = "owner_key\tssh-rsa AAAA owner-key\ncontext\tprimary-ctx\nhost\t192.168.1.100\n"
        fields = parse_batch_output(text)
        assert fields == {
            "owner_key": "ssh-rsa AAAA owner-key",
            "context": "primary-ctx",
            "host": "192.168.1.100",
        }, f"Unexpected dict: {fields}"
        logger.info("[IMP:9][test_bootstrap_resolver] Happy-path parse OK: %d fields", len(fields))

    # endregion FUNC_test_parse_happy_path

    # region FUNC_test_parse_broken_tab_line_raises
    ## @purpose — R5-negative: строка без TAB → ResolverParseError (exit 2 в main()).
    ## @io — ⇥ caplog → ⎋ None (asserts raise)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · NEGATIVE (R5) · битая tab-строка → exit 2 (DevPlan 170 W9-F1)
    # · Scenario: строка 'owner_key:value-without-tab' (дрейф формата node_yaml) → ResolverParseError —
    # ·   shell `while IFS=$'\t' read` молча давал пустой value — класс скрытого бага
    # · Last fail: N/A (new test — защита нового Python-парсинга)
    # · Remove if: parse_batch_output строка-без-TAB обработка меняется
    def test_parse_broken_tab_line_raises(self, caplog: pytest.LogCaptureFixture) -> None:
        """R5-negative: строка без TAB-разделителя → ResolverParseError (fail-fast, exit 2)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing broken tab line (R5-negative)")

        with pytest.raises(ResolverParseError, match="no TAB separator"):
            parse_batch_output("owner_key:value-without-tab\n")
        logger.info("[IMP:9][test_bootstrap_resolver] Broken tab line rejected with ResolverParseError")

    # endregion FUNC_test_parse_broken_tab_line_raises

    # region FUNC_test_parse_unknown_alias_raises
    ## @purpose — Unknown alias в выводе (дрейф node_yaml) → ResolverParseError (fail-fast).
    ## @io — ⇥ caplog → ⎋ None (asserts raise)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · NEGATIVE (R5) · unknown alias → exit 2 (DevPlan 170 W9-F1)
    # · Scenario: вывод --get-many содержит неожиданный алиас (дрейф контракта) → ResolverParseError
    # · Last fail: N/A (new test)
    # · Remove if: expected_aliases проверка меняется
    def test_parse_unknown_alias_raises(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unknown alias в выводе → ResolverParseError (fail-fast против дрейфа формата)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing unknown alias (R5-negative)")

        with pytest.raises(ResolverParseError, match="Unexpected --get-many alias"):
            parse_batch_output("owner_key\tkey\nbogus_alias\tvalue\n")
        logger.info("[IMP:9][test_bootstrap_resolver] Unknown alias rejected with ResolverParseError")

    # endregion FUNC_test_parse_unknown_alias_raises

    # region FUNC_test_parse_empty_text
    ## @purpose — Пустой текст → {} (не ошибка; owner_key-валидация ловит реальный сбой).
    ## @io — ⇥ caplog → ⎋ None (asserts {})
    ## @complexity — O(1)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · пустой вывод → {} (DevPlan 170 W9-F1)
    # · Scenario: пустой/whitespace-текст → {} без исключения (missing-key семантика --get-many)
    # · Last fail: N/A (new test)
    # · Remove if: parse_batch_output empty-обработка меняется
    def test_parse_empty_text(self, caplog: pytest.LogCaptureFixture) -> None:
        """Пустой текст → {} (без ошибки — owner_key-валидация в main() покрывает сбой)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing empty output")

        assert parse_batch_output("") == {}
        assert parse_batch_output("   \n  \n") == {}
        logger.info("[IMP:9][test_bootstrap_resolver] Empty output → {} (no raise)")

    # endregion FUNC_test_parse_empty_text


# endregion CLASS_TestParseBatchOutput


# ═══════════════════════════════════════════════════════════════════
# TestResolveFields
# ═══════════════════════════════════════════════════════════════════


# region CLASS_TestResolveFields
## @purpose  resolve_fields — оркестрация: runner → parse → CONTEXT fallback (bootstrap.sh:99 семантика).
## @scope    top-level context wins, contexts.0.name fallback, оба пусты → "".
## @invariants — context0 удаляется из результата; fallback ТОЛЬКО при пустом top-level context
class TestResolveFields:
    # region FUNC_test_context_fallback_contexts0
    ## @purpose — Top-level context пуст → CONTEXT берётся из contexts.0.name (fallback bootstrap.sh:99).
    ## @io — ⇥ caplog, tmp_path → ⎋ None (asserts fallback)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · CONTEXT fallback ← contexts.0.name (DevPlan 170 W9-F1)
    # · Scenario: context= (пусто), context0=contexts-zero-ctx → результат context=contexts-zero-ctx
    # · Last fail: N/A (new test — перенос bootstrap.sh:99 fallback в Python)
    # · Remove if: context fallback contract меняется
    def test_context_fallback_contexts0(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        """Top-level context пуст → CONTEXT из contexts.0.name (fallback bootstrap.sh:99)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing CONTEXT fallback (contexts.0.name)")

        fields = resolve_fields(
            str(tmp_path / "node.yaml"), get_many_runner=_fake_runner(context="", context0="contexts-zero-ctx")
        )
        assert fields["context"] == "contexts-zero-ctx", f"Fallback failed: {fields}"
        assert "context0" not in fields, "context0 должен быть удалён из результата"
        logger.info("[IMP:9][test_bootstrap_resolver] CONTEXT fallback → contexts-zero-ctx ✓")

    # endregion FUNC_test_context_fallback_contexts0

    # region FUNC_test_context_top_level_wins
    ## @purpose — Top-level context приоритетнее contexts.0.name (bootstrap.sh:99 семантика).
    ## @io — ⇥ caplog, tmp_path → ⎋ None (asserts top-level wins)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · top-level context wins (DevPlan 170 W9-F1)
    # · Scenario: context=primary-ctx + context0=contexts-zero-ctx → context=primary-ctx (fallback НЕ срабатывает)
    # · Last fail: N/A (new test)
    # · Remove if: context priority contract меняется
    def test_context_top_level_wins(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        """Top-level context приоритетнее contexts.0.name (fallback не перекрывает)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing top-level context priority")

        fields = resolve_fields(
            str(tmp_path / "node.yaml"), get_many_runner=_fake_runner(context="primary-ctx", context0="other")
        )
        assert fields["context"] == "primary-ctx", f"Top-level context must win, got {fields}"
        logger.info("[IMP:9][test_bootstrap_resolver] Top-level context wins: primary-ctx ✓")

    # endregion FUNC_test_context_top_level_wins

    # region FUNC_test_resolve_fields_runner_di
    ## @purpose — DI runner инжектится (native-тест без subprocess); все 6 полей + host.
    ## @io — ⇥ caplog, tmp_path → ⎋ None (asserts полный набор)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · DI runner + полный набор полей (DevPlan 170 W9-F1)
    # · Scenario: _fake_runner (все поля) → dict с owner_key/ci_deploy_key/ci_root_key/domain/context/host
    # · Last fail: N/A (new test)
    # · Remove if: resolve_fields контракт меняется
    def test_resolve_fields_runner_di(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        """resolve_fields с DI-раннером возвращает все 6 полей (owner_key..host)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing resolve_fields with DI runner")

        fields = resolve_fields(str(tmp_path / "node.yaml"), get_many_runner=_fake_runner())
        assert fields == {
            "owner_key": "ssh-rsa AAAA owner-key",
            "ci_deploy_key": "ssh-rsa BBBB ci-deploy-key",
            "ci_root_key": "ssh-rsa CCCC ci-root-key",
            "platform_domain": "tronyx.ru",
            "context": "primary-ctx",
            "host": "192.168.1.100",
        }, f"Unexpected fields: {fields}"
        logger.info("[IMP:9][test_bootstrap_resolver] resolve_fields DI-раннер: 6 полей ✓")

    # endregion FUNC_test_resolve_fields_runner_di


# endregion CLASS_TestResolveFields


# ═══════════════════════════════════════════════════════════════════
# TestMaskAgeKey
# ═══════════════════════════════════════════════════════════════════


# region CLASS_TestMaskAgeKey
## @purpose  mask_age_key — маска AGE-ключа для dry-run (замена bootstrap.sh:148 cut -c1-8).
## @scope    формат <AGE_KEY:prefix...>, пустой ключ → "", короткий ключ → весь.
## @invariants — никогда не содержит полный ключ; пустой → "" (безопасно для stdout/stderr)
class TestMaskAgeKey:
    # region FUNC_test_mask_format
    ## @purpose — Ключ → <AGE_KEY:первые8...> (паритет bootstrap.sh:148).
    ## @io — ⇥ caplog → ⎋ None (asserts формат)
    ## @complexity — O(1)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · формат маски <AGE_KEY:xxxx...> (DevPlan 170 W9-F1)
    # · Scenario: AGE-SECRET-KEY-abcdef123456 → <AGE_KEY:AGE-SECR...> (первые 8 символов)
    # · Last fail: N/A (new test — перенос cut -c1-8 из bootstrap.sh)
    # · Remove if: mask_age_key формат меняется
    def test_mask_format(self, caplog: pytest.LogCaptureFixture) -> None:
        """mask_age_key: полный ключ маскируется первыми 8 символами."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing mask format")

        masked = mask_age_key("AGE-SECRET-KEY-abcdef123456")
        assert masked == "<AGE_KEY:AGE-SECR...>", f"Unexpected mask: {masked}"
        assert "abcdef123456" not in masked, "Полный ключ не должен утекать в маску"
        logger.info("[IMP:9][test_bootstrap_resolver] Mask format OK: %s", masked)

    # endregion FUNC_test_mask_format

    # region FUNC_test_mask_empty
    ## @purpose — Пустой ключ → "" (bootstrap.sh вызывает mask только при non-empty ключе).
    ## @io — ⇥ caplog → ⎋ None (asserts "")
    ## @complexity — O(1)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · пустой ключ → "" (DevPlan 170 W9-F1)
    # · Scenario: mask_age_key("") → "" (никакого <AGE_KEY:...> для пустоты)
    # · Last fail: N/A (new test)
    # · Remove if: mask_age_key empty-обработка меняется
    def test_mask_empty(self, caplog: pytest.LogCaptureFixture) -> None:
        """mask_age_key: пустой ключ → пустая строка (безопасно)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing empty key mask")

        assert not mask_age_key("")
        logger.info("[IMP:9][test_bootstrap_resolver] Empty key → empty mask ✓")

    # endregion FUNC_test_mask_empty

    # region FUNC_test_mask_short_key
    ## @purpose — Короткий ключ (< 8 символов) → весь ключ в маске (не падает).
    ## @io — ⇥ caplog → ⎋ None (asserts префикс = весь ключ)
    ## @complexity — O(1)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · короткий ключ → префикс=весь ключ (DevPlan 170 W9-F1)
    # · Scenario: ключ 'abc' → <AGE_KEY:abc...> (слайс key[:8] не падает на коротких)
    # · Last fail: N/A (new test)
    # · Remove if: mask_age_key slicing меняется
    def test_mask_short_key(self, caplog: pytest.LogCaptureFixture) -> None:
        """mask_age_key: короткий ключ → весь ключ как префикс (без ошибки слайса)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing short key mask")

        assert mask_age_key("abc") == "<AGE_KEY:abc...>"
        logger.info("[IMP:9][test_bootstrap_resolver] Short key masked without error ✓")

    # endregion FUNC_test_mask_short_key


# endregion CLASS_TestMaskAgeKey


# ═══════════════════════════════════════════════════════════════════
# TestCLI
# ═══════════════════════════════════════════════════════════════════


# region CLASS_TestCLI
## @purpose  CLI main() — exit-контракт 0/1/2 (bootstrap.sh потребляет key=value-вывод).
## @scope    resolve key=value / resolve json / missing-owner exit1 / broken-tab exit2 /
##           runner-FATAL exit1 / mask. DI get_many_runner — без subprocess.
## @invariants — stdout содержит ТОЛЬКО данные; capsys читает stdout CLI
class TestCLI:
    # region FUNC_test_cli_resolve_key_value
    ## @purpose — resolve (--file) с DI-раннером → rc 0, key=value-вывод с node_yaml_path.
    ## @io — ⇥ main() + capsys + tmp_path → ⎋ None (asserts rc + stdout)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · CLI resolve key=value + exit 0 (DevPlan 170 W9-F1)
    # · Scenario: main(['resolve','--file',...], runner=_fake_runner()) → rc 0, строки key=value,
    # ·   включая node_yaml_path (нужен shell: --node-yaml / NODE_CONFIGS_DIR)
    # · Last fail: N/A (new test — интерфейс, которым пользуется bootstrap.sh)
    # · Remove if: CLI resolve контракт меняется
    def test_cli_resolve_key_value(
        self,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        """resolve --file + DI-раннер → rc 0, key=value-вывод (owner_key + node_yaml_path)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing CLI resolve key=value")

        rc = main(["resolve", "--file", str(tmp_path / "node.yaml")], get_many_runner=_fake_runner())
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        lines = [ln for ln in captured.out.splitlines() if ln]
        assert lines[0] == "owner_key=ssh-rsa AAAA owner-key", f"Unexpected line: {lines[0]!r}"
        assert any(ln.startswith("node_yaml_path=") for ln in lines), "node_yaml_path отсутствует в выводе"
        assert all("=" in ln for ln in lines), f"Не все строки key=value: {lines}"
        logger.info("[IMP:9][test_bootstrap_resolver] CLI resolve key=value: rc=0, %d строк", len(lines))

    # endregion FUNC_test_cli_resolve_key_value

    # region FUNC_test_cli_resolve_json
    ## @purpose — resolve --format json → rc 0, JSON-вывод (6 полей + node_yaml_path).
    ## @io — ⇥ main() + capsys + tmp_path → ⎋ None (asserts rc + JSON)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · CLI resolve json (DevPlan 170 W9-F1)
    # · Scenario: main(['resolve','--file',...,'--format','json'], runner=...) → rc 0, JSON c context
    # · Last fail: N/A (new test)
    # · Remove if: --format json контракт меняется
    def test_cli_resolve_json(
        self,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        """resolve --format json → rc 0, JSON с полями (включая context после fallback)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing CLI resolve --format json")

        rc = main(
            ["resolve", "--file", str(tmp_path / "node.yaml"), "--format", "json"], get_many_runner=_fake_runner()
        )
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"

        data = json.loads(captured.out)
        assert data["owner_key"] == "ssh-rsa AAAA owner-key"
        assert data["context"] == "primary-ctx"
        assert "node_yaml_path" in data, "node_yaml_path должен быть в JSON"
        logger.info("[IMP:9][test_bootstrap_resolver] CLI resolve json: rc=0, context=%s", data["context"])

    # endregion FUNC_test_cli_resolve_json

    # region FUNC_test_cli_missing_owner_key_exit1
    ## @purpose — owner_key пуст (нет строки owner_key) → exit 1 (bootstrap.sh:101 контракт).
    ## @io — ⇥ main() + capsys + tmp_path → ⎋ None (asserts rc 1)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · NEGATIVE (R5) · owner_key missing → exit 1 (DevPlan 170 W9-F1)
    # · Scenario: раннер без owner_key-строки → rc 1, stderr с owner_key not found —
    # ·   контракт bootstrap.sh:101 («FATAL: owner_key not found») перенесён в Python
    # · Last fail: N/A (new test)
    # · Remove if: owner_key-валидация меняется
    def test_cli_missing_owner_key_exit1(
        self,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        """owner_key отсутствует → exit 1 (FATAL, контракт bootstrap.sh:101)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing missing owner_key (R5-negative)")

        rc = main(
            ["resolve", "--file", str(tmp_path / "node.yaml")],
            get_many_runner=_fake_runner(owner=""),
        )
        captured = capsys.readouterr()
        assert rc == 1, f"Expected exit 1 (owner_key FATAL), got {rc}"
        assert not captured.out.strip(), "stdout должен быть пуст при FATAL"
        logger.info("[IMP:9][test_bootstrap_resolver] Missing owner_key → exit 1 (FATAL) ✓")

    # endregion FUNC_test_cli_missing_owner_key_exit1

    # region FUNC_test_cli_broken_tab_exit2
    ## @purpose — R5-negative (CLI): битая tab-строка в выводе раннера → exit 2.
    ## @io — ⇥ main() + capsys + tmp_path → ⎋ None (asserts rc 2)
    ## @complexity — O(L)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · NEGATIVE (R5) · CLI битая tab-строка → exit 2 (DevPlan 170 W9-F1)
    # · Scenario: раннер возвращает 'owner_key:broken' (без TAB) → rc 2 (ResolverParseError) —
    # ·   точный вход волнового требования «битая tab-строка → exit 2»
    # · Last fail: N/A (new test)
    # · Remove if: exit 2 для parse-ошибки меняется
    def test_cli_broken_tab_exit2(
        self,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        """R5-negative: битая tab-строка → exit 2 (invalid input, fail-fast)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing CLI broken tab line → exit 2")

        def broken_runner(_p: str, _s: str) -> str:
            return "owner_key:broken-no-tab\n"

        rc = main(["resolve", "--file", str(tmp_path / "node.yaml")], get_many_runner=broken_runner)
        captured = capsys.readouterr()
        assert rc == 2, f"Expected exit 2 (invalid input), got {rc}"
        assert not captured.out.strip(), "stdout должен быть пуст при exit 2"
        logger.info("[IMP:9][test_bootstrap_resolver] Broken tab line → CLI exit 2 ✓")

    # endregion FUNC_test_cli_broken_tab_exit2

    # region FUNC_test_cli_runner_fatal_exit1
    ## @purpose — Раннер поднял ResolverError (node_yaml rc≠0) → exit 1 (FATAL).
    ## @io — ⇥ main() + capsys + tmp_path → ⎋ None (asserts rc 1)
    ## @complexity — O(1)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · NEGATIVE (R5) · runner FATAL (node_yaml rc≠0) → exit 1 (DevPlan 170 W9-F1)
    # · Scenario: _default_get_many_runner path недоступен (rc≠0) → ResolverError → exit 1
    # · Last fail: N/A (new test)
    # · Remove if: FATAL-маппинг exit 1 меняется
    def test_cli_runner_fatal_exit1(
        self,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        """Раннер поднял ResolverError (node_yaml rc≠0) → exit 1 (FATAL)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing runner FATAL → exit 1")

        def failing_runner(_p: str, _s: str) -> str:
            msg = "node_yaml --get-many failed: test"
            raise ResolverError(msg)

        rc = main(["resolve", "--file", str(tmp_path / "node.yaml")], get_many_runner=failing_runner)
        capsys.readouterr()
        assert rc == 1, f"Expected exit 1 (FATAL), got {rc}"
        logger.info("[IMP:9][test_bootstrap_resolver] Runner FATAL → exit 1 ✓")

    # endregion FUNC_test_cli_runner_fatal_exit1

    # region FUNC_test_cli_mask
    ## @purpose — mask --key → rc 0, stdout = маска (dry-run-вывод bootstrap.sh).
    ## @io — ⇥ main() + capsys → ⎋ None (asserts rc + stdout)
    ## @complexity — O(1)
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · CLI mask --key → <AGE_KEY:xxxx...> (DevPlan 170 W9-F1)
    # · Scenario: main(['mask','--key','AGE-SECRET-KEY-abcdef123456']) → rc 0, <AGE_KEY:AGE-SECR...>
    # · Last fail: N/A (new test — замена cut -c1-8 в bootstrap.sh:148)
    # · Remove if: mask CLI контракт меняется
    def test_cli_mask(self, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture) -> None:
        """mask --key → rc 0, stdout = маскированная форма (без полного ключа)."""
        caplog.set_level(logging.DEBUG)
        logger.info("[IMP:7][test_bootstrap_resolver] Testing CLI mask")

        rc = main(["mask", "--key", "AGE-SECRET-KEY-abcdef123456"])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        assert captured.out.strip() == "<AGE_KEY:AGE-SECR...>", f"Unexpected mask: {captured.out!r}"
        assert "abcdef123456" not in captured.out, "Полный ключ не должен попасть в stdout"
        logger.info("[IMP:9][test_bootstrap_resolver] CLI mask → rc 0, маска без полного ключа ✓")

    # endregion FUNC_test_cli_mask


# endregion CLASS_TestCLI
