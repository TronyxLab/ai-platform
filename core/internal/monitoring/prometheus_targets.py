#!/usr/bin/env python3
# GREP_SUMMARY: monitoring prometheus-targets file-sd target-json metrics-enabled labels node-targets node-exporter cadvisor exporters multi-node placement di-seam
# STRUCTURE: ▶ generate_prometheus_target(config) → ◇ metrics_enabled? → ⊕ {targets,labels} JSON → ⎋ RenderResult
#           ▶ generate_node_targets(nodes, output_dir) → ◇ placement data? → ⊕ nodes/*.json (8 file_sd jobs, job_name 1:1) → ⎋ RenderResult
# region MODULE_CONTRACT
## @purpose  Prometheus file-based service discovery target generator — extracted from
##           monitoring_config_renderer.py (DevPlan 117 G T54). + DevPlan 010 T3.3:
##           multi-node remote target renderer (node-exporter/cadvisor/service-exporters
##           по placement-нодам; static→file_sd миграция с сохранением job_name 1:1).
## @scope    Consumed by monitoring_config_renderer.main() (lazy import). Non-fatal.
##           generate_node_targets — DI-seam для provision/emission (другой агент Волны 2/3).
## @invariants
##   - JSON schema: {"targets": ["<project>:<port>"], "labels": {"project","type","node","service"}}
##   - Skips if metrics_enabled is False (status="noop")
##   - Non-fatal: file write failure → logged, continue
##   - DevPlan 010 T3.3: node targets пишутся в <targets_dir>/nodes/*.json — ОТДЕЛЬНАЯ
##     поддиректория от проектных *.json (glob job platform-projects = /prometheus-targets/*.json
##     не должен подхватывать нодовые файлы). Имена файлов = job_name 1:1 (node-exporter.json →
##     job "node-exporter") — дашборды/алерты зависят от job_name, ЛОВУШКА T3.3.
##   - single-node (nodes None/[]) → fallback Docker-DNS target'ы, байт-паритет прежнему
##     static_configs набору (инвариант 2 плана: single-node поведение не меняется)
##   - Экспортёры рендерятся по ПРИСУТСТВИЮ сервис-модуля на ноде (postgres→9187,
##     redis→9121, nginx→9113), а не по модулю service-exporters (singleton у сервисов §3;
##     S2/S3-фикстуры: data-1 публикует ТОЛЬКО 9187+9121, nginx-exporter — на apps-ноде)
##   - Идемпотентность: файл перезаписывается ТОЛЬКО при изменении содержимого
##   - Порт-литералы из shared/platform_ports.py (DevPlan 010 T2.2; инвариант порт-SoT)
## @rationale  DevPlan 117 G T54 — extracted verbatim (generate_prometheus_target, ~54 LOC).
##            DevPlan 010 T3.3 — центральный Prometheus скрейпит кросс-нодово (peer-порты
##            T2.2/T2.4), рендер file_sd по placement; single-node остаётся на локальных
##            Docker-DNS target'ах (байт-идентично статике).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
## @changes  2026-08-22 · DevPlan 010 T3.3 — +NodeInfo/generate_node_targets (multi-node file_sd)
# endregion MODULE_CONTRACT

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Канон пермов file_sd (018 W4, F-21c; класс NOTE-N5 017: prometheus(nobody) читатель) ──
# Рендер выполняется root (converge/core-deliver) — umask 077 даёт 0600/0700, prometheus
# (uid 65534) получает Permission denied и МОЛЧА теряет job'ы. Нормализация безусловная
# (даже при byte-skip) — лечит уже созданные файлы без ручного chmod.
_TARGET_FILE_MODE = 0o644  # root:platform (setgid-каталог), others-read — никто не читает содержимое секретов
_TARGETS_SUBDIR_MODE = 0o2775  # setgid + group-writable (канон bootstrap: users.py 2775)

# Dual-import pattern (monitoring_config_renderer L43-52): core.* path under pytest/python3 -m,
# parent-dir bootstrap for direct-script invocation.
try:
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        ProjectMonitoringConfig,
        RenderResult,
    )
    from monitoring.constants import DEFAULT_PROMETHEUS_TARGETS_DIR  # pyright: ignore[reportImplicitRelativeImport]
