"""
Tests for dashboard layout reorganization:
- Tab navigation in header
- Search as its own tab ("Arama")
- Overview ("Genel Bakis") as default tab
- Each tab content isolated
- Mobile navigation structure
"""
import pytest
from pathlib import Path

# ─── File paths ───────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
PARTIALS = TEMPLATES / "partials"
STATIC = ROOT / "static"


class TestDashboardHTML:
    """Tests for the main dashboard.html template."""

    def setup_method(self):
        self.html = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")

    def test_includes_all_partials(self):
        for partial in ["_navbar.html", "_search_panel.html", "_results_panel.html",
                        "_leads_panel.html", "_ai_studio_panel.html", "_reports_panel.html",
                        "_modals.html"]:
            assert partial in self.html, f"Missing include for {partial}"

    def test_tab_panel_enter_animation_css(self):
        assert ".tab-panel-enter" in self.html
        assert "@keyframes tabFadeIn" in self.html

    def test_nav_tab_btn_hover_css(self):
        assert ".nav-tab-btn:not(.bg-indigo-600):hover" in self.html

    def test_no_desktop_tabs_class_rule(self):
        """Old .desktop-tabs CSS rule should be removed."""
        assert ".desktop-tabs" not in self.html

    def test_mobile_bottom_bar_hidden_on_desktop(self):
        assert ".mobile-bottom-bar { display: none !important; }" in self.html

    def test_mobile_drawer_hidden_on_desktop(self):
        assert ".mobile-drawer-backdrop { display: none !important; }" in self.html


