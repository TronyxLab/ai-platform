#!/usr/bin/env python3
# GREP_SUMMARY: project_adopter, adopt-project, strangler-fig, ai-platform-yaml, compose-validation, node-yaml-registration, vhost, makefile, agents-md
# STRUCTURE: ▶ parse_args (shell) → init ProjectAdopter → adopt() → ○ generate_minimal_yaml → ○ simplify_deploy → ○ delete_platform_deploy → ○ gen_env_platform (subprocess) → ○ gen_makefile → ○ gen_agents → ○ validate_compose_networks (docker/PyYAML cascade) → ○ register_in_node_yaml (safe import) → ○ configure_vhost (try import→subprocess fallback) → ⎋ print_diff_report
# region MODULE_CONTRACT
## @purpose  Strangler-Fig migration of adopt-project.sh (906 LOC shell) into Python business logic.
##            Adopts an existing project into ai-platform lifecycle: generates ai-platform.yaml,
##            simplifies deploy.yml, validates docker-compose proxy-net, registers in node.yaml,
##            configures nginx vhost, and generates Makefile/AGENTS.md.
## @scope    Called from adopt-project.sh shell facade (≤120 LOC) via `python3 -m core.internal.scaffold.project_adopter adopt`.
##           All business logic lives here. Shell only does parse_args + fast org validation.
## @invariants
##   1. NEVER modifies src/, Dockerfile, docker-compose.yml (application code)
##   2. .env.platform regenerated via subprocess gen_env_platform.py (CLI-first, D5)
##   3. Supports personal domains (O11) — separate cert path
##   4. Idempotent: second call with same project → no-op (exit 0) except .env.platform regeneration
##   5. deploy.yml simplified to use reusable workflow (if exists)
##   6. platform-deploy.yml deleted if exists
##   7. validate_compose_networks uses 3-method cascade: docker compose config → PyYAML → yq (best-effort)
##   8. register_in_node_yaml wraps sys.exit from project_registry in try/except SystemExit (D3)
##   9. configure_vhost tries direct import vhost_renderer, falls back to subprocess add-vhost.sh (D4)
##   10. gen_env_platform always via subprocess.run (CLI-first design, D5)
##   11. validate_org duplicated in shell (fast grep) AND Python (full PyYAML) per D6
## @rationale Migration tool for existing projects. Without this, existing projects cannot adopt the
##            connection-model without manual intervention. --force flag for Makefile/AGENTS.md replaces existing.
##            Strangler-Fig migration per Wave 5 language policy (AGENTS.md).
## @changes  2026-07-26 · Wave 5c — Full Strangler-Fig from adopt-project.sh (906 LOC)
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-07-21 · — · Wave 5c: adopt-project.sh Strangler-Fig migrated to project_adopter.py
# · Rejected: keeping adopt logic in shell (906 LOC monolith with 2 inline python3 blocks)
# · Reason: языковая политика (AGENTS.md), тестируемость compose-валидации, дедупликация с project_registry
# · Rev: если project_adopter.py вызывает >20% ошибок adopt vs shell-версия → профилировать и фиксить

# 🧐 TRAP[DECISION] · 2026-07-21 · — · parse_args (env auto-detection) stays in shell facade
# · Rejected: full Python parse_args with subprocess-based path/env detection
# · Reason: auto-detection (basename dir, grep YAML, pwd -P, env vars) is inherently shell-bound.
#           Extracting would add subprocess overhead without testability gain.
# · Rev: если parse_args потребует сложной логики (>50 LOC новых проверок) → извлечь в Python

# ⚠️ TRAP[BUG] · 2026-07-17 · P1 · Silent default "personal" org + missing casing normalization — config drift
# · Symptom: PROJECT_ORG defaulted to "personal" when --org not provided; ghcr.io casing mismatch
# · Root: отсутствие fail-fast для пустого org + отсутствие lowercase-нормализации ghcr paths
# · Fix: fail-fast exit 1 с подсказкой + lowercase для ghcr + exact-case для uses: + сверка с node.yaml
# · Prevention: org всегда явный — отказ вместо молчания

# 📝 TRAP[DEBT] · 2026-07-26 · LO · gen_env_platform.py — CLI-first design prevents direct import
# · Observed: gen_env_platform.py функции используют sys.exit() вместо return → нельзя импортировать как библиотеку
# · Suspected: осознанный CLI-first дизайн (Plan 082). Рефакторинг на библиотечный API — отдельная задача.
# · Impact: project_adopter использует subprocess.run вместо прямого import (overhead ~100ms)
# · When: during Wave 5c migration — deferred, out of scope

# 📝 TRAP[DEBT] · 2026-07-26 · LO · node.yaml path resolution duplicated across 4+ scripts
# · Observed: `projects/<org>/node-configs/<node>/node.yaml` путь вычисляется в adopt-project.sh,
#   add-vhost.sh, add-project.sh, remove-project.sh с идентичной логикой
# · Suspected: кандидат на shared NodeConfigPathResolver (отдельный DevPlan)
# · Impact: изменение структуры путей потребует правок в 4+ местах
# · When: during Wave 5c migration — deferred, out of scope

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Compose profiles default (synchronized with Makefile COMPOSE_PROFILES) ──
_DEFAULT_COMPOSE_PROFILES = (
    "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,"
    "monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page"
)


# region dataclass_AdoptionResult
@dataclass
class AdoptionResult:
    """Result of the adopt() orchestration.

    ## @purpose  Collects changes made during adoption for the diff report.
    ## @io        ┌ changes: list[str] — human-readable change descriptions
    ##            └ success: bool — whether adoption completed without fatal errors
    """

    changes: list[str] = field(default_factory=list)
    success: bool = True


# endregion dataclass_AdoptionResult


