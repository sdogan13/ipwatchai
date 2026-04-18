from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
APP_ASSETS = ROOT / "app_assets.py"


def test_admin_feature_page_loads_canonical_script_url():
    html = (TEMPLATES / "admin" / "page.html").read_text(encoding="utf-8")
    assert "{% include 'admin/partials/_overview.html' %}" in html
    assert '<script src="/static/js/admin/panel.js"></script>' in html


def test_admin_feature_bundle_contains_panel_bootstrap():
    script = (STATIC / "js" / "admin" / "panel.js").read_text(encoding="utf-8")
    assert "function adminPanel()" in script
    assert "function adminOverview()" in script


def test_asset_routes_render_canonical_feature_templates():
    source = APP_ASSETS.read_text(encoding="utf-8")
    assert 'name="marketing/landing.html"' in source
    assert 'name="dashboard/page.html"' in source
    assert 'name="admin/page.html"' in source
    assert 'name="billing/pricing.html"' in source
    assert 'name="billing/checkout.html"' in source


def test_pricing_feature_page_keeps_checkout_links():
    html = (TEMPLATES / "billing" / "pricing.html").read_text(encoding="utf-8")
    assert 'x-data="pricingPage()"' in html
    assert 'href="/checkout?plan=free&billing=monthly"' in html


def test_checkout_feature_page_keeps_checkout_bootstrap():
    html = (TEMPLATES / "billing" / "checkout.html").read_text(encoding="utf-8")
    assert 'x-data="checkoutPage()"' in html
    assert "function checkoutPage()" in html


def test_dashboard_feature_page_loads_canonical_dashboard_script_url():
    html = (TEMPLATES / "dashboard" / "page.html").read_text(encoding="utf-8")
    assert "{% include 'dashboard/partials/_navbar.html' %}" in html
    assert '<script src="/static/js/dashboard/app.js?v=54"></script>' in html


def test_dashboard_feature_bundle_contains_dashboard_bootstrap():
    script = (STATIC / "js" / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert "function dashboard()" in script


def test_landing_feature_page_loads_canonical_landing_script_url():
    html = (TEMPLATES / "marketing" / "landing.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/marketing/landing.js?v=34"></script>' in html
    assert 'x-text="t(\'landing.nav_pricing\')"' in html


def test_landing_feature_bundle_contains_landing_bootstrap():
    script = (STATIC / "js" / "marketing" / "landing.js").read_text(encoding="utf-8")
    assert "function landing()" in script