except ImportError:  # pragma: no cover — direct-script invocation path
    _INTERNAL_DIR = str(Path(__file__).resolve().parent.parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    # W2 T2.6 (DevPlan 136, латентный класс A): канон config_renderer.py — корень репо
    # (fallback добавляет И core/internal/ для top-level monitoring-импортов, И корень
    # для core.internal.* — единый документированный канон self-bootstrap).
    _PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        ProjectMonitoringConfig,
        RenderResult,
    )
    from monitoring.constants import DEFAULT_PROMETHEUS_TARGETS_DIR  # pyright: ignore[reportImplicitRelativeImport]

logger = logging.getLogger(__name__)


# ── DevPlan 010 T3.3: remote node targets (multi-node) ──────────────────────
# Порт-литералы — ТОЛЬКО из shared/platform_ports.py (инвариант: порт-SoT, DevPlan 010 T2.2).
from core.internal.shared.platform_ports import (
    CADVISOR,
    LANGFUSE_REDIS_EXPORTER,
    NGINX_EXPORTER,
    NODE_EXPORTER,
    PGBOUNCER_EXPORTER,
    PLATFORM_PORT_MINIO,
    POSTGRES_EXPORTER,
    REDIS_EXPORTER,
)


# region DATACLASS_NodeInfo
@dataclass(frozen=True)
class NodeInfo:
    """Placement node descriptor for multi-node target rendering (DevPlan 010 T3.3 DI-seam).

    ## @purpose  Входной контракт generate_node_targets: форма {name, host, modules[]}.
    ##            Данные приходят от provision/emission (другой агент Волны 2/3, placement.yaml
    ##            резолв core/internal/shared/placement.py) — рендерер не знает источник.
    ## @invariants
    ##   - name — имя ноды (data-1 / agent-1 / apps-1)
    ##   - host — приватный адрес ноды (RFC1918/100.64; VPN-аттестация — инвариант 7 плана)
    ##   - modules — размещённые на ноде модули платформы (имена module.yaml канона)
    ##   - frozen: контрактный объект, мутация запрещена
    """

    name: str
    host: str
    modules: tuple[str, ...] = field(default_factory=tuple)


# endregion DATACLASS_NodeInfo


# region CONSTANTS_NODE_TARGET_JOBS
@dataclass(frozen=True)
class _NodeTargetJob:
    """Один file_sd job рендера нод (внутренний контракт, не экспортируется).

    ## @purpose  Таблица file_sd jobs рендера нод (5 базовых T3.3 + 3 honesty REF-0010):
    ##            имя файла (= job_name 1:1), scrape-порт, labels (байт-паритет прежним
    ##            static_configs), условие размещения, single-node fallback target (Docker-DNS).
    """

    file_name: str  # "node-exporter.json" — job_name 1:1 (ЛОВУШКА T3.3)
    port: int  # scrape-порт из platform_ports
    labels: dict[str, str]  # service/component — паритет статике prometheus.yml.tmpl
    required_module: str | None  # None → все ноды (node-metrics all-nodes)
    local_target: str  # single-node fallback: Docker-DNS target