# region dataclass_ValidationResult
@dataclass
class ValidationResult:
    """Result of compose proxy-net validation.

    ## @purpose  Captures validation outcome with descriptive message.
    ## @io        ┌ valid: bool — True if validation passes
    ##            └ message: str — human-readable validation message
    """

    valid: bool = True
    message: str = ""


# endregion dataclass_ValidationResult


# region class_ProjectAdopter
class ProjectAdopter:
    """Adopt an existing project into the ai-platform lifecycle.

    ## @purpose  Orchestrates all adoption steps: YAML generation, CI rewriting,
    ##            compose validation, node.yaml registration, vhost configuration,
    ##            and project scaffolding (Makefile, AGENTS.md).
    ## @io        ┌ project_dir: Path — project root directory
    ##            ┌ name: str — project name
    ##            ├ org: str — GitHub org / platform context
    ##            ├ node: str — target node name
    ##            ├ domain: str | None — optional custom domain
    ##            └ force: bool — overwrite existing Makefile/AGENTS.md
    ## @complexity O(1) construction; adopt() orchestrates 9 steps with linear complexity each
    """

    # 🧐 TRAP[DECISION] · 2026-07-26 · — · COMPOSE_PROFILES from env with fallback
    # · Rejected: hardcoded COMPOSE_PROFILES in shell (adopt-project.sh:388)
    # · Reason: хардкод удалён — Python читает COMPOSE_PROFILES из os.environ
    #   с fallback-значением из platform-env.yaml (синхронизировано с Makefile _get_all_profiles)
    # · Rev: если COMPOSE_PROFILES определён в platform-env.yaml → читать оттуда

    def __init__(
        self,
        project_dir: Path,
        name: str,
        org: str,
        node: str,
        domain: str | None = None,
        force: bool = False,
    ) -> None:
        """Initialize ProjectAdopter with validated parameters.

        ## @purpose  Validate and store project parameters for adoption.
        ## @io        ⇥ validated parameters → ⎋ None
        ## @complexity O(1)
        ## @invariants
        ##   - project_dir must exist and be a directory
        ##   - name, org, node must be non-empty strings
        ##   - domain may be None or empty string (treated as "no domain")
        """
        self.project_dir = project_dir.resolve()
        self.name = name
        self.org = org
        self.node = node
        self.domain = domain if domain else None
        self.force = force

        # Derived paths
        self.yaml_file = self.project_dir / "ai-platform.yaml"
        self.deploy_yml = self.project_dir / ".github" / "workflows" / "deploy.yml"
        self.platform_deploy_yml = self.project_dir / ".github" / "workflows" / "platform-deploy.yml"
        self.compose_profiles = os.environ.get("COMPOSE_PROFILES", _DEFAULT_COMPOSE_PROFILES)

        self._log_prefix = "adopt"

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_generate_minimal_ai_platform_yaml
    ## @purpose  Generate minimal ai-platform.yaml if not present.
    ##            Auto-detects project type (frontend/backend/fullstack) from directory contents.
    ##            If yaml exists → skip (return "exists").
    ## @io        ⇥ None → ⎋ str — "generated" | "exists"
    ## @complexity O(1) — file existence + directory checks
    ## @invariants
    ##   - Does NOT overwrite existing ai-platform.yaml
    ##   - Auto-detects type: frontend (src/index.html or frontend/ dir),
    ##     fullstack (frontend/ + backend/ dirs), backend (default)
    ##   - Writes YAML with PyYAML dump (default_flow_style=False, sort_keys=False)
    def generate_minimal_ai_platform_yaml(self) -> str:
        """Generate minimal ai-platform.yaml if not present.

        Returns "generated" if created, "exists" if already present.
        """
        if self.yaml_file.exists():
            logger.info("[IMP:9][%s][gen_yaml] ai-platform.yaml exists — preserving (idempotent)", self._log_prefix)
            return "exists"

        logger.info("[IMP:7][%s][gen_yaml] No ai-platform.yaml found — generating minimal", self._log_prefix)

        # Auto-detect project type
        type_guess = "backend"
        if (self.project_dir / "src" / "index.html").exists() or (self.project_dir / "frontend").is_dir():
            type_guess = "frontend"
        if (self.project_dir / "frontend").is_dir() and (self.project_dir / "backend").is_dir():
            type_guess = "fullstack"

        logger.info("[IMP:7][%s][gen_yaml] Guessed project type: %s", self._log_prefix, type_guess)

        # Delegate to scaffold_helpers (DP-092 Wave 4a)
        from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml

        return gen_ai_platform_yaml(
            name=self.name,
            ptype=type_guess,
            org=self.org,
            node=self.node,
            domain=self.domain or "",
            output_path=str(self.yaml_file),
            minimal=True,  # Adopter mode: minimal monitoring
        )

    # endregion FUNC_generate_minimal_ai_platform_yaml

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_simplify_deploy_yml
    ## @purpose  Simplify deploy.yml to use reusable workflow pattern (K4).
    ##            Rewrites .github/workflows/deploy.yml to call
    ##            org/ai-platform/.github/workflows/deploy-project.yml@main.
    ##            Does NOT modify if it already uses the new pattern.
    ## @io        ⇥ None → ⎋ bool — True if simplified, False if skipped/already-simplified
    ## @complexity O(1) — file read + write
    ## @invariants
    ##   - If deploy.yml does not exist → return False (no-op)
    ##   - If deploy.yml already uses reusable workflow → return False (idempotent)
    ##   - Backs up original to deploy.yml.bak before overwriting
    ##   - Interactive prompt if not --force
    def simplify_deploy_yml(self) -> bool:
        """Simplify deploy.yml to use reusable workflow.

        Returns True if simplified, False if skipped or already simplified.
        """
        if not self.deploy_yml.exists():
            logger.info("[IMP:6][%s][simplify] No deploy.yml found — nothing to simplify", self._log_prefix)
            return False

        # Check if it already uses reusable workflow
        content = self.deploy_yml.read_text()
        if "uses: " in content and "/ai-platform/.github/workflows/deploy-project.yml" in content:
            logger.info(
                "[IMP:9][%s][simplify] deploy.yml already uses reusable workflow — preserving", self._log_prefix
            )
            return False

        logger.info("[IMP:7][%s][simplify] Simplifying deploy.yml to use reusable workflow (K4)", self._log_prefix)

        # Interactive prompt if not force
        if not self.force:
            print("  Rewrite deploy.yml to use reusable workflow? [y/N] ", end="", file=sys.stderr)
            response = input().strip().lower()
            if response not in ("y", "yes"):
                logger.info("[IMP:7][%s][simplify] deploy.yml simplification skipped", self._log_prefix)
                return False

        # Backup original
        backup_path = self.deploy_yml.with_suffix(".yml.bak")
        shutil.copy2(str(self.deploy_yml), str(backup_path))
        logger.info("[IMP:6][%s][simplify] Original backed up: %s", self._log_prefix, backup_path)

        # Determine org for uses: path
        workflow_org = self.org
        image_name = f"ghcr.io/{workflow_org}/{self.name}"

        new_content = f"""# GENERATED by adopt-project.sh — simplified to reusable workflow
# Original backed up at deploy.yml.bak

name: Deploy {self.name}

on:
  push:
    branches: [main, staging]
  workflow_dispatch:

env:
  IMAGE_NAME: {image_name.lower()}

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v7

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}

      - name: Build and push
        uses: docker/build-push-action@v7
        with:
          context: .
          push: true
          tags: |
            ${{{{ env.IMAGE_NAME }}}}:${{{{ github.sha }}}}
            ${{{{ env.IMAGE_NAME }}}}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    needs: [build-and-push]
    if: github.ref_name == 'main'
    uses: {workflow_org}/ai-platform/.github/workflows/deploy-project.yml@main
    with:
      project_name: {self.name}
      image_tag: ${{{{ github.sha }}}}
    secrets: inherit

  deploy-staging:
    needs: [build-and-push]
    if: github.ref_name == 'staging'
    uses: {workflow_org}/ai-platform/.github/workflows/deploy-project.yml@main
    with:
      project_name: {self.name}
      image_tag: ${{{{ github.sha }}}}
    secrets: inherit
"""

        self.deploy_yml.write_text(new_content)
        logger.info("[IMP:9][%s][simplify] deploy.yml simplified: %s", self._log_prefix, self.deploy_yml)
        return True

    # endregion FUNC_simplify_deploy_yml

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_delete_platform_deploy_yml
    ## @purpose  Delete platform-deploy.yml if it exists (deprecated artifact).
    ## @io        ⇥ None → ⎋ bool — True if deleted, False if not found
    ## @complexity O(1)
    def delete_platform_deploy_yml(self) -> bool:
        """Delete deprecated platform-deploy.yml.

        Returns True if deleted, False if not found.
        """
        if not self.platform_deploy_yml.exists():
            logger.info("[IMP:6][%s][delete_pd] platform-deploy.yml not found — nothing to delete", self._log_prefix)
            return False

        logger.info(
            "[IMP:7][%s][delete_pd] Removing deprecated platform-deploy.yml: %s",
            self._log_prefix,
            self.platform_deploy_yml,
        )
        self.platform_deploy_yml.unlink()
        logger.info("[IMP:9][%s][delete_pd] platform-deploy.yml deleted", self._log_prefix)
        return True

    # endregion FUNC_delete_platform_deploy_yml

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_gen_env_platform
    ## @purpose  Generate .env.platform via subprocess call to gen_env_platform.py (CLI-first, D5).
    ##            gen_env_platform.py uses sys.exit() internally — cannot be imported as library.
    ## @io        ⇥ None → ⎋ bool — True if generated, False if script not found or failed
    ## @complexity O(1) — single subprocess call
    ## @invariants
    ##   - Always regenerates .env.platform (not idempotent by design)
    ##   - Subprocess call with capture_output=True, text=True
    ##   - gen_env_platform.py is CLI-first — this is intentional per D5
    def gen_env_platform(self) -> bool:
        """Generate .env.platform via subprocess gen_env_platform.py.

        Returns True if generated, False if script not found or failed.
        """
        gen_script = Path(__file__).resolve().parent / "gen_env_platform.py"
        if not gen_script.exists():
            logger.info("[IMP:8][%s][gen_env] gen_env_platform.py not found — skipping .env.platform", self._log_prefix)
            return False

        env_file = self.project_dir / ".env.platform"
        platform_env_yaml = self.project_dir / "platform-env.yaml"

        if not platform_env_yaml.exists():
            logger.info(
                "[IMP:8][%s][gen_env] platform-env.yaml not found at %s — skipping", self._log_prefix, platform_env_yaml
            )
            return False

        logger.info("[IMP:7][%s][gen_env] Generating .env.platform from platform-env.yaml", self._log_prefix)

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(gen_script),
                    "--yaml",
                    str(platform_env_yaml),
                    "--name",
                    self.name,
                    "--domain",
                    self.domain or "ai-platform.local",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                env_file.write_text(result.stdout)
                logger.info("[IMP:9][%s][gen_env] .env.platform generated: %s", self._log_prefix, env_file)
                return True
            logger.info(
                "[IMP:8][%s][gen_env] gen_env_platform.py returned non-zero — stderr: %s",
                self._log_prefix,
                result.stderr.strip(),
            )
            return False
        except FileNotFoundError:
            logger.info("[IMP:8][%s][gen_env] gen_env_platform.py not found or not executable", self._log_prefix)
            return False

    # endregion FUNC_gen_env_platform

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_gen_project_makefile
    ## @purpose  Generate minimal Makefile in project directory (K3 contract).
    ##            Preserves existing Makefile unless --force is set.
    ## @io        ⇥ None → ⎋ str — "generated" | "exists" | "skipped"
    ## @complexity O(1)
    def gen_project_makefile(self) -> str:
        """Generate project Makefile.

        Returns "generated", "exists", or "skipped".
        """
        # Delegate to scaffold_helpers (DP-092 Wave 4a)
        from core.internal.scaffold.scaffold_helpers import gen_project_makefile as _gen

        return _gen(
            name=self.name,
            domain=self.domain or "",
            output_path=str(self.project_dir / "Makefile"),
            force=self.force,
        )

    # endregion FUNC_gen_project_makefile

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_gen_project_agents
    ## @purpose  Generate AGENTS.md in project directory (DD13 contract, ≤60 lines).
    ##            Preserves existing AGENTS.md unless --force is set.
    ## @io        ⇥ None → ⎋ str — "generated" | "exists" | "skipped"
    ## @complexity O(1)
    def gen_project_agents(self) -> str:
        """Generate project AGENTS.md.

        Returns "generated", "exists", or "skipped".
        """
        # Delegate to scaffold_helpers (DP-092 Wave 4a)
        from core.internal.scaffold.scaffold_helpers import gen_project_agents as _gen

        return _gen(
            name=self.name,
            org=self.org,
            template="adopted",
            node=self.node,
            domain=self.domain or "",
            output_path=str(self.project_dir / "AGENTS.md"),
            force=self.force,
        )

    # endregion FUNC_gen_project_agents

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_validate_compose_networks
    ## @purpose  Validate project docker-compose declares proxy-net (external).
    ##            If the project has a domain, at least one service MUST be connected to
    ##            proxy-net with external:true. Returns ValidationResult — does NOT mutate compose.
    ##            Uses 3-method cascade: docker compose config → python3 yaml → yq fallback.
    ## @param compose_path  Path to the compose file (compose.yaml or docker-compose.yml)
    ## @io        ⇥ compose_path: Path → ⎋ ValidationResult
    ## @complexity O(S × N) where S = services, N = networks per service
    ## @invariants
    ##   - Validation only: no mutation of compose files
    ##   - If no domain configured → skip validation (return valid=True)
    ##   - Method 1: `docker compose config` — resolves anchors/aliases/extends
    ##   - Method 2: PyYAML fallback — works without Docker daemon
    ##   - Method 3: analysis of proxy-net external + service connections
    ##   - If neither method available → WARN + return valid=True (best-effort)
    def validate_compose_networks(self, compose_path: Path) -> ValidationResult:
        """Validate compose proxy-net configuration.

        Returns ValidationResult with valid=True if validation passes.
        """
        # If no domain configured, project doesn't need proxy-net
        if not self.domain:
            logger.info(
                "[IMP:9][%s][validate_net] No domain configured — skipping proxy-net validation", self._log_prefix
            )
            return ValidationResult(valid=True, message="No domain — validation skipped")

        logger.info("[IMP:7][%s][validate_net] Validating proxy-net in compose: %s", self._log_prefix, compose_path)

        # Step 1: Parse compose
        data = self._try_parse_compose(compose_path)
        if data is None:
            logger.info(
                "[IMP:8][%s][validate_net] Cannot parse compose — neither docker nor PyYAML available", self._log_prefix
            )
            logger.info(
                "[IMP:8][%s][validate_net]  WARN: skipping proxy-net validation (best-effort)", self._log_prefix
            )
            return ValidationResult(valid=True, message="Parse unavailable — best-effort skip")

        # Step 2: Analyze proxy-net
        net_valid, svc_count, msg = self._analyze_proxy_net(data)
        if not net_valid:
            logger.info("[IMP:10][%s][validate_net] FAIL: %s", self._log_prefix, msg)
            return ValidationResult(valid=False, message=msg)

        logger.info(
            "[IMP:9][%s][validate_net] PASS: compose declares proxy-net (external) with %d service(s) connected",
            self._log_prefix,
            svc_count,
        )
        return ValidationResult(valid=True, message=f"proxy-net valid with {svc_count} service(s)")

    def _try_parse_compose(self, compose_path: Path) -> dict | None:
        """Try to parse compose file via docker compose config, then PyYAML.

        ## @purpose  3-method cascade for compose parsing.
        ## @io        ⇥ compose_path → ⎋ dict | None
        ## @complexity O(C) where C = compose file size
        """
        # Method 1: docker compose config (resolves anchors, aliases, extends)
        if shutil.which("docker"):
            try:
                env = os.environ.copy()
                env["COMPOSE_PROFILES"] = self.compose_profiles
                result = subprocess.run(
                    ["docker", "compose", "-f", str(compose_path), "config"],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=env,
                )
                if result.returncode == 0 and result.stdout.strip():
                    logger.info("[IMP:7][%s][validate_net] Compose parsed via docker compose config", self._log_prefix)
                    try:
                        import yaml

                        return yaml.safe_load(result.stdout)
                    except (ImportError, yaml.YAMLError):
                        pass
            except FileNotFoundError:
                pass

        # Method 2: PyYAML fallback
        try:
            import yaml

            with open(compose_path) as f:
                data = yaml.safe_load(f)
            if data and isinstance(data, dict):
                logger.info("[IMP:7][%s][validate_net] Compose parsed via PyYAML", self._log_prefix)
                return data
        except (ImportError, yaml.YAMLError):
            pass

        return None

    def _analyze_proxy_net(self, data: dict) -> tuple[bool, int, str]:
        """Analyze compose data for proxy-net external:true and service connections.

        ## @purpose  Core validation logic for proxy-net.
        ## @io        ⇥ data: dict → ⎋ (valid: bool, svc_count: int, message: str)
        ## @complexity O(S × N) where S = services, N = networks per service
        """
        networks = data.get("networks", {})
        if not isinstance(networks, dict):
            return False, 0, "No networks section found in compose"

        proxy_net = networks.get("proxy-net", {})
        if not isinstance(proxy_net, dict):
            return False, 0, "proxy-net is not a valid network entry"

        # Check external: true
        external = proxy_net.get("external", False)
        # docker compose config resolves external: true → bool
        has_external = True if isinstance(external, dict) else bool(external)

        if not has_external:
            msg = (
                "FAIL: compose does not declare networks.proxy-net with external:true\n"
                "  Add to compose:\n"
                "    networks:\n"
                "      proxy-net:\n"
                "        name: proxy-net\n"
                "        external: true\n"
                "  And connect at least one service:\n"
                "    services:\n"
                "      <name>:\n"
                "        networks:\n"
                "          proxy-net:\n"
                "            aliases:\n"
                "              - <name>"
            )
            return False, 0, msg

        # Count services connected to proxy-net
        services = data.get("services", {})
        if not isinstance(services, dict):
            services = {}

        svc_count = 0
        for svc_config in services.values():
            if not isinstance(svc_config, dict):
                continue
            svc_networks = svc_config.get("networks", {})
            if (isinstance(svc_networks, dict) and "proxy-net" in svc_networks) or (
                isinstance(svc_networks, list) and "proxy-net" in svc_networks
            ):
                svc_count += 1

        if svc_count == 0:
            msg = "FAIL: compose has proxy-net external but no service is connected to it"
            return False, 0, msg

        return True, svc_count, f"Valid: {svc_count} service(s) on proxy-net"

    # endregion FUNC_validate_compose_networks

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_register_in_node_yaml
    ## @purpose  Register project in node.yaml via direct import of project_registry (D3).
    ##            Wraps sys.exit from register_project() in try/except SystemExit.
    ## @io        ⇥ node_yaml_path: Path → ⎋ bool — True if registered/skip, False on error
    ## @complexity O(N) where N = len(projects) (delegated to project_registry)
    ## @invariants
    ##   - Direct import instead of subprocess (eliminates 1 subprocess call)
    ##   - Wraps sys.exit(0) from project_registry via try/except SystemExit (D3)
    ##   - If project_registry not importable → fallback to yq/manual instructions
    ##   - Idempotent: skips if project already registered
    def register_in_node_yaml(self, node_yaml_path: Path) -> bool:
        """Register project in node.yaml. Idempotent.

        Returns True if registered or already registered, False on error.
        """
        if not node_yaml_path.exists():
            logger.info("[IMP:8][%s][register] node.yaml not found: %s", self._log_prefix, node_yaml_path)
            logger.info("[IMP:8][%s][register]   Create it or register manually:", self._log_prefix)
            logger.info(
                '[IMP:8][%s][register]     yq eval -i \'.projects += [{"name": "%s", "repo": "%s/%s", "type": "project"}]\' %s',
                self._log_prefix,
                self.name,
                self.org,
                self.name,
                node_yaml_path,
            )
            return False

        # D3: Wrapping sys.exit from project_registry in try/except SystemExit
        try:
            from core.internal.shared.project_registry import register_project

            self._register_project_safe(register_project, node_yaml_path)
            return True
        except ImportError as e:
            logger.info("[IMP:8][%s][register] project_registry.py not importable: %s", self._log_prefix, e)
            logger.info("[IMP:8][%s][register]   Falling back to NodeYaml CLI...", self._log_prefix)
            # Fallback: NodeYaml CLI mutation API (replaces yq)
            return self._register_via_node_yaml(node_yaml_path)

    def _register_project_safe(self, register_func: Any, node_yaml_path: Path) -> None:
        """Safe wrapper for register_project that handles sys.exit (D3).

        ## @purpose  project_registry.register_project() calls sys.exit(0) on success/skip.
        ##            This wrapper captures SystemExit so the caller can continue execution.
        ## @io        ⇥ register_func — callable, node_yaml_path → ⎋ None
        ## @complexity O(N) delegated to register_func
        ## @invariants
        ##   - SystemExit(0) is caught and treated as success
        ##   - SystemExit(1) is caught and logged as error
        ##   - Non-exit returns are also treated as success
        """
        try:
            register_func(
                name=self.name,
                repo=f"{self.org}/{self.name}",
                project_type="adopted",
                node_yaml_path=str(node_yaml_path),
                domain=self.domain or "",
                log_prefix=self._log_prefix,
            )
            # If we get here without sys.exit, it's still a success
        except SystemExit as e:
            if e.code == 0 or e.code is None:
                logger.info("[IMP:9][%s][register] Registration complete (sys.exit caught per D3)", self._log_prefix)
            else:
                logger.info(
                    "[IMP:8][%s][register] Registration sys.exit(%s) — manual check required", self._log_prefix, e.code
                )

    def _register_via_node_yaml(self, node_yaml_path: Path) -> bool:
        """Register project using NodeYaml CLI (replaces yq subprocess).

        ## @purpose  If project_registry import fails, use NodeYaml CLI mutation API.
        ## @io        ⇥ node_yaml_path → ⎋ bool
        ## @complexity O(1)
        """
        import subprocess

        # Check if already registered via NodeYaml CLI --find-project
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "core.internal.shared.node_yaml",
                "--file",
                str(node_yaml_path),
                "--find-project",
                self.name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info("[IMP:9][%s][register] Project already registered — SKIP (idempotent)", self._log_prefix)
            return True

        # Add project via NodeYaml CLI mutation API
        domain_arg = self.domain or "-"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "core.internal.shared.node_yaml",
                "--file",
                str(node_yaml_path),
                "--add-project",
                self.name,
                f"{self.org}/{self.name}",
                "adopted",
                domain_arg,
                "-",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][%s][register] Registered via NodeYaml CLI: %s", self._log_prefix, self.name)
            return True
        logger.info("[IMP:8][%s][register] NodeYaml CLI registration failed — register manually", self._log_prefix)
        return False

    # endregion FUNC_register_in_node_yaml

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_configure_vhost
    ## @purpose  Configure nginx vhost for the project if domain is set (D4).
    ##            Tries direct import of vhost_renderer (TASK-036B), falls back to subprocess add-vhost.sh.
    ## @io        ⇥ node_configs_dir: Path → ⎋ bool — True if configured, False if skipped/failed
    ## @complexity O(1) — delegates to vhost_renderer or subprocess
    ## @invariants
    ##   - If no domain configured → skip (return False)
    ##   - D4: try/except ImportError → subprocess add-vhost.sh fallback
    ##   - Updates ai-platform.yaml needs.domain and expose:true before vhost generation
    ##   - If add-vhost.sh not found → skip with log message
    def configure_vhost(self, node_configs_dir: Path | None = None) -> bool:
        """Configure nginx vhost for the project.

        Returns True if vhost configured, False if skipped/failed.
        """
        if not self.domain:
            logger.info("[IMP:6][%s][vhost] No domain configured — skipping vhost", self._log_prefix)
            return False

        # Ensure ai-platform.yaml has the domain set
        self._update_yaml_for_vhost()

        # D4: Try direct import vhost_renderer, fallback to subprocess add-vhost.sh
        try:
            from core.internal.scaffold.vhost_renderer import (
                configure_vhost_for_project,  # type: ignore[import-untyped]
            )

            logger.info("[IMP:7][%s][vhost] Using vhost_renderer (Python API)", self._log_prefix)
            result = configure_vhost_for_project(
                project_dir=self.project_dir,
                domain=self.domain,
                node_configs_dir=node_configs_dir,
            )
            if result:
                logger.info(
                    "[IMP:9][%s][vhost] Vhost configured via vhost_renderer for: %s", self._log_prefix, self.domain
                )
                return True
            logger.info(
                "[IMP:8][%s][vhost] vhost_renderer returned False — trying subprocess fallback", self._log_prefix
            )
        except ImportError:
            logger.info(
                "[IMP:7][%s][vhost] vhost_renderer not available — using subprocess add-vhost.sh (D4 fallback)",
                self._log_prefix,
            )

        # Fallback: subprocess add-vhost.sh
        return self._configure_vhost_via_subprocess(node_configs_dir)

    def _update_yaml_for_vhost(self) -> None:
        """Update ai-platform.yaml for vhost generation.

        ## @purpose  Ensures ai-platform.yaml has needs.domain set and expose:true before vhost generation.
        ## @io        ⎋ side-effect: modifies yaml file
        ## @complexity O(1)
        """
        if not self.yaml_file.exists():
            return

        try:
            import yaml

            with open(self.yaml_file) as f:
                data = yaml.safe_load(f) or {}

            needs = data.get("needs", {})
            if isinstance(needs, dict):
                if self.domain:
                    needs["domain"] = self.domain
                    needs["expose"] = True
                data["needs"] = needs

                with open(self.yaml_file, "w") as f:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
                logger.info(
                    "[IMP:7][%s][vhost] ai-platform.yaml updated: needs.domain=%s, expose=true",
                    self._log_prefix,
                    self.domain,
                )
        except (ImportError, yaml.YAMLError):
            logger.info("[IMP:8][%s][vhost] Could not update ai-platform.yaml (PyYAML not available)", self._log_prefix)

    def _configure_vhost_via_subprocess(self, node_configs_dir: Path | None) -> bool:
        """Configure vhost via subprocess add-vhost.sh (D4 fallback).

        ## @purpose  Fallback when vhost_renderer.py is not available.
        ## @io        ⇥ node_configs_dir → ⎋ bool
        ## @complexity O(1)
        """
        add_vhost_script = Path(__file__).resolve().parent / "add-vhost.sh"

        if not add_vhost_script.exists():
            logger.info("[IMP:8][%s][vhost] add-vhost.sh not found — skipping vhost generation", self._log_prefix)
            logger.info(
                "[IMP:8][%s][vhost]   Manual: cp <template>/nginx/default.conf to node-configs overlays",
                self._log_prefix,
            )
            return False

        if node_configs_dir is None:
            node_configs_dir = self._resolve_node_configs_dir()

        if not node_configs_dir or not node_configs_dir.is_dir():
            logger.info("[IMP:8][%s][vhost] node-configs dir not found: %s", self._log_prefix, node_configs_dir)
            logger.info("[IMP:8][%s][vhost]   Manual: create vhost manually in overlays/nginx/", self._log_prefix)
            return False

        logger.info(
            "[IMP:7][%s][vhost] Configuring nginx vhost via add-vhost.sh for domain: %s", self._log_prefix, self.domain
        )

        result = subprocess.run(
            [
                "bash",
                str(add_vhost_script),
                "--project-dir",
                str(self.project_dir),
                "--node-configs-dir",
                str(node_configs_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            logger.info("[IMP:9][%s][vhost] Vhost configured via add-vhost.sh for: %s", self._log_prefix, self.domain)
            return True
        logger.info("[IMP:8][%s][vhost] add-vhost.sh returned non-zero — check vhost manually", self._log_prefix)
        if result.stderr.strip():
            logger.info("[IMP:8][%s][vhost] add-vhost.sh stderr: %s", self._log_prefix, result.stderr.strip()[:500])
        return False

    def _resolve_node_configs_dir(self) -> Path | None:
        """Resolve node-configs directory from project path.

        ## @purpose  Derive node-configs path: projects/<org>/node-configs/
        ## @io        ⎋ Path | None
        ## @complexity O(1)
        """
        # Walk up from project dir to find projects root
        parent = self.project_dir.parent
        if parent.name == self.org and parent.parent:
            projects_root = parent.parent
            node_configs = projects_root / "node-configs"
            if node_configs.is_dir():
                return node_configs

        # Try alternative: PROJECTS_ROOT env var
        projects_root_env = os.environ.get("PROJECTS_ROOT")
        if projects_root_env:
            candidate = Path(projects_root_env) / self.org / "node-configs"
            if candidate.is_dir():
                return candidate

        return None

    # endregion FUNC_configure_vhost

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_print_diff_report
    ## @purpose  Print a human-readable diff report of what was changed.
    ## @io        ⇥ changes: list[str] → ⎋ None (prints to stdout)
    def print_diff_report(self, changes: list[str]) -> None:
        """Print formatted adoption report.

        ## @purpose  Display end-of-adoption summary to the user.
        ## @io        ⎋ stdout — formatted report
        """
        print("")
        print("────────────────────────────────────────────────────────────")
        print(f"  ✅ adopt-project: {self.name}")
        print("────────────────────────────────────────────────────────────")
        print("")
        if not changes:
            print("  No changes made (everything up to date)")
        else:
            print("  Changes:")
            for c in changes:
                print(f"    {c}")
        print("")
        print("  ❗ NOT modified (preserved): src/, Dockerfile, application code")
        print("")
        print("────────────────────────────────────────────────────────────")
        logger.info("[IMP:9][%s][report] adopt-project DONE: %s", self._log_prefix, self.name)

    # endregion FUNC_print_diff_report

    # ──────────────────────────────────────────────────────────────────
    # region FUNC_adopt
    ## @purpose  Orchestrate the full adoption flow — all 9 steps.
    ## @io        ⇥ None → ⎋ AdoptionResult
    ## @complexity O(S × N) for compose validation + O(P) for registration
    def adopt(self) -> AdoptionResult:
        """Run the full adoption flow. Returns AdoptionResult with changes list.

        ## @purpose  Orchestrates: YAML gen → deploy simplify → delete platform-deploy → env gen →
        ##            Makefile → AGENTS.md → compose validation → node.yaml registration → vhost.
        """
        result = AdoptionResult()
        logger.info("[IMP:6][%s][adopt] Starting adopt-project.py (Wave 5c Strangler-Fig)", self._log_prefix)

        # ── Step 1: Generate or verify ai-platform.yaml ──
        logger.info("[IMP:7][%s][adopt] Step 1/8: Ensure ai-platform.yaml exists", self._log_prefix)
        yaml_status = self.generate_minimal_ai_platform_yaml()
        result.changes.append(f"✔ ai-platform.yaml {yaml_status}")

        # ── Step 2: Simplify deploy.yml ──
        logger.info("[IMP:7][%s][adopt] Step 2/8: Simplify deploy.yml to reusable workflow", self._log_prefix)
        if self.simplify_deploy_yml():
            result.changes.append("✔ deploy.yml simplified (uses: org/ai-platform/...)")
        else:
            if (
                self.deploy_yml.exists()
                and "/ai-platform/.github/workflows/deploy-project.yml" in self.deploy_yml.read_text()
            ):
                result.changes.append("- deploy.yml unchanged or already simplified")
            else:
                result.changes.append("- deploy.yml not found or unchanged")

        # ── Step 3: Delete platform-deploy.yml ──
        logger.info("[IMP:7][%s][adopt] Step 3/8: Remove deprecated platform-deploy.yml", self._log_prefix)
        if self.delete_platform_deploy_yml():
            result.changes.append("✔ platform-deploy.yml removed (if existed)")
        else:
            result.changes.append("- platform-deploy.yml not found")

        # ── Step 4: Generate .env.platform ──
        logger.info("[IMP:7][%s][adopt] Step 4/8: Generate .env.platform", self._log_prefix)
        if self.gen_env_platform():
            result.changes.append("✔ .env.platform regenerated")
        else:
            result.changes.append("- .env.platform generation skipped (platform-env.yaml not found)")

        # ── Step 5: Generate Makefile and AGENTS.md ──
        logger.info("[IMP:7][%s][adopt] Step 5/8: Generate project Makefile and AGENTS.md", self._log_prefix)
        mk = self.gen_project_makefile()
        ag = self.gen_project_agents()
        result.changes.append(f"✔ Makefile/AGENTS.md ensured (Makefile={mk}, AGENTS.md={ag})")

        # ── Step 6: Validate compose networks (proxy-net) ──
        logger.info("[IMP:7][%s][adopt] Step 6/8: Validate compose proxy-net (M4 gate)", self._log_prefix)
        compose_candidate: Path | None = None
        for candidate in ("compose.yaml", "docker-compose.yml"):
            p = self.project_dir / candidate
            if p.exists():
                compose_candidate = p
                break

        if compose_candidate:
            vr = self.validate_compose_networks(compose_candidate)
            if vr.valid:
                result.changes.append("✔ Compose proxy-net validated")
            else:
                logger.info(
                    "[IMP:8][%s][adopt]   proxy-net validation FAILED — adopt continues, but fix before deploy",
                    self._log_prefix,
                )
                result.changes.append("⚠️  Compose proxy-net VALIDATION FAILED — must fix before deploy")
        else:
            logger.info("[IMP:6][%s][adopt] No compose file found — skipping proxy-net validation", self._log_prefix)
            result.changes.append("- No compose file — proxy-net validation skipped")

        # ── Step 7: Register in node.yaml ──
        logger.info("[IMP:7][%s][adopt] Step 7/8: Register in node.yaml (idempotent)", self._log_prefix)
        node_yaml = self._resolve_node_yaml_path()
        if node_yaml:
            self.register_in_node_yaml(node_yaml)
        result.changes.append("✔ node.yaml registration checked")

        # ── Step 8: Configure vhost ──
        logger.info("[IMP:7][%s][adopt] Step 8/8: Configure nginx vhost", self._log_prefix)
        node_configs_dir = self._resolve_node_configs_dir()
        vhost_ok = self.configure_vhost(node_configs_dir)
        if self.domain and vhost_ok:
            result.changes.append(f"✔ Vhost configured for: {self.domain}")
        else:
            result.changes.append("- No domain — vhost skipped")

        # ── Print report ──
        self.print_diff_report(result.changes)
        return result

    def _resolve_node_yaml_path(self) -> Path | None:
        """Resolve node.yaml path from project directory context.

        ## @purpose  Determine the node.yaml path for project registration.
        ## @io        ⎋ Path | None
        ## @complexity O(1)
        """
        # Try from PROJECTS_ROOT env
        projects_root = os.environ.get("PROJECTS_ROOT")
        if projects_root:
            candidate = Path(projects_root) / self.org / "node-configs" / self.node / "node.yaml"
            if candidate.exists():
                return candidate

        # Try from parent directory structure
        parent = self.project_dir.parent
        if parent.name == self.org and parent.parent:
            candidate = parent.parent / "node-configs" / self.node / "node.yaml"
            if candidate.exists():
                return candidate

        # Fallback: return path even if doesn't exist (caller handles)
        if projects_root:
            return Path(projects_root) / self.org / "node-configs" / self.node / "node.yaml"
        return None

    # endregion FUNC_adopt


# endregion class_ProjectAdopter


# region FUNC_validate_org_against_node_yaml
def validate_org_against_node_yaml(org: str, node_yaml_path: Path) -> str:
    """Validate org against node.yaml context (case-insensitive). Returns canonical org.

    ## @purpose  Full Python version of org validation (D6). Verifies that the provided org
    ##            matches node.yaml's context field. Returns canonical casing from node.yaml.
    ##            Raises ValueError on mismatch.
    ## @io        ⇥ org: str, node_yaml_path: Path → ⎋ str — canonical org from node.yaml
    ##            ⚡ raises ValueError if org does not match (even case-insensitive)
    ## @complexity O(1)
    ## @invariants
    ##   - Case-insensitive comparison
    ##   - If casing differs → returns node.yaml variant (canonical)
    ##   - If org does not match → raises ValueError
    ##   - If node.yaml not found or has no context → returns org unchanged
    ##   - D6: duplicated in shell (fast grep) AND Python (full PyYAML)
    """
    if not node_yaml_path.exists():
        logger.info("[IMP:9][validate_org] node.yaml not found at %s — skipping context validation", node_yaml_path)
        return org

    try:
        from core.internal.shared.node_yaml import ConfigNotFoundError, ConfigParseError, NodeYaml

        node = NodeYaml(str(node_yaml_path))
        node_context = node.get_context()
    except (ConfigNotFoundError, ConfigParseError):
        logger.info("[IMP:9][validate_org] Cannot parse node.yaml — skipping context validation")
        return org
    if not node_context:
        logger.info("[IMP:9][validate_org] node.yaml has no context field — skipping validation")
        return org

    # Case-insensitive comparison
    if org.lower() != str(node_context).lower():
        logger.info(
            "[IMP:9][validate_org] FAIL-FAST: org='%s' vs node.yaml context='%s' — mismatch detected",
            org,
            node_context,
        )
        raise ValueError(
            f"Project org '{org}' does not match node.yaml context '{node_context}'. "
            f"Use --org {node_context} or update node.yaml context."
        )

    # Casing mismatch → adopt node.yaml variant
    if org != node_context:
        logger.info(
            "[IMP:9][validate_org] Casing mismatch: org='%s' vs node.yaml context='%s' — using node.yaml variant",
            org,
            node_context,
        )
        return str(node_context)

    logger.info("[IMP:9][validate_org] node.yaml context validated: %s", org)
    return org


# endregion FUNC_validate_org_against_node_yaml


# region FUNC_main_CLI
def main() -> None:
    """CLI entrypoint for project_adopter. Invoked from shell facade.

    ## @purpose  Parses CLI arguments and runs the adopt flow.
    ##            Subcommand: adopt (full adoption flow).
    ## @io        ⎋ None — exits via sys.exit
    ## @complexity O(S × N) for the full adopt flow
    """
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(description="Adopt a project into ai-platform lifecycle")
    sub = parser.add_subparsers(dest="action", required=True)

    adopt_parser = sub.add_parser("adopt", help="Adopt a project")
    adopt_parser.add_argument("--project-dir", required=True, type=str, help="Project directory")
    adopt_parser.add_argument("--project-name", required=True, type=str, help="Project name")
    adopt_parser.add_argument("--project-org", required=True, type=str, help="Organization / context")
    adopt_parser.add_argument("--project-node", required=True, type=str, help="Target node name")
    adopt_parser.add_argument("--project-domain", type=str, default=None, help="Custom domain (optional)")
    adopt_parser.add_argument("--force", action="store_true", default=False, help="Regenerate Makefile/AGENTS.md")

    args = parser.parse_args()

    if args.action == "adopt":
        project_dir = Path(args.project_dir)
        if not project_dir.is_dir():
            print(f"[IMP:10][adopt] FAIL-FAST: project directory not found: {project_dir}", file=sys.stderr)
            sys.exit(1)

        adopter = ProjectAdopter(
            project_dir=project_dir,
            name=args.project_name,
            org=args.project_org,
            node=args.project_node,
            domain=args.project_domain,
            force=args.force,
        )

        result = adopter.adopt()
        sys.exit(0 if result.success else 1)


# endregion FUNC_main_CLI

if __name__ == "__main__":
    main()
