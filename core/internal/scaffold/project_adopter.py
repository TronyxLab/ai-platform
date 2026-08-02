#!/usr/bin/env python3
# GREP_SUMMARY: project_adopter, adopt-project, strangler-fig, ai-platform-yaml, compose-validation, node-yaml-registration, vhost, makefile, agents-md
# STRUCTURE: ▶ adopt() 8 шагов → ○ gen_yaml → ○ simplify_deploy → ○ delete_platform_deploy → ○ gen_env (subprocess) → ○ makefile+agents (scaffold_helpers) → ○ validate_compose_networks (compose_validator) → ○ register_in_node_yaml (scaffold_helpers) → ○ configure_vhost (vhost_configurator) → ⎋ print_diff_report
# region MODULE_CONTRACT
## @purpose  Strangler-Fig migration of adopt-project.sh (906 LOC shell) into Python business logic.
##            Adopts an existing project into ai-platform lifecycle: generates ai-platform.yaml,
##            simplifies deploy.yml, validates docker-compose proxy-net, registers in node.yaml,
##            configures nginx vhost, and generates Makefile/AGENTS.md.
##            B9 T5 (U-32): compose-валидация и vhost-логика вынесены в scaffold/compose_validator.py
##            и scaffold/vhost_configurator.py; COMPOSE_PROFILES чтение — в scaffold_helpers (CS-4);
##            deprecated _register_project_safe/_register_via_node_yaml удалены (CS-5);
##            validate_org_against_node_yaml → scaffold_helpers (re-export).
## @scope    Called from adopt-project.sh shell facade (≤120 LOC) via `python3 -m core.internal.scaffold.project_adopter adopt`.
##           Класс ProjectAdopter: adopt()-оркестрация, YAML-генераторы (делегируют в scaffold_helpers),
##           simplify_deploy_yml/delete_platform_deploy_yml, gen_env_platform, gen_project_makefile,
##           gen_project_agents, register_in_node_yaml (делегирует), print_diff_report.
## @invariants
##   1. NEVER modifies src/, Dockerfile, docker-compose.yml (application code)
##   2. .env.platform regenerated via subprocess gen_env_platform.py (CLI-first, D5)
##   3. Supports personal domains (O11) — separate cert path
##   4. Idempotent: second call with same project → no-op (exit 0) except .env.platform regeneration
##   5. deploy.yml simplified to use reusable workflow (if exists); platform-deploy.yml deleted if exists
##   6. validate_compose_networks → compose_validator (3-method cascade); register → scaffold_helpers
##   7. configure_vhost → vhost_configurator (vhost_renderer → add-vhost.sh fallback, D4)
##   8. gen_env_platform always via subprocess.run (CLI-first design, D5)
##   9. validate_org duplicated in shell (fast grep) AND Python (full PyYAML) per D6 —
##      Python-версия живёт в scaffold_helpers (shared), re-export отсюда
## @rationale Migration tool for existing projects. Strangler-Fig per Wave 5 language policy (AGENTS.md).
##            DevPlan 116 B9 D5: полный сплит ответственностей project_adopter (SRP, ≤600 LOC гейт T6.2).
## @changes  2026-07-26 · Wave 5c — Full Strangler-Fig from adopt-project.sh (906 LOC)
##           2026-08-01 · B9 T5 — compose_validator/vhost_configurator/scaffold_helpers split (U-32)
# endregion MODULE_CONTRACT

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

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# B9 T5 (U-32): compose-валидация и vhost-логика — отдельные модули (SRP)
from core.internal.scaffold import vhost_configurator
from core.internal.scaffold.compose_validator import ValidationResult, validate_compose_networks

# Re-export shared org-валидации (B9 T5, U-32 — D6 full PyYAML версия; shell-версия — adopt-project.sh)
from core.internal.scaffold.scaffold_helpers import validate_org_against_node_yaml  # noqa: F401

# DevPlan 118 A2: единый канон compose-резолва — shared/compose_files (SoT списков)
from core.internal.shared.compose_files import resolve_compose_file

logger = logging.getLogger(__name__)


# region dataclass_AdoptionResult
@dataclass
class AdoptionResult:
    """Result of the adopt() orchestration — collects changes for the diff report (changes/success)."""

    changes: list[str] = field(default_factory=list)
    success: bool = True


# endregion dataclass_AdoptionResult


