"""
Browser smoke suite for public-facing journeys.

Run directly:
    python tests/browser/test_public_browser_smoke.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.auth_state import (
    create_verified_browser_account,
    delete_browser_test_account,
    lookup_email_verification_code,
    lookup_password_reset_code,
)
from tests.browser.helpers.config import load_browser_config
from tests.browser.helpers.session import launch_browser_page, open_url
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.config import DEFAULT_PASSWORD


CONFIG = load_browser_config()
REPORTER = LiveReporter()
FORGOT_SUCCESS_EMAIL = os.environ.get(
    "TEST_BROWSER_FORGOT_SUCCESS_EMAIL",
    "managed-browser-forgot-success@example.com",
)
REGISTRATION_EMAIL = os.environ.get(
    "TEST_BROWSER_REGISTER_EMAIL",
    "managed-browser-register@example.com",
)
I18N_ASSET_VERSION = "38"
pytestmark = pytest.mark.skip(reason="Browser E2E script; run directly with python tests/browser/test_public_browser_smoke.py")


def _get_body_state(page):
    return page.evaluate(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            const upgradeModal = document.getElementById('upgrade-modal');
            return {
                searchResults: state ? (state.searchResults || []).length : -1,
                searchError: state ? (state.searchError || '') : 'missing alpine state',
                searchLoading: state ? !!state.searchLoading : false,
                selectedClasses: state ? (state.selectedClasses || []) : [],
                imageName: state ? (state.imageName || '') : '',
                searchQuery: state ? (state.searchQuery || '') : '',
                searchView: state ? (state.searchView || '') : '',
                upgradeModalVisible: !!(upgradeModal && !upgradeModal.classList.contains('hidden'))
            };
        }"""
    )


def _retry_after_seconds(response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return 15.0


def _clear_rate_limit_artifacts(monitor, endpoint: str) -> None:
    monitor.console_errors = [
        error
        for error in monitor.console_errors
        if "status of 429" not in error
    ]
    monitor.request_failures = [
        failure
        for failure in monitor.request_failures
        if not (failure.startswith("429 ") and endpoint in failure)
    ]


def _clear_auth(page) -> None:
    page.goto(f"{CONFIG.base_url}/", wait_until="domcontentloaded", timeout=CONFIG.timeout_ms)
    page.evaluate(
        """() => {
            localStorage.removeItem('auth_token');
            localStorage.removeItem('access_token');
            localStorage.removeItem('refresh_token');
            sessionStorage.removeItem('auth_token');
            sessionStorage.removeItem('access_token');
            sessionStorage.removeItem('refresh_token');
        }"""
    )


def _submit_with_rate_limit_retry(page, monitor, endpoint: str, submit, *, success_statuses: tuple[int, ...]) -> object:
    response = None
    for attempt in range(1, 4):
        with page.expect_response(lambda candidate: endpoint in candidate.url, timeout=CONFIG.timeout_ms) as response_info:
            submit()
        response = response_info.value
        if response.status in success_statuses:
            return response
        if response.status == 429 and attempt < 3:
            _clear_rate_limit_artifacts(monitor, endpoint)
            time.sleep(_retry_after_seconds(response))
            continue
        break
    raise AssertionError(f"unexpected {endpoint} status: {response.status}")


def _wait_for_public_search_idle(page, timeout_ms: int | None = None) -> None:
    page.wait_for_function(
        "() => document.body._x_dataStack && document.body._x_dataStack[0] && !document.body._x_dataStack[0].searchLoading",
        timeout=timeout_ms or CONFIG.timeout_ms,
    )


def _submit_public_search_with_retry(page, monitor, trigger) -> object:
    return _submit_with_rate_limit_retry(
        page,
        monitor,
        "/api/v1/search/public",
        trigger,
        success_statuses=(200,),
    )


def _invoke_public_search(page) -> None:
    page.wait_for_function(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            return !!(state && typeof state.publicSearch === 'function');
        }""",
        timeout=CONFIG.timeout_ms,
    )
    page.evaluate(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            if (!state || typeof state.publicSearch !== 'function') {
                throw new Error('landing publicSearch unavailable');
            }
            state.publicSearch();
        }"""
    )


def _assert_public_search_success(page, *, timeout_ms: int | None = None) -> dict:
    _wait_for_public_search_idle(page, timeout_ms=timeout_ms)
    state = _get_body_state(page)
    if state["searchError"]:
        raise AssertionError(f"unexpected public search error: {state['searchError']}")
    if state["searchResults"] <= 0:
        raise AssertionError(f"expected public search results > 0, got {state['searchResults']}")
    return state


def _build_valid_public_search_png() -> bytes:
    image = Image.new("RGB", (2, 2), color=(99, 102, 241))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _read_locale_bundle(locale: str) -> dict:
    return json.loads((ROOT / "static" / "locales" / f"{locale}.json").read_text(encoding="utf-8"))


def _seed_locale_bundle_cache(page, locale: str) -> None:
    page.evaluate(
        """({ locale, bundle, version }) => {
            localStorage.setItem('app_locale', locale);
            localStorage.setItem('app_locale_bundle::' + locale + '::v' + version, JSON.stringify(bundle));
        }""",
        {"locale": locale, "bundle": _read_locale_bundle(locale), "version": I18N_ASSET_VERSION},
    )


def _read_i18n_render_state(page, pattern: str, selector: str) -> dict:
    return page.evaluate(
        """({ pattern, selector }) => {
            const regex = new RegExp(pattern, 'g');
            return {
                ready: !!(window.AppI18n && window.AppI18n._ready),
                dir: document.documentElement.getAttribute('dir') || '',
                sample: (document.querySelector(selector)?.textContent || '').trim(),
                rawKeys: document.body.innerText.match(regex) || []
            };
        }""",
        {"pattern": pattern, "selector": selector},
    )