class TestNavbar:
    """Tests for the top navigation bar with inline tabs."""

    def setup_method(self):
        self.html = (PARTIALS / "_navbar.html").read_text(encoding="utf-8")

    def test_sticky_header(self):
        assert 'class="sticky top-0 z-50' in self.html

    def test_desktop_tabs_in_header(self):
        """Tab buttons should be inline in the header nav, not a separate bar."""
        assert 'role="tablist"' in self.html
        assert 'aria-label="Dashboard tabs"' in self.html

    def test_all_five_tab_buttons_exist(self):
        assert 'id="tab-btn-overview"' in self.html
        assert 'id="tab-btn-search"' in self.html
        assert 'id="tab-btn-opposition-radar"' in self.html
        assert 'id="tab-btn-ai-studio"' in self.html
        assert 'id="tab-btn-reports"' in self.html

    def test_overview_is_default_active_tab(self):
        """Overview tab button should start with active styling."""
        # Find the overview button and check it has bg-indigo-600
        idx = self.html.index('id="tab-btn-overview"')
        # Look backward to find the button tag
        btn_start = self.html.rfind("<button", 0, idx)
        btn_chunk = self.html[btn_start:idx + 200]
        assert "bg-indigo-600" in btn_chunk
        assert "text-white" in btn_chunk

    def test_search_tab_not_active_by_default(self):
        """Search tab button should NOT start with active styling."""
        idx = self.html.index('id="tab-btn-search"')
        btn_start = self.html.rfind("<button", 0, idx)
        btn_chunk = self.html[btn_start:idx + 200]
        assert "bg-indigo-600" not in btn_chunk

    def test_tab_buttons_call_showDashboardTab(self):
        assert "showDashboardTab('overview')" in self.html
        assert "showDashboardTab('search')" in self.html
        assert "showDashboardTab('opposition-radar')" in self.html
        assert "showDashboardTab('ai-studio')" in self.html
        assert "showDashboardTab('reports')" in self.html

    def test_pro_badge_on_opposition_radar(self):
        # Find the opposition-radar tab area and verify PRO badge
        idx = self.html.index('id="tab-btn-opposition-radar"')
        chunk = self.html[idx:idx + 700]
        assert "from-amber-500 to-orange-500" in chunk
        assert "tabs.pro" in chunk

    def test_new_badge_on_ai_studio(self):
        idx = self.html.index('id="tab-btn-ai-studio"')
        chunk = self.html[idx:idx + 700]
        assert "from-violet-500 to-purple-500" in chunk
        assert "tabs.new" in chunk

    def test_tabs_hidden_on_mobile(self):
        """Desktop tabs nav should be hidden on mobile via md:flex."""
        assert 'class="hidden md:flex items-center' in self.html

    def test_i18n_on_search_tab(self):
        assert "t('tabs.search')" in self.html

    # ─── Mobile Drawer ──────────────────────────────────
    def test_mobile_drawer_has_search_link(self):
        assert "showDashboardTab('search'); closeMobileDrawer();" in self.html

    def test_mobile_drawer_has_all_tabs(self):
        for tab in ['overview', 'search', 'opposition-radar', 'ai-studio', 'reports']:
            assert f"showDashboardTab('{tab}'); closeMobileDrawer();" in self.html

    # ─── Mobile Bottom Bar ──────────────────────────────
    def test_mobile_bottom_bar_has_five_buttons(self):
        assert 'id="bottom-tab-overview"' in self.html
        assert 'id="bottom-tab-search"' in self.html
        assert 'id="bottom-tab-radar"' in self.html
        assert 'id="bottom-tab-ai-studio"' in self.html
        assert 'id="bottom-tab-reports"' in self.html

    def test_bottom_bar_overview_is_default_active(self):
        """Overview bottom tab should start with primary color."""
        idx = self.html.index('id="bottom-tab-overview"')
        btn_start = self.html.rfind("<button", 0, idx)
        btn_chunk = self.html[btn_start:idx + 200]
        assert "color:var(--color-primary)" in btn_chunk

    def test_bottom_bar_search_not_active(self):
        """Search bottom tab should start with muted color."""
        idx = self.html.index('id="bottom-tab-search"')
        btn_start = self.html.rfind("<button", 0, idx)
        btn_chunk = self.html[btn_start:idx + 200]
        assert "color:var(--color-text-muted)" in btn_chunk

    def test_update_bottom_tab_active_maps_all_tabs(self):
        """updateBottomTabActive should map all 5 tabs."""
        assert "'overview': 'bottom-tab-overview'" in self.html
        assert "'search': 'bottom-tab-search'" in self.html
        assert "'opposition-radar': 'bottom-tab-radar'" in self.html
        assert "'ai-studio': 'bottom-tab-ai-studio'" in self.html
        assert "'reports': 'bottom-tab-reports'" in self.html


class TestSearchPanel:
    """Tests for the search panel as its own tab."""

    def setup_method(self):
        self.html = (PARTIALS / "_search_panel.html").read_text(encoding="utf-8")

    def test_has_tab_content_search_id(self):
        assert 'id="tab-content-search"' in self.html

    def test_hidden_by_default(self):
        """Search tab should start hidden (overview is default)."""
        idx = self.html.index('id="tab-content-search"')
        tag_start = self.html.rfind("<div", 0, idx)
        tag_chunk = self.html[tag_start:idx + 50]
        assert "hidden" in tag_chunk

    def test_no_old_desktop_tab_bar(self):
        """The old desktop tab bar should be removed."""
        assert "desktop-tabs" not in self.html
        assert 'id="tab-btn-overview"' not in self.html  # Old tab buttons

    def test_search_input_has_id(self):
        assert 'id="search-input"' in self.html

    def test_lightbox_outside_tab_content(self):
        """Lightbox modal should be outside the tab-content-search div."""
        # tab-content-search closes, then lightbox starts
        tab_close_idx = self.html.index("</div>\n\n<!-- Image Lightbox -->")
        assert tab_close_idx > 0

    def test_portfolio_modal_outside_tab_content(self):
        """Portfolio modal should be outside the tab-content-search div."""
        assert "<!-- Portfolio Modal" in self.html


