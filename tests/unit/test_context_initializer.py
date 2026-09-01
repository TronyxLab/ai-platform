# GREP_SUMMARY: test context_initializer scaffold context overlay platform node-configs skeleton deploy-key idempotent registration
# STRUCTURE: ┌fixture setup┐ → ○ 16 tests (nested layout + single overlay repo + read-only deploy key) → ⊕ LDD trajectory (IMP:9) → ⚡ anti-loop counter
# region MODULE_CONTRACT
## @purpose  Unit-тесты context_initializer.py: вложенный overlay-layout (platform/{node-configs,
##           modules/hermes-agent,projects} — DevPlan 022 TASK-2), skeleton node.yaml с repos.core
##           (SSH-алиасный URL — DevPlan 024 TASK-2), один GitHub overlay-репо (<org>/<ctx>-overlay)
##           с read-only deploy key (provision_deploy_key: ssh-keygen → gh repo deploy-key add,
##           идемпотентность, graceful degradation при отсутствии gh), регистрация в platform
##           node.yaml. LDD IMP:9 + Anti-Loop + R1-R5.
## @scope    Tests under tests/ (unit, no Docker). DI over Mocks для gh/git/keygen subprocess
##           и context_registry.
## @invariants
##   - Все тесты используют tmp_path (R1: No hardcoded paths)
##   - gh_runner, git_runner и keygen_runner внедряются как callable (DI)
##   - context_registry.register_context mock для теста регистрации (исключение:
##     test_register_in_platform_yaml — реальный путь записи в YAML + idempotency)
##   - Все тесты с @ldd_trajectory декоратором
##   - R1-R5 compliance
##   - DevPlan 022 TASK-2: сестринские hermes-agent/ + node-configs/ НЕ создаются;
##     ровно один gh repo create <ctx>-overlay
##   - DevPlan 024 TASK-2: skeleton repos.core = git@github.com-overlay:<org>/<ctx>-overlay.git
##     (НЕ https://github.com); deploy key read-only (БЕЗ --allow-write); keypair в
##     <ctx>/.secrets/ (0600/0644); дубликат «already exists» = success; gh-fail → БЕЗ keygen
## @rationale AC2 (DevPlan 022): new-context порождает вложенную структуру + один overlay-репо.
##            AC3 (DevPlan 024): scaffold провижинит repo-side deploy key автоматически.
## ⚠️ TRAP[DECISION] · 2026-07-31 · MED · Дедупликация: unit-версия тестов удалена (import file mismatch)
## · Rejected: оставить tests/unit/test_context_initializer.py (риск: pytest import file mismatch —
##   одинаковый basename с tests/test_context_initializer.py ломает collection всего сьюта)
## · Reason: корневая версия каноническая; уникальный сценарий
##   test_register_in_platform_yaml (реальная регистрация, не mock) перенесён сюда.
## · Rev: если unit-директория вернётся к полному покрытию — ресинхронизировать inventory.
## @changes 2026-09-01 · DevPlan 024 TASK-2 — SSH-алиасный skeleton URL, 5 deploy-key тестов
##           (happy-path, gh-unavailable, duplicate-tolerated, idempotent-keypair, skeleton-URL)
## @changes 2026-07-31 · DevPlan 092 AC4 — initial implementation
## @changes 2026-07-31 · Dedup fix — test_register_in_platform_yaml перенесён из tests/unit/
## @changes 2026-09-01 · DevPlan 022 TASK-2 — nested layout, один overlay-репо, skeleton repos.core,
##           main() glob-резолв */platform/node-configs/<node>/node.yaml
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib
import stat

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

from core.internal.scaffold.context_initializer import (
    check_idempotent,
    create_dirs,
    create_skeleton_node_yaml,
    gh_repo_create,
    main,
    provision_deploy_key,
    register_in_platform_yaml,
    report_summary,
    validate_name,
)