# job_name 1:1 с прежними static_configs (node-exporter, cadvisor, postgres-exporter,
# redis-exporter, nginx-exporter) — ЛОВУШКА T3.3: переименование молча ломает
# дашборды (infrastructure.json) и алерты, селекторящие по job.
# REF-0010 добавил pgbouncer-exporter/langfuse-redis-exporter/minio (новые jobs,
# ретроспективно 1:1 со static-дефолтами prometheus.yml.tmpl и alert-rules селекторами).
_NODE_TARGET_JOBS: tuple[_NodeTargetJob, ...] = (
    _NodeTargetJob(
        file_name="node-exporter.json",
        port=NODE_EXPORTER,
        labels={"service": "node-exporter", "component": "host-monitor"},
        required_module=None,  # node-metrics — all-nodes (каждая нода)
        local_target="node-exporter:9100",
    ),
    _NodeTargetJob(
        file_name="cadvisor.json",
        port=CADVISOR,
        labels={"service": "cadvisor", "component": "container-monitor"},
        required_module=None,  # node-metrics — all-nodes
        local_target="cadvisor:8080",
    ),
    _NodeTargetJob(
        file_name="postgres-exporter.json",
        port=POSTGRES_EXPORTER,
        labels={"service": "postgres", "component": "database"},
        required_module="postgres",  # service-exporters singleton у сервисов (§3 плана)
        local_target="postgres-exporter:9187",
    ),
    _NodeTargetJob(
        file_name="redis-exporter.json",
        port=REDIS_EXPORTER,
        labels={"service": "redis", "component": "cache"},
        required_module="redis",
        local_target="redis-exporter:9121",
    ),
    _NodeTargetJob(
        file_name="nginx-exporter.json",
        port=NGINX_EXPORTER,
        labels={"service": "nginx", "component": "reverse-proxy"},
        required_module="nginx",
        local_target="nginx-prometheus-exporter:9113",
    ),
    # ── REF-0010 (2026-08-24): honesty-jobs — фасад БД, очередь langfuse, S3 ──
    # required_module-gated: модуль выключен → targets=[] → up-серия отсутствует →
    # алерт НЕ firing (нет ложной сирены на нодах без модуля).
    _NodeTargetJob(
        file_name="pgbouncer-exporter.json",
        port=PGBOUNCER_EXPORTER,
        labels={"service": "pgbouncer", "component": "connection-pool"},
        required_module="postgres",  # service-exporters singleton у postgres (§3 плана)
        local_target="pgbouncer-exporter:9127",
    ),
    _NodeTargetJob(
        file_name="langfuse-redis-exporter.json",
        port=LANGFUSE_REDIS_EXPORTER,
        labels={"service": "langfuse-redis", "component": "ingestion-queue"},
        required_module="langfuse",  # exporter co-located с langfuse (langfuse compose)
        local_target="langfuse-redis-exporter:9121",
    ),
    _NodeTargetJob(
        file_name="minio.json",
        port=PLATFORM_PORT_MINIO,
        labels={"service": "minio", "component": "object-storage"},
        required_module="minio",  # опциональный модуль: выключен → targets=[] → тишина
        local_target="minio:9000",
    ),
)
# endregion CONSTANTS_NODE_TARGET_JOBS


# region FUNC_generate_prometheus_target
def generate_prometheus_target(
    config: ProjectMonitoringConfig,
    output_dir: Path | None = None,
) -> RenderResult:
    """Generate Prometheus file-based service discovery target JSON.

    ## @purpose  Create project target JSON for Prometheus file_sd_config.
    ##           Skips if metrics_enabled is False.
    ## @io
    ##   ⇥ config: ProjectMonitoringConfig — resolved monitoring config
    ##   ⇥ output_dir: Path — output directory (default: platform_root/prometheus-targets)
    ##   ⎋ RenderResult — outcome with status and output path
    ## @complexity O(1)
    ## @invariants
    ##   - JSON schema: {"targets": ["<project>:<port>"], "labels": {"project", "type", "node", "service"}}
    ##   - Creates output directory if missing
    ##   - Non-fatal: file write failure → logged, continue
    """
    if not config.metrics_enabled:
        logger.info("[IMP:8][prometheus] Metrics disabled for %s — skipping Prometheus target", config.project_name)
        return RenderResult(component="prometheus", status="noop", detail="metrics_enabled=False")

    port = config.metrics_port
    targets_dir = output_dir or (config.platform_root / DEFAULT_PROMETHEUS_TARGETS_DIR)
    targets_dir.mkdir(parents=True, exist_ok=True)

    target_file = targets_dir / f"{config.project_name}.json"
    target: dict[str, object] = {
        "targets": [f"{config.project_name}:{port}"],
        "labels": {
            "project": config.project_name,
            "type": config.project_type,
            "node": config.node_name,
            "service": config.project_name,
        },
    }
    # 018 W4 (F-21c): file_sd формат — СПИСОК групп []*targetgroup.Group; одиночный объект
    # = "json: cannot unmarshal object" в prometheus (проверено на ноде 2026-08-29).
    # dict-значения инвариантны: аннотация на target (не на list-обёртку).
    target_payload = [target]

    try:
        target_file.write_text(json.dumps(target_payload, indent=2), encoding="utf-8")
        Path(target_file).chmod(_TARGET_FILE_MODE)
        logger.info("[IMP:9][prometheus] Prometheus target file generated: %s (port=%d)", target_file, port)
        return RenderResult(
            component="prometheus",
            status="created",
            output_path=target_file,
            detail=f"targets=[{config.project_name}:{port}]",
        )
    except OSError as e:
        logger.info("[IMP:6][prometheus] Failed to write Prometheus target file %s: %s", target_file, e)
        return RenderResult(component="prometheus", status="failed", detail=str(e))


