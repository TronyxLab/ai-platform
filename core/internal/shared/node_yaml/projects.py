#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-projects, ProjectsMixin, projects, get-projects, get-project, get-project-entries, add-project, remove-project, update-project, ProjectEntry, 119-H
# STRUCTURE: ▶ ProjectsMixin → ◇ get_projects() list → ◇ get_project(name) → ◇ get_project_entries() typed → ◇ add_project/remove_project/update_project → _write_back → ⎋ list[ProjectEntry] | bool
# region MODULE_CONTRACT
## @purpose  Доменный миксин NodeYaml — поддомен `projects` node.yaml (DevPlan 119 H1).
##           Чтение: get_projects()/get_project()/get_project_entries() (canon parser, D3).
##           Мутация: add_project()/remove_project()/update_project() (DevPlan 088 T3.5 + 116 B6 T6).
## @scope    Миксин для NodeYaml-агрегатора (node_yaml/__init__.py). Потребители: context_deployer,
##           reconciler_projects, vhost_renderer, project_registry, converge/projects, scaffold.
## @invariants
##   1. Single project parser canon (invariant 13, DevPlan 116 B6 D3/T4): ВСЕ потребители
##      node.yaml#projects делегируют get_project_entries()/get_projects(); malformed record →
##      ConfigValidationError (fail-fast, никогда silent skip).
##   2. Мутации работают на DEEPCOPY — cache никогда не отравляется провалом _write_back
##      (DevPlan 116 B6 T6; TRAP 2026-07-30 fixed).
##   3. remove_project/update_project возвращают False если проект не найден (no raise).
## @rationale DevPlan 119 H1 (AUDIT-2 M1): поддомен projects выделен из монолита node_yaml.py.
##            ProjectEntry — ЕДИНСТВЕННОЕ определение в core/ (gate test_gate_single_project_parser
##            требует ровно 1 `class ProjectEntry`; путь обновлён на node_yaml/projects.py).
## @changes 2026-08-03 · DevPlan 119 H1 — извлечено из node_yaml.py (get_projects/get_project/
##           get_project_entries/add_project/remove_project/update_project + ProjectEntry)
##           в node_yaml/projects.py без изменения логики
## @changes 2026-08-01 · DevPlan 116 B6 — get_project_entries canon (T4), deepcopy mutations (T6.1)
## @changes 2026-07-30 · DevPlan 088 — mutation API + typed parser (T3.5)
# endregion MODULE_CONTRACT

import copy
import logging
from dataclasses import dataclass
from typing import cast

from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.ssl_certs import validate_cert_domain_fqdn  # REF-0008: fail-fast fqdn

logger = logging.getLogger(__name__)


# region DATACLASS_ProjectEntry
@dataclass
class ProjectEntry:
    """Typed project entry from node.yaml projects array.

    ## @purpose  Structured representation of a project entry for mutation operations.
    ## @fields   name — project name
    ##           repo — GitHub repository path (org/repo)
    ##           type — project type (frontend, backend, agent, bot, landing)
    ##           domain — FQDN for HTTP-routable projects
    ##           database — database name for postgres projects
    ##           context — context name this project belongs to
    ## @invariants  domain, database, context default to empty string.
    """

    name: str = ""
    repo: str = ""
    type: str = ""
    domain: str = ""
    database: str = ""
    context: str = ""


# endregion DATACLASS_ProjectEntry