@ldd_trajectory
def test_create_dirs_nested_layout(tmp_path: pathlib.Path, caplog) -> None:
    """DevPlan 022 TASK-2: вложенный overlay-layout вместо сестринских каталогов.

    ## @purpose — AC2: create_dirs создаёт platform/{node-configs,modules/hermes-agent,projects};
    ##            сестринские hermes-agent/ и node-configs/ НЕ создаются.
    ## @io — ⇥ tmp_path, caplog → ⎋ None (asserts)
    """
    context_dir = tmp_path / "test-context"
    logger.info("[IMP:9][test][context] test_create_dirs_nested_layout — creating %s", context_dir)
    create_dirs(context_dir)
    assert context_dir.exists(), f"Context dir not created: {context_dir}"
    assert (context_dir / "platform" / "node-configs").exists(), "platform/node-configs/ not created"
    assert (context_dir / "platform" / "modules" / "hermes-agent").exists(), (
        "platform/modules/hermes-agent/ not created"
    )
    assert (context_dir / "platform" / "projects").exists(), "platform/projects/ not created"
    assert (context_dir / "platform" / "node-configs").is_dir(), "platform/node-configs/ is not a directory"
    assert (context_dir / "platform" / "modules" / "hermes-agent").is_dir(), (
        "platform/modules/hermes-agent/ is not a directory"
    )
    # Negative (R5): сестринский legacy-layout не создаётся
    assert not (context_dir / "hermes-agent").exists(), "sibling hermes-agent/ must NOT be created"
    assert not (context_dir / "node-configs").exists(), "sibling node-configs/ must NOT be created"


@ldd_trajectory
def test_create_skeleton_node_yaml_nested_path(tmp_path: pathlib.Path, caplog) -> None:
    """DevPlan 022 TASK-2: skeleton в platform/node-configs/<node>/node.yaml + repos.core = overlay.

    ## @purpose — AC2: шаблон содержит секцию repos.core → <org>/<ctx>-overlay.git.
    ## @io — ⇥ tmp_path, caplog → ⎋ None (asserts)
    """
    skeleton_path = tmp_path / "platform" / "node-configs" / "tronyx-vps" / "node.yaml"
    context_name = "test-ctx"
    logger.info("[IMP:9][test][context] test_create_skeleton_node_yaml_nested_path — %s", skeleton_path)
    create_skeleton_node_yaml(skeleton_path, context_name, "test-org")
    assert skeleton_path.exists(), f"Skeleton node.yaml not created: {skeleton_path}"
    content = skeleton_path.read_text()
    assert "GREP_SUMMARY:" in content, "Missing GREP_SUMMARY in skeleton"
    assert "STRUCTURE:" in content, "Missing STRUCTURE in skeleton"
    # contexts[] canon (DevPlan 116 B6 T1.4): `context:` поле заменено на contexts:[0].name
    assert f"contexts:\n  - name: {context_name}" in content, f"Missing 'contexts[0].name: {context_name}' in skeleton"
    # DevPlan 022 TASK-2 + 024 TASK-2: repos.core = единственный overlay-репо, SSH-алиасный URL
    assert "repos:" in content, "Missing 'repos:' section in skeleton"
    assert f"core: git@github.com-overlay:test-org/{context_name}-overlay.git" in content, (
        f"Missing SSH-alias repos.core URL for '{context_name}' in skeleton"
    )
    assert "https://github.com" not in content, "Skeleton must NOT contain HTTPS URL (VPS clone needs SSH alias)"
    assert "node:" in content, "Missing 'node:' section"
    assert "modules:" in content, "Missing 'modules:' section"
    assert "projects:" in content, "Missing 'projects:' section"


@ldd_trajectory
def test_existing_context_idempotent(tmp_path: pathlib.Path, caplog) -> None:
    context_dir = tmp_path / "existing-context"
    context_dir.mkdir(parents=True)
    (context_dir / "README.md").write_text("# existing")
    logger.info("[IMP:9][test][context] test_existing_context_idempotent — should SKIP")
    assert check_idempotent(context_dir) is True  # T3.3: sys.exit(0) → return True
    idem_logs = [r for r in caplog.records if "SKIP" in r.message or "idempotent" in r.message.lower()]
    assert len(idem_logs) >= 1, f"Expected SKIP/idempotent log, got {len(idem_logs)}"


@ldd_trajectory
def test_gh_repo_create_gh_not_found(tmp_path: pathlib.Path, caplog) -> None:
    def gh_not_found(cmd: list[str]) -> tuple[int, str, str]:
        return -1, "", "gh: command not found"

    logger.info("[IMP:9][test][context] test_gh_repo_create_gh_not_found — gh not found")
    overlay_repo, reserved, warnings = gh_repo_create(
        org="test-org",
        ctx="test-ctx",
        skip=False,
        context_dir=tmp_path,
        gh_runner=gh_not_found,
    )
    assert overlay_repo is None, f"Expected None overlay_repo, got {overlay_repo}"
    assert reserved is None, f"Expected None reserved slot, got {reserved}"
    assert warnings >= 1, f"Expected at least 1 warning, got {warnings}"


