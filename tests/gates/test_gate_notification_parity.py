# GREP_SUMMARY: gate notification-parity resolve-chat-id severity grafana contact-points catalog events call-sites 003
# STRUCTURE: ▶ B2: SEVERITY_CHAT_ENV (notifications.py) ↔ contact-points.yml (severity routes + chatid env) → ◇ B4: каталог ↔ call-sites (core/*.py event= / --event; workflows event:) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Parity-гейты уведомлений (DevPlan 003 B2/B4):
##           (B2) ЕДИНЫЙ SoT severity→chat (notifications.SEVERITY_CHAT_ENV) сверяется с
##           Grafana contact-points.yml — закрывает двойной SoT и TRAP о fallback-чате;
##           (B4) notification-catalog.yaml — код шлёт ТОЛЬКО зарегистрированные события.
## @scope    tests/gates — статический анализ core/internal/shared/notifications.py,
##           core/modules/monitoring/config/alerting/contact-points.yml,
##           core/notification-catalog.yaml, core/internal/**/*.py, .github/workflows/*.yml.
## @invariants
##   - B2: для каждого severity из SEVERITY_CHAT_ENV (critical/warning) contact-points.yml
##     обязан иметь receiver с chatid-плейсхолдером ${SEVERITY_CHAT_ENV[severity]}
##   - B2: severity-роуты contact-points (critical/warning) ⊆ ключей SEVERITY_CHAT_ENV
##   - B4: каждый event-id из call-sites (python event="…" / --event "…", workflows event:)
##     присутствует в core/notification-catalog.yaml (used ⊆ catalog)
##   - B4: неиспользуемые события каталога — WARN (non-blocking, планируемые события)
##   - R5: негативы — event вне каталога RED (детектор ловит самовольные id)
## @rationale  Два несвязанных механизма (Grafana vs Python) без parity-гейта дрейфуют
##             (DevPlan 003 аудит: 4 из 6 отправителей без severity). B2 — словарь, B4 —
##             реестр: статическая сверка дешевле runtime-инфраструктуры (S5 не делаем).
## @changes  2026-08-16 | DevPlan 003 B2/B4 — created
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT: pathlib.Path = repo_root()

NOTIFICATIONS_PY: pathlib.Path = ROOT / "core" / "internal" / "shared" / "notifications.py"
CONTACT_POINTS: pathlib.Path = ROOT / "core" / "modules" / "monitoring" / "config" / "alerting" / "contact-points.yml"
CATALOG: pathlib.Path = ROOT / "core" / "notification-catalog.yaml"

# event-id паттерны call-sites
_EVENT_ATTR_RE = re.compile(r'event="([a-z0-9._-]+)"')
_EVENT_CLI_RE = re.compile(r'"--event"\s*,\s*"([a-z0-9._-]+)"')
_WF_EVENT_RE = re.compile(r"^\s*event:\s*([a-z0-9._-]+)\s*$", re.MULTILINE)

pytestmark = pytest.mark.gate


# region B2_PARITY


# 🧪 TRAP[TEST] · Regression · Scenario: Python SoT severity→chat ↔ Grafana contact-points.yml
# · Last fail: N/A (new — DevPlan 003 B2: закрывает двойной SoT severity→chat)
# · Remove if: severity-роутинг перестаёт быть SoT-контрактом
def test_notification_channels_parity(caplog) -> None:
    """B2: SEVERITY_CHAT_ENV (critical/warning) ↔ contact-points.yml (chatid + severity routes)."""
    caplog.set_level(logging.INFO)
    assert NOTIFICATIONS_PY.is_file() and CONTACT_POINTS.is_file(), "SoT-файлы обязательны (B2)"

    # ── Python SoT: SEVERITY_CHAT_ENV из notifications.py ──
    src = NOTIFICATIONS_PY.read_text(encoding="utf-8")
    m = re.search(r"SEVERITY_CHAT_ENV:\s*Mapping\[str,\s*str\]\s*=\s*\{(.*?)\}", src, re.DOTALL)
    assert m, "SEVERITY_CHAT_ENV отсутствует в notifications.py (SoT B2)"
    severity_env = dict(re.findall(r'"(critical|warning|info)"\s*:\s*"(TELEGRAM_CHAT_ID(?:_[A-Z_]+)?)"', m.group(1)))
    for required in ("critical", "warning", "info"):
        assert required in severity_env, f"SoT severity неполон (missing {required}): {severity_env}"

    # ── Grafana contact-points.yml ──
    data = yaml.safe_load(CONTACT_POINTS.read_text(encoding="utf-8")) or {}
    receivers: dict[str, str] = {}
    for cp in data.get("contactPoints", []) or []:
        for recv in cp.get("receivers", []) or []:
            chatid = (recv.get("settings", {}) or {}).get("chatid", "")
            receivers[cp.get("name", "")] = str(chatid)
    # маршруты severity из policies (matcher: severity="critical" — кавычки снимаются)
    routed: set[str] = set()
    for policy in data.get("policies", []) or []:
        for route in policy.get("routes", []) or []:
            for matcher in route.get("matchers", []) or []:
                if matcher.startswith("severity="):
                    routed.add(matcher.split("=", 1)[1].strip('"'))

    # Инвариант: critical/warning — один канал на severity в обоих SoT
    for severity in ("critical", "warning"):
        env_var = severity_env[severity]
        assert any(f"${{{env_var}}}" in chatid for chatid in receivers.values()), (
            f"Grafana contact-points не читает {env_var} (SoT severity={severity})"
        )
    assert routed <= set(severity_env), f"Grafana severity-роуты вне SoT: {routed - set(severity_env)}"
    logger.info(
        "[IMP:9][parity][B2] severity→chat parity PASS (severity_env=%s, grafana_routes=%s)", severity_env, routed
    )