# region CLASS_ProjectsMixin
class ProjectsMixin:
    """Доменный миксин NodeYaml: поддомен projects (DevPlan 119 H1).

    GREP_SUMMARY: ProjectsMixin, projects, get-projects, get-project-entries, mutation
    STRUCTURE: ▶ ProjectsMixin → ◇ get_projects() → ◇ get_project(name) → ◇ get_project_entries() → ◇ add/remove/update → ⎋ typed
    """

    # ── Mixin-контракт: _load/_write_back предоставляет NodeYamlCore (агрегатор) ──
    def _load(self) -> dict[str, object]:
        """Read node.yaml (реализация в NodeYamlCore — mixin живёт только в составе NodeYaml)."""
        msg = "_load provided by NodeYamlCore"
        raise NotImplementedError(msg)

    def _write_back(self, data: dict[str, object]) -> None:
        """Write node.yaml атомарно (реализация в NodeYamlCore — mixin живёт только в составе NodeYaml)."""
        msg = "_write_back provided by NodeYamlCore"
        raise NotImplementedError(msg)

    # region FUNC_get_projects
    ## @purpose  Get projects list from node.yaml.
    ## @io — ⇥ → ⎋ list[dict]
    ## @complexity — O(1) after _load()
    ## @invariants
    ##   - Returns [] if 'projects' key missing
    ##   - Raises ConfigValidationError if 'projects' exists but is not a list
    def get_projects(self) -> list[dict[str, object]]:
        """Get projects list from node.yaml.

        Returns:
            List of project dicts (empty list if 'projects' key missing)

        Raises:
            ConfigValidationError: 'projects' exists but is not a list
        """
        data = self._load()
        projects = data.get("projects")
        if projects is None:
            logger.info("[IMP:7][NodeYaml] Projects: 0")
            return []
        if not isinstance(projects, list):
            logger.error("[IMP:9][NodeYaml] 'projects' is not a list: %s", type(projects))
            msg = f"'projects' is not a list: {type(projects)}"
            raise ConfigValidationError(msg)
        logger.info("[IMP:7][NodeYaml] Projects: %d", len(projects))
        # yaml-payload → типизированная граница (W11, DevPlan 170): isinstance-сужение даёт list[Any],
        # каст до list[dict[str, object]] — поля читаются объектно.
        return cast(list[dict[str, object]], projects)

    # endregion FUNC_get_projects

    # region FUNC_get_project
    ## @purpose  Get a single project entry by name.
    ## @io — ⇥ name: str → ⎋ Optional[dict]
    ## @complexity — O(P) where P = number of projects
    def get_project(self, name: str) -> dict[str, object] | None:
        """Get a project entry by name.

        Args:
            name: Project name to find

        Returns:
            Project dict or None if not found
        """
        projects = self.get_projects()
        for p in projects:
            if isinstance(p, dict) and p.get("name") == name:
                logger.info("[IMP:8][NodeYaml.get_project] Found project: %s", name)
                return p
        logger.info("[IMP:7][NodeYaml.get_project] Project not found: %s", name)
        return None

    # endregion FUNC_get_project

    # region FUNC_get_project_entries
    ## @purpose  Canonical typed parser of node.yaml#projects → list[ProjectEntry].
    ## @io — ⇥ → ⎋ list[ProjectEntry]
    ## @complexity — O(P) where P = number of projects
    ## @invariants
    ##   - Fail-fast (decision D3, DevPlan 116 B6 T4): str-entry, non-dict, or dict without a
    ##     non-empty 'name' → ConfigValidationError with record index. Malformed records are
    ##     NEVER silently skipped.
    ##   - Single parser canon: all node.yaml#projects consumers delegate to
    ##     get_project_entries()/get_projects() (reconciler, context_deployer,
    ##     reconciler_projects, vhost_renderer, lister).
    ##   - Empty optional fields → "".
    def get_project_entries(self) -> list[ProjectEntry]:
        """Parse node.yaml#projects into typed ProjectEntry list (canonical parser).

        Returns:
            List of ProjectEntry (empty list if 'projects' key missing)

        Raises:
            ConfigValidationError: malformed record (str, non-dict, or missing/empty 'name')
        """
        projects = self.get_projects()
        entries: list[ProjectEntry] = []
        for idx, p in enumerate(projects):
            if not isinstance(p, dict) or not p.get("name"):
                logger.error(
                    "[IMP:10][NodeYaml.get_project_entries] Malformed project record at index %d: %r",
                    idx,
                    p,
                )
                msg = (
                    f"Malformed project entry at projects[{idx}]: expected dict with non-empty 'name' "
                    "(fail-fast, DevPlan 116 B6 D3)"
                )
                raise ConfigValidationError(msg)
            entries.append(
                ProjectEntry(
                    name=str(p.get("name", "")),
                    repo=str(p.get("repo", "")),
                    type=str(p.get("type", "")),
                    domain=str(p.get("domain", "")),
                    database=str(p.get("database", "")),
                    context=str(p.get("context", "")),
                )
            )
        logger.info("[IMP:9][NodeYaml.get_project_entries] %d project(s) parsed", len(entries))
        return entries

    # endregion FUNC_get_project_entries

    # region FUNC_add_project
    ## @purpose  Add a project to node.yaml and write back to disk.
    ## @io — ⇥ project: ProjectEntry → ⎋ None
    ## @complexity — O(P) for duplicate check + O(N) for YAML dump
    ## @invariants
    ##   Raises ConfigValidationError if project with same name already exists.
    ##   Raises ConfigValidationError if project.domain is set and fails FQDN validation
    ##   (REF-0008/SEC-0026: needs.domain попадает в cert-pipeline пути/reloadcmd под root —
    ##   `../`-домен = path traversal/RCE; fail-fast на mutation-входе, до _write_back).
    ##   Writes back via _write_back() preserving comments (ruamel.yaml) if available.
    ##   Mutates a DEEPCOPY of _load() — cache is never poisoned by a failed write
    ##   (DevPlan 116 B6 T6.1; TRAP 2026-07-30 fixed).
    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · FIXED — add_project mutates _data cache in-place before _write_back
    # · Symptom: If _write_back fails (disk full, permission denied), the in-memory _data
    # ·   cache already contains the appended project but the file is NOT updated → cache/file desync.
    # · Root: _load() returns the cached dict by reference; appending to its "projects" list
    # ·   mutates the cache in-place.
    # · Fix: `data = copy.deepcopy(self._load())` — mutation happens on a copy; _write_back
    # ·   invalidates cache on success and on failure (DevPlan 116 B6 T6).
    # · Prevention: all mutation methods must deepcopy before modifying; never mutate _load() ref.
    def add_project(self, project: ProjectEntry) -> None:
        """Add a project to node.yaml and write back to disk.

        Args:
            project: ProjectEntry with name, repo, type, domain, database, context

        Raises:
            ConfigValidationError: if project with same name already exists
            ConfigValidationError: if project.domain is set but not a valid FQDN (REF-0008)
        """
        # ── REF-0008 fail-fast: FQDN-валидация домена ДО любых мутаций/write-back ──
        if project.domain:
            validate_cert_domain_fqdn(project.domain)

        data = copy.deepcopy(self._load())
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            projects = []

        # Duplicate check
        for p in projects:
            if isinstance(p, dict) and p.get("name") == project.name:
                logger.error("[IMP:10][NodeYaml.add_project] Duplicate project: %s", project.name)
                msg = f"Project already exists: {project.name}"
                raise ConfigValidationError(msg)

        new_entry: dict[str, str] = {
            "name": project.name,
            "repo": project.repo,
            "type": project.type,
        }
        if project.domain:
            new_entry["domain"] = project.domain
        if project.database:
            new_entry["database"] = project.database
        if project.context:
            new_entry["context"] = project.context

        projects.append(new_entry)
        data["projects"] = projects

        self._write_back(data)
        logger.info("[IMP:9][NodeYaml.add_project] Added project: %s", project.name)

    # endregion FUNC_add_project

    # region FUNC_remove_project
    ## @purpose  Remove a project from node.yaml and write back to disk.
    ## @io — ⇥ name: str → ⎋ bool
    ## @complexity — O(P) for filter + O(N) for YAML dump
    ## @invariants  Returns False if project not found (no exception raised).
    ##   Mutates a DEEPCOPY — cache clean on write failure (DevPlan 116 B6 T6.1).
    def remove_project(self, name: str) -> bool:
        """Remove a project from node.yaml and write back to disk.

        Args:
            name: Project name to remove

        Returns:
            True if project was found and removed, False if not found
        """
        data = copy.deepcopy(self._load())
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            return False

        new_projects = [p for p in projects if not (isinstance(p, dict) and p.get("name") == name)]

        if len(new_projects) == len(projects):
            logger.info("[IMP:8][NodeYaml.remove_project] Project not found: %s", name)
            return False

        data["projects"] = new_projects
        self._write_back(data)
        logger.info("[IMP:9][NodeYaml.remove_project] Removed project: %s", name)
        return True

    # endregion FUNC_remove_project

    # region FUNC_update_project
    ## @purpose  Update fields of an existing project entry.
    ## @io — ⇥ name: str, **updates → ⎋ bool
    ## @complexity — O(P) for search + O(N) for YAML dump
    ## @invariants  None-value fields are removed from the dict (pop). Returns False if not found.
    ##   Mutates a DEEPCOPY (nested dict entries) — cache clean on write failure
    ##   (DevPlan 116 B6 T6.1; TRAP 2026-07-30 fixed).
    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · FIXED — update_project mutates cached dict in-place
    # · Symptom: Same cache-corruption risk as add_project. If _write_back fails after
    #   updating the project dict in-place (p[key] = value), the in-memory cache is
    #   desynchronized from the file on disk.
    # · Root: p is a reference into the cached list self._data["projects"]. Mutating p
    #   mutates the cache directly.
    # · Fix: `data = copy.deepcopy(self._load())` — deep copy required because update_project
    #   mutates nested dict entries (shallow would still share the inner project dicts).
    def update_project(self, name: str, **updates: object) -> bool:
        """Update fields of an existing project entry.

        Args:
            name: Project name to update
            updates: Fields to update (e.g., domain="new.example.com", context="prod")

        Returns:
            True if project was found and updated, False if not found
        """
        data = copy.deepcopy(self._load())
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            return False

        updated = False
        for p in projects:
            if isinstance(p, dict) and p.get("name") == name:
                for key, value in updates.items():
                    if value is not None:
                        p[key] = value
                    else:
                        p.pop(key, None)
                updated = True
                break

        if not updated:
            logger.info("[IMP:8][NodeYaml.update_project] Project not found: %s", name)
            return False

        data["projects"] = projects
        self._write_back(data)
        logger.info("[IMP:9][NodeYaml.update_project] Updated project: %s (%s)", name, ", ".join(updates.keys()))
        return True

    # endregion FUNC_update_project


# endregion CLASS_ProjectsMixin
