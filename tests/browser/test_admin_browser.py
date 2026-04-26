"""
Browser journeys for admin-capable moderation and admin surfaces.

Run directly:
    python tests/browser/test_admin_browser.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.config import load_browser_config, with_live_credentials
from tests.browser.helpers.session import launch_browser_page, login_via_modal, open_url
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.config import load_live_config


CONFIG = load_browser_config()
REPORTER = LiveReporter()
pytestmark = pytest.mark.skip(reason="Browser E2E script; run directly with python tests/browser/test_admin_browser.py")


def _resolve_admin_browser_config():
    candidate_configs = []
    if os.environ.get("TEST_SUPERADMIN_EMAIL") and os.environ.get("TEST_SUPERADMIN_PASSWORD"):
        live_config = load_live_config(
            email_env="TEST_SUPERADMIN_EMAIL",
            password_env="TEST_SUPERADMIN_PASSWORD",
        )
        candidate_configs.append(("configured superadmin", with_live_credentials(CONFIG, live_config)))
    candidate_configs.append(("default browser persona", CONFIG))

    for label, candidate in candidate_configs:
        login_response = None
        for attempt in range(1, 6):
            login_response = requests.post(
                f"{candidate.base_url}/api/v1/auth/login",
                json={"email": candidate.email, "password": candidate.password},
                timeout=30,
            )
            if login_response.status_code == 200:
                break
            if login_response.status_code == 429 and attempt < 5:
                retry_after = login_response.headers.get("Retry-After")
                try:
                    delay = max(1.0, float(retry_after)) if retry_after else 15.0
                except ValueError:
                    delay = 15.0
                REPORTER.warn(
                    f"admin browser bootstrap login -> 429 rate limited, retrying in {delay:.0f}s "
                    f"(attempt {attempt}/5, {label})"
                )
                time.sleep(delay)
                continue
            break

        if login_response is None or login_response.status_code != 200:
            if login_response is not None:
                REPORTER.warn(
                    f"admin browser bootstrap login -> skipping {label}: "
                    f"{login_response.status_code} {login_response.text[:200]}"
                )
            continue

        token = login_response.json().get("access_token")
        if not token:
            REPORTER.warn(f"admin browser bootstrap login -> skipping {label}: missing access_token")
            continue

        profile_response = requests.get(
            f"{candidate.base_url}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if profile_response.status_code != 200:
            REPORTER.warn(
                f"admin browser bootstrap profile -> skipping {label}: "
                f"{profile_response.status_code} {profile_response.text[:200]}"
            )
            continue

        profile = profile_response.json()
        role = str(profile.get("role") or "").strip().lower()
        if profile.get("is_superadmin") or role == "admin":
            REPORTER.ok(f"admin browser bootstrap profile -> {candidate.email} ({label})")
            REPORTER.record("admin browser bootstrap profile", True, label)
            return {
                "config": candidate,
                "profile": profile,
                "label": label,
            }

        REPORTER.warn(f"admin browser bootstrap profile -> skipping {label}: role={role!r}")

    return None


def _put_quiz_moderation_override(page, question_id: str, explanation: str, summary: str) -> dict:
    return page.evaluate(
        """async ({ questionId, explanation, summary }) => {
            const token = localStorage.getItem('auth_token') || localStorage.getItem('access_token');
            const response = await fetch('/api/v1/education/moderation', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token
                },
                body: JSON.stringify({
                    item_type: 'quiz_question',
                    item_id: questionId,
                    explanation,
                    summary
                })
            });
            let data = null;
            try {
                data = await response.json();
            } catch (error) {
                data = null;
            }
            return { status: response.status, data };
        }""",
        {"questionId": question_id, "explanation": explanation, "summary": summary},
    )


def main() -> None:
    REPORTER.print_heading("ADMIN BROWSER", server=CONFIG.base_url)

    browser_context = _resolve_admin_browser_config()
    if browser_context is None:
        REPORTER.warn("admin browser journey -> skipped (no admin-capable browser credentials available)")
        REPORTER.record("admin browser journey", True, "skipped: no admin-capable browser credentials available")
        sys.exit(0)

    browser_config = browser_context["config"]
    browser_profile = browser_context["profile"]

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, browser_config)
        try:
            def admin_panel_navigation() -> None:
                login_via_modal(page, browser_config, monitor)
                open_url(page, browser_config, "/admin")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(state && state.authorized === true);
                    }""",
                    timeout=browser_config.timeout_ms,
                )
                page.get_by_text("Admin Panel", exact=False).first.wait_for()
                page.get_by_role("button", name="Organizations").click()
                page.get_by_role("heading", name="Organizations").wait_for()
                page.get_by_role("button", name="Users").click()
                page.get_by_role("heading", name="Users").wait_for()
                page.get_by_role("button", name="Plans & Limits").click()
                page.get_by_role("heading", name="Plans & Limits").wait_for()
                page.get_by_role("button", name="Analytics").click()
                page.get_by_role("heading", name="Usage Analytics").wait_for()
                page.get_by_role("button", name="All Settings").click()
                page.get_by_role("heading", name="All Settings").wait_for()

            if browser_profile.get("is_superadmin"):
                run_browser_step(
                    "admin browser journey",
                    REPORTER,
                    page,
                    monitor,
                    browser_config,
                    admin_panel_navigation,
                )
            else:
                REPORTER.info("admin browser journey -> skipped /admin navigation because the active browser persona is admin-only")
                REPORTER.record("admin browser journey", True, "skipped: admin-only persona")

            def admin_education_moderation_journey() -> None:
                login_via_modal(page, browser_config, monitor)
                open_url(page, browser_config, "/?tab=education")
                page.wait_for_function(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        return !!(
                            state &&
                            state.activeTab === 'education' &&
                            state.educationCanModerate === true &&
                            state.currentEducationQuizQuestion &&
                            state.currentEducationQuizQuestion() &&
                            document.querySelector('[data-testid="education-quiz-tester-tools"]') &&
                            document.querySelector('[data-testid="education-flashcard-tester-tools"]')
                        );
                    }""",
                    timeout=browser_config.timeout_ms,
                )

                question_state = page.evaluate(
                    """() => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        const question = state.currentEducationQuizQuestion();
                        const wrongOption = (question.options || []).find((option) => option.id !== question.correct_option_id);
                        return {
                            questionId: String(question.id || ''),
                            originalExplanation: String(question.explanation || ''),
                            originalSummary: String(question.summary || ''),
                            wrongOptionId: wrongOption ? String(wrongOption.id || '') : '',
                            quizTesterVisible: !!document.querySelector('[data-testid="education-quiz-tester-tools"]'),
                            flashcardTesterVisible: !!document.querySelector('[data-testid="education-flashcard-tester-tools"]')
                        };
                    }"""
                )
                if not question_state["questionId"] or not question_state["wrongOptionId"]:
                    raise AssertionError(f"expected quiz question with a wrong option for tester moderation, got {question_state}")
                if not question_state["quizTesterVisible"] or not question_state["flashcardTesterVisible"]:
                    raise AssertionError(f"expected tester tools to be visible for superadmin, got {question_state}")

                updated_explanation = f"Browser tester explanation {uuid4().hex[:8]}"
                updated_summary = f"Browser tester summary {uuid4().hex[:8]}"

                try:
                    page.locator('[data-testid="education-quiz-edit-explanation-button"]').click()
                    page.locator('[data-testid="education-quiz-explanation-editor"]').wait_for(
                        state="visible",
                        timeout=browser_config.timeout_ms,
                    )
                    page.locator('[data-testid="education-quiz-explanation-input"]').fill(updated_explanation)
                    page.locator('[data-testid="education-quiz-summary-input"]').fill(updated_summary)

                    with page.expect_response(
                        lambda response: "/api/v1/education/moderation" in response.url and response.request.method == "PUT",
                        timeout=browser_config.timeout_ms,
                    ) as response_info:
                        page.locator('[data-testid="education-quiz-explanation-save-button"]').click()
                    save_response = response_info.value
                    if save_response.status != 200:
                        raise AssertionError(f"expected moderation save 200, got {save_response.status}")

                    page.wait_for_function(
                        """({ questionId, explanation, summary }) => {
                            const state = document.body._x_dataStack && document.body._x_dataStack[0];
                            const question = state && state.currentEducationQuizQuestion ? state.currentEducationQuizQuestion() : null;
                            const editor = document.querySelector('[data-testid="education-quiz-explanation-editor"]');
                            return !!(
                                question &&
                                question.id === questionId &&
                                question.explanation === explanation &&
                                question.summary === summary &&
                                (!editor || editor.offsetParent === null)
                            );
                        }""",
                        arg={
                            "questionId": question_state["questionId"],
                            "explanation": updated_explanation,
                            "summary": updated_summary,
                        },
                        timeout=browser_config.timeout_ms,
                    )

                    page.locator(f'[data-testid="education-quiz-option-{question_state["wrongOptionId"].lower()}"]').click()
                    page.locator('[data-testid="education-quiz-explain-button"]').wait_for(
                        state="visible",
                        timeout=browser_config.timeout_ms,
                    )
                    page.locator('[data-testid="education-quiz-explain-button"]').click()
                    page.locator('[data-testid="education-quiz-explanation-loading"]').wait_for(
                        state="visible",
                        timeout=browser_config.timeout_ms,
                    )
                    page.locator('[data-testid="education-quiz-explanation-panel"]').wait_for(
                        state="visible",
                        timeout=browser_config.timeout_ms,
                    )
                    panel_text = page.locator('[data-testid="education-quiz-explanation-panel"]').inner_text()
                    if updated_explanation not in panel_text or updated_summary not in panel_text:
                        raise AssertionError(
                            "expected edited explanation and summary in the quiz explanation panel, "
                            f"got {panel_text!r}"
                        )
                finally:
                    restore_result = _put_quiz_moderation_override(
                        page,
                        question_state["questionId"],
                        question_state["originalExplanation"],
                        question_state["originalSummary"],
                    )
                    if restore_result["status"] != 200:
                        raise AssertionError(f"failed to restore quiz moderation override: {restore_result}")

            run_browser_step(
                "admin education moderation browser journey",
                REPORTER,
                page,
                monitor,
                browser_config,
                admin_education_moderation_journey,
            )
        finally:
            context.close()
            browser.close()

    sys.exit(0 if REPORTER.summary("ADMIN BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()
