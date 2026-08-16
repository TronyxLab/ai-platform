MODULE_NAME := minio
CONTAINER := minio
include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile — minio lifecycle through module.mk
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides zero-boilerplate lifecycle for all docker modules;
# ·   minio has no custom backup/restore logic (uses S3-level backup)
# · Rev: when module needs custom behavior → override targets after include