def main() -> None:
    REPORTER.print_heading("PUBLIC BROWSER SMOKE", server=CONFIG.base_url)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, CONFIG)
        try:
            run_browser_step(
                "landing page bootstrap",
                REPORTER,
                page,
                monitor,
                CONFIG,
                lambda: (
                    open_url(page, CONFIG, "/"),
                    page.locator("#search-input").wait_for(state="visible"),
                    "IPWatchAI" in page.title() or (_ for _ in ()).throw(AssertionError(f"unexpected title: {page.title()}")),
                ),
            )

            def landing_cached_locale_bootstrap() -> None:
                locale = "en"
                route_pattern = f"**/static/locales/{locale}.json?v=*"

                def delay_locale_request(route) -> None:
                    time.sleep(1.5)
                    route.continue_()

                open_url(page, CONFIG, "/")
                _seed_locale_bundle_cache(page, locale)
                page.route(route_pattern, delay_locale_request)
                try:
                    with page.expect_response(
                        lambda candidate: f"/static/locales/{locale}.json" in candidate.url,
                        timeout=CONFIG.timeout_ms,
                    ) as locale_response_info:
                        page.goto(f"{CONFIG.base_url}/", wait_until="domcontentloaded", timeout=CONFIG.timeout_ms)
                        page.wait_for_timeout(250)
                        landing_state = _read_i18n_render_state(
                            page,
                            r"(?:landing|auth|search)\.[\w_]+",
                            'nav button[x-text="t(\'auth.login_button\')"]',
                        )
                        if landing_state["dir"] != "ltr":
                            raise AssertionError(f"expected cached landing dir=ltr during delayed locale fetch, got {landing_state!r}")
                        if not landing_state["ready"]:
                            raise AssertionError(f"expected cached landing locale to be ready during delayed fetch, got {landing_state!r}")
                        if not landing_state["sample"] or landing_state["sample"].startswith("auth."):
                            raise AssertionError(f"unexpected cached landing text during delayed locale fetch: {landing_state!r}")
                        if landing_state["rawKeys"]:
                            raise AssertionError(f"unexpected raw landing keys during delayed locale fetch: {landing_state['rawKeys']}")

                    if locale_response_info.value.status != 200:
                        raise AssertionError(f"unexpected delayed landing locale response status: {locale_response_info.value.status}")
                finally:
                    page.unroute(route_pattern, delay_locale_request)

            run_browser_step(
                "landing cached locale bootstrap",
                REPORTER,
                page,
                monitor,
                CONFIG,
                landing_cached_locale_bootstrap,
            )

            def education_tab_bootstrap() -> None:
                open_url(page, CONFIG, "/?tab=education")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(
                            state &&
                            state.activeTab === 'education' &&
                            state.educationCatalog &&
                            Array.isArray(state.educationCatalog.categories) &&
                            state.educationCatalog.categories.length > 0 &&
                            Array.isArray(state.educationCatalog.pdfs) &&
                            state.educationCatalog.pdfs.length > 0 &&
                            Array.isArray(state.educationCatalog.flashcard_decks) &&
                            state.educationCatalog.flashcard_decks.length > 0 &&
                            Array.isArray(state.educationCatalog.quiz_sections) &&
                            state.educationCatalog.quiz_sections.length > 0 &&
                            !!state.educationSelectedCategoryId
                        );
                    }""",
                    timeout=max(CONFIG.timeout_ms, 60000),
                )
                counts = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        const orderedCategories = state && typeof state.getEducationCategories === 'function'
                            ? state.getEducationCategories()
                            : (state.educationCatalog && Array.isArray(state.educationCatalog.categories)
                                ? state.educationCatalog.categories
                                : []);
                        return {
                            categories: state.educationCatalog.categories.length,
                            pdfs: state.educationCatalog.pdfs.length,
                            flashcardDecks: state.educationCatalog.flashcard_decks.length,
                            quizSections: state.educationCatalog.quiz_sections.length,
                            selectedCategoryId: state.educationSelectedCategoryId || '',
                            firstOrderedCategoryId: orderedCategories.length ? String(orderedCategories[0].id || '') : '',
                            testerToolsVisible: !!(
                                document.querySelector('[data-testid="education-flashcard-tester-tools"]') ||
                                document.querySelector('[data-testid="education-quiz-tester-tools"]')
                            )
                        };
                    }"""
                )
                if counts["categories"] <= 0 or counts["pdfs"] <= 0 or counts["flashcardDecks"] <= 0 or counts["quizSections"] <= 0 or not counts["selectedCategoryId"]:
                    raise AssertionError(f"expected education catalog counts > 0, got {counts}")
                if counts["firstOrderedCategoryId"] != "genel" or counts["selectedCategoryId"] != "genel":
                    raise AssertionError(f"expected Genel to be the first and default education category, got {counts}")
                if counts["testerToolsVisible"]:
                    raise AssertionError(f"tester moderation controls should stay hidden for public visitors, got {counts}")
                first_mobile_category = page.locator('[data-testid^="education-mobile-category-"]').first.inner_text().strip()
                if first_mobile_category != "Genel":
                    raise AssertionError(f"expected Genel to be the first rendered mobile education category, got {first_mobile_category!r}")

            run_browser_step(
                "education tab bootstrap",
                REPORTER,
                page,
                monitor,
                CONFIG,
                education_tab_bootstrap,
            )

            def education_mobile_quick_access() -> None:
                page.set_viewport_size({"width": 390, "height": 844})
                try:
                    open_url(page, CONFIG, "/?tab=education")
                    page.locator('[data-testid="education-mobile-quick-quiz"]').wait_for(state="visible", timeout=CONFIG.timeout_ms)
                    page.locator('[data-testid="education-mobile-quick-pdfs"]').wait_for(state="visible", timeout=CONFIG.timeout_ms)
                    page.wait_for_function(
                        """() => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            return !!(state && state.educationMobileSection === 'quiz');
                        }""",
                        timeout=CONFIG.timeout_ms,
                    )

                    initial_visibility = page.evaluate(
                        """() => {
                            const isVisible = (id) => {
                                const node = document.getElementById(id);
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                return style.display !== 'none' && style.visibility !== 'hidden' && node.offsetParent !== null;
                            };
                            return {
                                quiz: isVisible('education-quiz-panel'),
                                flashcards: isVisible('education-flashcards-panel'),
                                pdfs: isVisible('education-pdf-library-panel')
                            };
                        }"""
                    )
                    if initial_visibility != {"quiz": True, "flashcards": False, "pdfs": False}:
                        raise AssertionError(f"expected quiz-only mobile workspace by default, got {initial_visibility}")

                    page.locator('[data-testid="education-mobile-quick-pdfs"]').click()
                    page.wait_for_function(
                        """() => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            const isVisible = (id) => {
                                const node = document.getElementById(id);
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                return style.display !== 'none' && style.visibility !== 'hidden' && node.offsetParent !== null;
                            };
                            return !!(
                                state &&
                                state.educationMobileSection === 'pdfs' &&
                                isVisible('education-pdf-library-panel') &&
                                !isVisible('education-quiz-panel') &&
                                !isVisible('education-flashcards-panel')
                            );
                        }""",
                        timeout=CONFIG.timeout_ms,
                    )

                    page.locator('[data-testid="education-mobile-nav-flashcards"]').click()
                    page.wait_for_function(
                        """() => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            const isVisible = (id) => {
                                const node = document.getElementById(id);
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                return style.display !== 'none' && style.visibility !== 'hidden' && node.offsetParent !== null;
                            };
                            return !!(
                                state &&
                                state.educationMobileSection === 'flashcards' &&
                                isVisible('education-flashcards-panel') &&
                                !isVisible('education-quiz-panel') &&
                                !isVisible('education-pdf-library-panel')
                            );
                        }""",
                        timeout=CONFIG.timeout_ms,
                    )

                    category_targets = page.evaluate(
                        """() => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            const categories = state && state.educationCatalog && Array.isArray(state.educationCatalog.categories)
                                ? state.educationCatalog.categories
                                : [];
                            const first = state ? String(state.educationSelectedCategoryId || '') : '';
                            const secondCategory = categories.find((category) => String(category.id || '') !== first);
                            return {
                                first,
                                second: secondCategory ? String(secondCategory.id || '') : ''
                            };
                        }"""
                    )
                    if not category_targets["first"] or not category_targets["second"]:
                        raise AssertionError(f"expected at least two education categories for mobile mode memory, got {category_targets}")

                    page.evaluate("window.scrollTo(0, 0)")
                    page.locator(f'[data-testid="education-mobile-category-{category_targets["second"]}"]').click()
                    page.wait_for_function(
                        """(secondId) => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            return !!(
                                state &&
                                state.educationSelectedCategoryId === secondId &&
                                state.educationMobileSection === 'flashcards'
                            );
                        }""",
                        arg=category_targets["second"],
                        timeout=CONFIG.timeout_ms,
                    )

                    page.locator('[data-testid="education-mobile-nav-pdfs"]').click()
                    page.wait_for_function(
                        """() => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            return !!(state && state.educationMobileSection === 'pdfs');
                        }""",
                        timeout=CONFIG.timeout_ms,
                    )

                    page.evaluate("window.scrollTo(0, 0)")
                    page.locator(f'[data-testid="education-mobile-category-{category_targets["first"]}"]').click()
                    page.wait_for_function(
                        """(firstId) => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            const isVisible = (id) => {
                                const node = document.getElementById(id);
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                return style.display !== 'none' && style.visibility !== 'hidden' && node.offsetParent !== null;
                            };
                            return !!(
                                state &&
                                state.educationSelectedCategoryId === firstId &&
                                state.educationMobileSection === 'flashcards' &&
                                isVisible('education-flashcards-panel') &&
                                !isVisible('education-quiz-panel') &&
                                !isVisible('education-pdf-library-panel')
                            );
                        }""",
                        arg=category_targets["first"],
                        timeout=CONFIG.timeout_ms,
                    )

                    page.evaluate("window.scrollTo(0, 0)")
                    page.locator(f'[data-testid="education-mobile-category-{category_targets["second"]}"]').click()
                    page.wait_for_function(
                        """(secondId) => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            const isVisible = (id) => {
                                const node = document.getElementById(id);
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                return style.display !== 'none' && style.visibility !== 'hidden' && node.offsetParent !== null;
                            };
                            return !!(
                                state &&
                                state.educationSelectedCategoryId === secondId &&
                                state.educationMobileSection === 'pdfs' &&
                                isVisible('education-pdf-library-panel') &&
                                !isVisible('education-quiz-panel') &&
                                !isVisible('education-flashcards-panel')
                            );
                        }""",
                        arg=category_targets["second"],
                        timeout=CONFIG.timeout_ms,
                    )
                finally:
                    page.set_viewport_size({"width": 1280, "height": 900})

            run_browser_step(
                "education mobile quick access",
                REPORTER,
                page,
                monitor,
                CONFIG,
                education_mobile_quick_access,
            )

            def education_quiz_explain_behavior() -> None:
                open_url(page, CONFIG, "/?tab=education")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(
                            state &&
                            state.activeTab === 'education' &&
                            state.educationSelectedQuiz &&
                            typeof state.currentEducationQuizQuestion === 'function' &&
                            state.currentEducationQuizQuestion()
                        );
                    }""",
                    timeout=max(CONFIG.timeout_ms, 60000),
                )

                wrong_answer = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        const question = state && typeof state.currentEducationQuizQuestion === 'function'
                            ? state.currentEducationQuizQuestion()
                            : null;
                        if (!question) return null;
                        const wrongOption = (question.options || []).find((option) => option.id !== question.correct_option_id);
                        return {
                            wrongOptionId: wrongOption ? String(wrongOption.id).toLowerCase() : '',
                            hasExplanation: !!(question.summary || question.explanation)
                        };
                    }"""
                )
                if not wrong_answer or not wrong_answer["wrongOptionId"] or not wrong_answer["hasExplanation"]:
                    raise AssertionError(f"expected an answerable quiz question with explanation content, got {wrong_answer}")

                page.locator(f'[data-testid="education-quiz-option-{wrong_answer["wrongOptionId"]}"]').click()

                explain_button = page.locator('[data-testid="education-quiz-explain-button"]')
                explain_button.wait_for(state="visible", timeout=CONFIG.timeout_ms)

                feedback_visibility = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        const question = state && typeof state.currentEducationQuizQuestion === 'function'
                            ? state.currentEducationQuizQuestion()
                            : null;
                        if (!question) return [];
                        return (question.options || []).map((option) => {
                            const testId = `education-quiz-option-${String(option.id || '').toLowerCase()}`;
                            const root = document.querySelector(`[data-testid="${testId}"]`);
                            const feedbackText = String(option.short_feedback || '');
                            const feedbackVisible = !feedbackText || !!(
                                root &&
                                Array.from(root.querySelectorAll('*')).some((node) => {
                                    const text = (node.textContent || '').trim();
                                    return text.includes(feedbackText)
                                        && window.getComputedStyle(node).display !== 'none'
                                        && node.offsetParent !== null;
                                })
                            );
                            return {
                                id: option.id,
                                shortFeedback: feedbackText,
                                feedbackVisible
                            };
                        });
                    }"""
                )
                missing_feedback = [
                    item["id"]
                    for item in feedback_visibility
                    if item["shortFeedback"] and not item["feedbackVisible"]
                ]
                if missing_feedback:
                    raise AssertionError(f"expected short feedback to be visible for all options, missing {missing_feedback}")

                explanation_loading = page.locator('[data-testid="education-quiz-explanation-loading"]')
                explanation_panel = page.locator('[data-testid="education-quiz-explanation-panel"]')
                if explanation_panel.count() and explanation_panel.first.is_visible():
                    raise AssertionError("explanation panel should stay hidden until the explain button is clicked")
                if explanation_loading.count() and explanation_loading.first.is_visible():
                    raise AssertionError("explanation loading state should stay hidden until the explain button is clicked")

                explain_button.click()
                explanation_loading.wait_for(state="visible", timeout=CONFIG.timeout_ms)
                loading_state = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return {
                            loading: !!(state && state.educationQuizExplanationLoading),
                            open: !!(state && state.educationQuizExplanationOpen)
                        };
                    }"""
                )
                if loading_state != {"loading": True, "open": False}:
                    raise AssertionError(f"expected quiz explanation to enter a loading state first, got {loading_state}")
                if explanation_panel.count() and explanation_panel.first.is_visible():
                    raise AssertionError("explanation panel should stay hidden while the thinking state is visible")

                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && !state.educationQuizExplanationLoading && state.educationQuizExplanationOpen);
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                explanation_panel.wait_for(state="visible", timeout=CONFIG.timeout_ms)
                if len(explanation_panel.inner_text().strip()) < 20:
                    raise AssertionError("expected detailed quiz explanation text after clicking explain")

                explanation_order = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        const question = state && typeof state.currentEducationQuizQuestion === 'function'
                            ? state.currentEducationQuizQuestion()
                            : null;
                        const panel = document.querySelector('[data-testid="education-quiz-explanation-panel"]');
                        const panelText = panel ? (panel.innerText || '') : '';
                        const explanation = question ? String(question.explanation || '') : '';
                        const summary = question ? String(question.summary || '') : '';
                        const explanationNeedle = explanation ? explanation.slice(0, Math.min(80, explanation.length)) : '';
                        const summaryNeedle = summary ? summary.slice(0, Math.min(80, summary.length)) : '';
                        return {
                            explanationIndex: explanationNeedle ? panelText.indexOf(explanationNeedle) : -1,
                            summaryIndex: summaryNeedle ? panelText.indexOf(summaryNeedle) : -1,
                            hasExplanation: !!explanation,
                            hasSummary: !!summary
                        };
                    }"""
                )
                if explanation_order["hasExplanation"] and explanation_order["explanationIndex"] < 0:
                    raise AssertionError(f"expected core explanation text in panel, got {explanation_order}")
                if explanation_order["hasSummary"] and explanation_order["summaryIndex"] < 0:
                    raise AssertionError(f"expected summary text in panel, got {explanation_order}")
                if (
                    explanation_order["hasExplanation"]
                    and explanation_order["hasSummary"]
                    and explanation_order["summaryIndex"] <= explanation_order["explanationIndex"]
                ):
                    raise AssertionError(f"expected summary to appear after explanation, got {explanation_order}")

                page.locator('[data-testid="education-quiz-next-button"]').click()
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        const question = state && typeof state.currentEducationQuizQuestion === 'function'
                            ? state.currentEducationQuizQuestion()
                            : null;
                        return !!(
                            state &&
                            question &&
                            state.educationQuizIndex === 1 &&
                            !state.educationQuizExplanationLoading &&
                            !state.educationQuizExplanationOpen
                        );
                    }""",
                    timeout=CONFIG.timeout_ms,
                )

                correct_answer = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        const question = state && typeof state.currentEducationQuizQuestion === 'function'
                            ? state.currentEducationQuizQuestion()
                            : null;
                        return question ? String(question.correct_option_id || '').toLowerCase() : '';
                    }"""
                )
                if not correct_answer:
                    raise AssertionError("expected a correct option id on the next quiz question")

                page.locator(f'[data-testid="education-quiz-option-{correct_answer}"]').click()
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        const question = state && typeof state.currentEducationQuizQuestion === 'function'
                            ? state.currentEducationQuizQuestion()
                            : null;
                        return !!(
                            state &&
                            question &&
                            typeof state.getEducationQuizAnswer === 'function' &&
                            state.getEducationQuizAnswer(question.id) === question.correct_option_id
                        );
                    }""",
                    timeout=CONFIG.timeout_ms,
                )

                if explain_button.is_visible():
                    raise AssertionError("explain button should stay hidden after a correct answer")

            run_browser_step(
                "education quiz explain behavior",
                REPORTER,
                page,
                monitor,
                CONFIG,
                education_quiz_explain_behavior,
            )

            def public_search() -> None:
                open_url(page, CONFIG, "/")
                page.fill("#search-input", "wosen")
                _invoke_public_search(page)
                _assert_public_search_success(page, timeout_ms=max(CONFIG.timeout_ms, 60000))

            run_browser_step(
                "public search journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search,
                allow_console_errors=("status of 429",),
                allow_request_failures=(f"429 GET {CONFIG.base_url}/api/v1/search/public",),
            )

            def public_search_edge_validation() -> None:
                open_url(page, CONFIG, "/")
                page.fill("#search-input", "w")
                _invoke_public_search(page)
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && !state.searchLoading && state.searchError);
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                state = _get_body_state(page)
                if not state["searchError"]:
                    raise AssertionError("expected short-query validation error")
                if state["searchResults"] != 0:
                    raise AssertionError(f"expected no results for short-query validation, got {state['searchResults']}")

            run_browser_step(
                "public search short-query validation",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search_edge_validation,
            )

            def public_search_with_class_filter() -> None:
                open_url(page, CONFIG, "/")
                page.locator("div.max-w-2xl.mx-auto.mb-5 button").first.click()
                page.locator('input[x-model="classInput"]').wait_for(state="visible")
                page.fill('input[x-model="classInput"]', "9")
                page.press('input[x-model="classInput"]', "Enter")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && Array.isArray(state.selectedClasses) && state.selectedClasses.includes(9));
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                page.fill("#search-input", "wosen")
                _invoke_public_search(page)
                state = _assert_public_search_success(page, timeout_ms=max(CONFIG.timeout_ms, 60000))
                if 9 not in state["selectedClasses"]:
                    raise AssertionError(f"expected selected class 9 to persist, got {state['selectedClasses']}")

            run_browser_step(
                "public search class-filter journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search_with_class_filter,
                allow_console_errors=("status of 429",),
                allow_request_failures=(f"429 POST {CONFIG.base_url}/api/v1/search/public",),
            )

            def public_search_with_image() -> None:
                open_url(page, CONFIG, "/")
                page.fill("#search-input", "wosen")
                image_bytes = _build_valid_public_search_png()
                page.locator('input[x-ref="landingImageInput"]').set_input_files(
                    [{"name": "public-search.png", "mimeType": "image/png", "buffer": image_bytes}]
                )
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.imageName === 'public-search.png');
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                _invoke_public_search(page)
                state = _assert_public_search_success(page, timeout_ms=max(CONFIG.timeout_ms, 60000))
                if state["imageName"] != "public-search.png":
                    raise AssertionError(f"expected uploaded image name to persist, got {state['imageName']!r}")

            run_browser_step(
                "public search image journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search_with_image,
                allow_console_errors=("status of 429",),
                allow_request_failures=(f"429 POST {CONFIG.base_url}/api/v1/search/public",),
            )

            def landing_registry_switcher_state() -> None:
                """Click each of the 4 registry tabs and confirm Alpine
                state flips + the matching filter strip becomes visible.
                Validates the wiring between the switcher buttons in
                landing.html and the searchView state in landing.js."""
                page.evaluate("() => localStorage.removeItem('landingSearchView')")
                open_url(page, CONFIG, "/")
                page.locator("#search-input").wait_for(state="visible")

                # Default tab should be trademark — confirms localStorage
                # was cleared and the IIFE fell back to the default.
                initial = _get_body_state(page)
                if initial["searchView"] != "trademark":
                    raise AssertionError(
                        f"expected default searchView=trademark, got {initial['searchView']!r}"
                    )

                tab_specs = [
                    ("design", 'input[data-testid="landing-design-locarno"]'),
                    ("patent", 'input[data-testid="landing-patent-ipc"]'),
                    ("cografi", 'input[data-testid="landing-cografi-section"]'),
                    ("trademark", None),
                ]
                for tab, filter_selector in tab_specs:
                    page.locator(f'[data-testid="landing-switch-{tab}"]').click()
                    page.wait_for_function(
                        """(expected) => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            return !!(state && state.searchView === expected);
                        }""",
                        arg=tab,
                        timeout=CONFIG.timeout_ms,
                    )
                    if filter_selector:
                        page.locator(filter_selector).wait_for(
                            state="visible", timeout=CONFIG.timeout_ms,
                        )
                    # Risk-report button is trademark-only — verify it
                    # disappears for the other 3 registries.
                    risk_btn_visible = page.evaluate(
                        """() => {
                            const btn = document.getElementById('landing-risk-report-btn');
                            if (!btn) return false;
                            const style = window.getComputedStyle(btn);
                            return style.display !== 'none' && btn.offsetParent !== null;
                        }"""
                    )
                    if tab == "trademark" and not risk_btn_visible:
                        raise AssertionError("risk-report button must be visible on trademark tab")
                    if tab != "trademark" and risk_btn_visible:
                        raise AssertionError(
                            f"risk-report button must hide on {tab} tab (got visible)"
                        )

            run_browser_step(
                "landing registry switcher state",
                REPORTER,
                page,
                monitor,
                CONFIG,
                landing_registry_switcher_state,
            )

            def landing_registry_switcher_persistence() -> None:
                """Pick a non-default tab, reload, and confirm the choice
                survives via localStorage.landingSearchView — the same
                pattern dashboard's _search_panel.html uses."""
                open_url(page, CONFIG, "/")
                page.locator('[data-testid="landing-switch-patent"]').click()
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.searchView === 'patent');
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                stored = page.evaluate("() => localStorage.getItem('landingSearchView')")
                if stored != "patent":
                    raise AssertionError(
                        f"expected localStorage.landingSearchView=patent, got {stored!r}"
                    )
                open_url(page, CONFIG, "/")
                page.locator("#search-input").wait_for(state="visible")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.searchView === 'patent');
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                state = _get_body_state(page)
                if state["searchView"] != "patent":
                    raise AssertionError(
                        f"expected searchView=patent to persist across reload, got {state['searchView']!r}"
                    )

            run_browser_step(
                "landing registry switcher persistence",
                REPORTER,
                page,
                monitor,
                CONFIG,
                landing_registry_switcher_persistence,
            )

            def landing_multi_registry_dispatch() -> None:
                """End-to-end: switch to each non-trademark registry,
                fill the matching filter, submit, and assert publicSearch()
                contacts the correct /api/v1/{registry}-search/public URL.
                Result count is tolerated as 0 since the dev corpus may
                not contain matches for the chosen query."""
                # Reset state so prior steps don't carry filters or an
                # exhausted quota cookie into this one.
                context.clear_cookies()
                page.evaluate(
                    """() => {
                        localStorage.removeItem('landingSearchView');
                        localStorage.removeItem('search_history');
                    }"""
                )
                open_url(page, CONFIG, "/")
                page.locator("#search-input").wait_for(state="visible")

                dispatch_specs = [
                    {
                        "tab": "design",
                        "endpoint": "/api/v1/design-search/public",
                        "query": "sandalye",
                        "filter_selector": 'input[data-testid="landing-design-locarno"]',
                        "filter_value": "06-01",
                    },
                    {
                        "tab": "patent",
                        "endpoint": "/api/v1/patent-search/public",
                        "query": "elektrik motoru",
                        "filter_selector": 'input[data-testid="landing-patent-ipc"]',
                        "filter_value": "H02",
                    },
                    {
                        "tab": "cografi",
                        "endpoint": "/api/v1/cografi-search/public",
                        "query": "konya",
                        "filter_selector": 'input[data-testid="landing-cografi-region"]',
                        "filter_value": "Konya",
                    },
                ]
                for spec in dispatch_specs:
                    page.locator(f'[data-testid="landing-switch-{spec["tab"]}"]').click()
                    page.wait_for_function(
                        """(tab) => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            return !!(state && state.searchView === tab);
                        }""",
                        arg=spec["tab"],
                        timeout=CONFIG.timeout_ms,
                    )
                    page.fill("#search-input", spec["query"])
                    page.fill(spec["filter_selector"], spec["filter_value"])
                    response = _submit_with_rate_limit_retry(
                        page,
                        monitor,
                        spec["endpoint"],
                        _invoke_public_search,
                        success_statuses=(200,),
                    )
                    if response.status != 200:
                        raise AssertionError(
                            f"{spec['tab']} dispatch: expected 200 from {spec['endpoint']}, got {response.status}"
                        )
                    _wait_for_public_search_idle(
                        page, timeout_ms=max(CONFIG.timeout_ms, 60000)
                    )
                    state = _get_body_state(page)
                    if state["searchView"] != spec["tab"]:
                        raise AssertionError(
                            f"{spec['tab']} dispatch: searchView slipped to {state['searchView']!r}"
                        )
                    # Either non-zero hits OR an error message ('no_results').
                    # A hard failure is searchResults < 0 (Alpine state missing).
                    if state["searchResults"] < 0:
                        raise AssertionError(
                            f"{spec['tab']} dispatch: Alpine state unavailable after search"
                        )

            run_browser_step(
                "landing multi-registry dispatch",
                REPORTER,
                page,
                monitor,
                CONFIG,
                landing_multi_registry_dispatch,
                allow_console_errors=("status of 429",),
                allow_request_failures=(
                    f"429 POST {CONFIG.base_url}/api/v1/design-search/public",
                    f"429 POST {CONFIG.base_url}/api/v1/patent-search/public",
                    f"429 POST {CONFIG.base_url}/api/v1/cografi-search/public",
                ),
            )

            def upgrade_modal_plan_handoff_rules() -> None:
                open_url(page, CONFIG, "/")
                page.wait_for_function("() => !!(window.AppUpgradeModal && window.AppUpgradeModal.resolveOffer)", timeout=CONFIG.timeout_ms)
                resolved = page.evaluate(
                    """() => ({
                        freeLeads: window.AppUpgradeModal.resolveOffer({ current_plan: 'free' }, 'leads').recommendedPlan,
                        starterApi: window.AppUpgradeModal.resolveOffer({ current_plan: 'starter' }, 'api_access').recommendedPlan,
                        freeWatchlistLogo: window.AppUpgradeModal.resolveOffer({ current_plan: 'free' }, 'watchlist_logo').recommendedPlan,
                        quickCopyMatches: (() => {
                            const offer = window.AppUpgradeModal.resolveOffer({ current_plan: 'free' }, 'agentic_search');
                            return offer.title === window.AppI18n.t('upgrade.search_limit_title')
                                && offer.description === window.AppI18n.t('upgrade.search_limit_description');
                        })(),
                        watchlistCopyMatches: (() => {
                            const offer = window.AppUpgradeModal.resolveOffer({ current_plan: 'free' }, 'watchlist_items');
                            return offer.title === window.AppI18n.t('upgrade.watchlist_title')
                                && offer.description === window.AppI18n.t('upgrade.watchlist_description');
                        })(),
                        liveCopyMatches: (() => {
                            const offer = window.AppUpgradeModal.resolveOffer({ current_plan: 'free' }, 'live_search');
                            return offer.title === window.AppI18n.t('upgrade.live_search_title')
                                && offer.description === window.AppI18n.t('upgrade.live_search_description');
                        })(),
                        leadsCopyMatches: (() => {
                            const offer = window.AppUpgradeModal.resolveOffer({ current_plan: 'free' }, 'leads');
                            return offer.title === window.AppI18n.t('upgrade.leads_title')
                                && offer.description === window.AppI18n.t('upgrade.leads_description');
                        })(),
                        dailyLimitHandled: window.AppUpgradeModal.maybeHandle({ error: 'daily_limit_exceeded', current_plan: 'free' }, 'agentic_search'),
                        dailyLimitPlan: (document.getElementById('upgrade-plan-code')?.textContent || '').trim().toLowerCase(),
                        genericRateHandled: (() => {
                            hideUpgradeModal();
                            return window.AppUpgradeModal.maybeHandle({ message: 'Rate limit exceeded' }, 'agentic_search');
                        })(),
                        modalVisibleAfterGenericRate: !!(document.getElementById('upgrade-modal') && !document.getElementById('upgrade-modal').classList.contains('hidden'))
                    })"""
                )
                if resolved["freeLeads"] != "professional":
                    raise AssertionError(f"expected free leads gate to recommend professional, got {resolved}")
                if resolved["starterApi"] != "enterprise":
                    raise AssertionError(f"expected starter api gate to recommend enterprise, got {resolved}")
                if resolved["freeWatchlistLogo"] != "starter":
                    raise AssertionError(f"expected free watchlist-logo gate to recommend starter, got {resolved}")
                if not resolved["quickCopyMatches"] or not resolved["watchlistCopyMatches"] or not resolved["liveCopyMatches"] or not resolved["leadsCopyMatches"]:
                    raise AssertionError(f"expected context-specific upgrade copy, got {resolved}")
                if resolved["dailyLimitHandled"] is not True or resolved["dailyLimitPlan"] != "starter":
                    raise AssertionError(f"expected daily quick-search limit to open starter upgrade modal, got {resolved}")
                if resolved["genericRateHandled"] is not False or resolved["modalVisibleAfterGenericRate"]:
                    raise AssertionError(f"expected generic rate-limit payloads to avoid upgrade modal, got {resolved}")

            run_browser_step(
                "upgrade modal plan handoff rules",
                REPORTER,
                page,
                monitor,
                CONFIG,
                upgrade_modal_plan_handoff_rules,
            )

            def public_search_daily_limit_upgrade_gate() -> None:
                context.clear_cookies()
                page.evaluate(
                    """() => {
                        localStorage.removeItem('auth_token');
                        localStorage.removeItem('access_token');
                        localStorage.removeItem('refresh_token');
                        sessionStorage.removeItem('auth_token');
                        sessionStorage.removeItem('access_token');
                        sessionStorage.removeItem('refresh_token');
                    }"""
                )
                open_url(page, CONFIG, "/")
                page.fill("#search-input", "wosen")

                response = None
                for attempt in range(6):
                    with page.expect_response(lambda candidate: "/api/v1/search/public" in candidate.url, timeout=CONFIG.timeout_ms) as response_info:
                        _invoke_public_search(page)
                    response = response_info.value
                    _wait_for_public_search_idle(page, timeout_ms=max(CONFIG.timeout_ms, 60000))
                    if attempt < 5:
                        if response.status != 200:
                            raise AssertionError(f"expected 200 before daily free limit, got {response.status}")
                        state = _get_body_state(page)
                        if state["upgradeModalVisible"]:
                            raise AssertionError("upgrade modal should stay hidden before the free daily limit is exhausted")
                        continue
                    if response.status != 429:
                        raise AssertionError(f"expected 429 on sixth public search, got {response.status}")

                page.locator("#upgrade-modal").wait_for(state="visible", timeout=CONFIG.timeout_ms)
                recommended_plan = (page.locator("#upgrade-plan-code").text_content() or "").strip().lower()
                state = _get_body_state(page)
                if not state["upgradeModalVisible"]:
                    raise AssertionError("expected upgrade modal after the sixth public search")
                if recommended_plan != "starter":
                    raise AssertionError(f"expected starter recommendation after the public daily limit, got {recommended_plan!r}")
                modal_offer = page.evaluate(
                    """() => ({
                        price: (document.getElementById('upgrade-plan-price')?.textContent || '').trim(),
                        features: Array.from(document.querySelectorAll('#upgrade-feature-list li span:last-child')).map((el) => (el.textContent || '').trim())
                    })"""
                )
                if "499" not in modal_offer["price"]:
                    raise AssertionError(f"expected starter monthly price after public daily limit, got {modal_offer}")
                if not any("50" in feature for feature in modal_offer["features"]):
                    raise AssertionError(f"expected starter quick-search highlight after public daily limit, got {modal_offer}")

            run_browser_step(
                "public search daily free limit upgrade gate",
                REPORTER,
                page,
                monitor,
                CONFIG,
                public_search_daily_limit_upgrade_gate,
                allow_console_errors=("status of 429",),
                allow_request_failures=(f"429 GET {CONFIG.base_url}/api/v1/search/public",),
            )

            def pricing_to_checkout() -> None:
                open_url(page, CONFIG, "/pricing")
                page.locator('a[href^="/checkout?plan="]').first.wait_for(state="visible")
                with page.expect_navigation(url="**/checkout?**", timeout=CONFIG.timeout_ms):
                    page.locator('a[href^="/checkout?plan="]').first.click()
                page.wait_for_load_state("networkidle", timeout=CONFIG.timeout_ms)
                if "/checkout" not in page.url:
                    raise AssertionError(f"expected checkout URL, got {page.url}")
                if "plan=" not in page.url:
                    raise AssertionError(f"expected checkout plan query string, got {page.url}")

            run_browser_step(
                "pricing to checkout navigation",
                REPORTER,
                page,
                monitor,
                CONFIG,
                pricing_to_checkout,
            )

            forgot_email = f"browser-forgot-{uuid4().hex[:10]}@example.com"
            forgot_success_email = FORGOT_SUCCESS_EMAIL
            forgot_success_password = DEFAULT_PASSWORD
            forgot_success_new_password = "Reset9876!"
            create_verified_browser_account(
                forgot_success_email,
                forgot_success_password,
                organization_name=f"Browser Reset {uuid4().hex[:8]}",
            )

            def forgot_password_request() -> None:
                open_url(page, CONFIG, "/?login=1")
                page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
                page.locator('button[x-text="t(\'auth.forgot_password\')"]').click()
                page.locator('input[x-model="forgotEmail"]').wait_for(state="visible")
                page.fill('input[x-model="forgotEmail"]', forgot_email)

                response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/forgot-password",
                    lambda: page.locator('div[x-show="showForgotPassword"] button[type="submit"]').click(),
                    success_statuses=(200,),
                )
                if response.status != 200:
                    raise AssertionError(f"unexpected forgot-password status: {response.status}")

                page.locator('input[x-model="forgotCode"]').wait_for(state="visible")
                success_box = page.locator('div[x-show="forgotSuccess"]')
                success_box.wait_for(state="visible")
                success_text = success_box.text_content() or ""
                if not success_text.strip():
                    raise AssertionError("expected forgot-password success message")

            run_browser_step(
                "forgot password request journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                forgot_password_request,
            )

            def forgot_password_invalid_code() -> None:
                page.locator('input[x-model="forgotCode"]').wait_for(state="visible")
                page.fill('input[x-model="forgotCode"]', "000000")
                page.fill('input[x-model="forgotNewPassword"]', "Reset1234!")
                page.fill('input[x-model="forgotConfirmPassword"]', "Reset1234!")

                with page.expect_response(lambda response: "/api/v1/auth/reset-password" in response.url, timeout=CONFIG.timeout_ms) as response_info:
                    page.locator('div[x-show="showForgotPassword"] button[type="submit"]').click()
                response = response_info.value
                if response.status != 400:
                    raise AssertionError(f"expected invalid reset code 400, got {response.status}")

                page.locator('div[x-show="forgotError"]').wait_for(state="visible")
                error_text = page.locator('div[x-show="forgotError"]').text_content() or ""
                if not error_text.strip():
                    raise AssertionError("expected forgot-password error message")

            run_browser_step(
                "forgot password invalid code handling",
                REPORTER,
                page,
                monitor,
                CONFIG,
                forgot_password_invalid_code,
                allow_console_errors=("status of 400",),
                allow_request_failures=("/api/v1/auth/reset-password",),
            )

            def forgot_password_success() -> None:
                open_url(page, CONFIG, "/?login=1")
                page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
                page.locator('button[x-text="t(\'auth.forgot_password\')"]').click()
                page.locator('input[x-model="forgotEmail"]').wait_for(state="visible")
                page.fill('input[x-model="forgotEmail"]', forgot_success_email)

                request_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/forgot-password",
                    lambda: page.locator('div[x-show="showForgotPassword"] button[type="submit"]').click(),
                    success_statuses=(200,),
                )
                if request_response.status != 200:
                    raise AssertionError(f"unexpected forgot-password status: {request_response.status}")

                page.locator('input[x-model="forgotCode"]').wait_for(state="visible")
                reset_code = lookup_password_reset_code(forgot_success_email)
                page.fill('input[x-model="forgotCode"]', reset_code)
                page.fill('input[x-model="forgotNewPassword"]', forgot_success_new_password)
                page.fill('input[x-model="forgotConfirmPassword"]', forgot_success_new_password)

                reset_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/reset-password",
                    lambda: page.locator('div[x-show="showForgotPassword"] button[type="submit"]').click(),
                    success_statuses=(200,),
                )
                if reset_response.status != 200:
                    raise AssertionError(f"unexpected reset-password status: {reset_response.status}")

                success_box = page.locator('div[x-show="forgotSuccess"]')
                success_box.wait_for(state="visible")
                success_text = success_box.text_content() or ""
                if not success_text.strip():
                    raise AssertionError("expected reset-password success message")

                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.showLogin === true && state.showForgotPassword === false);
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
                page.fill('input[x-model="loginEmail"]', forgot_success_email)
                page.fill('input[x-model="loginPassword"]', forgot_success_new_password)

                login_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/login",
                    lambda: page.locator('[role="dialog"] button[type="submit"]').first.click(),
                    success_statuses=(200,),
                )
                if login_response.status != 200:
                    raise AssertionError(f"unexpected post-reset login status: {login_response.status}")

                page.wait_for_url("**/dashboard", timeout=CONFIG.timeout_ms)
                page.locator("#tab-btn-overview").wait_for(state="visible")
                token = page.evaluate("() => localStorage.getItem('auth_token')")
                if not token:
                    raise AssertionError("expected auth_token after post-reset login")

            run_browser_step(
                "forgot password success and login journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                forgot_password_success,
                allow_console_errors=("status of 429",),
                allow_request_failures=(
                    "/api/v1/auth/forgot-password",
                    "/api/v1/auth/reset-password",
                    "/api/v1/auth/login",
                ),
            )

            registration_email = REGISTRATION_EMAIL
            registration_password = DEFAULT_PASSWORD
            delete_browser_test_account(registration_email)

            def register_account() -> None:
                _clear_auth(page)
                open_url(page, CONFIG, "/?register=1")
                page.locator('input[x-model="regFirstName"]').wait_for(state="visible")
                page.fill('input[x-model="regFirstName"]', "Browser")
                page.fill('input[x-model="regLastName"]', "Signup")
                page.fill('input[x-model="regEmail"]', registration_email)
                page.fill('input[x-model="regPassword"]', registration_password)
                page.fill('input[x-model="regConfirmPassword"]', registration_password)
                page.fill('input[x-model="regOrgName"]', f"Browser Signup {uuid4().hex[:8]}")

                response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/register",
                    lambda: page.locator('div[x-show="showRegister"] button[type="submit"]').click(),
                    success_statuses=(200,),
                )
                if response.status != 200:
                    raise AssertionError(f"unexpected register status: {response.status}")

                page.wait_for_url("**/dashboard", timeout=CONFIG.timeout_ms)
                page.locator("#tab-btn-overview").wait_for(state="visible")
                page.locator('input[x-model="verificationCode"]').wait_for(state="visible")
                monitor.clear()
                token = page.evaluate("() => localStorage.getItem('auth_token')")
                if not token:
                    raise AssertionError("expected auth_token after registration")

            run_browser_step(
                "registration modal journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                register_account,
            )

            def verify_email_modal() -> None:
                page.locator('input[x-model="verificationCode"]').wait_for(state="visible")

                resend_button = page.locator('div[x-show="showEmailVerification"] button').nth(1)
                resend_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/resend-verification",
                    lambda: resend_button.click(),
                    success_statuses=(200,),
                )
                if resend_response.status != 200:
                    raise AssertionError(f"unexpected resend-verification status: {resend_response.status}")

                page.locator('div[x-show="verificationSuccess"]').wait_for(state="visible")
                cooldown = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return state ? state.verificationResendCooldown : -1;
                    }"""
                )
                if cooldown <= 0:
                    raise AssertionError(f"expected resend cooldown > 0, got {cooldown}")

                verification_code = lookup_email_verification_code(registration_email)
                page.fill('input[x-model="verificationCode"]', verification_code)

                verify_response = _submit_with_rate_limit_retry(
                    page,
                    monitor,
                    "/api/v1/auth/verify-email",
                    lambda: page.locator('div[x-show="showEmailVerification"] button').first.click(),
                    success_statuses=(200,),
                )
                if verify_response.status != 200:
                    raise AssertionError(f"unexpected verify-email status: {verify_response.status}")

                page.locator('div[x-show="verificationSuccess"]').wait_for(state="visible")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.showEmailVerification === false);
                    }""",
                    timeout=CONFIG.timeout_ms,
                )

                profile = page.evaluate(
                    """async () => {
                        const token = localStorage.getItem('auth_token');
                        const res = await fetch('/api/v1/auth/me', {
                            headers: { Authorization: 'Bearer ' + token }
                        });
                        let data = null;
                        try {
                            data = await res.json();
                        } catch (error) {
                            data = null;
                        }
                        return {
                            status: res.status,
                            is_verified: data ? data.is_verified : null
                        };
                    }"""
                )
                if profile["status"] != 200:
                    raise AssertionError(f"unexpected auth/me status after email verification: {profile['status']}")
                if profile["is_verified"] is not True:
                    raise AssertionError(f"expected is_verified after email verification, got {profile['is_verified']!r}")

            run_browser_step(
                "email verification modal journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                verify_email_modal,
            )
        finally:
            context.close()
            browser.close()
            delete_browser_test_account(REGISTRATION_EMAIL)

    sys.exit(0 if REPORTER.summary("PUBLIC BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()
