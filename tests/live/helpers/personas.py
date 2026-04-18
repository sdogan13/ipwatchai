from __future__ import annotations

import os
import time
from dataclasses import dataclass
from uuid import uuid4

from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.auth import _rate_limit_wait_seconds, login_user
from tests.live.helpers.client import LiveClient
from tests.live.helpers.config import DEFAULT_PASSWORD, LiveConfig, load_live_config


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


def _provision_free_persona(
    *,
    label: str,
    reporter: LiveReporter,
    email_prefix: str,
    organization_prefix: str,
) -> PersonaSession | None:
    base_config = load_live_config()
    email = f"{email_prefix}-{uuid4().hex[:10]}@example.com"
    password = os.environ.get("TEST_FREE_PASSWORD", DEFAULT_PASSWORD)
    config = build_live_config(base_config, email=email, password=password)
    client = LiveClient(config)

    payload = {
        "email": email,
        "password": password,
        "first_name": "Live",
        "last_name": "Free",
        "organization_name": f"{organization_prefix}-{uuid4().hex[:8]}",
        "lang": "en",
    }
    name = f"{label} register"
    response = None
    for attempt in range(1, 5 + 1):
        response = client.post(
            "/api/v1/auth/register",
            json_data=payload,
            token=False,
        )
        if response.status_code != 429 or attempt == 5:
            break

        wait_seconds = _rate_limit_wait_seconds(response)
        reporter.warn(f"{name} -> 429 rate limited, retrying in {int(wait_seconds)}s (attempt {attempt}/5)")
        time.sleep(wait_seconds)

    if response.status_code != 200:
        reporter.fail(f"{name} -> {response.status_code}: {response.text[:200]}")
        reporter.record(name, False, response.text[:200])
        return None

    try:
        token_pair = response.json()
    except ValueError:
        reporter.fail(f"{name} -> invalid JSON response")
        reporter.record(name, False, "invalid json")
        return None

    access_token = token_pair.get("access_token")
    if not access_token:
        reporter.fail(f"{name} -> missing access_token")
        reporter.record(name, False, "missing access_token")
        return None

    client.token = access_token
    reporter.ok(f"{name} -> created {email}")
    reporter.record(name, True)

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
        email=email,
        plan=plan_name,
        display_name=usage.get("display_name", plan_name.title()),
        source="registered",
        user_id=profile.get("id"),
        organization_id=profile.get("organization_id"),
        role=profile.get("role"),
        is_superadmin=bool(profile.get("is_superadmin")),
    )


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

    session = _provision_free_persona(
        label=label,
        reporter=reporter,
        email_prefix="live-free",
        organization_prefix="Live Free Org",
    )
    if session is None:
        return None
    if session.plan not in FREE_PLANS:
        reporter.fail(f"{resolution_name} -> expected free plan after registration, got {session.plan}")
        reporter.record(resolution_name, False, f"expected free, got {session.plan}")
        return None
    reporter.ok(f"{resolution_name} -> {session.email} ({session.plan}) [{session.source}]")
    reporter.record(resolution_name, True, session.source)
    return session


def _try_superadmin_provision(
    *,
    reporter: LiveReporter,
    label: str,
    required_plans: set[str] | frozenset[str],
    target_plan: str,
) -> PersonaSession | None:
    if not _has_explicit_credentials("TEST_SUPERADMIN_EMAIL", "TEST_SUPERADMIN_PASSWORD"):
        return None

    super_config = load_live_config(
        email_env="TEST_SUPERADMIN_EMAIL",
        password_env="TEST_SUPERADMIN_PASSWORD",
    )
    super_session = _build_session(
        label=f"{label} superadmin provisioner",
        reporter=reporter,
        config=super_config,
        source="superadmin",
    )
    if super_session is None:
        return None
    if not super_session.is_superadmin:
        reporter.fail(f"{label} superadmin provisioner -> authenticated user is not superadmin")
        reporter.record(f"{label} superadmin provisioner", False, "not superadmin")
        return None

    provisioned = _provision_free_persona(
        label=label,
        reporter=reporter,
        email_prefix=f"live-{target_plan}",
        organization_prefix=f"Live {target_plan.title()} Org",
    )
    if provisioned is None or not provisioned.organization_id:
        return None

    change_name = f"{label} plan upgrade"
    response = super_session.client.put(
        f"/api/v1/admin/organizations/{provisioned.organization_id}/plan",
        data={"plan_name": target_plan},
    )
    if response.status_code != 200:
        reporter.fail(f"{change_name} -> {response.status_code}: {response.text[:200]}")
        reporter.record(change_name, False, response.text[:200])
        return None

    reporter.ok(f"{change_name} -> upgraded org {provisioned.organization_id} to {target_plan}")
    reporter.record(change_name, True)

    upgraded_session = _build_session(
        label=label,
        reporter=reporter,
        config=provisioned.config,
        source=f"provisioned:{target_plan}",
    )
    if upgraded_session is None:
        return None
    if upgraded_session.plan not in required_plans:
        reporter.fail(f"{label} upgraded persona -> expected {sorted(required_plans)}, got {upgraded_session.plan}")
        reporter.record(
            f"{label} upgraded persona",
            False,
            f"expected one of {sorted(required_plans)}, got {upgraded_session.plan}",
        )
        return None
    return upgraded_session


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

    if provision_plan is not None and _has_explicit_credentials("TEST_SUPERADMIN_EMAIL", "TEST_SUPERADMIN_PASSWORD"):
        provisioned = _try_superadmin_provision(
            reporter=reporter,
            label=label,
            required_plans=required_plans,
            target_plan=provision_plan,
        )
        if provisioned is not None:
            reporter.ok(f"{resolution_name} -> provisioned {provisioned.email} ({provisioned.plan})")
            reporter.record(resolution_name, True, provisioned.source)
            return provisioned, False
        return None, False

    reason = (
        f"set {email_env}/{password_env} or configure TEST_SUPERADMIN_EMAIL/"
        f"TEST_SUPERADMIN_PASSWORD for {provision_plan or label} provisioning"
    )
    reporter.warn(f"{resolution_name} -> skipped ({reason})")
    reporter.record(resolution_name, True, f"skipped: {reason}")
    return None, True
