# 🧪 TRAP[TEST] · REF-0013 · merge-guard Step 3.5 + file-wins override
# GREP_SUMMARY: test-secrets-merge-guard, merge-guard, step-3.5, unparsed-nonempty, operator-secrets, file-wins, protected-allowlist, stale-os-environ, persist-new-vars-guard
# STRUCTURE: ▶ ensure_secrets [nonempty-unparseable env + generated manifest] → ⚡ConfigValidationError + file byte-identical → ▶ controls: missing/empty/parseable → ⎋ write proceeds → ▶ apply_env_file_to_osenv → ◇ file-wins / protected kept → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for REF-0013 guards in core/internal/bootstrap/lifecycle/secrets_manager.py:
##           (1) Merge-guard Step 3.5: непустой secrets.env, распарсившийся в 0 записей,
##           НЕ перезаписывается набором `{} + generated` (исходный баг: необратимое уничтожение
##           GHCR_PULL_TOKEN/TELEGRAM_*/PLATFORM_MASTER_*); (2) _persist_new_vars — тот же guard;
##           (3) apply_env_file_to_osenv — file-wins после decrypt (свежий decrypt больше не
##           проигрывает stale os.environ), protected lifecycle-переменные не перезаписываются.
## @scope    Pure unit tests — subprocess замокан (_generate_secret), файлы в tmp_path.
## @invariants
##   - Guard-кейс: ConfigValidationError, байты файла на диске НЕ меняются
##   - Контролы: отсутствующий/пустой/парсабельный файл guard НЕ триггерят
##   - file-wins: значение файла сильнее os.environ; AGE_SECRET_KEY/NODE_NAME/etc — исключения
## @rationale REF-0013: merge-from-parsed-copy без сверки с фактом файла = silent data loss;
##            inverted precedence (`if k not in os.environ`) обыгрывал свежий decrypt.
# endregion MODULE_CONTRACT

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from core.internal.bootstrap.lifecycle import secrets_manager as sm
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.secrets_env_parser import parse as parse_secrets_env
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# Autogen no-op предустановка (мастер-креды + derived пароли уже заданы → ensure их не трогает)
_AUTOGEN_ENV_VARS = (
    "PLATFORM_MASTER_EMAIL",
    "PLATFORM_MASTER_PASSWORD",
    "HERMES_DASHBOARD_PASSWORD",
    "GF_SECURITY_ADMIN_PASSWORD",
    "LANGFUSE_INIT_USER_PASSWORD",
)


@pytest.fixture(autouse=True)
def _isolate_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Изоляция os.environ: autogen-переменные предустановлены (no-op), целевые — удалены.

    Teardown делает явный os.environ.pop: ensure_secrets применяет значения НАПРЯМУЮ в
    os.environ (file-wins), monkeypatch.delenv-undo такие присваивания не откатывает —
    без pop переменные утекают в соседние тест-файлы при общем процессе (check-diff).
    """
    for var in _AUTOGEN_ENV_VARS:
        monkeypatch.setenv(var, "pre-set-noop-value")
    for var in ("GHCR_PULL_TOKEN", "TELEGRAM_BOT_TOKEN", "OTHER_KEEP_VAR"):
        monkeypatch.delenv(var, raising=False)
    yield
    import os

    for var in (*_AUTOGEN_ENV_VARS, "GHCR_PULL_TOKEN", "TELEGRAM_BOT_TOKEN", "OTHER_KEEP_VAR"):
        os.environ.pop(var, None)


def _write_manifest(tmp_path: Path, entries: list[dict[str, str]]) -> Path:
    """Write a minimal secrets-manifest.yaml with the given generated entries."""
    if not entries:
        body = "secrets: []\n"
    else:
        lines = ["secrets:"]
        for entry in entries:
            lines.append(f"  - name: {entry['name']}")
            lines.append(f"    tier: {entry.get('tier', 'generated')}")
            if "source" in entry:
                lines.append(f"    source: {entry['source']}")
            lines.append(f"    gen_command: '{entry['gen_command']}'")
        body = "\n".join(lines) + "\n"
    manifest = tmp_path / "secrets-manifest.yaml"
    manifest.write_text(body, encoding="utf-8")
    return manifest


# ═══════════════════════════════════════════════════════════════════
# Tests: Merge-guard Step 3.5
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_merge_guard_aborts_on_unparsed_nonempty_file
## @purpose  Исходный вход бага REF-0013: непустой secrets.env на диске + parse → {} +
##           generated-набор → guard ДОЛЖЕН прервать запись; файл остаётся байт-идентичным.
## @io       ⇥ tmp_path, caplog → ⎋ None (asserts)
@ldd_trajectory
def test_merge_guard_aborts_on_unparsed_nonempty_file(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Nonempty-but-unparseable secrets.env is never overwritten by `{} + generated`."""
    secrets_env = tmp_path / "secrets.env"
    original_content = "GARBAGE LINE WITHOUT EQUALS\n"
    secrets_env.write_text(original_content, encoding="utf-8")
    manifest = _write_manifest(tmp_path, [{"name": "GHCR_PULL_TOKEN", "tier": "generated", "gen_command": "echo tok"}])

    with (
        patch.object(sm, "_generate_secret", return_value="tok-value"),
        patch.object(sm, "_ensure_master_credentials"),
        patch.object(sm, "_ensure_derived_passwords"),
        patch.object(sm, "_ensure_htpasswd", return_value=True),
        pytest.raises(ConfigValidationError, match=r"[Mm]erge-guard"),
    ):
        sm.ensure_secrets(str(manifest), str(secrets_env), persist_to_sops=False)

    assert secrets_env.read_text(encoding="utf-8") == original_content, (
        "Merge-guard must leave the on-disk file byte-identical (operator secrets preserved)"
    )
    imp10 = any("[IMP:10]" in r.message and "MERGE-GUARD" in r.message for r in caplog.records)
    assert imp10, "Missing IMP:10 MERGE-GUARD log"
    logger.info("[IMP:9][test_merge_guard_aborts_on_unparsed_nonempty_file] ✅ Guard aborted, file intact")


