# Project __PROJECT_NAME__

## Platform context
- Domain: __DOMAIN__
- CI/CD: __ORG_NAME__/ai-platform/.github/workflows/deploy-project.yml@main

## What platform provides
Run `grep PLATFORM_ .env.platform` for full service list.

## Commands
- `make sync-env` — regenerate .env.platform
- `make status`  — check deployment status

## DO NOT
- Edit `.env.platform` manually — it is auto-generated
- Store secrets in git
