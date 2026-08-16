#!/usr/bin/env python3
# GREP_SUMMARY: provider-registry cert-provider acme dns-01 registry per-domain resolver allowlist credentials http01
# STRUCTURE: ┌certs-providers.yaml SoT┐ → load_registry (кэш) → ○ resolve_provider (longest-suffix) → ⊕ provider_env (allowlist) → ⎋
# region MODULE_CONTRACT
## @purpose  Модульный реестр DNS-провайдеров для выпуска Let's Encrypt сертификатов (DevPlan 154 W1,
##           вариант C Brief 154 S1): декларативный каталог провайдеров (certs-providers.yaml),
##           per-domain резолв (node.yaml#acme_dns_plugins → #acme_dns_plugin), строгий allowlist кредов.
## @scope    Потребители: cert_orchestrator.py (резолв провайдера + env кредов per-domain).
##           Реестр — единственный источник имён провайдеров; неизвестное имя → ConfigValidationError(4).
## @invariants
##   1. load_registry() кэширует по resolved-пути (idempotent, повторный вызов = тот же объект)
##   2. resolve_provider(): longest-suffix match по acme_dns_plugins (foo.asiteam.ru → asiteam.ru);
##      fallback — acme_dns_plugin (обратная совместимость single-plugin); неизвестное имя → raise (fail-fast)
##   3. provider_env(): ТОЛЬКО имена из provider.creds (allowlist) — посторонние ключи secrets.env не уходят
##   4. all_cred_names(): объединение creds всех провайдеров — для фильтра _source_secrets_env
##   5. mode: env | inject | http01; inject = webnames-паттерн (инъекция+shred в issue_cert.py)
##   6. Нет I/O в функциях резолва (чистые функции; YAML I/O — только в load_registry)
## @rationale Языковая политика: бизнес-логика реестра — Python; YAML — данные (новый провайдер = запись,
##           не код). Fail-fast на неизвестном провайдере вместо тихого generic-fallback (TRAP 154 W1).
## @changes 2026-08-12 | DevPlan 154 W1 — создан
# endregion MODULE_CONTRACT

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]

from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

# ── Default SoT: реестр рядом с модулем (доставляется с core/) ──────────
_DEFAULT_REGISTRY = Path(__file__).resolve().parent / "certs-providers.yaml"

# Режимы кредов (инвариант 5)
MODE_ENV = "env"
MODE_INJECT = "inject"
MODE_HTTP01 = "http01"


# region DATACLASSES


@dataclass(frozen=True)
class ProviderConfig:
    """Декларативная запись провайдера из реестра.

    ## @purpose — Неизменяемая конфигурация одного DNS-провайдера.
    ## @io — ⇥ поля реестра → ⎋ frozen dataclass
    ## @complexity — O(1)
    """

    name: str
    plugin: str | None
    mode: str
    creds: tuple[str, ...] = field(default_factory=tuple)
    dnsapi_ext: bool = False
    note: str = ""


