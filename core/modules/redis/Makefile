# GREP_SUMMARY: redis Makefile start stop restart status logs help
# region MODULE_CONTRACT
## @purpose  Module lifecycle management for redis Docker module
## @scope    Called by platform users per role; sudo-whitelist enforces per-role restrictions
## @invariants
##   - All docker compose calls use docker-compose.base.yml
##   - env-file loaded from SECRETS_ENV_FILE (default: /run/platform/secrets.env)
##   - agent: NO stop — stop target guarded by sudo-whitelist
##   - No backup/restore — Redis data is ephemeral cache; persistence via AOF for restart recovery
##   - All targets log at IMP:7 minimum, critical paths at IMP:9
## @rationale Targets match standard module lifecycle; sudo-whitelist restricts agent from stop
# endregion MODULE_CONTRACT

MODULE_NAME := redis
COMPOSE_FILE = $(MODULE_DIR)/docker-compose.base.yml
CONTAINER := redis

include ../../templates/module.mk

# ── Override: custom start with redis-cli PONG readiness check ──

## start: Start redis container
start: ## Start redis (compose up -d)
	@echo "[IMP:7][redis-make][start] Starting redis"
	$(COMPOSE_CMD) up -d
	@echo "[IMP:8][redis-make][start] redis started — waiting for PONG..."
	@for i in $$(seq 1 15); do \
		if docker exec $(CONTAINER) redis-cli -h 127.0.0.1 ping 2>/dev/null | grep -q "PONG"; then \
			echo "[IMP:9][redis-make][start] redis ready — PONG"; \
			break; \
		fi; \
		echo "[IMP:7][redis-make][start] Waiting for PONG (attempt $$i/15)..."; \
		sleep 1; \
	done

# ── Override: custom status with redis PING ──

## status: Show container status with health
status: ## Show redis container status
	@echo "[IMP:7][redis-make][status] redis status"
	$(COMPOSE_CMD) ps
	@echo "[IMP:7][redis-make][status] Docker health status:"
	docker inspect --format='{{.State.Health.Status}}' $(CONTAINER) 2>/dev/null || echo "container not running"
	@echo "[IMP:7][redis-make][status] Redis PING:"
	docker exec $(CONTAINER) redis-cli -h 127.0.0.1 ping 2>/dev/null || echo "  not responding"
