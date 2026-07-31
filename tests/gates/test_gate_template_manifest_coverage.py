# GREP_SUMMARY: gate template-manifest-coverage *.template registered nginx-templates volume-sources
# STRUCTURE: ▶ ┌repo *.template files┐ → ◇ (a) each registered in template-manifest → ▶ ┌nginx volumes→/etc/nginx/templates┐ → ◇ (b) each source registered → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Coverage gate (DevPlan 116 T6/T9, U-47): (a) каждый *.template файл репо
##           зарегистрирован в core/templates/template-manifest.yaml; (b) каждый volume-источник
##           nginx/docker-compose.base.yml, монтируемый в /etc/nginx/templates/*.conf.template,
##           зарегистрирован. Устраняет U-47: nginx монтирует 9 шаблонов, зарегистрированы 2.
## @scope    Read-only gate — сканирует файловую систему + manifest, не модифицирует.
## @invariants
##   - (a) все *.template (исключая .git/node_modules/.venv) ∈ manifest template paths
##   - (b) каждый source volume'а nginx с target /etc/nginx/templates/*.conf.template
##         имеет зарегистрированную запись (по basename)
##   - Manifest paths резолвятся относительно core/templates/ (директория манифеста)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale Единый реестр шаблонов (template-manifest) — render/check покрывают их;
##            незарегистрированный шаблон выпадает из валидации (templates-check).
## @changes 2026-07-31 | Created (DevPlan 116 T9)
# endregion MODULE_CONTRACT

import logging
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
MANIFEST = ROOT / "core" / "templates" / "template-manifest.yaml"
MANIFEST_DIR = MANIFEST.parent
NGINX_COMPOSE = ROOT / "core" / "modules" / "nginx" / "docker-compose.base.yml"

# Директории, исключаемые из find *.template
_EXCLUDED_PARTS = {".git", "node_modules", ".venv", "__pycache__"}


def _registered_template_paths() -> set[str]:
    """Resolved absolute paths of all single-file templates in the manifest."""
    with open(MANIFEST) as f:
        manifest = yaml.safe_load(f)
    paths: set[str] = set()
    for entry in manifest.get("templates", []):
        tmpl = entry.get("template", "")
        if entry.get("type") == "directory":
            continue
        abs_path = (MANIFEST_DIR / tmpl).resolve()
        paths.add(str(abs_path))
        # dev-config и config могут иметь одинаковые basename — basename-индекс для гейта (b)
    return paths


def _registered_basenames() -> set[str]:
    """Basenames of all registered single-file templates (для volume-source матчинга)."""
    with open(MANIFEST) as f:
        manifest = yaml.safe_load(f)
    names: set[str] = set()
    for entry in manifest.get("templates", []):
        if entry.get("type") == "directory":
            continue
        names.add(entry.get("template", "").rsplit("/", 1)[-1])
    return names


# ── (a) every *.template registered ───────────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_all_template_files_registered(caplog) -> None:
    """Every *.template file in the repo must be registered in template-manifest (U-47)."""
    registered = _registered_template_paths()
    unregistered: list[str] = []

    for p in sorted(ROOT.rglob("*.template")):
        rel_parts = p.relative_to(ROOT).parts
        if any(part in _EXCLUDED_PARTS for part in rel_parts):
            continue
        # Только реальные файлы — docker bind-mount может создать пустые директории
        # с именем *.template (dev-config артефакты) — они не репо-контент
        if not p.is_file():
            logger.info("[IMP:8][template_coverage][a] skip non-file: %s", p.relative_to(ROOT).as_posix())
            continue
        if str(p.resolve()) not in registered:
            unregistered.append(p.relative_to(ROOT).as_posix())

    if unregistered:
        logger.error("[IMP:10][template_coverage][a] Unregistered *.template files: %s", unregistered)
        pytest.fail(
            f"{len(unregistered)} *.template file(s) not registered in template-manifest.yaml:\n"
            + "\n".join(f"  - {f}" for f in unregistered)
            + "\n\nAdd a `- template:` entry with the actual consumer (DevPlan 116 T6)."
        )

    logger.info("[IMP:9][template_coverage][a] PASS: all %d *.template files registered", len(registered))


# ── (b) nginx /etc/nginx/templates volume sources registered ─────────────────


@pytest.mark.gate
@ldd_trajectory
def test_nginx_template_volume_sources_registered(caplog) -> None:
    """Every nginx volume source mounted into /etc/nginx/templates/ must be registered."""
    with open(NGINX_COMPOSE) as f:
        compose = yaml.safe_load(f)

    registered_basenames = _registered_basenames()
    unregistered: list[str] = []

    for svc in (compose.get("services") or {}).values():
        for volume in svc.get("volumes") or []:
            if not isinstance(volume, str):
                continue
            # volume: "<source>:<target>:ro" — ищем target /etc/nginx/templates/*.conf.template
            parts = volume.split(":")
            if len(parts) < 2:
                continue
            target = parts[1]
            if not target.startswith("/etc/nginx/templates/"):
                continue
            # source может быть "${NGINX_CONF_DIR:-./config}/platform-http.conf" → берём basename
            source = parts[0]
            if "${" in source:
                source = source.split("}", 1)[-1].lstrip("/")
            basename = source.rsplit("/", 1)[-1]
            if basename not in registered_basenames:
                unregistered.append(f"{basename} (volume {volume!r})")

    if unregistered:
        logger.error("[IMP:10][template_coverage][b] Unregistered nginx template mounts: %s", unregistered)
        pytest.fail(
            f"{len(unregistered)} nginx /etc/nginx/templates volume source(s) not in template-manifest:\n"
            + "\n".join(f"  - {u}" for u in unregistered)
            + "\n\nRegister them (consumer: core/modules/nginx/docker-compose.base.yml, DevPlan 116 T6)."
        )

    logger.info("[IMP:9][template_coverage][b] PASS: all nginx /etc/nginx/templates volume sources registered")
