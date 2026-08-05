# GREP_SUMMARY: unit-test, node-detect, detect-age-key, auto-detect-node-name, AGE_SECRET_KEY, SOPS_AGE_KEY, AGE_SECRET_KEY_FILE, node-configs, NodeDetectionError, CLI, devplan-104
# STRUCTURE: ▶ TestDetectAgeKey×4 (env→SOPS→file→none) → ▶ TestAutoDetectNodeName×4 (single→multi→none→skip) → ▶ TestCLI×3 (age-key→node-name→not-found) → ⎋ 11 pass

# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/node_detect.py — detect_age_key(),
##           auto_detect_node_name() and CLI main(). 11 tests per DevPlan 104 §9 $TEST_SPEC.
## @scope    Pure unit tests — native imports, no subprocess (CLI tested via main() + capsys),
##           no Docker. monkeypatch for env manipulation, tmp_path for dirs/files.
## @invariants
##   - Detection chain: AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE
##   - auto_detect_node_name skips scripts/ + secrets/; raises NodeDetectionError on 0 or >1 candidates
##   - Every test has real asserts (Test Honesty R1/R2) + TRAP[TEST] comment + IMP:9 LDD log
## @rationale DevPlan 104 §9 $TEST_SPEC: 11 tests — 4×detect_age_key, 4×auto_detect_node_name, 3×CLI
## @changes  2026-07-31 | DevPlan 104 — Created
# endregion MODULE_CONTRACT

import builtins
import io
import logging
from pathlib import Path

import pytest

from core.internal.shared.node_detect import (
    NodeDetectionError,
    auto_detect_node_name,
    detect_age_key,
    main,
)
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

TEST_AGE_KEY = "AGE-SECRET-KEY-0123456789abcdef"