@ldd_trajectory
def test_gh_repo_create_skip_flag(tmp_path: pathlib.Path, caplog) -> None:
    call_count = [0]

    def counting_gh_runner(cmd: list[str]) -> tuple[int, str, str]:
        call_count[0] += 1
        return 0, "ok", ""

    logger.info("[IMP:9][test][context] test_gh_repo_create_skip_flag — skip")
    overlay_repo, reserved, warnings = gh_repo_create(
        org="test-org",
        ctx="test-ctx",
        skip=True,
        context_dir=tmp_path,
        gh_runner=counting_gh_runner,
    )
    assert call_count[0] == 0, f"gh_runner called {call_count[0]} times despite skip=True"
    assert overlay_repo is None
    assert reserved is None
    assert warnings == 0, f"Expected 0 warnings with skip, got {warnings}"


# region FUNC_test_skeleton_repos_core_ssh_alias_url
# 🧪 TRAP[TEST] · 2026-09-01 · REGRESSION · DevPlan 024 TASK-2: skeleton repos.core = SSH-алиасный URL
# · Scenario: skeleton содержит git@github.com-overlay:<org>/<ctx>-overlay.git и НЕ содержит
#             https://github.com (VPS не клонирует приватный репо по unauthenticated HTTPS)
# · Last fail: 2026-09-01 — старый skeleton писал HTTPS-URL (анти-survivorship: падает на старом коде)
# · Remove if: форма repos.core изменена (другой SSH-алиас/канал доступа)
def test_skeleton_repos_core_ssh_alias_url(tmp_path: pathlib.Path, caplog) -> None:
    skeleton_path = tmp_path / "platform" / "node-configs" / "node" / "node.yaml"
    logger.info("[IMP:9][test][context] test_skeleton_repos_core_ssh_alias_url — SSH-alias URL")
    create_skeleton_node_yaml(skeleton_path, "alias-ctx", "test-org")
    content = skeleton_path.read_text()
    assert "core: git@github.com-overlay:test-org/alias-ctx-overlay.git" in content, content
    assert "https://github.com" not in content, "HTTPS URL forbidden in skeleton repos.core (DevPlan 024 D2)"


# endregion FUNC_test_skeleton_repos_core_ssh_alias_url


# region FUNC_test_provision_deploy_key_happy_path
# 🧪 TRAP[TEST] · 2026-09-01 · REGRESSION · DevPlan 024 TASK-2: happy-path provision_deploy_key
# · Scenario: fake keygen создаёт файлы → gh-вызовы содержат deploy-key add + --repo + title
#             и НЕ содержат --allow-write; приватный ключ 0600; путь вне context_dir/platform/
# · Last fail: 2026-09-01 — deploy-key add не вызывался (анти-survivorship: падает на старом коде)
# · Remove if: контракт provision_deploy_key изменён (read-only/путь/права)
def test_provision_deploy_key_happy_path(tmp_path: pathlib.Path, caplog) -> None:
    context_dir = tmp_path / "happy-ctx"
    context_dir.mkdir(parents=True)
    gh_cmds: list[list[str]] = []
    keygen_cmds: list[list[str]] = []

    def fake_gh(cmd: list[str]) -> tuple[int, str, str]:
        gh_cmds.append(cmd)
        return 0, "ok", ""

    def fake_keygen(cmd: list[str]) -> tuple[int, str, str]:
        keygen_cmds.append(cmd)
        key_file = pathlib.Path(cmd[cmd.index("-f") + 1])
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text("FAKE-PRIVATE-KEY\n", encoding="utf-8")
        key_file.with_name(key_file.name + ".pub").write_text("ssh-ed25519 FAKE\n", encoding="utf-8")
        return 0, "", ""

    logger.info("[IMP:9][test][context] test_provision_deploy_key_happy_path — read-only key provisioned")
    pub_path, warnings = provision_deploy_key(
        "test-org", "happy-ctx", context_dir, gh_runner=fake_gh, keygen_runner=fake_keygen
    )

    assert pub_path is not None, "Expected pub key path on success"
    assert warnings == 0, f"Expected 0 warnings on happy path, got {warnings}"
    # keygen: ed25519, пустая passphrase, comment overlay-deploy-<ctx>
    assert len(keygen_cmds) == 1, f"Expected exactly 1 ssh-keygen call, got {len(keygen_cmds)}"
    assert "-t" in keygen_cmds[0] and "ed25519" in keygen_cmds[0], "Keypair must be ed25519"
    assert "overlay-deploy-happy-ctx" in keygen_cmds[0], "Missing keygen comment overlay-deploy-<ctx>"
    # gh: deploy-key add, --repo, title, read-only
    add_cmds = [c for c in gh_cmds if c[:4] == ["gh", "repo", "deploy-key", "add"]]
    assert len(add_cmds) == 1, f"Expected exactly 1 deploy-key add, got {len(add_cmds)}: {gh_cmds}"
    assert "--repo" in add_cmds[0] and "test-org/happy-ctx-overlay" in add_cmds[0], "Wrong deploy-key repo"
    assert "--title" in add_cmds[0] and "vps-happy-ctx-readonly" in add_cmds[0], "Missing deploy-key title"
    assert "--allow-write" not in add_cmds[0], "Deploy key must be READ-ONLY (no --allow-write)"
    # Права: приватный 0600 / pub 0644
    priv = pathlib.Path(pub_path).with_name(pathlib.Path(pub_path).name.removesuffix(".pub"))
    assert priv.exists(), f"Private key not created: {priv}"
    assert stat.S_IMODE(priv.stat().st_mode) == 0o600, f"Private key must be 0600, got {oct(priv.stat().st_mode)}"
    assert stat.S_IMODE(pathlib.Path(pub_path).stat().st_mode) == 0o644, "Pub key must be 0644"
    # D3: ключ ВНЕ platform/-репо (каталог .secrets контекста, не platform/)
    assert ".secrets" in str(pub_path), f"Key must live in <ctx>/.secrets/: {pub_path}"
    assert "platform" not in pathlib.Path(pub_path).parts, f"Key must be OUTSIDE platform/ repo: {pub_path}"


