"""
Regional billing catalog resolution.

Prices and Stripe Price IDs are configured through BILLING_REGION_CATALOG_JSON.
This module merges that deploy-time catalog with local development defaults and
the existing entitlement definitions.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping, Optional

from config.settings import settings
from utils.subscription import CREDIT_PACKS, PLAN_FEATURES

logger = logging.getLogger(__name__)

REGION_UK = "UK"
REGION_EU = "EU"
REGION_TR = "TR"
DEFAULT_REGION = REGION_UK
SUPPORTED_REGIONS = (REGION_UK, REGION_EU, REGION_TR)

EU_EEA_CH_COUNTRIES = {
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
    "IS",
    "LI",
    "NO",
    "CH",
}

COUNTRY_NAME_TO_CODE = {
    "gb": "GB",
    "uk": "GB",
    "united kingdom": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
    "tr": "TR",
    "turkey": "TR",
    "turkiye": "TR",
    "türkiye": "TR",
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "spain": "ES",
    "italy": "IT",
    "ireland": "IE",
    "netherlands": "NL",
    "switzerland": "CH",
    "norway": "NO",
    "iceland": "IS",
    "liechtenstein": "LI",
}

DEFAULT_REGION_CATALOG: Dict[str, Dict[str, Any]] = {
    REGION_UK: {
        "provider": "stripe",
        "currency": "GBP",
        "plans": {
            "free": {"price_monthly": 0, "price_annual_monthly": 0, "stripe_price_monthly": "", "stripe_price_annual": ""},
            "starter": {"price_monthly": 29, "price_annual_monthly": 23, "stripe_price_monthly": "", "stripe_price_annual": ""},
            "professional": {"price_monthly": 99, "price_annual_monthly": 79, "stripe_price_monthly": "", "stripe_price_annual": ""},
            "enterprise": {"price_monthly": 249, "price_annual_monthly": 199, "stripe_price_monthly": "", "stripe_price_annual": ""},
        },
        "credit_packs": {
            "small": {"price": 5, "stripe_price": ""},
            "medium": {"price": 15, "stripe_price": ""},
            "large": {"price": 60, "stripe_price": ""},
        },
    },
    REGION_EU: {
        "provider": "stripe",
        "currency": "EUR",
        "plans": {
            "free": {"price_monthly": 0, "price_annual_monthly": 0, "stripe_price_monthly": "", "stripe_price_annual": ""},
            "starter": {"price_monthly": 34, "price_annual_monthly": 27, "stripe_price_monthly": "", "stripe_price_annual": ""},
            "professional": {"price_monthly": 119, "price_annual_monthly": 95, "stripe_price_monthly": "", "stripe_price_annual": ""},
            "enterprise": {"price_monthly": 299, "price_annual_monthly": 239, "stripe_price_monthly": "", "stripe_price_annual": ""},
        },
        "credit_packs": {
            "small": {"price": 6, "stripe_price": ""},
            "medium": {"price": 18, "stripe_price": ""},
            "large": {"price": 70, "stripe_price": ""},
        },
    },
    REGION_TR: {
        "provider": "iyzico",
        "currency": "TRY",
        "plans": {
            "free": {"price_monthly": 0, "price_annual_monthly": 0},
            "starter": {"price_monthly": 499, "price_annual_monthly": 399},
            "professional": {"price_monthly": 1499, "price_annual_monthly": 1199},
            "enterprise": {"price_monthly": 3999, "price_annual_monthly": 3199},
        },
        "credit_packs": {
            "small": {"price": 200},
            "medium": {"price": 800},
            "large": {"price": 4000},
        },
    },
}


class BillingCatalogError(ValueError):
    """Raised when a requested catalog entry cannot be used for checkout."""


def normalize_region(region: Optional[str]) -> str:
    value = (region or "").strip().upper()
    return value if value in SUPPORTED_REGIONS else DEFAULT_REGION


def normalize_country(country: Optional[str]) -> Optional[str]:
    value = (country or "").strip()
    if not value or value.upper() in {"XX", "T1", "UNKNOWN"}:
        return None
    upper = value.upper()
    if len(upper) == 2 and upper.isalpha():
        return upper
    return COUNTRY_NAME_TO_CODE.get(value.casefold())


def region_for_country(country: Optional[str]) -> str:
    country_code = normalize_country(country)
    if country_code == "TR":
        return REGION_TR
    if country_code in EU_EEA_CH_COUNTRIES:
        return REGION_EU
    if country_code == "GB":
        return REGION_UK
    return DEFAULT_REGION


def region_from_headers(headers: Mapping[str, str]) -> str:
    for header_name in ("CF-IPCountry", "CloudFront-Viewer-Country", "X-Country-Code", "X-App-Country"):
        region = region_for_country(headers.get(header_name))
        if region != DEFAULT_REGION or normalize_country(headers.get(header_name)) == "GB":
            return region
    return DEFAULT_REGION


def select_billing_region(
    *,
    explicit_region: Optional[str] = None,
    organization_country: Optional[str] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> str:
    if explicit_region:
        return normalize_region(explicit_region)
    if organization_country:
        return region_for_country(organization_country)
    if headers:
        return region_from_headers(headers)
    return DEFAULT_REGION


def _merge_dict(base: Dict[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_region_catalog(settings_obj=settings) -> Dict[str, Dict[str, Any]]:
    catalog = deepcopy(DEFAULT_REGION_CATALOG)
    raw = getattr(getattr(settings_obj, "billing", None), "region_catalog_json", "") or ""
    if not raw.strip():
        return catalog
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("Invalid BILLING_REGION_CATALOG_JSON; falling back to defaults")
        return catalog
    if not isinstance(parsed, Mapping):
        logger.error("BILLING_REGION_CATALOG_JSON must be a JSON object; falling back to defaults")
        return catalog
    for region, values in parsed.items():
        region_key = str(region or "").strip().upper()
        if region_key not in catalog or not isinstance(values, Mapping):
            continue
        catalog[region_key] = _merge_dict(catalog[region_key], values)
    return catalog


def _public_plan(plan_id: str, region_catalog: Mapping[str, Any], include_private: bool) -> Dict[str, Any]:
    entitlements = PLAN_FEATURES[plan_id]
    configured = dict(region_catalog.get("plans", {}).get(plan_id, {}))
    plan = {
        "id": plan_id,
        "name": entitlements.get("name", plan_id.title()),
        "price_monthly": configured.get("price_monthly", entitlements.get("price_monthly", 0)),
        "price_annual_monthly": configured.get("price_annual_monthly", entitlements.get("price_annual_monthly", 0)),
        "entitlements": entitlements,
    }
    if include_private:
        plan["stripe_price_monthly"] = configured.get("stripe_price_monthly", "")
        plan["stripe_price_annual"] = configured.get("stripe_price_annual", "")
    else:
        plan["stripe_price_monthly_configured"] = bool(configured.get("stripe_price_monthly"))
        plan["stripe_price_annual_configured"] = bool(configured.get("stripe_price_annual"))
    return plan


def _public_pack(pack_id: str, region_catalog: Mapping[str, Any], include_private: bool) -> Dict[str, Any]:
    legacy = CREDIT_PACKS[pack_id]
    configured = dict(region_catalog.get("credit_packs", {}).get(pack_id, {}))
    pack = {
        "id": pack_id,
        "name": legacy.get("name", pack_id.title()),
        "label_key": legacy.get("label_key", ""),
        "credits": legacy["credits"],
        "price": configured.get("price", legacy.get("price_try", 0)),
    }
    if include_private:
        pack["stripe_price"] = configured.get("stripe_price", "")
    else:
        pack["stripe_price_configured"] = bool(configured.get("stripe_price"))
    return pack


def get_billing_catalog(
    region: Optional[str] = None,
    *,
    organization_country: Optional[str] = None,
    headers: Optional[Mapping[str, str]] = None,
    include_private: bool = False,
    settings_obj=settings,
) -> Dict[str, Any]:
    selected_region = select_billing_region(
        explicit_region=region,
        organization_country=organization_country,
        headers=headers,
    )
    catalogs = load_region_catalog(settings_obj)
    region_catalog = catalogs[selected_region]
    public_plan_ids = [plan_id for plan_id in ("free", "starter", "professional", "enterprise") if plan_id in PLAN_FEATURES]
    pack_ids = [pack_id for pack_id in ("small", "medium", "large") if pack_id in CREDIT_PACKS]
    return {
        "region": selected_region,
        "provider": region_catalog["provider"],
        "currency": region_catalog["currency"],
        "region_options": [
            {"id": REGION_UK, "label_key": "pricing.region_uk"},
            {"id": REGION_EU, "label_key": "pricing.region_eu"},
            {"id": REGION_TR, "label_key": "pricing.region_tr"},
        ],
        "plans": {plan_id: _public_plan(plan_id, region_catalog, include_private) for plan_id in public_plan_ids},
        "credit_packs": [_public_pack(pack_id, region_catalog, include_private) for pack_id in pack_ids],
    }


def get_catalog_plan(catalog: Mapping[str, Any], plan_name: str) -> Dict[str, Any]:
    plan = catalog.get("plans", {}).get(plan_name)
    if not plan:
        raise BillingCatalogError("Invalid subscription plan")
    return dict(plan)


def get_catalog_pack(catalog: Mapping[str, Any], pack_id: str) -> Dict[str, Any]:
    for pack in catalog.get("credit_packs", []):
        if pack.get("id") == pack_id:
            return dict(pack)
    raise BillingCatalogError("Invalid credit pack")


def stripe_price_for_plan(catalog: Mapping[str, Any], plan_name: str, billing_period: str) -> str:
    plan = get_catalog_plan(catalog, plan_name)
    key = "stripe_price_annual" if billing_period == "annual" else "stripe_price_monthly"
    price_id = (plan.get(key) or "").strip()
    if not price_id:
        raise BillingCatalogError("Stripe Price ID is not configured for this plan and region")
    return price_id


def stripe_price_for_pack(catalog: Mapping[str, Any], pack_id: str) -> str:
    pack = get_catalog_pack(catalog, pack_id)
    price_id = (pack.get("stripe_price") or "").strip()
    if not price_id:
        raise BillingCatalogError("Stripe Price ID is not configured for this credit pack and region")
    return price_id


def list_region_codes() -> Iterable[str]:
    return SUPPORTED_REGIONS