# endregion B2_PARITY


# region B4_CATALOG


# 🧪 TRAP[TEST] · Regression · Scenario: каталог парсится и содержит версию + events
# · Last fail: N/A (new — DevPlan 003 B4)
# · Remove if: notification-catalog.yaml контракт меняется
def test_notification_catalog_valid(caplog) -> None:
    """B4: catalog.yaml — валидный YAML, version 1, уникальные event ids."""
    caplog.set_level(logging.INFO)
    data = yaml.safe_load(CATALOG.read_text(encoding="utf-8")) or {}
    assert data.get("version") == 1
    events = data.get("events", [])
    assert isinstance(events, list) and events, "events обязателен"
    ids = [e.get("id") for e in events if isinstance(e, dict)]
    assert all(isinstance(i, str) and re.fullmatch(r"[a-z0-9._-]+", i) for i in ids), f"невалидные id: {ids}"
    assert len(ids) == len(set(ids)), "дубликаты id в каталоге"
    logger.info("[IMP:9][parity][B4] catalog valid PASS (%d events)", len(ids))


# 🧪 TRAP[TEST] · Regression · Scenario: код шлёт только зарегистрированные события (used ⊆ catalog)
# · Last fail: N/A (new — DevPlan 003 B4: «код шлёт только зарегистрированные события»)
# · Remove if: событийный контракт отменяется
def test_notification_events_registered(caplog) -> None:
    """B4: каждый event-id из call-sites (python + workflows) присутствует в каталоге."""
    caplog.set_level(logging.INFO)
    data = yaml.safe_load(CATALOG.read_text(encoding="utf-8")) or {}
    catalog_ids = {e.get("id") for e in data.get("events", []) if isinstance(e, dict)}

    used: set[str] = set()
    # Python call-sites: event="…" в Notification/notify_event + "--event", "…" в subprocess CLI
    for py_file in sorted((ROOT / "core").rglob("*.py")):
        if "test" in py_file.parts:
            continue
        text = py_file.read_text(encoding="utf-8")
        used |= set(_EVENT_ATTR_RE.findall(text))
        used |= set(_EVENT_CLI_RE.findall(text))
    # Workflows: event: <id> в notify-telegram шагах (и inline-комментарии не считаются)
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        used |= set(_WF_EVENT_RE.findall(wf.read_text(encoding="utf-8")))

    unknown = used - catalog_ids
    assert not unknown, (
        f"RED: незарегистрированные события: {sorted(unknown)} (добавь в core/notification-catalog.yaml)"
    )
    unused = catalog_ids - used
    if unused:
        logger.warning(
            "[IMP:8][parity][B4] событий в каталоге без call-sites (планируемые/документированные): %s", sorted(unused)
        )
    logger.info("[IMP:9][parity][B4] events parity PASS (used=%d, catalog=%d)", len(used), len(catalog_ids))


# 🧪 TRAP[TEST] · Regression · R5-негатив: детектор ловит самовольный event вне каталога
# · Scenario: «событие» ci.rogue не в каталоге → RED (детектор жив)
# · Last fail: N/A (new — DevPlan 003 B4)
# · Remove if: событийный контракт отменяется
def test_negative_unregistered_event_detected(tmp_path, caplog) -> None:
    """R5: event="ci.rogue" (вне каталога) детектируется как unknown (RED-путь жив)."""
    caplog.set_level(logging.INFO)
    data = yaml.safe_load(CATALOG.read_text(encoding="utf-8")) or {}
    catalog_ids = {e.get("id") for e in data.get("events", []) if isinstance(e, dict)}
    assert "ci.rogue" not in catalog_ids, "precondition: rogue-событие не в каталоге"
    sample = 'notify_event(Notification(event="ci.rogue", ...))'
    detected = set(_EVENT_ATTR_RE.findall(sample)) | set(_EVENT_CLI_RE.findall(sample))
    assert detected == {"ci.rogue"}, f"детектор не поймал rogue-событие: {detected}"
    assert detected - catalog_ids, "R5 FAIL: детектор бы не отличил rogue от каталога"
    logger.info("[IMP:9][parity][B4][negative] unregistered-event detector PASS")


# endregion B4_CATALOG