# endregion FUNC_test_provision_deploy_key_happy_path


# region FUNC_test_deploy_key_skipped_when_gh_unavailable
# 🧪 TRAP[TEST] · 2026-09-01 · SCENARIO · DevPlan 024 TASK-2 (D2 graceful): gh недоступен
# · Scenario: gh rc≠0 → warnings≥1, keygen НЕ вызван; skeleton всё равно SSH-алиасный
#             (репо-URL не зависит от успешности deploy-key)
# · Last fail: N/A (preventive — graceful-ветка 024)
# · Remove if: graceful-семантика deploy key изменена (fail вместо warn)
def test_deploy_key_skipped_when_gh_unavailable(tmp_path: pathlib.Path, caplog) -> None:
    context_dir = tmp_path / "gh-less-ctx"
    context_dir.mkdir(parents=True)
    keygen_cmds: list[list[str]] = []

    def gh_broken(cmd: list[str]) -> tuple[int, str, str]:
        return -1, "", "gh: command not found"

    def spy_keygen(cmd: list[str]) -> tuple[int, str, str]:
        keygen_cmds.append(cmd)
        return 0, "", ""

    logger.info("[IMP:9][test][context] test_deploy_key_skipped_when_gh_unavailable — graceful skip")
    pub_path, warnings = provision_deploy_key(
        "test-org", "gh-less-ctx", context_dir, gh_runner=gh_broken, keygen_runner=spy_keygen
    )

    assert pub_path is None, f"Expected None pub path when gh unavailable, got {pub_path}"
    assert warnings >= 1, f"Expected ≥1 warning when gh unavailable, got {warnings}"
    assert not keygen_cmds, f"keygen must NOT be called without gh, got: {keygen_cmds}"
    # Skeleton URL не зависит от gh: SSH-алиасная форма канонична в любом случае (D2)
    skeleton_path = tmp_path / "skeleton" / "node-configs" / "node" / "node.yaml"
    create_skeleton_node_yaml(skeleton_path, "gh-less-ctx", "test-org")
    assert "core: git@github.com-overlay:test-org/gh-less-ctx-overlay.git" in skeleton_path.read_text()


# endregion FUNC_test_deploy_key_skipped_when_gh_unavailable