class TestOverviewPanel:
    """Tests for the overview panel."""

    def setup_method(self):
        self.html = (PARTIALS / "_results_panel.html").read_text(encoding="utf-8")

    def test_has_tab_content_overview_id(self):
        assert 'id="tab-content-overview"' in self.html

    def test_visible_by_default(self):
        """Overview should NOT have hidden class (it's the default tab)."""
        idx = self.html.index('id="tab-content-overview"')
        tag_start = self.html.rfind("<main", 0, idx)
        tag_chunk = self.html[tag_start:idx + 50]
        assert "hidden" not in tag_chunk


class TestOtherPanels:
    """Tests for other tab panels — all should start hidden."""

    def test_leads_panel_hidden(self):
        html = (PARTIALS / "_leads_panel.html").read_text(encoding="utf-8")
        assert 'id="tab-content-opposition-radar"' in html
        idx = html.index('id="tab-content-opposition-radar"')
        tag_start = html.rfind("<div", 0, idx)
        tag_chunk = html[tag_start:idx + 60]
        assert "hidden" in tag_chunk

    def test_ai_studio_panel_hidden(self):
        html = (PARTIALS / "_ai_studio_panel.html").read_text(encoding="utf-8")
        assert 'id="tab-content-ai-studio"' in html
        idx = html.index('id="tab-content-ai-studio"')
        tag_start = html.rfind("<div", 0, idx)
        tag_chunk = html[tag_start:idx + 50]
        assert "hidden" in tag_chunk

    def test_reports_panel_hidden(self):
        html = (PARTIALS / "_reports_panel.html").read_text(encoding="utf-8")
        assert 'id="tab-content-reports"' in html
        idx = html.index('id="tab-content-reports"')
        tag_start = html.rfind("<div", 0, idx)
        tag_chunk = html[tag_start:idx + 50]
        assert "hidden" in tag_chunk


class TestAppJS:
    """Tests for the showDashboardTab function in app.js."""

    def setup_method(self):
        self.js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")

    def test_showDashboardTab_handles_search(self):
        assert "'search'" in self.js
        assert "'tab-content-search'" in self.js or "tab-content-' + tabId" in self.js

    def test_showDashboardTab_hides_all_five_panels(self):
        """All 5 panel IDs should be in the hide list."""
        assert "'overview'" in self.js
        assert "'search'" in self.js
        assert "'opposition-radar'" in self.js
        assert "'ai-studio'" in self.js
        assert "'reports'" in self.js

    def test_search_tab_in_title_map(self):
        assert "'search': 'Search'" in self.js

    def test_no_clear_search_on_search_tab(self):
        """Search results should NOT be cleared when switching TO search tab."""
        assert "if (tabId !== 'search')" in self.js

    def test_search_input_focus(self):
        """Search input should be focused when switching to search tab."""
        assert "search-input" in self.js
        assert "focus()" in self.js


class TestLocaleFiles:
    """Tests for i18n locale files — tabs.search key."""

    def test_english_has_search_tab(self):
        import json
        data = json.loads((STATIC / "locales" / "en.json").read_text(encoding="utf-8"))
        assert data["tabs"]["search"] == "Search"

    def test_turkish_has_search_tab(self):
        import json
        data = json.loads((STATIC / "locales" / "tr.json").read_text(encoding="utf-8"))
        assert data["tabs"]["search"] == "Arama"

    def test_arabic_has_search_tab(self):
        import json
        data = json.loads((STATIC / "locales" / "ar.json").read_text(encoding="utf-8"))
        assert data["tabs"]["search"] == "\u0628\u062d\u062b"

    def test_all_locales_have_same_tab_keys(self):
        import json
        en = json.loads((STATIC / "locales" / "en.json").read_text(encoding="utf-8"))
        tr = json.loads((STATIC / "locales" / "tr.json").read_text(encoding="utf-8"))
        ar = json.loads((STATIC / "locales" / "ar.json").read_text(encoding="utf-8"))
        assert set(en["tabs"].keys()) == set(tr["tabs"].keys()) == set(ar["tabs"].keys())