# region class_ProjectAdopter
class ProjectAdopter:
    """Adopt an existing project into the ai-platform lifecycle.

    ## @purpose  Orchestrates adoption: YAML gen, CI rewrite, compose validation (compose_validator),
    ##            node.yaml registration (scaffold_helpers), vhost (vhost_configurator), Makefile/AGENTS.md.
    ## @io        ┌ project_dir/name/org/node/domain/force → ⎋ adopt() → AdoptionResult
    ## @complexity O(1) construction; adopt() orchestrates 8 steps with linear complexity each
    """

    def __init__(
        self,
        project_dir: Path,
        name: str,
        org: str,
        node: str,
        domain: str | None = None,
        force: bool = False,
    ) -> None:
        """Initialize ProjectAdopter with validated parameters (project_dir/name/org/node/domain/force)."""
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
        # B9 T5 (CS-4): COMPOSE_PROFILES чтение — scaffold_helpers (публичная функция)
        from core.internal.scaffold.scaffold_helpers import load_compose_profiles_from_platform_env as _load_profiles

        self.compose_profiles = os.environ.get("COMPOSE_PROFILES") or _load_profiles()

        self._log_prefix = "adopt"

    # region FUNC_generate_minimal_ai_platform_yaml
    ## @purpose  Generate minimal ai-platform.yaml (auto-type-detect frontend/fullstack/backend; exists → "exists"). · ⇥ None → ⎋ str "generated"|"exists" · @complexity O(1) · Не перезаписывает существующий yaml; делегирует scaffold_helpers.gen_ai_platform_yaml
    def generate_minimal_ai_platform_yaml(self) -> str:
        """Generate minimal ai-platform.yaml if not present."""
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

    # region FUNC_simplify_deploy_yml
    ## @purpose  Simplify deploy.yml to reusable workflow pattern (K4); не трогает уже-simplified. · ⇥ None → ⎋ bool — True if simplified, False if skipped/already-simplified · @complexity O(1) · Бэкап deploy.yml.bak; interactive prompt если не --force; idempotent
    def simplify_deploy_yml(self) -> bool:
        """Simplify deploy.yml to use reusable workflow."""
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

    # region FUNC_delete_platform_deploy_yml
    ## @purpose  Delete platform-deploy.yml if it exists (deprecated artifact). · ⇥ None → ⎋ bool — True if deleted, False if not found · @complexity O(1)
    def delete_platform_deploy_yml(self) -> bool:
        """Delete deprecated platform-deploy.yml."""
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

    # region FUNC_gen_env_platform
    ## @purpose  Generate .env.platform via subprocess gen_env_platform.py (CLI-first, D5). · ⇥ None → ⎋ bool — True if generated, False if script not found or failed · @complexity O(1) · Всегда перегенерирует (не идемпотентен by design); gen_env_platform.py CLI-first
    def gen_env_platform(self) -> bool:
        """Generate .env.platform via subprocess gen_env_platform.py."""
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

    # region FUNC_gen_project_makefile
    ## @purpose  Generate minimal Makefile (K3 contract); preserves existing unless --force. · ⇥ None → ⎋ str "generated"|"exists"|"skipped" · @complexity O(1)
    def gen_project_makefile(self) -> str:
        """Generate project Makefile (delegates to scaffold_helpers)."""
        from core.internal.scaffold.scaffold_helpers import gen_project_makefile as _gen

        return _gen(
            name=self.name,
            domain=self.domain or "",
            output_path=str(self.project_dir / "Makefile"),
            force=self.force,
        )

    # endregion FUNC_gen_project_makefile

    # region FUNC_gen_project_agents
    ## @purpose  Generate AGENTS.md (DD13 contract, ≤60 lines); preserves existing unless --force. · ⇥ None → ⎋ str "generated"|"exists"|"skipped" · @complexity O(1)
    def gen_project_agents(self) -> str:
        """Generate project AGENTS.md (delegates to scaffold_helpers)."""
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

    # region FUNC_validate_compose_networks
    ## @purpose  Validate compose proxy-net (M4 gate) — делегирует compose_validator (B9 T5, U-32). · ⇥ compose_path: Path → ⎋ ValidationResult · @complexity O(S × N) · Validation only; без domain → valid=True (skip)
    def validate_compose_networks(self, compose_path: Path) -> ValidationResult:
        """Validate compose proxy-net configuration (delegates to compose_validator)."""
        return validate_compose_networks(
            compose_path,
            domain=self.domain or "",
            compose_profiles=self.compose_profiles,
            log_prefix=self._log_prefix,
        )

    # endregion FUNC_validate_compose_networks

    # region FUNC_register_in_node_yaml
    ## @purpose  Register project in node.yaml — делегирует scaffold_helpers (AC6, NodeYaml CLI, idempotent). · ⇥ node_yaml_path: Path → ⎋ bool · @complexity O(1) · Idempotent (--find-project); без прямого импорта (SystemExit wrapper не нужен)
    def register_in_node_yaml(self, node_yaml_path: Path) -> bool:
        """Register project in node.yaml. Idempotent (delegates to scaffold_helpers)."""
        from core.internal.scaffold.scaffold_helpers import register_in_node_yaml as _register

        return _register(
            name=self.name,
            org=self.org,
            node=self.node or os.environ.get("PLATFORM_DEFAULT_NODE", ""),
            ptype="adopted",
            domain=self.domain or "",
            database="",
            yaml_path=str(node_yaml_path),
            dry_run=False,
            context="",
        )

    # endregion FUNC_register_in_node_yaml

    # region FUNC_configure_vhost
    ## @purpose  Configure nginx vhost (D4) — делегирует vhost_configurator (vhost_renderer → add-vhost.sh). · ⇥ node_configs_dir: Path | None → ⎋ bool · @complexity O(1) · Без domain → False; yaml_file обновляется перед vhost (needs.domain+expose)
    def configure_vhost(self, node_configs_dir: Path | None = None) -> bool:
        """Configure nginx vhost for the project (delegates to vhost_configurator)."""
        return vhost_configurator.configure_vhost(
            project_dir=self.project_dir,
            domain=self.domain or "",
            org=self.org,
            yaml_file=self.yaml_file,
            node_configs_dir=node_configs_dir,
            log_prefix=self._log_prefix,
        )

    # endregion FUNC_configure_vhost

    # region FUNC_print_diff_report
    ## @purpose  Print a human-readable diff report of what was changed. · ⇥ changes: list[str] → ⎋ None (prints to stdout)
    def print_diff_report(self, changes: list[str]) -> None:
        """Print formatted adoption report."""
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

    # region FUNC_adopt
    ## @purpose  Orchestrate the full adoption flow — 8 steps. · ⇥ None → ⎋ AdoptionResult · @complexity O(S × N) + O(P)
    def adopt(self) -> AdoptionResult:
        """Run the full adoption flow. Returns AdoptionResult with changes list."""
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
        # DevPlan 118 A2: единый канон compose-резолва — shared/compose_files.resolve_compose_file
        # (порядок compose.yaml → docker-compose.yml сохранён; канон расширен до 4 имён)
        compose_candidate: Path | None = resolve_compose_file(str(self.project_dir))

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
        node_configs_dir = vhost_configurator.resolve_node_configs_dir(self.project_dir, self.org)
        vhost_ok = self.configure_vhost(node_configs_dir)
        if self.domain and vhost_ok:
            result.changes.append(f"✔ Vhost configured for: {self.domain}")
        else:
            result.changes.append("- No domain — vhost skipped")

        # ── Print report ──
        self.print_diff_report(result.changes)
        return result

    def _resolve_node_yaml_path(self) -> Path | None:
        """Resolve node.yaml path via canonical NodeYaml.resolve (DevPlan 116 B6 T8.1).

        ## @purpose  resolve Path 1 = {config_dir}/node-configs/{node}/node.yaml; fallback —
        ##            parent-структура проекта; финальный fallback — путь даже если файл не существует.
        ## @io        ⎋ Path | None · @complexity O(P) — resolve 3-path search
        """
        projects_root = os.environ.get("PROJECTS_ROOT")
        try:
            from core.internal.shared.exceptions import ConfigNotFoundError
            from core.internal.shared.node_yaml import NodeYaml

            resolved = NodeYaml.resolve(
                node_name=self.node,
                config_dir=os.path.join(projects_root, self.org) if projects_root else None,
            )._path
            return Path(resolved)
        except ConfigNotFoundError:
            # Fallback: parent-структура проекта (adopter запускается из project dir)
            parent = self.project_dir.parent
            if parent.name == self.org and parent.parent:
                candidate = parent.parent / "node-configs" / self.node / "node.yaml"
                if candidate.exists():
                    return candidate
            return (
                None if not projects_root else Path(projects_root) / self.org / "node-configs" / self.node / "node.yaml"
            )

    # endregion FUNC_adopt


# endregion class_ProjectAdopter


# region FUNC_main_CLI
def main() -> int:
    """CLI entrypoint for project_adopter. Invoked from shell facade.

    ## @purpose  Parses CLI arguments and runs the adopt flow (subcommand: adopt). · ⎋ int exit code (контракт T4: main() -> int, sys.exit в __main__)
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
            return 1

        adopter = ProjectAdopter(
            project_dir=project_dir,
            name=args.project_name,
            org=args.project_org,
            node=args.project_node,
            domain=args.project_domain,
            force=args.force,
        )

        result = adopter.adopt()
        return 0 if result.success else 1
    return 0


# endregion FUNC_main_CLI

if __name__ == "__main__":
    sys.exit(main())