# region FUNC_test_deploy_key_add_duplicate_tolerated
# 🧪 TRAP[TEST] · 2026-09-01 · SCENARIO · DevPlan 024 TASK-2 (R4): дубликат deploy key = success
# · Scenario: gh rc=1, stderr «already exists» → success (warnings без роста), паттерн reuse
#             gh_repo_create
# · Last fail: N/A (preventive — tolerant-ветка повторного scaffold)
# · Remove if: семантика дубликатов deploy key изменена
def test_deploy_key_add_duplicate_tolerated(tmp_path: pathlib.Path, caplog) -> None:
    context_dir = tmp_path / "dup-ctx"
    context_dir.mkdir(parents=True)

    def fake_gh(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:4] == ["gh", "repo", "deploy-key", "add"]:
            return 1, "", "error: failed to add deploy key: Title has already been taken (already exists)"
        return 0, "ok", ""

    def fake_keygen(cmd: list[str]) -> tuple[int, str, str]:
        key_file = pathlib.Path(cmd[cmd.index("-f") + 1])
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text("FAKE-PRIVATE-KEY\n", encoding="utf-8")
        key_file.with_name(key_file.name + ".pub").write_text("ssh-ed25519 FAKE\n", encoding="utf-8")
        return 0, "", ""

    logger.info("[IMP:9][test][context] test_deploy_key_add_duplicate_tolerated — already exists = success")
    pub_path, warnings = provision_deploy_key(
        "test-org", "dup-ctx", context_dir, gh_runner=fake_gh, keygen_runner=fake_keygen
    )

    assert pub_path is not None, "Expected pub path despite duplicate (key exists, reuse)"
    assert warnings == 0, f"Duplicate deploy key must be tolerated (0 warnings), got {warnings}"


# endregion FUNC_test_deploy_key_add_duplicate_tolerated


# region FUNC_test_deploy_key_idempotent_existing_keypair
# 🧪 TRAP[TEST] · 2026-09-01 · SCENARIO · DevPlan 024 TASK-2: идемпотентность keypair
# · Scenario: приватный ключ существует → keygen НЕ вызван, существующий pub переиспользован
#             в deploy-key add
# · Last fail: N/A (preventive — идемпотентность повторного scaffold)
# · Remove if: идемпотентная семантика keypair изменена
def test_deploy_key_idempotent_existing_keypair(tmp_path: pathlib.Path, caplog) -> None:
    context_dir = tmp_path / "idem-ctx"
    secrets_dir = context_dir / ".secrets"
    secrets_dir.mkdir(parents=True)
    key_path = secrets_dir / "idem-ctx-overlay-deploy-key"
    pub_path = secrets_dir / "idem-ctx-overlay-deploy-key.pub"
    key_path.write_text("EXISTING-PRIVATE-KEY\n", encoding="utf-8")
    pub_path.write_text("ssh-ed25519 EXISTING\n", encoding="utf-8")
    gh_cmds: list[list[str]] = []
    keygen_cmds: list[list[str]] = []

    def fake_gh(cmd: list[str]) -> tuple[int, str, str]:
        gh_cmds.append(cmd)
        return 0, "ok", ""

    def spy_keygen(cmd: list[str]) -> tuple[int, str, str]:
        keygen_cmds.append(cmd)
        return 0, "", ""

    logger.info("[IMP:9][test][context] test_deploy_key_idempotent_existing_keypair — reuse existing key")
    returned_pub, warnings = provision_deploy_key(
        "test-org", "idem-ctx", context_dir, gh_runner=fake_gh, keygen_runner=spy_keygen
    )

    assert not keygen_cmds, f"keygen must NOT run for existing keypair, got: {keygen_cmds}"
    assert returned_pub == str(pub_path), f"Existing pub must be reused, got {returned_pub}"
    assert key_path.read_text() == "EXISTING-PRIVATE-KEY\n", "Existing private key must not be overwritten"
    add_cmds = [c for c in gh_cmds if c[:4] == ["gh", "repo", "deploy-key", "add"]]
    assert len(add_cmds) == 1 and str(pub_path) in add_cmds[0], f"Existing pub must be passed to add: {gh_cmds}"
    assert warnings == 0, f"Expected 0 warnings for idempotent reuse, got {warnings}"


# endregion FUNC_test_deploy_key_idempotent_existing_keypair