# endregion FUNC_test_merge_guard_aborts_on_unparsed_nonempty_file


# region FUNC_test_merge_guard_not_triggered_controls
## @purpose  Негативные контроли guard'а: отсутствующий файл, пустой файл (0 байт),
##           comment-only файл (семантически пуст — парсер-контракт «only-comments → {}»,
##           DevPlan 176 flow легитимно перезаписывает) и парсабельный непустой файл
##           guard НЕ триггерят (fresh-node first-write работает).
## @io       ⇥ tmp_path → ⎋ None (asserts)
@pytest.mark.parametrize("scenario", ["missing-file", "empty-file", "comment-only-file", "parseable-file"])
@ldd_trajectory
def test_merge_guard_not_triggered_controls(tmp_path: Path, scenario: str) -> None:
    """Guard fires only for files with unparsed non-comment content; valid flows proceed."""
    secrets_env = tmp_path / "secrets.env"
    if scenario == "empty-file":
        secrets_env.write_text("", encoding="utf-8")
    elif scenario == "comment-only-file":
        secrets_env.write_text("# empty secrets.env\n\n", encoding="utf-8")
    elif scenario == "parseable-file":
        secrets_env.write_text("OTHER_KEEP_VAR=keep-me\n", encoding="utf-8")
    before = secrets_env.read_text(encoding="utf-8") if secrets_env.exists() else None

    manifest = _write_manifest(tmp_path, [{"name": "GHCR_PULL_TOKEN", "tier": "generated", "gen_command": "echo tok"}])

    with (
        patch.object(sm, "_generate_secret", return_value="tok-value"),
        patch.object(sm, "_ensure_master_credentials"),
        patch.object(sm, "_ensure_derived_passwords"),
        patch.object(sm, "_ensure_htpasswd", return_value=True),
    ):
        generated = sm.ensure_secrets(str(manifest), str(secrets_env), persist_to_sops=False)

    assert generated == ["GHCR_PULL_TOKEN"], f"Expected GHCR_PULL_TOKEN generated in {scenario}, got {generated}"
    parsed_after = parse_secrets_env(str(secrets_env))
    assert parsed_after.get("GHCR_PULL_TOKEN") == "tok-value", f"Generated var not persisted in {scenario}"
    if scenario == "parseable-file":
        assert parsed_after.get("OTHER_KEEP_VAR") == "keep-me", (
            "Existing parseable content must be preserved through the merge"
        )
    elif scenario == "missing-file":
        pass  # файла не было до прогона
    else:
        # empty/comment-only: файл существовал, но был семантически пуст
        assert before is not None, f"Control scenario precondition violated: {before!r}"
    logger.info("[IMP:9][test_merge_guard_not_triggered_controls] PASS: %s proceeds without guard", scenario)


# endregion FUNC_test_merge_guard_not_triggered_controls