# region CLASS_TestDetectAgeKey
## @purpose  Detect_age_key chain scenarios (DevPlan 104 §9 — 4 tests + default-file 2026-08-02).
## @scope    env → SOPS → file → default-file → not-found. Uses monkeypatch for env, tmp_path for file.
## @invariants — chain order preserved; None (not empty string) on not found; default-file probed
##               only after the full env chain is empty (HOME isolated via monkeypatch in every test)
class TestDetectAgeKey:
    # region FUNC_test_from_age_secret_key_env
    ## @purpose — Verify detect_age_key reads from AGE_SECRET_KEY env var (first chain link).
    ## @io — ⇥ monkeypatch → ⎋ None (asserts key matches)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · AGE_SECRET_KEY env detection (DevPlan 104 §9)
    # · Last fail: N/A (new test)
    # · Remove if: detect_age_key chain changes
    def test_from_age_secret_key_env(self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        """detect_age_key returns AGE_SECRET_KEY from env var."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.setenv("AGE_SECRET_KEY", TEST_AGE_KEY)
        monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
        monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)

        logger.info("[IMP:7][test_node_detect] Testing AGE_SECRET_KEY env detection")
        result = detect_age_key()
        assert result == TEST_AGE_KEY, f"Expected {TEST_AGE_KEY}, got {result}"
        logger.info("[IMP:9][test_node_detect] detect_age_key returned key from AGE_SECRET_KEY env")

    # endregion FUNC_test_from_age_secret_key_env

    # region FUNC_test_from_sops_age_key_env
    ## @purpose — Verify detect_age_key falls back to SOPS_AGE_KEY env var (second chain link).
    ## @io — ⇥ monkeypatch → ⎋ None (asserts key matches)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · SOPS_AGE_KEY fallback detection (DevPlan 104 §9)
    # · Last fail: N/A (new test)
    # · Remove if: detect_age_key chain changes
    def test_from_sops_age_key_env(self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        """detect_age_key falls back to SOPS_AGE_KEY when AGE_SECRET_KEY is not set."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
        monkeypatch.setenv("SOPS_AGE_KEY", TEST_AGE_KEY)
        monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)

        logger.info("[IMP:7][test_node_detect] Testing SOPS_AGE_KEY fallback")
        result = detect_age_key()
        assert result == TEST_AGE_KEY, f"Expected {TEST_AGE_KEY}, got {result}"
        logger.info("[IMP:9][test_node_detect] detect_age_key returned key from SOPS_AGE_KEY fallback")

    # endregion FUNC_test_from_sops_age_key_env

    # region FUNC_test_from_file
    ## @purpose — Verify detect_age_key reads AGE_SECRET_KEY_FILE content (third chain link).
    ## @io — ⇥ monkeypatch, tmp_path → ⎋ None (asserts key matches)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · AGE_SECRET_KEY_FILE detection (DevPlan 104 §9)
    # · Last fail: N/A (new test)
    # · Remove if: detect_age_key chain changes
    def test_from_file(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """detect_age_key reads AGE_SECRET_KEY_FILE when env vars are not set."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
        monkeypatch.delenv("SOPS_AGE_KEY", raising=False)

        key_file = tmp_path / "age-key.txt"
        key_file.write_text(TEST_AGE_KEY + "\n")
        monkeypatch.setenv("AGE_SECRET_KEY_FILE", str(key_file))

        logger.info("[IMP:7][test_node_detect] Testing AGE_SECRET_KEY_FILE detection")
        result = detect_age_key()
        assert result == TEST_AGE_KEY, f"Expected {TEST_AGE_KEY}, got {result}"
        logger.info("[IMP:9][test_node_detect] detect_age_key returned key from AGE_SECRET_KEY_FILE")

    # endregion FUNC_test_from_file

    # region FUNC_test_not_found
    ## @purpose — Verify detect_age_key returns None when no key source is available.
    ## @io — ⇥ monkeypatch, tmp_path → ⎋ None (asserts None)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · No AGE key source available (DevPlan 104 §9)
    # · 2026-08-02: HOME isolated to tmp_path — default-file chain link (added for E2E auto-detect)
    # ·   would otherwise find the operator's real ~/.ssh/age-key-personal.txt on dev machines
    # · Remove if: detect_age_key chain changes
    def test_not_found(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """detect_age_key returns None when all three sources + default files are absent."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
        monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
        monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))  # isolate default-file probe from real home

        logger.info("[IMP:7][test_node_detect] Testing missing key — all sources absent")
        result = detect_age_key()
        assert result is None, f"Expected None, got {result}"
        logger.info("[IMP:9][test_node_detect] detect_age_key returned None when all sources absent")

    # endregion FUNC_test_not_found

    # region FUNC_test_from_default_user_file
    ## @purpose — Verify detect_age_key falls back to ~/.config/age/keys.txt (4th chain link,
    ##            E2E auto-detect, 2026-08-02; единственный default-путь с 2026-08-03) when
    ##            the full env chain is empty.
    ## @io — ⇥ monkeypatch, tmp_path → ⎋ None (asserts key matches)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · default key file detection (E2E channel)
    # · 2026-08-03: путь заменён на ~/.config/age/keys.txt (age CLI default, symlink-конвенция)
    # · Last fail: N/A (new test — recreatable test-VPS 103.88.243.151)
    # · Remove if: detect_age_key default-file chain link is removed
    def test_from_default_user_file(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """detect_age_key reads ~/.config/age/keys.txt when env chain is empty."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
        monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
        monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        config_dir = tmp_path / ".config" / "age"
        config_dir.mkdir(parents=True)
        (config_dir / "keys.txt").write_text(TEST_AGE_KEY + "\n")

        logger.info("[IMP:7][test_node_detect] Testing default ~/.config/age/keys.txt detection")
        result = detect_age_key()
        assert result == TEST_AGE_KEY, f"Expected {TEST_AGE_KEY}, got {result}"
        logger.info("[IMP:9][test_node_detect] detect_age_key returned key from default key file")

    # endregion FUNC_test_from_default_user_file


# endregion CLASS_TestDetectAgeKey


# region CLASS_TestDetectAgeKeyNodePersistence
## @purpose  D15 (DevPlan 136 W1 T1.4, d2ded6a) — node canonical key file /etc/age/key.txt:
##           detect-цепочка (Check 5) читает персистённый φ4 ключ. /etc/age/key.txt ОТСУТСТВУЕТ →
##           цепочка завершается None (R5 negative на точный вход: CI node-update без env-ключа,
##           ключ не персистён → decrypt fail); ПРИСУТСТВУЕТ → ключ возвращается (assert персист).
## @scope    mock Path.is_file + builtins.open для /etc/age/key.txt (детерминизм на любой машине);
##           HOME изолирован в tmp_path (default-file probe Check 4 не мешает).
## @invariants — Check 5 = последнее звено цепочки; чтение через comment-scan (AGE-SECRET-KEY- строка)
class TestDetectAgeKeyNodePersistence:
    # region FUNC_test_detect_age_key_from_node_key_file
    ## @purpose — D15: /etc/age/key.txt ПРИСУТСТВУЕТ (φ4 персист) → detect_age_key возвращает ключ.
    ## @io — ⇥ caplog, monkeypatch, tmp_path → ⎋ None (asserts key + IMP:9)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · D15 — node key file /etc/age/key.txt (d2ded6a)
    # · Scenario: /etc/age/key.txt существует (персист φ4) + env-цепочка пуста → ключ возвращается
    # · Last fail: 2026-08-04 — CI node-update decrypt fail (ключ не жил на ноде)
    # · Remove if: detect-цепочка Check 5 удаляется/меняется
    def test_detect_age_key_from_node_key_file(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """detect_age_key возвращает персистённый ключ из /etc/age/key.txt (D15 assert персист)."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
        monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
        monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))  # isolate default-file probe

        real_is_file = Path.is_file
        real_open = builtins.open

        def fake_is_file(self):
            if str(self) == "/etc/age/key.txt":
                return True
            return real_is_file(self)

        def fake_open(path, *args, **kwargs):
            if str(path) == "/etc/age/key.txt":
                return io.StringIO("# created by bootstrap φ4\n# public key: age1...\n" + TEST_AGE_KEY + "\n")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "is_file", fake_is_file)
        monkeypatch.setattr(builtins, "open", fake_open)

        logger.info("[IMP:7][test_node_detect] Testing /etc/age/key.txt node file detection (D15)")
        result = detect_age_key()
        assert result == TEST_AGE_KEY, f"Expected key from /etc/age/key.txt, got {result}"
        logger.info("[IMP:9][test_node_detect] detect_age_key вернул персистённый node-ключ (D15)")

    # endregion FUNC_test_detect_age_key_from_node_key_file

    # region FUNC_test_detect_age_key_node_file_absent_chain_completes
    ## @purpose — R5 negative (D15): /etc/age/key.txt ОТСУТСТВУЕТ + env пуст → цепочка завершается
    ##            None (без исключений, IMP:8 warning). Точный вход бага: CI node-update без
    ##            AGE_SECRET_KEY env и без персистённого файла → раньше decrypt fail.
    ## @io — ⇥ caplog, monkeypatch, tmp_path → ⎋ None (asserts None + warning)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · D15 — /etc/age/key.txt отсутствует → цепочка доходит до None
    # · Scenario: env пуст, HOME изолирован, /etc/age/key.txt mocked False → detect_age_key() is None
    # · Last fail: 2026-08-04 — ключ не персистился на ноду → CI node-update decrypt FAIL
    # · Remove if: Check 5 / persist-логика меняются
    def test_detect_age_key_node_file_absent_chain_completes(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """R5 negative (D15): без node-файла цепочка завершается None (не ломается)."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
        monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
        monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        real_is_file = Path.is_file

        def fake_is_file(self):
            if str(self) == "/etc/age/key.txt":
                return False  # ключ не персистён — точный вход D15
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)

        logger.info("[IMP:7][test_node_detect] Testing node key file ABSENT (D15 negative)")
        result = detect_age_key()
        assert result is None, f"Expected None when /etc/age/key.txt absent, got {result}"
        assert "AGE_SECRET_KEY not found" in caplog.text, "Должен быть IMP:8 warning о ненайденном ключе"
        logger.info("[IMP:9][test_node_detect] detect-цепочка завершилась без node-файла (D15 negative)")

    # endregion FUNC_test_detect_age_key_node_file_absent_chain_completes

    # region FUNC_test_detect_age_key_node_file_without_prefix_line
    ## @purpose — D15: /etc/age/key.txt существует, но без строки AGE-SECRET-KEY- (comment-файл) →
    ##            warning «no AGE-SECRET-KEY- line» + None (comment-scan, не слепой readline).
    ## @io — ⇥ caplog, monkeypatch, tmp_path → ⎋ None (asserts None + warning)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION · D15 — node-файл без AGE-префикса (comment-scan)
    # · Scenario: /etc/age/key.txt с комментариями без AGE-SECRET-KEY- → None + warning
    # · Last fail: N/A (защита comment-scan контракта Check 5)
    # · Remove if: Check 5 чтение меняется
    def test_detect_age_key_node_file_without_prefix_line(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """detect_age_key: node-файл без AGE-SECRET-KEY- строки → None (comment-scan)."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
        monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
        monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        real_is_file = Path.is_file
        real_open = builtins.open

        def fake_is_file(self):
            if str(self) == "/etc/age/key.txt":
                return True
            return real_is_file(self)

        def fake_open(path, *args, **kwargs):
            if str(path) == "/etc/age/key.txt":
                return io.StringIO("# public key: age1...\n# no secret here\n")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "is_file", fake_is_file)
        monkeypatch.setattr(builtins, "open", fake_open)

        logger.info("[IMP:7][test_node_detect] Testing node key file without AGE prefix line")
        result = detect_age_key()
        assert result is None, f"Expected None for non-AGE content, got {result}"
        assert "no AGE-SECRET-KEY- line" in caplog.text, "Должен быть warning о строке без AGE-префикса"
        logger.info("[IMP:9][test_node_detect] comment-scan отклонил файл без AGE-строки (D15)")

    # endregion FUNC_test_detect_age_key_node_file_without_prefix_line