@ldd_trajectory
def test_gh_repo_create_single_overlay_repo(tmp_path: pathlib.Path, caplog) -> None:
    """DevPlan 022 TASK-2 + 024 TASK-2: РОВНО ОДИН gh repo create <ctx>-overlay;
    read-only deploy key добавлен; git push — на platform/.

    ## @purpose — AC2/D3: один приватный overlay-репо вместо двух сестринских;
    ##            deploy-key add (024) после подтверждения репо, до git init+push;
    ##            _git_init_and_push выполняется на context_dir/platform; return (repo, None, warnings).
    ## @io — ⇥ tmp_path, caplog → ⎋ None (asserts)
    """
    context_dir = tmp_path / "test-ctx"
    gh_cmds: list[list[str]] = []
    git_cmds: list[tuple[list[str], str]] = []
    keygen_cmds: list[list[str]] = []

    def fake_gh(cmd: list[str]) -> tuple[int, str, str]:
        gh_cmds.append(cmd)
        return 0, "ok", ""

    def fake_git(cmd: list[str], cwd: pathlib.Path) -> tuple[int, str, str]:
        git_cmds.append((cmd, str(cwd)))
        return 0, "ok", ""

    def fake_keygen(cmd: list[str]) -> tuple[int, str, str]:
        keygen_cmds.append(cmd)
        key_file = pathlib.Path(cmd[cmd.index("-f") + 1])
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text("FAKE-PRIVATE-KEY\n", encoding="utf-8")
        key_file.with_name(key_file.name + ".pub").write_text("ssh-ed25519 FAKE\n", encoding="utf-8")
        return 0, "", ""

    logger.info("[IMP:9][test][context] test_gh_repo_create_single_overlay_repo — one overlay repo + deploy key")
    overlay_repo, reserved, warnings = gh_repo_create(
        org="test-org",
        ctx="test-ctx",
        skip=False,
        context_dir=context_dir,
        gh_runner=fake_gh,
        git_runner=fake_git,
        keygen_runner=fake_keygen,
    )

    create_cmds = [c for c in gh_cmds if c[:3] == ["gh", "repo", "create"]]
    assert len(create_cmds) == 1, f"Expected exactly 1 'gh repo create', got {len(create_cmds)}: {create_cmds}"
    assert create_cmds[0][3] == "test-org/test-ctx-overlay", f"Wrong repo slug: {create_cmds[0][3]}"
    assert "--private" in create_cmds[0], "Overlay repo must be private"
    assert "Context overlay for 'test-ctx'" in create_cmds[0], "Missing overlay description"
    assert overlay_repo == "test-org/test-ctx-overlay"
    assert reserved is None, "Reserved slot (legacy hermes_agent_repo) must be None"
    assert warnings == 0, f"Expected 0 warnings, got {warnings}"

    # DevPlan 024: read-only deploy key добавлен после создания репо (024 TASK-2)
    add_cmds = [c for c in gh_cmds if c[:4] == ["gh", "repo", "deploy-key", "add"]]
    assert len(add_cmds) == 1, f"Expected exactly 1 'gh repo deploy-key add', got {len(add_cmds)}"
    assert "--repo" in add_cmds[0] and "test-org/test-ctx-overlay" in add_cmds[0], "Wrong deploy-key repo"
    assert "--allow-write" not in add_cmds[0], "Deploy key must be read-only (no --allow-write)"

    # Git init+push — на ВЕСЬ overlay (context_dir/platform), не на сестринские каталоги
    assert git_cmds, "Expected git init+push calls on platform/"
    git_dirs = {cwd for _, cwd in git_cmds}
    assert git_dirs == {str(context_dir / "platform")}, f"git must run only on platform/, got {git_dirs}"
    push_cmds = [c for c, _ in git_cmds if c[:2] == ["git", "push"]]
    assert len(push_cmds) == 1, f"Expected exactly 1 git push, got {len(push_cmds)}"


@ldd_trajectory
def test_register_in_platform_yaml_mocked(tmp_path: pathlib.Path, caplog) -> None:
    import yaml as _yaml

    platform_yaml = tmp_path / "platform-node.yaml"
    platform_yaml.write_text(_yaml.dump({"contexts": []}, default_flow_style=False))

    call_args = []

    def mock_register(yaml_path, name, desc="", node_cfg_repo="", hermes_agent_repo=""):
        call_args.append({
            "yaml_path": yaml_path,
            "name": name,
            "desc": desc,
            "node_cfg_repo": node_cfg_repo,
            "hermes_agent_repo": hermes_agent_repo,
        })
        return "OK"

    logger.info("[IMP:9][test][context] test_register_in_platform_yaml_mocked")
    rc = register_in_platform_yaml(
        yaml_path=str(platform_yaml),
        ctx_name="test-ctx",
        ctx_desc="Test context",
        node_cfg_repo="org/node-configs",
        hermes_agent_repo="org/hermes-agent",
        register_fn=mock_register,  # DI (167 D3) — fake register вместо monkeypatch context_registry
    )
    assert rc == 0, f"Expected return code 0, got {rc}"
    assert len(call_args) == 1, f"Expected register_context called once, called {len(call_args)}"
    assert call_args[0]["name"] == "test-ctx"
    assert call_args[0]["desc"] == "Test context"