# endregion FUNC_generate_prometheus_target


# region FUNC__write_target_file_if_changed
def _write_target_file_if_changed(path: Path, payload: dict[str, object]) -> bool:
    """Write target JSON only when content differs; chmod ALWAYS (018 W4 perm normalization).

    ## @purpose  Идемпотентная запись file_sd target-файла: байт-идентичное содержимое →
    ##            skip (noop), иначе перезапись. Возвращает True при записи.
    ## @io       ⇥ path: Path — целевой JSON-файл
    ##           ⇥ payload: dict — {targets, labels}
    ##           ⎋ bool — True если СОДЕРЖИМОЕ записано, False если байт-идентично (skip)
    ## @raises   OSError — пробрасывается вызывающему (RenderResult "failed")
    ## @complexity O(P) где P = размер содержимого
    ## @invariants
    ##   - Повторный рендер тех же данных → False по содержимому (skip записи);
    ##     пермы нормализуются ВСЕГДА (0644 — прометей(nobody) читатель, 018 W4)
    ##   - Пустые target'ы ([] — сервис/нода ушла) записываются — stale target'ы не застревают
    ##   - Формат файла — СПИСОК групп [payload] (prometheus file_sd: []*targetgroup.Group;
    ##     одиночный объект = unmarshal-error, 018 W4 F-21c)
    """
    content = json.dumps([payload], indent=2)
    changed = not (path.exists() and path.read_text(encoding="utf-8") == content)
    if changed:
        path.write_text(content, encoding="utf-8")
    # 018 W4: пермы нормализуются БЕЗУСЛОВНО (byte-skip не должен сохранять 0600-легаси —
    # prometheus(nobody) с umask-077-рендера получал Permission denied молча)
    Path(path).chmod(_TARGET_FILE_MODE)
    if changed:
        logger.info("[IMP:9][prometheus] Node target file %s written", path.name)
    return changed


# endregion FUNC__write_target_file_if_changed