@dataclass(frozen=True)
class CertProviderRegistry:
    """Каталог провайдеров + per-domain резолв.

    ## @purpose — Неизменяемый каталог после загрузки; резолв — чистые функции.
    ## @io — ⇥ YAML-реестр → ⎋ registry; resolve_provider() → ProviderConfig
    ## @complexity — O(P + D*K) где P=провайдеры, D=домены, K=ключи plugins_map
    """

    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    source: str = ""

    # region FUNC_resolve_provider
    def resolve_provider(
        self,
        domain: str,
        node_plugin: str = "",
        plugins_map: dict[str, str] | None = None,
    ) -> ProviderConfig:
        """Резолв провайдера для домена: acme_dns_plugins (longest-suffix) → acme_dns_plugin → raise.

        ▶ ┌domain + node.yaml acme-поля┐ → ○ plugins_map suffix-match → ◇ node_plugin → ⎋ ProviderConfig | ⊕ ConfigValidationError(4)

        ## @purpose — Единая точка выбора DNS-провайдера per-domain (инвариант 2).
        ## @io — ⇥ domain: str, node_plugin: str, plugins_map: dict[str,str]|None → ⎋ ProviderConfig
        ## @complexity — O(K) где K = ключи plugins_map
        ## @invariants
        ##   - longest-suffix: foo.asiteam.ru матчит ключ asiteam.ru (но НЕ asiteam.r)
        ##   - точное совпадение домена = самый длинный суффикс
        ##   - пустой plugins_map / нет совпадения → node_plugin (single-plugin fallback)
        ##   - имя вне реестра → ConfigValidationError(4) с перечнем доступных (fail-fast, TRAP 154)
        """
        name = ""
        if plugins_map:
            matched = self._longest_suffix_key(domain, list(plugins_map))
            if matched:
                name = plugins_map[matched]
                logger.info(
                    "[IMP:8][provider_registry] %s → provider '%s' (plugins_map key '%s')", domain, name, matched
                )
        if not name:
            name = node_plugin
            if name:
                logger.info("[IMP:8][provider_registry] %s → provider '%s' (node acme_dns_plugin)", domain, name)
        if not name:
            msg = (
                f"No DNS provider configured for domain '{domain}' — set node.yaml#acme_dns_plugin "
                f"or node.yaml#acme_dns_plugins"
            )
            raise ConfigValidationError(msg)
        provider = self.providers.get(name)
        if provider is None:
            msg = (
                f"Unknown cert provider '{name}' for domain '{domain}' — available: "
                f"{', '.join(sorted(self.providers))} (SoT: {self.source})"
            )
            raise ConfigValidationError(msg)
        logger.info(
            "[IMP:9][provider_registry] Resolved %s → provider '%s' (mode=%s, plugin=%s)",
            domain,
            provider.name,
            provider.mode,
            provider.plugin or "-",
        )
        return provider

    # endregion FUNC_resolve_provider

    # region FUNC_longest_suffix_key
    @staticmethod
    def _longest_suffix_key(domain: str, keys: list[str]) -> str:
        """Вернуть ключ из keys, являющийся самым длинным суффиксом domain (пусто — нет совпадения)."""
        best = ""
        for key in keys:
            if key and (domain == key or domain.endswith("." + key)) and len(key) > len(best):
                best = key
        return best

    # endregion FUNC_longest_suffix_key

    # region FUNC_provider_env
    @staticmethod
    def provider_env(provider: ProviderConfig, secrets: dict[str, str]) -> dict[str, str]:
        """Allowlist-креды провайдера из secrets (инвариант 3).

        ## @purpose — В env issue_cert уходят ТОЛЬКО имена из provider.creds — никакого
        ##            полного passthrough secrets.env (риск утечки S3/GHCR-кредов в acme.sh env).
        ## @io — ⇥ provider, secrets: dict → ⎋ dict (подмножество secrets)
        ## @complexity — O(C) где C = creds провайдера
        """
        result = {k: v for k, v in secrets.items() if k in provider.creds}
        logger.info(
            "[IMP:9][provider_registry] Provider '%s' env allowlist: %d/%d creds (names only)",
            provider.name,
            len(result),
            len(provider.creds),
        )
        return result

    # endregion FUNC_provider_env

    # region FUNC_all_cred_names
    def all_cred_names(self) -> set[str]:
        """Объединение имён кредов всех провайдеров (инвариант 4) — фильтр secrets.env."""
        names: set[str] = set()
        for p in self.providers.values():
            names.update(p.creds)
        logger.info(
            "[IMP:9][provider_registry] Allowed credential names across %d providers: %d",
            len(self.providers),
            len(names),
        )
        return names

    # endregion FUNC_all_cred_names

    # region FUNC_challenge_mode
    @staticmethod
    def challenge_mode(provider: ProviderConfig, env_mode: str = "dns") -> str:
        """ACME_CHALLENGE_MODE для провайдера: http01-провайдер принудительно http (инвариант 5).

        ## @purpose — http01-запись реестра не требует ACME_CHALLENGE_MODE вручную;
        ##            env_mode (dns/auto/http) сохраняет приоритет для остальных провайдеров.
        ## @io — ⇥ provider, env_mode → ⎋ "http" | env_mode
        ## @complexity — O(1)
        """
        if provider.mode == MODE_HTTP01:
            logger.info("[IMP:9][provider_registry] Provider '%s' → challenge 'http' (http01 mode)", provider.name)
            return "http"
        return env_mode

    # endregion FUNC_challenge_mode