@ldd_trajectory
def test_register_in_platform_yaml(tmp_path: pathlib.Path, caplog) -> None:
    """Real registration path (no context_registry mock): YAML updated + idempotent re-register.

    # 🧪 TRAP[TEST] · 2026-07-30 · — · Regression: test_register_in_platform_yaml · Scenario: fresh node.yaml → context registered · Last fail: N/A · Remove if: initializer API changes
    ## @purpose — Real-path coverage of register_in_platform_yaml(): writes contexts[] entries
    ##            into the platform node.yaml and is idempotent (re-register keeps 1 entry).
    ##            Persisted from tests/unit/ during dedup (import file mismatch fix).
    ## @io — ⇥ tmp_path, caplog → ⎋ None (asserts)
    ## @complexity — O(1) — two register calls + YAML parse
    """
    platform_yaml = tmp_path / "platform" / "node.yaml"
    platform_yaml.parent.mkdir(parents=True)
    platform_yaml.write_text(
        yaml.dump(
            {"node": {"name": "test-node", "host": "127.0.0.1"}, "contexts": [], "modules": [], "projects": []},
            default_flow_style=False,
            sort_keys=False,
        )
    )

    logger.info("[IMP:9][test][context] test_register_in_platform_yaml — real registration path")
    rc = register_in_platform_yaml(
        yaml_path=str(platform_yaml),
        ctx_name="test-context",
        ctx_desc="Test context for unit tests",
        node_cfg_repo="test-org/test-context-node-configs",
        hermes_agent_repo="test-org/test-context-hermes-agent",
    )
    assert rc == 0, f"Expected return code 0, got {rc}"

    data = yaml.safe_load(platform_yaml.read_text())
    contexts = data.get("contexts", [])
    assert len(contexts) == 1, f"Expected 1 context after register, got {len(contexts)}"
    ctx = contexts[0]
    assert ctx["name"] == "test-context"
    assert ctx["description"] == "Test context for unit tests"
    assert ctx["node_configs_repo"] == "test-org/test-context-node-configs"
    assert ctx["hermes_agent_repo"] == "test-org/test-context-hermes-agent"

    # Idempotent: register again → still 1 entry
    rc2 = register_in_platform_yaml(yaml_path=str(platform_yaml), ctx_name="test-context")
    assert rc2 == 0, f"Expected idempotent register rc 0, got {rc2}"
    data2 = yaml.safe_load(platform_yaml.read_text())
    assert len(data2.get("contexts", [])) == 1, "Re-register must not duplicate the context entry"


@ldd_trajectory
def test_report_summary_nested_paths(tmp_path: pathlib.Path, caplog, capsys) -> None:
    """DevPlan 022 TASK-2: summary печатает вложенные platform/...-пути.

    ## @purpose — AC2: печатаемые пути отражают канонический nested layout.
    ## @io — ⇥ tmp_path, caplog, capsys → ⎋ None (asserts)
    """
    context_dir = tmp_path / "test-ctx"
    logger.info("[IMP:9][test][context] test_report_summary_nested_paths")
    report_summary(
        ctx_name="test-ctx",
        context_dir=context_dir,
        warnings=0,
        platform_yaml="/tmp/platform-node.yaml",
        node_cfg_repo="test-org/test-ctx-overlay",
        hermes_agent_repo=None,
        node="tronyx-vps",
    )
    out = capsys.readouterr().out
    assert f"{context_dir}/platform/" in out, "Missing platform/ path in summary"
    assert f"{context_dir}/platform/node-configs/" in out, "Missing platform/node-configs/ path in summary"
    assert f"{context_dir}/platform/modules/hermes-agent/" in out, (
        "Missing platform/modules/hermes-agent/ path in summary"
    )
    assert f"{context_dir}/platform/projects/" in out, "Missing platform/projects/ path in summary"
    assert f"{context_dir}/platform/node-configs/tronyx-vps/node.yaml (skeleton)" in out, (
        "Missing nested skeleton path in summary"
    )
    assert "test-org/test-ctx-overlay" in out, "Missing overlay repo in summary"
    # Negative (R5): сестринские legacy-пути не печатаются
    assert f"{context_dir}/hermes-agent/" not in out.replace(f"{context_dir}/platform/modules/hermes-agent/", ""), (
        "Legacy sibling hermes-agent/ path must not be printed"
    )
    assert f"{context_dir}/node-configs/" not in out.replace(f"{context_dir}/platform/node-configs/", "").replace(
        f"{context_dir}/platform/node-configs/tronyx-vps/node.yaml (skeleton)", ""
    ), "Legacy sibling node-configs/ path must not be printed"