# region FUNC_generate_node_targets
def generate_node_targets(
    nodes: list[NodeInfo] | None,
    output_dir: Path,
    deployed_modules: frozenset[str] | None = None,
) -> RenderResult:
    """Render Prometheus file_sd node targets for multi-node placement (DevPlan 010 T3.3).

    ## @purpose  Рендер 5-и file_sd jobs нод (node-exporter/cadvisor — все ноды;
    ##            postgres/redis/nginx-exporter — ноды, размещающие сервис-модуль) в
    ##            <output_dir>/nodes/*.json. Идемпотентен; single-node (nodes None/[]) —
    ##            fallback Docker-DNS target'ы, байт-паритет прежнему static_configs набору.
    ## 🧐 TRAP[DI-SEAM] · 2026-08-22 · — · nodes — DI-вход (аргумент функции), данные придут
    ## · от provision/emission (другой агент) · Rejected: чтение placement.yaml внутри рендера
    ## · Reason: рендерер не знает источник топологии (placement.yaml / node-configs /
    ## ·   будущий emission); seam = тестируемость (S3-фикстура напрямую) + ноль связности
    ## · Rev: появление второго потребителя рендера нод → общий загрузчик placement в shared/
    ## @io       ⇥ nodes: list[NodeInfo] | None — placement-ноды {name, host, modules[]};
    ##           None/[] → single-node fallback (статический набор Docker-DNS target'ов)
    ##           ⇥ output_dir: Path — prometheus-targets каталог (родитель nodes/); на VPS
    ##           = ${PROMETHEUS_TARGETS_DIR} (platform_root/prometheus-targets), mount :112
    ##           ⇥ deployed_modules: frozenset[str] | None — включённые модули ноды
    ##           (single-node fallback только): None → полный легаси-набор; набор →
    ##           honesty-гейтинг REF-0010 (required_module не включён → targets=[] —
    ##           up-серии нет, алерт молчит; 018 W4: MinioScrapeDown на ноде без minio)
    ##           ⎋ RenderResult — "created" (≥1 файл записан) / "noop" (все байт-идентичны) /
    ##           "failed" (OSError)
    ## @complexity O(J × N) где J = 8 jobs, N = число нод
    ## @invariants
    ##   - Файлы: <output_dir>/nodes/<job_name>.json; поддиректория nodes/ ОБЯЗАТЕЛЬНА —
    ##     иначе job platform-projects (glob /prometheus-targets/*.json) подхватит нодовые
    ##     target'ы и будет скрейпить их как проектные (дубли + неверные labels)
    ##   - job_name 1:1 (файл node-exporter.json → job "node-exporter") — ЛОВУШКА T3.3
    ##   - Экспортёры — по сервис-модулю ноды (postgres/redis/nginx), НЕ по модулю
    ##     service-exporters (S2/S3: data-1 публикует 9187+9121, nginx-exporter на apps-ноде)
    ##   - Идемпотентность: повторный рендер с теми же входными данными → файлы не меняются
    ##   - Пустые target'ы (нода ушла/сервис переехал) → файл перезаписывается с [] —
    ##     устаревшие target'ы не застревают в file_sd
    ##   - Non-fatal: OSError → RenderResult "failed", остальные файлы НЕ рендерятся
    ## @rationale Миграция static→file_sd (T3.3): центральный Prometheus скрейпит кросс-нодовые
    ##            порты (T2.2/T2.4), target'ы — из placement. Single-node = fallback (без
    ##            placement файлы не пишутся разве что в single-node-наборе — см. fallback).
    """
    nodes_dir = output_dir / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    Path(nodes_dir).chmod(_TARGETS_SUBDIR_MODE)  # 018 W4: umask-077 mkdir даёт 0700 — nobody не читает

    # ── Вход: single-node (нет placement-данных) → статический Docker-DNS набор ──
    if not nodes:
        # 018 W4: honesty-гейтинг REF-0010 — модуль не включён → targets=[] (up-серии нет,
        # алерт молчит). deployed_modules=None → полный легаси-набор (back-compat прямых вызовов).
        mods = frozenset(deployed_modules) if deployed_modules is not None else None
        entries: dict[str, list[str]] = {}
        for job in _NODE_TARGET_JOBS:
            gated_off = mods is not None and job.required_module is not None and job.required_module not in mods
            entries[job.file_name] = [] if gated_off else [job.local_target]
        logger.info(
            "[IMP:8][prometheus] No node placement data — single-node fallback targets (deployed_modules=%s)",
            "None(legacy-full)" if mods is None else sorted(mods),
        )
    else:
        logger.info("[IMP:9][prometheus] Rendering node targets for %d node(s)", len(nodes))
        entries = {}
        for job in _NODE_TARGET_JOBS:
            targets = [
                f"{node.host}:{job.port}"
                for node in nodes
                if job.required_module is None or job.required_module in node.modules
            ]
            entries[job.file_name] = targets
            logger.info(
                "[IMP:8][prometheus] %s targets (module=%s): %s",
                job.file_name,
                job.required_module or "all-nodes",
                targets,
            )

    # ── Запись: идемпотентная (skip при байт-идентичном содержимом) ─────────────
    written = 0
    for job in _NODE_TARGET_JOBS:
        payload: dict[str, object] = {
            "targets": entries[job.file_name],
            "labels": dict(job.labels),
        }
        path = nodes_dir / job.file_name
        try:
            if _write_target_file_if_changed(path, payload):
                written += 1
        except OSError as e:
            logger.info("[IMP:6][prometheus] Failed to write node target %s: %s", path, e)
            return RenderResult(component="prometheus", status="failed", detail=str(e))

    detail = "nodes=none" if not nodes else ",".join(node.host for node in nodes)
    status = "noop" if written == 0 else "created"
    logger.info("[IMP:9][prometheus] Node targets render %s (%d file(s) written)", status, written)
    return RenderResult(
        component="prometheus",
        status=status,
        output_path=nodes_dir,
        detail=detail,
    )


# endregion FUNC_generate_node_targets
