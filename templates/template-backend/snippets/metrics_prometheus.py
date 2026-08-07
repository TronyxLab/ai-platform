# GREP_SUMMARY: snippets metrics prometheus make_asgi_app counter reference
# STRUCTURE: ┌Counter metric┐ → ◇ Вариант A (mount) → ◇ Вариант B (route generate_latest) → ⎋ reference
# region MODULE_CONTRACT
## @purpose  Reference: кастомные Prometheus-метрики (DevPlan 141 Q4). Базовый /metrics endpoint
##           уже есть в src/main.py (prometheus-client — обязательная зависимость); этот файл —
##           пример расширения: счётчики, гистограммы, mount-вариант.
## @scope    Backend projects с кастомными метриками (помимо стандартных python_*/process_*)
## @invariants
##   - Не подключается автоматически — копируется/адаптируется разработчиком
##   - Вариант B (route) — канон шаблона (src/main.py), Вариант A (mount) — альтернатива
## @rationale Prometheus-метрики — часть платформенного контракта monitoring (metrics: true)
# endregion MODULE_CONTRACT

"""Prometheus metrics — reference: кастомные метрики + mount (DevPlan 141 Q4)."""

from prometheus_client import Counter

# Пример кастомной метрики — используйте при необходимости (импорт в main.py)
REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "path"])


# ── Вариант A: mount на уровне ASGI-приложения (если /metrics НЕ определён как route) ──
# from prometheus_client import make_asgi_app
#
# metrics_app = make_asgi_app()
# app.mount("/metrics", metrics_app)


# ── Вариант B: route через generate_latest() — канон шаблона (src/main.py) ──
# from fastapi import Response
# from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
#
# @app.get("/metrics")
# async def metrics() -> Response:
#     """Prometheus metrics endpoint (prometheus-client обязателен, контракт monitoring)."""
#     return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