@ldd_trajectory
def test_main_resolves_platform_node_yaml_nested(tmp_path: pathlib.Path, caplog) -> None:
    """DevPlan 022 TASK-2: main() резолвит platform node.yaml по новому glob.

    ## @purpose — AC2: search_dirs находит */platform/node-configs/<node>/node.yaml;
    ##            регистрация идёт в СУЩЕСТВУЮЩИЙ overlay node.yaml (свежий skeleton
    ##            не затирает — TRAP[DECISION] preference overlay > fixture > skeleton).
    ##            Падал бы на старом коде: старый glob */node-configs/<node>/node.yaml
    ##            не видит вложенный путь → «Could not resolve platform node.yaml» (rc 1).
    ## @io — ⇥ tmp_path, caplog → ⎋ None (asserts)
    """
    projects_dir = tmp_path / "projects"
    existing_yaml = projects_dir / "ctx-a" / "platform" / "node-configs" / "test-node" / "node.yaml"
    existing_yaml.parent.mkdir(parents=True)
    existing_yaml.write_text(
        yaml.dump(
            {"node": {"name": "test-node", "host": "127.0.0.1"}, "contexts": [], "modules": [], "projects": []},
            default_flow_style=False,
            sort_keys=False,
        )
    )

    logger.info("[IMP:9][test][context] test_main_resolves_platform_node_yaml_nested — full main() flow")
    rc = main([
        "new-ctx",
        "--projects-dir",
        str(projects_dir),
        "--node",
        "test-node",
        "--org",
        "test-org",
        "--skip-gh-repo",
    ])
    assert rc == 0, f"Expected main() rc 0, got {rc}"

    # Skeleton создан по вложенному пути с repos.core = overlay URL
    skeleton = projects_dir / "new-ctx" / "platform" / "node-configs" / "test-node" / "node.yaml"
    assert skeleton.exists(), f"Skeleton not created at nested path: {skeleton}"
    skeleton_content = skeleton.read_text()
    assert "core: git@github.com-overlay:test-org/new-ctx-overlay.git" in skeleton_content, (
        "Skeleton repos.core must point to overlay repo via SSH alias (DevPlan 024)"
    )

    # Регистрация — в СУЩЕСТВУЮЩИЙ overlay node.yaml (не в свежий skeleton)
    data = yaml.safe_load(existing_yaml.read_text())
    contexts = data.get("contexts", [])
    names = [c.get("name") for c in contexts]
    assert "new-ctx" in names, f"new-ctx must be registered in existing overlay node.yaml, got {names}"
    entry = next(c for c in contexts if c.get("name") == "new-ctx")
    # NodeYaml.add_context контракт: пустая строка → ключ не пишется (None при чтении)
    assert not entry.get("node_configs_repo"), "Expected no node_configs_repo with --skip-gh-repo"

    # Свежий skeleton остался нетронутым (регистрация не перезаписала его contexts[])
    skeleton_data = yaml.safe_load(skeleton_content)
    assert [c.get("name") for c in skeleton_data.get("contexts", [])] == ["new-ctx"], (
        "Skeleton contexts[] must keep only its own context"
    )


# GUARD-PRESERVE (168): единственное покрытие validate_name (fail-fast ConfigValidationError exit 4 на невалидном имени) — negative-ветка без позитивной пары в файле
@ldd_trajectory
def test_validate_name_invalid(caplog) -> None:
    logger.info("[IMP:9][test][context] test_validate_name_invalid")
    from core.internal.shared.exceptions import ConfigValidationError

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_name("bad name!@#")
    assert exc_info.value.exit_code == 4, f"Expected exit code 4 for invalid name, got {exc_info.value.exit_code}"