class TestNoSearchLeakage:
    """Ensure search elements don't appear outside the search tab."""

    def test_overview_has_no_search_input(self):
        html = (PARTIALS / "_results_panel.html").read_text(encoding="utf-8")
        assert "searchQuery" not in html
        assert "dashboardQuickSearch" not in html

    def test_leads_has_no_search_input(self):
        html = (PARTIALS / "_leads_panel.html").read_text(encoding="utf-8")
        assert "searchQuery" not in html
        assert "dashboardQuickSearch" not in html

    def test_ai_studio_has_no_search_input(self):
        html = (PARTIALS / "_ai_studio_panel.html").read_text(encoding="utf-8")
        assert "searchQuery" not in html
        assert "dashboardQuickSearch" not in html

    def test_reports_has_no_search_input(self):
        html = (PARTIALS / "_reports_panel.html").read_text(encoding="utf-8")
        assert "searchQuery" not in html
        assert "dashboardQuickSearch" not in html

    def test_search_panel_is_only_file_with_search_form(self):
        """Only _search_panel.html should contain the search form elements."""
        search_html = (PARTIALS / "_search_panel.html").read_text(encoding="utf-8")
        assert 'id="search-input"' in search_html
        for other in ["_results_panel.html", "_leads_panel.html",
                      "_ai_studio_panel.html", "_reports_panel.html"]:
            other_html = (PARTIALS / other).read_text(encoding="utf-8")
            assert 'id="search-input"' not in other_html


class TestTabContentIDs:
    """Verify all tab content panels have correct IDs."""

    def test_all_tab_content_ids_exist(self):
        """Each panel partial should have its expected tab-content-* id."""
        mapping = {
            "_search_panel.html": "tab-content-search",
            "_results_panel.html": "tab-content-overview",
            "_leads_panel.html": "tab-content-opposition-radar",
            "_ai_studio_panel.html": "tab-content-ai-studio",
            "_reports_panel.html": "tab-content-reports",
        }
        for filename, expected_id in mapping.items():
            html = (PARTIALS / filename).read_text(encoding="utf-8")
            assert f'id="{expected_id}"' in html, f"{filename} missing id={expected_id}"

    def test_only_overview_visible_by_default(self):
        """Only overview should be visible on initial load."""
        visible_tabs = []
        hidden_tabs = []
        mapping = {
            "_search_panel.html": "tab-content-search",
            "_results_panel.html": "tab-content-overview",
            "_leads_panel.html": "tab-content-opposition-radar",
            "_ai_studio_panel.html": "tab-content-ai-studio",
            "_reports_panel.html": "tab-content-reports",
        }
        for filename, tab_id in mapping.items():
            html = (PARTIALS / filename).read_text(encoding="utf-8")
            idx = html.index(f'id="{tab_id}"')
            # Find the opening tag that contains this id
            # Search backwards for the opening < of the tag
            tag_start = idx
            while tag_start > 0 and html[tag_start] != '<':
                tag_start -= 1
            # Extract the full opening tag (up to the >)
            tag_end = html.index('>', idx) + 1
            tag_chunk = html[tag_start:tag_end]
            # Check for "hidden" as a class word (not inside attribute values)
            # Split by quotes to get class attribute content
            has_hidden = False
            if 'class="' in tag_chunk:
                class_start = tag_chunk.index('class="') + 7
                class_end = tag_chunk.index('"', class_start)
                classes = tag_chunk[class_start:class_end].split()
                has_hidden = "hidden" in classes

            if has_hidden:
                hidden_tabs.append(tab_id)
            else:
                visible_tabs.append(tab_id)

        assert visible_tabs == ["tab-content-overview"], \
            f"Expected only overview visible, got: {visible_tabs}"
        assert set(hidden_tabs) == {
            "tab-content-search",
            "tab-content-opposition-radar",
            "tab-content-ai-studio",
            "tab-content-reports",
        }