# region FUNC_test_persist_new_vars_guard
## @purpose  Тот же инвариант в _persist_new_vars (autogen-персист): файл со значимым
##           нераспарсенным контентом → ConfigValidationError ДО записи, файл не тронут.
##           Вызывающие ловят (OSError, ConfigValidationError, ValueError) и остаются
##           non-fatal по дизайну autogen (typed hierarchy — контракт static-детектора).
## @io       ⇥ tmp_path → ⎋ None (asserts)
@ldd_trajectory
def test_persist_new_vars_guard(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """_persist_new_vars refuses to persist over unparsed content (ConfigValidationError)."""
    secrets_env = tmp_path / "secrets.env"
    original_content = "NOT A KEY VALUE LINE\n"
    secrets_env.write_text(original_content, encoding="utf-8")

    with pytest.raises(ConfigValidationError, match=r"[Mm]erge-guard"):
        sm._persist_new_vars("Master credentials", {"K": "v"}, parse_secrets_env, str(secrets_env))

    assert secrets_env.read_text(encoding="utf-8") == original_content, "File must remain untouched"
    logger.info("[IMP:9][test_persist_new_vars_guard] PASS: persist guard raised, file intact")


# endregion FUNC_test_persist_new_vars_guard


# ═══════════════════════════════════════════════════════════════════
# Tests: file-wins apply_env_file_to_osenv (REF-0013)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_apply_env_file_wins_overrides_stale_environ
## @purpose  File-wins: значение из файла перезаписывает stale os.environ (инверсия прежнего
##           `if k not in os.environ`) — свежий decrypt выигрывает у протухшего env.
## @io       ⇥ monkeypatch → ⎋ None (asserts)
@ldd_trajectory
def test_apply_env_file_wins_overrides_stale_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parsed file value overrides pre-existing os.environ value (file-wins)."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "stale-token")

    applied = sm.apply_env_file_to_osenv({"TELEGRAM_BOT_TOKEN": "fresh-from-decrypt"})

    import os

    assert os.environ["TELEGRAM_BOT_TOKEN"] == "fresh-from-decrypt", (
        "file-wins violated: stale os.environ survived a fresh decrypt value"
    )
    assert applied >= 1, "At least one override must be reported"
    logger.info("[IMP:9][test_apply_env_file_wins_overrides_stale_environ] PASS: file beat stale environ")


# endregion FUNC_test_apply_env_file_wins_overrides_stale_environ


# region FUNC_test_apply_env_protects_lifecycle_vars
## @purpose  Protected allowlist: AGE_SECRET_KEY и NODE_NAME из файла НЕ перезаписывают
##           os.environ (lifecycle-controlled переменные).
## @io       ⇥ monkeypatch → ⎋ None (asserts)
@ldd_trajectory
def test_apply_env_protects_lifecycle_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lifecycle-controlled vars keep their os.environ values regardless of file content."""
    import os

    monkeypatch.setenv("AGE_SECRET_KEY", "env-key-value")
    monkeypatch.setenv("NODE_NAME", "orchestrated-node")

    sm.apply_env_file_to_osenv({"AGE_SECRET_KEY": "file-key-value", "NODE_NAME": "file-node"})

    assert os.environ["AGE_SECRET_KEY"] == "env-key-value", "Protected AGE_SECRET_KEY was overwritten from file"
    assert os.environ["NODE_NAME"] == "orchestrated-node", "Protected NODE_NAME was overwritten from file"
    logger.info("[IMP:9][test_apply_env_protects_lifecycle_vars] PASS: protected vars kept")


# endregion FUNC_test_apply_env_protects_lifecycle_vars


# region FUNC_test_ensure_secrets_step1_file_wins_integration
## @purpose  Интеграционный контроль через ensure_secrets Step 1: stale TELEGRAM_BOT_TOKEN
##           в os.environ заменяется значением из secrets.env в ходе ensure-потока.
## @io       ⇥ tmp_path, monkeypatch → ⎋ None (asserts)
@ldd_trajectory
def test_ensure_secrets_step1_file_wins_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_secrets applies file-wins sourcing: fresh file beats stale environ."""
    import os

    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("TELEGRAM_BOT_TOKEN=fresh-token\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "stale-token")
    manifest = _write_manifest(tmp_path, [])  # пустой манифест — только Step 1 sourcing

    with (
        patch.object(sm, "_ensure_master_credentials"),
        patch.object(sm, "_ensure_derived_passwords"),
        patch.object(sm, "_ensure_htpasswd", return_value=True),
    ):
        sm.ensure_secrets(str(manifest), str(secrets_env), persist_to_sops=False)

    assert os.environ["TELEGRAM_BOT_TOKEN"] == "fresh-token", (
        "Step 1 must apply file-wins sourcing (REF-0013): stale environ survived"
    )
    logger.info("[IMP:9][test_ensure_secrets_step1_file_wins_integration] PASS: ensure flow applied fresh file value")


# endregion FUNC_test_ensure_secrets_step1_file_wins_integration