# endregion DATACLASSES


# region LOADER


# region FUNC_load_registry
@functools.lru_cache(maxsize=4)
def load_registry(path: str | os.PathLike[str] | None = None) -> CertProviderRegistry:
    """Загрузить реестр провайдеров из YAML SoT (кэш по resolved-пути).

    ▶ ┌path?┐ → ○ resolve (default: рядом с модулем) → ⚡ yaml.safe_load → ○ normalize → ⎋ CertProviderRegistry

    ## @purpose — Единственная I/O-точка реестра (инвариант 6); lru_cache → idempotent.
    ## @io — ⇥ path: str|None → ⎋ CertProviderRegistry
    ## @complexity — O(P) — P = записей YAML
    ## @invariants
    ##   - default path: core/internal/bootstrap/certs-providers.yaml (доставляется с core/)
    ##   - отсутствие файла/битый YAML → ConfigValidationError(4) (fail-fast, не тихий пустой реестр)
    ##   - дубликат имени → последняя запись побеждает (warn)
    ##   - неизвестный mode → ConfigValidationError(4)
    """
    src = Path(path).resolve() if path else _DEFAULT_REGISTRY
    if not src.is_file():
        msg = f"Cert provider registry not found: {src}"
        raise ConfigValidationError(msg)
    try:
        data = cast(
            "dict[str, object] | None", yaml.safe_load(src.read_text(encoding="utf-8"))
        )  # W11-G3: yaml.safe_load → Any; YAML-граница
    except yaml.YAMLError as e:
        msg = f"Cert provider registry parse error ({src}): {e}"
        raise ConfigValidationError(msg) from e

    providers: dict[str, ProviderConfig] = {}
    # W11-G3: yaml.safe_load → Any; YAML-граница реестра (certs-providers.yaml)
    for entry in cast("list[dict[str, object]]", (data or {}).get("providers", [])):
        name = str(entry.get("name", ""))
        mode = str(entry.get("mode", ""))
        if not name or mode not in {MODE_ENV, MODE_INJECT, MODE_HTTP01}:
            msg = (
                f"Invalid provider entry in {src}: name={name!r} mode={mode!r} — "
                f"mode must be one of {MODE_ENV}/{MODE_INJECT}/{MODE_HTTP01}"
            )
            raise ConfigValidationError(msg)
        if name in providers:
            logger.warning("[IMP:7][provider_registry] Duplicate provider '%s' in %s — last wins", name, src)
        plugin_raw = entry.get("plugin")
        providers[name] = ProviderConfig(
            name=name,
            plugin=str(plugin_raw) if plugin_raw else None,
            mode=mode,
            creds=tuple(str(c) for c in cast("list[object]", entry.get("creds") or [])),
            dnsapi_ext=bool(entry.get("dnsapi_ext", False)),
            note=str(entry.get("note", "")),
        )
    if not providers:
        msg = f"Cert provider registry empty: {src}"
        raise ConfigValidationError(msg)

    registry = CertProviderRegistry(providers=providers, source=str(src))
    logger.info("[IMP:9][provider_registry] Loaded %d providers from %s", len(providers), src)
    return registry


# endregion FUNC_load_registry


# endregion LOADER
