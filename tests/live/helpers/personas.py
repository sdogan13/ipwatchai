from __future__ import annotations

import os
from dataclasses import dataclass

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.auth import login_user
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import LiveConfig, load_live_config
from tests.live.helpers.test_accounts import ensure_managed_persona_account


PLAN_ALIASES = {
    "business": "professional",
}

FREE_PLANS = frozenset({"free"})
PAID_PLANS = frozenset({"starter", "professional", "enterprise", "superadmin"})
BUSINESS_PLANS = frozenset({"professional", "enterprise", "superadmin"})


@dataclass(frozen=True)
class PersonaSession:
    label: str
    config: LiveConfig
    client: LiveClient
    email: str
    plan: str
    display_name: str
    source: str
    user_id: str | None = None
    organization_id: str | None = None
    role: str | None = None
    is_superadmin: bool = False


def canonical_plan_name(plan_name: str | None) -> str:
    normalized = (plan_name or "free").strip().lower()
    return PLAN_ALIASES.get(normalized, normalized)


def build_live_config(base_config: LiveConfig, *, email: str, password: str) -> LiveConfig:
    return LiveConfig(
        base_url=base_config.base_url,
        timeout=base_config.timeout,
        email=email,
        password=password,
    )


def fetch_authenticated_json(
    client: LiveClient,
    reporter: LiveReporter,
    path: str,
    *,
    name: str,
    params: dict | None = None,
    expected_status: int = 200,
    record_success: bool = False,
):
    response = client.get(path, params=params)
    if response.status_code != expected_status:
        reporter.fail(f"{name} -> expected {expected_status}, got {response.status_code}: {response.text[:200]}")
        reporter.record(name, False, response.text[:200])
        return None

    try:
        payload = response.json()
    except ValueError:
        reporter.fail(f"{name} -> response was not valid JSON")
        reporter.record(name, False, "invalid json")
        return None

    reporter.ok(f"{name} -> {expected_status}")
    if record_success:
        reporter.record(name, True)
    return payload


def _has_explicit_credentials(email_env: str, password_env: str) -> bool:
    return bool(os.environ.get(email_env) and os.environ.get(password_env))


def _build_session(
    *,
    label: str,
    reporter: LiveReporter,
    config: LiveConfig,
    source: str,
) -> PersonaSession | None:
    client = LiveClient(config)
    if not login_user(
        client,
        reporter,
        config.email,
        config.password,
        name=f"{label} login",
    ):
        return None

    usage = fetch_authenticated_json(
        client,
        reporter,
        "/api/v1/usage/summary",
        name=f"{label} bootstrap usage summary",
    )
    if usage is None:
        return None

    profile = fetch_authenticated_json(
        client,
        reporter,
        "/api/v1/auth/me",
        name=f"{label} bootstrap profile",
    )
    if profile is None:
        return None

    plan_name = canonical_plan_name(usage.get("plan"))
    return PersonaSession(
        label=label,
        config=config,
        client=client,
        email=config.email,
        plan=plan_name,
        display_name=usage.get("display_name", plan_name.title()),
        source=source,
        user_id=profile.get("id"),
        organization_id=profile.get("organization_id"),
        role=profile.get("role"),
        is_superadmin=bool(profile.get("is_superadmin")),
    )


def _ensure_managed_persona_session(
    *,
    label: str,
    reporter: LiveReporter,
    plan_name: str,
) -> PersonaSession | None:
    managed = ensure_managed_persona_account(plan_name)
    config = build_live_config(
        load_live_config(),
        email=managed["email"],
        password=managed["password"],
    )
    session = _build_session(
        label=label,
        reporter=reporter,
        config=config,
        source=f"managed:{plan_name}",
    )
    if session is None:
        reporter.record(f"{label} managed persona", False, managed["email"])
        return None

    reporter.ok(f"{label} managed persona -> ready {managed['email']} ({session.plan})")
    reporter.record(f"{label} managed persona", True, managed["email"])
    return session


def resolve_free_persona_session(
    reporter: LiveReporter,
    *,
    label: str = "free user",
    email_env: str = "TEST_FREE_EMAIL",
    password_env: str = "TEST_FREE_PASSWORD",
) -> PersonaSession | None:
    resolution_name = f"{label} persona resolution"
    if _has_explicit_credentials(email_env, password_env):
        config = load_live_config(email_env=email_env, password_env=password_env)
        session = _build_session(
            label=label,
            reporter=reporter,
            config=config,
            source="explicit",
        )
        if session is None:
            return None
        if session.plan not in FREE_PLANS:
            reporter.fail(f"{resolution_name} -> expected free plan, got {session.plan}")
            reporter.record(resolution_name, False, f"expected free, got {session.plan}")
            return None
        reporter.ok(f"{resolution_name} -> {session.email} ({session.plan})")
        reporter.record(resolution_name, True, session.source)
        return session

    session = _ensure_managed_persona_session(
        label=label,
        reporter=reporter,
        plan_name="free",
    )
    if session is None:
        return None
    if session.plan not in FREE_PLANS:
        reporter.fail(f"{resolution_name} -> expected free plan, got {session.plan}")
        reporter.record(resolution_name, False, f"expected free, got {session.plan}")
        return None
    reporter.ok(f"{resolution_name} -> {session.email} ({session.plan}) [{session.source}]")
    reporter.record(resolution_name, True, session.source)
    return session


def resolve_plan_persona_session(
    reporter: LiveReporter,
    *,
    label: str,
    email_env: str,
    password_env: str,
    required_plans: set[str] | frozenset[str],
    fallback_to_default: bool = True,
    provision_plan: str | None = None,
) -> tuple[PersonaSession | None, bool]:
    resolution_name = f"{label} persona resolution"

    if _has_explicit_credentials(email_env, password_env):
        config = load_live_config(email_env=email_env, password_env=password_env)
        session = _build_session(
            label=label,
            reporter=reporter,
            config=config,
            source="explicit",
        )
        if session is None:
            return None, False
        if session.plan not in required_plans:
            reporter.fail(f"{resolution_name} -> expected one of {sorted(required_plans)}, got {session.plan}")
            reporter.record(resolution_name, False, f"expected {sorted(required_plans)}, got {session.plan}")
            return None, False
        reporter.ok(f"{resolution_name} -> {session.email} ({session.plan})")
        reporter.record(resolution_name, True, session.source)
        return session, False

    if fallback_to_default:
        default_session = _build_session(
            label=label,
            reporter=reporter,
            config=load_live_config(),
            source="default",
        )
        if default_session and default_session.plan in required_plans:
            reporter.ok(f"{resolution_name} -> using default account {default_session.email} ({default_session.plan})")
            reporter.record(resolution_name, True, default_session.source)
            return default_session, False

    if provision_plan is not None:
        provisioned = _ensure_managed_persona_session(
            label=label,
            reporter=reporter,
            plan_name=provision_plan,
        )
        if provisioned is not None:
            reporter.ok(f"{resolution_name} -> provisioned {provisioned.email} ({provisioned.plan})")
            reporter.record(resolution_name, True, provisioned.source)
            return provisioned, False
        return None, False

    reason = f"set {email_env}/{password_env} for {label}"
    reporter.warn(f"{resolution_name} -> skipped ({reason})")
    reporter.record(resolution_name, True, f"skipped: {reason}")
    return None, True