# endregion CLASS_TestDetectAgeKeyNodePersistence


# region CLASS_TestAutoDetectNodeName
## @purpose  Auto_detect_node_name scenarios (DevPlan 104 §9 — 4 tests).
## @scope    single → multiple → none → scripts/secrets exclusion. Uses tmp_path.
## @invariants — scripts/ and secrets/ never candidates; 0 or >1 candidates raise NodeDetectionError
class TestAutoDetectNodeName:
    # region FUNC_test_single_node
    ## @purpose — Verify auto_detect_node_name returns the single valid node dir name.
    ## @io — ⇥ tmp_path → ⎋ None (asserts name)
    ## @complexity — O(N)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · single-node detection (DevPlan 104 §9)
    # · Last fail: N/A (new test)
    # · Remove if: auto_detect_node_name logic changes
    def test_single_node(self, caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
        """auto_detect_node_name returns the single valid node dir name."""
        caplog.set_level(logging.DEBUG)

        ncd = tmp_path / "node-configs"
        (ncd / "tronyx-vps").mkdir(parents=True)

        logger.info("[IMP:7][test_node_detect] Testing single-node detection")
        result = auto_detect_node_name(str(ncd))
        assert result == "tronyx-vps", f"Expected 'tronyx-vps', got {result}"
        logger.info("[IMP:9][test_node_detect] auto_detect_node_name resolved single node '%s'", result)

    # endregion FUNC_test_single_node

    # region FUNC_test_multiple_nodes
    ## @purpose — Verify auto_detect_node_name raises on multiple candidates.
    ## @io — ⇥ tmp_path → ⎋ None (asserts NodeDetectionError)
    ## @complexity — O(N)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · ambiguous multiple nodes (DevPlan 104 §9)
    # · Last fail: N/A (new test)
    # · Remove if: auto_detect_node_name logic changes
    def test_multiple_nodes(self, caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
        """auto_detect_node_name raises NodeDetectionError on multiple candidates."""
        caplog.set_level(logging.DEBUG)

        ncd = tmp_path / "node-configs"
        (ncd / "node-alpha").mkdir(parents=True)
        (ncd / "node-beta").mkdir(parents=True)

        logger.info("[IMP:7][test_node_detect] Testing multiple-node ambiguity")
        with pytest.raises(NodeDetectionError, match="Multiple directories"):
            auto_detect_node_name(str(ncd))
        logger.info("[IMP:9][test_node_detect] Multiple nodes rejected with NodeDetectionError")

    # endregion FUNC_test_multiple_nodes

    # region FUNC_test_no_nodes
    ## @purpose — Verify auto_detect_node_name raises on an empty configs dir.
    ## @io — ⇥ tmp_path → ⎋ None (asserts NodeDetectionError)
    ## @complexity — O(N)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · empty configs dir (DevPlan 104 §9)
    # · Last fail: N/A (new test)
    # · Remove if: auto_detect_node_name logic changes
    def test_no_nodes(self, caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
        """auto_detect_node_name raises NodeDetectionError on a configs dir with no candidates."""
        caplog.set_level(logging.DEBUG)

        ncd = tmp_path / "node-configs"
        ncd.mkdir(parents=True)

        logger.info("[IMP:7][test_node_detect] Testing empty configs dir")
        with pytest.raises(NodeDetectionError, match="No node directories"):
            auto_detect_node_name(str(ncd))
        logger.info("[IMP:9][test_node_detect] Empty configs dir rejected with NodeDetectionError")

    # endregion FUNC_test_no_nodes

    # region FUNC_test_skips_scripts_secrets
    ## @purpose — Verify auto_detect_node_name excludes scripts/ and secrets/ subdirectories.
    ## @io — ⇥ tmp_path → ⎋ None (asserts only real node dir is considered)
    ## @complexity — O(N)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · scripts/secrets exclusion (DevPlan 104 §9)
    # · Last fail: N/A (new test)
    # · Remove if: auto_detect_node_name logic changes
    def test_skips_scripts_secrets(self, caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
        """auto_detect_node_name excludes scripts/ and secrets/ subdirectories."""
        caplog.set_level(logging.DEBUG)

        ncd = tmp_path / "node-configs"
        (ncd / "scripts").mkdir(parents=True)
        (ncd / "secrets").mkdir(parents=True)
        (ncd / "real-node").mkdir(parents=True)

        logger.info("[IMP:7][test_node_detect] Testing scripts/secrets exclusion")
        result = auto_detect_node_name(str(ncd))
        assert result == "real-node", f"scripts/secrets must be excluded, got {result}"
        logger.info("[IMP:9][test_node_detect] scripts/secrets skipped — node '%s' detected", result)

    # endregion FUNC_test_skips_scripts_secrets

    # region FUNC_test_skips_scripts_secrets_app_fixture
    ## @purpose — DevPlan 116 B3 T2 (U-38) fixture: dirs {app, scripts, secrets} → "app".
    ##            Reproduces the metrics-wrapper scenario (platform-export-metrics.sh) where
    ##            scripts/ would previously be picked as NODE_NAME by `ls | grep -v secrets`.
    ## @io — ⇥ tmp_path → ⎋ None (asserts "app")
    ## @complexity — O(N)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · app/scripts/secrets fixture (DevPlan 116 B3 T2, U-38)
    # · Last fail: scripts/ WAS picked as node name by `ls | grep -v secrets | head -1` in
    # ·   platform-export-metrics.sh:31-33 (grep -v secrets did not exclude scripts)
    # · Remove if: auto_detect_node_name SKIP_DIRS changes
    def test_skips_scripts_secrets_app_fixture(
        self, caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory
    ) -> None:
        """auto_detect_node_name with {app, scripts, secrets} resolves 'app' (T2 fixture)."""
        caplog.set_level(logging.DEBUG)

        ncd = tmp_path / "node-configs"
        (ncd / "app").mkdir(parents=True)
        (ncd / "scripts").mkdir(parents=True)
        (ncd / "secrets").mkdir(parents=True)

        logger.info("[IMP:7][test_node_detect] Testing T2 app/scripts/secrets fixture")
        result = auto_detect_node_name(str(ncd))
        assert result == "app", f"Expected 'app' (scripts/secrets excluded), got {result}"
        logger.info("[IMP:9][test_node_detect] app resolved with scripts/secrets excluded (T2 fixture)")

    # endregion FUNC_test_skips_scripts_secrets_app_fixture


# endregion CLASS_TestAutoDetectNodeName


# region CLASS_TestCLI
## @purpose  CLI scenarios (DevPlan 104 §9 — 3 tests).
## @scope    main() called directly with capsys — native import, no subprocess.
## @invariants — stdout carries only the value; exit 0 success / exit 3 key-absent / exit 1 error
class TestCLI:
    # region FUNC_test_detect_age_key_flag
    ## @purpose — Verify --detect-age-key prints key to stdout and returns 0.
    ## @io — ⇥ main(argv) + capsys → ⎋ None (asserts rc + stdout)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · CLI --detect-age-key (DevPlan 104 §9)
    # · Last fail: N/A (new test)
    # · Remove if: node_detect CLI is reworked
    def test_detect_age_key_flag(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """main(['--detect-age-key']) prints the key on stdout and returns 0."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.setenv("AGE_SECRET_KEY", TEST_AGE_KEY)
        monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
        monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)

        logger.info("[IMP:7][test_node_detect] Testing CLI --detect-age-key")
        rc = main(["--detect-age-key"])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        assert captured.out.strip() == TEST_AGE_KEY, f"Expected key on stdout, got {captured.out!r}"
        logger.info("[IMP:9][test_node_detect] CLI --detect-age-key printed key and exited 0")

    # endregion FUNC_test_detect_age_key_flag

    # region FUNC_test_detect_node_name_flag
    ## @purpose — Verify --detect-node-name with --node-configs-dir prints the node name.
    ## @io — ⇥ main(argv) + tmp_path + capsys → ⎋ None (asserts rc + stdout)
    ## @complexity — O(N)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · CLI --detect-node-name (DevPlan 104 §9)
    # · Last fail: N/A (new test)
    # · Remove if: node_detect CLI is reworked
    def test_detect_node_name_flag(
        self,
        caplog: pytest.LogCaptureFixture,
        tmp_path: pytest.TempPathFactory,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """main(['--detect-node-name']) with a tmp configs dir prints the node name and returns 0."""
        caplog.set_level(logging.DEBUG)

        ncd = tmp_path / "node-configs"
        (ncd / "cli-node").mkdir(parents=True)

        logger.info("[IMP:7][test_node_detect] Testing CLI --detect-node-name")
        rc = main(["--detect-node-name", "--node-configs-dir", str(ncd)])
        captured = capsys.readouterr()
        assert rc == 0, f"Expected exit 0, got {rc}"
        assert captured.out.strip() == "cli-node", f"Expected node name on stdout, got {captured.out!r}"
        logger.info("[IMP:9][test_node_detect] CLI --detect-node-name printed 'cli-node' and exited 0")

    # endregion FUNC_test_detect_node_name_flag

    # region FUNC_test_detect_age_key_not_found
    ## @purpose — Negative test: --detect-age-key with no key → exit 3, empty stdout (R5 anti-survivorship).
    ##            Exit 3 = module OK, key absent (language policy — no inline python3 probe, TRAP[DECISION]).
    ## @io — ⇥ main(argv) + capsys + tmp_path → ⎋ None (asserts rc + empty stdout)
    ## @complexity — O(1)
    @pytest.mark.unit
    @ldd_trajectory

    # 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · CLI --detect-age-key not-found path (DevPlan 104 §9)
    # · 2026-08-02: HOME isolated to tmp_path — default-file chain link (E2E auto-detect) would
    # ·   otherwise return the operator's real key on dev machines (exit 0 instead of 3)
    # · Remove if: node_detect CLI is reworked
    def test_detect_age_key_not_found(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """main(['--detect-age-key']) with no key returns 3 and empty stdout."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
        monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
        monkeypatch.delenv("AGE_SECRET_KEY_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))  # isolate default-file probe from real home

        logger.info("[IMP:7][test_node_detect] Testing CLI --detect-age-key not-found path")
        rc = main(["--detect-age-key"])
        captured = capsys.readouterr()
        assert rc == 3, f"Expected exit 3 (key absent, module OK), got {rc}"
        assert captured.out.strip() == "", f"Expected empty stdout, got {captured.out!r}"
        logger.info("[IMP:9][test_node_detect] CLI --detect-age-key exited 3 on missing key (negative)")

    # endregion FUNC_test_detect_age_key_not_found


# endregion CLASS_TestCLI
