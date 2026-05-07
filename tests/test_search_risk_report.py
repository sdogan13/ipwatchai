import base64
import json
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from models.schemas import SearchRiskReportCandidate, SearchRiskReportRequest, SearchRiskReportResponse
from services.search_risk_report_service import (
    RISK_REPORT_MAX_OUTPUT_TOKENS,
    build_search_risk_report_multimodal_messages,
    build_search_risk_report_messages,
    build_search_risk_report_prompt,
    claim_pending_search_risk_report_data,
    generate_pending_search_risk_report_data,
    generate_search_risk_report_data,
    persist_search_risk_report_pdf,
)


def _candidate(name="les cafes sati", application_no="2026/011163"):
    return {
        "name": name,
        "application_no": application_no,
        "status": "Yayinda",
        "status_code": "published",
        "nice_classes": [30, 43],
        "image_url": "/api/v1/trademarks/2026-011163/logo",
        "deterministic_score": 1.0,
        "text_similarity": 1.0,
        "visual_similarity": 0.0,
        "scores": {
            "total": 1.0,
            "text_idf_score": 1.0,
            "visual_similarity": 0.0,
            "textual_breakdown": {"large": "ignored"},
        },
    }


class _DbContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _RecordingDbContext:
    inserted_params = None
    committed = False

    def __enter__(self):
        type(self).inserted_params = None
        type(self).committed = False
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self

    def execute(self, query, params=None):
        type(self).inserted_params = params

    def commit(self):
        type(self).committed = True


def _valid_report(score=82, input_index=1, application_no="2026/011163"):
    return {
        "summary": "Exact shared brand wording creates meaningful risk.",
        "overall_risk_score": score,
        "highest_risk_application_no": application_no,
        "results": [
            {
                "input_index": input_index,
                "llm_risk_score": score,
                "risk_level": "high",
                "reasons": ["Exact SATI element is present."],
                "key_factors": ["exact token", "class overlap"],
                "uncertainty": "low",
            }
        ],
    }


class _GeminiClient:
    text_model = "gemini-test"

    def __init__(self, response=None, responses=None, available=True):
        self.response = response or _valid_report()
        self.responses = list(responses) if responses is not None else None
        self.available = available
        self.prompts = []
        self.system_prompts = []
        self.user_prompts = []
        self.temperatures = []

    def is_available(self):
        return self.available

    async def generate_json(
        self,
        prompt,
        max_output_tokens=4096,
        temperature=0.2,
        system_prompt=None,
        user_prompt=None,
    ):
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        self.max_output_tokens = max_output_tokens
        self.temperatures.append(temperature)
        if self.responses is not None:
            item = self.responses.pop(0) if self.responses else self.response
            if isinstance(item, Exception):
                raise item
            return item
        return self.response


class _ProviderClient(_GeminiClient):
    def __init__(self, provider_name, text_model, response=None, responses=None, available=True):
        super().__init__(response=response, responses=responses, available=available)
        self.provider_name = provider_name
        self.text_model = text_model


class _MultimodalProviderClient(_ProviderClient):
    async def generate_multimodal_json(
        self,
        *,
        system_prompt,
        user_prompt,
        images,
        max_output_tokens=4096,
        temperature=0.2,
    ):
        self.prompts.append(system_prompt + "\n" + user_prompt)
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        self.images = images
        self.max_output_tokens = max_output_tokens
        self.temperatures.append(temperature)
        if self.responses is not None:
            item = self.responses.pop(0) if self.responses else self.response
            if isinstance(item, Exception):
                raise item
            return item
        return self.response


def _request(results=None):
    return SearchRiskReportRequest(
        query="les cafes sati",
        selected_classes=[30, 43],
        language="tr",
        image_used=False,
        results=results or [_candidate()],
    )


def _noop_report_persister(**kwargs):
    return None


def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAIAAADZF8uwAAAAGElEQVR4nGP8"
        "//8/AwXgP4YqZGRkAACcAR/0WM6hAAAAAElFTkSuQmCC"
    )


@pytest.mark.asyncio
async def test_search_risk_report_service_generates_validated_report():
    client = _GeminiClient()
    calls = {"increment": 0}

    def report_eligibility_checker(db, plan_name, org_id):
        return {
            "eligible": True,
            "reports_used": calls["increment"],
            "reports_limit": 10,
            "reports_remaining": 10 - calls["increment"],
            "can_export": True,
        }

    def report_usage_incrementer(db, user_id, org_id, cost):
        calls["increment"] += cost
        return True

    response = await generate_search_risk_report_data(
        request=_request(),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=report_eligibility_checker,
        report_usage_incrementer=report_usage_incrementer,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        gemini_client_getter=lambda: client,
        report_persister=_noop_report_persister,
    )

    assert response.model == "gemini-test"
    assert response.results[0].name == "les cafes sati"
    assert response.results[0].image_url == "/api/v1/trademarks/2026-011163/logo"
    assert response.results[0].llm_risk_score == 82
    assert response.report_usage["reports_used"] == 1
    assert response.credits_remaining is None
    assert calls["increment"] == 1
    assert client.max_output_tokens == RISK_REPORT_MAX_OUTPUT_TOKENS
    assert "Calculate every risk score independently from scratch" in client.prompts[0]
    assert "summary under 80 words" in client.prompts[0]
    assert "exactly one reason" in client.prompts[0]
    assert client.system_prompts[0].startswith("You are a Turkish trademark risk analyst")
    assert client.user_prompts[0].startswith("Input JSON:")
    assert "deterministic_scores" not in client.prompts[0]
    assert "textual_breakdown" not in client.prompts[0]


@pytest.mark.asyncio
async def test_search_risk_report_provider_chain_uses_qwen_first_with_split_messages():
    from generative_ai.risk_report_client import RiskReportJsonClient

    qwen = _ProviderClient("qwen", "qwen-max")
    deepseek = _ProviderClient("deepseek", "deepseek-v4-pro")
    gemini = _ProviderClient("gemini", "gemini-test")
    provider_chain = RiskReportJsonClient(providers=[qwen, deepseek, gemini])

    response = await generate_search_risk_report_data(
        request=_request(),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 1,
            "reports_limit": 10,
            "reports_remaining": 9,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        gemini_client_getter=lambda: provider_chain,
        report_persister=_noop_report_persister,
    )

    assert response.model == "qwen:qwen-max"
    assert len(qwen.prompts) == 1
    assert not deepseek.prompts
    assert not gemini.prompts
    assert qwen.system_prompts[0].startswith("You are a Turkish trademark risk analyst")
    assert "Calculate every risk score independently from scratch" in qwen.system_prompts[0]
    assert qwen.user_prompts[0].startswith("Input JSON:")
    assert '"name": "les cafes sati"' in qwen.user_prompts[0]


@pytest.mark.asyncio
async def test_search_risk_report_provider_chain_falls_back_to_deepseek_then_gemini():
    from generative_ai.risk_report_client import RiskReportJsonClient

    qwen = _ProviderClient(
        "qwen",
        "qwen-max",
        responses=[RuntimeError("Qwen unavailable")],
    )
    deepseek = _ProviderClient(
        "deepseek",
        "deepseek-v4-pro",
        responses=[RuntimeError("DeepSeek unavailable")],
    )
    gemini = _ProviderClient("gemini", "gemini-test", response=_valid_report(score=81))
    provider_chain = RiskReportJsonClient(providers=[qwen, deepseek, gemini])

    response = await generate_search_risk_report_data(
        request=_request(),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 1,
            "reports_limit": 10,
            "reports_remaining": 9,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        gemini_client_getter=lambda: provider_chain,
        report_persister=_noop_report_persister,
    )

    assert response.model == "gemini:gemini-test"
    assert response.results[0].llm_risk_score == 81
    assert len(qwen.prompts) == 1
    assert len(deepseek.prompts) == 1
    assert len(gemini.prompts) == 1


@pytest.mark.asyncio
async def test_search_risk_report_provider_chain_uses_deepseek_when_qwen_fails():
    from generative_ai.risk_report_client import RiskReportJsonClient

    qwen = _ProviderClient(
        "qwen",
        "qwen-max",
        responses=[RuntimeError("Qwen unavailable")],
    )
    deepseek = _ProviderClient("deepseek", "deepseek-v4-pro", response=_valid_report(score=80))
    gemini = _ProviderClient("gemini", "gemini-test")
    provider_chain = RiskReportJsonClient(providers=[qwen, deepseek, gemini])

    response = await generate_search_risk_report_data(
        request=_request(),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 1,
            "reports_limit": 10,
            "reports_remaining": 9,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        gemini_client_getter=lambda: provider_chain,
        report_persister=_noop_report_persister,
    )

    assert response.model == "deepseek:deepseek-v4-pro"
    assert response.results[0].llm_risk_score == 80
    assert len(qwen.prompts) == 1
    assert len(deepseek.prompts) == 1
    assert not gemini.prompts


@pytest.mark.asyncio
async def test_search_risk_report_multimodal_provider_chain_uses_qwen_first():
    from generative_ai.risk_report_client import RiskReportMultimodalJsonClient

    qwen = _MultimodalProviderClient("qwen", "qwen3-vl-plus")
    gemini = _MultimodalProviderClient("gemini", "gemini-2.5-pro")
    provider_chain = RiskReportMultimodalJsonClient(providers=[qwen, gemini])
    text_client = _ProviderClient("deepseek", "deepseek-v4-pro")
    query_image = _png_bytes()
    assert query_image

    response = await generate_search_risk_report_data(
        request=SearchRiskReportRequest(
            query="les cafes sati",
            selected_classes=[30, 43],
            language="tr",
            image_used=True,
            results=[_candidate()],
        ),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        query_image_bytes=query_image,
        query_image_mime="image/png",
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 1,
            "reports_limit": 10,
            "reports_remaining": 9,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        gemini_client_getter=lambda: text_client,
        multimodal_client_getter=lambda: provider_chain,
        report_persister=_noop_report_persister,
    )

    assert response.model == "qwen:qwen3-vl-plus"
    assert qwen.images[0]["label"] == "query_logo"
    assert qwen.images[0]["data_url"].startswith("data:image/")
    assert "attached trademark logo images" in qwen.system_prompts[0]
    assert '"query_logo_ref": "query_logo"' in qwen.user_prompts[0]
    assert not text_client.prompts
    assert not gemini.prompts


@pytest.mark.asyncio
async def test_search_risk_report_multimodal_provider_chain_falls_back_to_gemini():
    from generative_ai.risk_report_client import RiskReportMultimodalJsonClient

    qwen = _MultimodalProviderClient(
        "qwen",
        "qwen3-vl-plus",
        responses=[RuntimeError("Qwen unavailable")],
    )
    gemini = _MultimodalProviderClient("gemini", "gemini-2.5-pro", response=_valid_report(score=79))
    provider_chain = RiskReportMultimodalJsonClient(providers=[qwen, gemini])
    query_image = _png_bytes()
    assert query_image

    response = await generate_search_risk_report_data(
        request=SearchRiskReportRequest(
            query="les cafes sati",
            selected_classes=[30, 43],
            language="tr",
            image_used=True,
            results=[_candidate()],
        ),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        query_image_bytes=query_image,
        query_image_mime="image/png",
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 1,
            "reports_limit": 10,
            "reports_remaining": 9,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        multimodal_client_getter=lambda: provider_chain,
        report_persister=_noop_report_persister,
    )

    assert response.model == "gemini:gemini-2.5-pro"
    assert response.results[0].llm_risk_score == 79
    assert len(qwen.prompts) == 1
    assert len(gemini.prompts) == 1


@pytest.mark.asyncio
async def test_search_risk_report_service_attaches_saved_pdf_report_metadata():
    client = _GeminiClient()
    calls = {"persist": 0}

    def report_persister(**kwargs):
        calls["persist"] += 1
        assert kwargs["request"].query == "les cafes sati"
        assert kwargs["report"].results[0].name == "les cafes sati"
        return {
            "report_id": "report-123",
            "report_download_url": "/api/v1/reports/report-123/download",
        }

    response = await generate_search_risk_report_data(
        request=_request(),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 1,
            "reports_limit": 10,
            "reports_remaining": 9,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        gemini_client_getter=lambda: client,
        report_persister=report_persister,
    )

    assert calls["persist"] == 1
    assert response.report_id == "report-123"
    assert response.report_download_url == "/api/v1/reports/report-123/download"


@pytest.mark.asyncio
async def test_pending_risk_report_generation_returns_claim_metadata_without_usage_quota():
    client = _GeminiClient()
    calls = {"persist": 0}

    def pending_report_persister(**kwargs):
        calls["persist"] += 1
        assert kwargs["request"].query == "les cafes sati"
        assert kwargs["report"].report_id is None
        return {
            "claim_token": "claim-token-12345678901234567890",
            "claim_expires_at": datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
        }

    response = await generate_pending_search_risk_report_data(
        request=_request(),
        gemini_client_getter=lambda: client,
        pending_report_persister=pending_report_persister,
    )

    assert calls["persist"] == 1
    assert response.is_pending is True
    assert response.claim_token == "claim-token-12345678901234567890"
    assert response.report_id is None
    assert response.report_usage is None


@pytest.mark.asyncio
async def test_claim_pending_risk_report_checks_quota_and_attaches_saved_pdf():
    state = {
        "row": {
            "id": "11111111-1111-1111-1111-111111111111",
            "claimed_at": None,
            "expires_at": datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc),
            "report_name": "Risk Raporu - sati",
            "summary": "Summary",
            "file_path": "reports/pending-risk.pdf",
            "file_size_bytes": 1234,
        },
        "last": None,
        "queries": [],
        "increment": 0,
        "commits": 0,
    }

    class _ClaimDbContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return self

        def execute(self, query, params=None):
            state["queries"].append((query, params))
            state["last"] = "select_pending" if "FROM pending_risk_reports" in query and "FOR UPDATE" in query else None

        def fetchone(self):
            if state["last"] == "select_pending":
                return state["row"]
            return None

        def commit(self):
            state["commits"] += 1

    result = await claim_pending_search_risk_report_data(
        claim_token="claim-token-12345678901234567890",
        current_user=SimpleNamespace(
            id="22222222-2222-2222-2222-222222222222",
            organization_id="33333333-3333-3333-3333-333333333333",
        ),
        database_factory=_ClaimDbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": state["increment"],
            "reports_limit": 10,
            "reports_remaining": 10 - state["increment"],
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: state.__setitem__("increment", state["increment"] + cost) or True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
    )

    assert result["report_id"]
    assert result["report_download_url"].startswith("/api/v1/reports/")
    assert result["report_usage"]["reports_used"] == 1
    assert state["increment"] == 1
    assert state["commits"] == 1
    assert any("INSERT INTO reports" in query for query, _ in state["queries"])
    assert any("UPDATE pending_risk_reports" in query for query, _ in state["queries"])


@pytest.mark.asyncio
async def test_search_risk_report_service_refunds_when_pdf_persistence_fails():
    client = _GeminiClient()
    calls = {"refund": 0}

    def report_persister(**kwargs):
        raise RuntimeError("pdf failed")

    with pytest.raises(HTTPException) as exc:
        await generate_search_risk_report_data(
            request=_request(),
            current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
            database_factory=_DbContext,
            user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
            report_eligibility_checker=lambda db, plan_name, org_id: {
                "eligible": True,
                "reports_used": 1,
                "reports_limit": 10,
                "reports_remaining": 9,
                "can_export": True,
            },
            report_usage_incrementer=lambda db, user_id, org_id, cost: True,
            report_usage_refunder=lambda db, user_id, org_id, cost: calls.__setitem__("refund", calls["refund"] + 1) or True,
            gemini_client_getter=lambda: client,
            report_persister=report_persister,
        )

    assert exc.value.status_code == 503
    assert calls["refund"] == 1


@pytest.mark.asyncio
async def test_search_risk_report_service_refunds_invalid_llm_response():
    client = _GeminiClient(
        response={
            "summary": "Invalid",
            "overall_risk_score": 60,
            "results": [
                {
                    "input_index": 2,
                    "llm_risk_score": 60,
                    "risk_level": "medium",
                    "reasons": [],
                    "key_factors": [],
                    "uncertainty": "medium",
                }
            ],
        }
    )
    calls = {"refund": 0}

    def refund_handler(db, org_id, cost):
        calls["refund"] += 1
        return True

    with pytest.raises(HTTPException) as exc:
        await generate_search_risk_report_data(
            request=_request(),
            current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
            database_factory=_DbContext,
            user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
            report_eligibility_checker=lambda db, plan_name, org_id: {
                "eligible": True,
                "reports_used": 0,
                "reports_limit": 10,
                "reports_remaining": 10,
                "can_export": True,
            },
            report_usage_incrementer=lambda db, user_id, org_id, cost: True,
            report_usage_refunder=lambda db, user_id, org_id, cost: refund_handler(db, org_id, cost),
            gemini_client_getter=lambda: client,
            report_persister=_noop_report_persister,
        )

    assert exc.value.status_code == 503
    assert calls["refund"] == 1
    assert len(client.prompts) == 2
    assert "Retry instruction" in client.prompts[1]


@pytest.mark.asyncio
async def test_search_risk_report_service_retries_provider_parse_failure():
    client = _GeminiClient(
        responses=[
            RuntimeError("Gemini response was not a JSON object"),
            _valid_report(score=83),
        ]
    )
    calls = {"refund": 0}

    response = await generate_search_risk_report_data(
        request=_request(),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 1,
            "reports_limit": 10,
            "reports_remaining": 9,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: calls.__setitem__("refund", calls["refund"] + 1) or True,
        gemini_client_getter=lambda: client,
        report_persister=_noop_report_persister,
    )

    assert response.results[0].llm_risk_score == 83
    assert len(client.prompts) == 2
    assert client.temperatures == [0.1, 0.0]
    assert "Retry instruction" in client.prompts[1]
    assert calls["refund"] == 0


@pytest.mark.asyncio
async def test_search_risk_report_service_retries_validation_failure():
    client = _GeminiClient(
        responses=[
            {
                "summary": "Invalid",
                "overall_risk_score": 60,
                "results": [
                    {
                        "input_index": 2,
                        "llm_risk_score": 60,
                        "risk_level": "medium",
                        "reasons": [],
                        "key_factors": [],
                        "uncertainty": "medium",
                    }
                ],
            },
            _valid_report(score=84),
        ]
    )

    response = await generate_search_risk_report_data(
        request=_request(),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 1,
            "reports_limit": 10,
            "reports_remaining": 9,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        gemini_client_getter=lambda: client,
        report_persister=_noop_report_persister,
    )

    assert response.results[0].llm_risk_score == 84
    assert len(client.prompts) == 2
    assert "Retry instruction" in client.prompts[1]


@pytest.mark.asyncio
async def test_search_risk_report_service_accepts_single_string_reason_fields():
    client = _GeminiClient(
        response={
            "summary": "Exact shared brand wording creates meaningful risk.",
            "overall_risk_score": 82,
            "highest_risk_application_no": "2026/011163",
            "results": [
                {
                    "input_index": 1,
                    "llm_risk_score": 82,
                    "risk_level": "high",
                    "reasons": "Ayni ibare ve sinif nedeniyle risk yuksek.",
                    "key_factors": "exact token",
                    "uncertainty": "low",
                }
            ],
        }
    )

    response = await generate_search_risk_report_data(
        request=_request(),
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 1,
            "reports_limit": 10,
            "reports_remaining": 9,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        gemini_client_getter=lambda: client,
        report_persister=_noop_report_persister,
    )

    assert response.results[0].reasons == ["Ayni ibare ve sinif nedeniyle risk yuksek."]
    assert response.results[0].key_factors == ["exact token"]
    assert len(client.prompts) == 1


@pytest.mark.asyncio
async def test_search_risk_report_service_returns_403_when_report_limit_exhausted():
    with pytest.raises(HTTPException) as exc:
        await generate_search_risk_report_data(
            request=_request(),
            current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
            database_factory=_DbContext,
            user_plan_getter=lambda db, user_id: {"plan_name": "free", "display_name": "Free Trial"},
            report_eligibility_checker=lambda db, plan_name, org_id: {
                "eligible": False,
                "reports_used": 1,
                "reports_limit": 1,
                "reports_remaining": 0,
                "can_export": False,
                "reason": "Bu ay 1 risk raporu hakkinin tamamini kullandiniz.",
            },
            report_usage_incrementer=lambda db, user_id, org_id, cost: True,
            report_usage_refunder=lambda db, user_id, org_id, cost: True,
            gemini_client_getter=lambda: _GeminiClient(),
            report_persister=_noop_report_persister,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "limit_exceeded"


@pytest.mark.asyncio
async def test_search_risk_report_service_returns_503_when_gemini_unavailable():
    calls = {"refund": 0}

    with pytest.raises(HTTPException) as exc:
        await generate_search_risk_report_data(
            request=_request(),
            current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
            database_factory=_DbContext,
            user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
            report_eligibility_checker=lambda db, plan_name, org_id: {
                "eligible": True,
                "reports_used": 0,
                "reports_limit": 10,
                "reports_remaining": 10,
                "can_export": True,
            },
            report_usage_incrementer=lambda db, user_id, org_id, cost: True,
            report_usage_refunder=lambda db, user_id, org_id, cost: calls.__setitem__("refund", calls["refund"] + 1) or True,
            gemini_client_getter=lambda: _GeminiClient(available=False),
            report_persister=_noop_report_persister,
        )

    assert exc.value.status_code == 503
    assert calls["refund"] == 1


def test_search_risk_report_prompt_uses_factual_payload_without_scores_or_logo_urls():
    prompt = build_search_risk_report_prompt(_request())
    system_prompt, user_prompt = build_search_risk_report_messages(_request())

    assert '"name": "les cafes sati"' in prompt
    assert '"application_no": "2026/011163"' in prompt
    assert '"nice_classes": [30, 43]' in prompt
    assert "Calculate every risk score independently from scratch" in system_prompt
    assert '"name": "les cafes sati"' in user_prompt
    assert '"application_no": "2026/011163"' in user_prompt
    assert "deterministic_score" not in user_prompt
    assert "Calculate every risk score independently from scratch" in prompt
    assert "sorted from highest llm_risk_score to lowest llm_risk_score" in prompt
    assert "summary under 80 words" in prompt
    assert "exactly one reason" in prompt
    assert "deterministic_scores" not in prompt
    assert "scoring_diagnostics" not in prompt
    assert "deterministic_score" not in prompt
    assert "text_similarity" not in prompt
    assert "visual_similarity" not in prompt
    assert "phonetic_similarity" not in prompt
    assert "translation_similarity" not in prompt
    assert "text_idf_score" not in prompt
    assert '"image_url": "/api/v1/trademarks/2026-011163/logo"' not in prompt
    assert "textual_breakdown" not in prompt


def test_search_risk_report_prompt_requires_selected_output_language():
    prompt = build_search_risk_report_prompt(
        SearchRiskReportRequest(
            query="les cafes sati",
            selected_classes=[30],
            language="ar",
            image_used=False,
            results=[_candidate()],
        )
    )

    assert '"output_language": "Arabic"' in prompt
    assert "Write every natural-language output field in Arabic" in prompt
    assert "summary, reasons, and key_factors" in prompt


def test_search_risk_report_multimodal_prompt_labels_candidate_logo_refs():
    request = _request()
    system_prompt, user_prompt = build_search_risk_report_multimodal_messages(
        request,
        {1: "candidate_logo_1"},
    )

    assert "attached trademark logo images" in system_prompt
    assert '"query_logo_ref": "query_logo"' in user_prompt
    assert '"logo_image_ref": "candidate_logo_1"' in user_prompt
    assert '"logo_image_available": true' in user_prompt
    assert "deterministic_score" not in user_prompt
    assert "visual_similarity" not in user_prompt


def test_search_risk_report_multimodal_image_parts_resolve_candidate_logos(monkeypatch, tmp_path):
    import services.search_risk_report_service as service

    logo_path = tmp_path / "candidate.png"
    query_image = _png_bytes()
    assert query_image
    logo_path.write_bytes(query_image)
    request = SearchRiskReportRequest(
        query="les cafes sati",
        selected_classes=[30],
        language="tr",
        image_used=True,
        results=[
            _candidate(
                name="les cafes sati",
                application_no="2026/011163",
            )
        ],
    )
    request.results[0].image_url = "/api/trademark-image/candidate"
    monkeypatch.setattr(service, "_logo_path_from_url", lambda image_url: str(logo_path))
    assert service._image_part("query_logo", query_image, "image/png") is not None

    images, refs = service._build_multimodal_image_parts(
        request,
        query_image,
        "image/png",
    )

    assert [image["label"] for image in images] == ["query_logo", "candidate_logo_1"]
    assert refs == {1: "candidate_logo_1"}
    assert all(image["data_url"].startswith("data:image/") for image in images)


def test_search_risk_report_pdf_is_written_and_recorded(monkeypatch, tmp_path):
    import services.search_risk_report_service as service

    monkeypatch.setattr(service.settings.paths, "report_dir", str(tmp_path))
    request = _request(
        results=[
            _candidate(name="ışık şeker", application_no="2026/011163"),
        ]
    )
    report = SearchRiskReportResponse(
        query="ışık çay",
        selected_classes=[30, 43],
        image_used=False,
        summary="Başvuru ve sınıflar arasında açık çakışma riski var.",
        overall_risk_score=82,
        highest_risk_application_no="2026/011163",
        results=[
            SearchRiskReportCandidate(
                input_index=1,
                name="ışık şeker",
                application_no="2026/011163",
                image_url=None,
                llm_risk_score=82,
                risk_level="high",
                reasons=["Başvuru ortak ayırt edici unsuru içeriyor."],
                key_factors=["sınıf örtüşmesi"],
                uncertainty="low",
            )
        ],
        model="gemini-test",
        generated_at=datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc),
    )

    saved = persist_search_risk_report_pdf(
        report=report,
        request=request,
        current_user=SimpleNamespace(id="user-1", organization_id="org-1", email="owner@example.com"),
        database_factory=_RecordingDbContext,
    )

    pdf_path = Path(saved["file_path"])
    assert saved["report_id"]
    assert saved["report_download_url"].endswith("/download")
    assert pdf_path.exists()
    pdf_bytes = pdf_path.read_bytes()
    assert pdf_bytes.startswith(b"%PDF")
    assert b"/ToUnicode" in pdf_bytes
    assert _RecordingDbContext.committed is True
    assert _RecordingDbContext.inserted_params[3] == "risk_assessment"
    assert _RecordingDbContext.inserted_params[6].endswith(".pdf")


@pytest.mark.asyncio
async def test_search_risk_report_service_sorts_results_by_llm_score_descending():
    client = _GeminiClient(
        response={
            "summary": "Independent risk order.",
            "overall_risk_score": 91,
            "highest_risk_application_no": "invalid",
            "results": [
                {
                    "input_index": 1,
                    "llm_risk_score": 48,
                    "risk_level": "low",
                    "reasons": ["Lower similarity."],
                    "key_factors": ["extra matter"],
                    "uncertainty": "medium",
                },
                {
                    "input_index": 2,
                    "llm_risk_score": 91,
                    "risk_level": "critical",
                    "reasons": ["Closer dominant element."],
                    "key_factors": ["shared wording"],
                    "uncertainty": "low",
                },
                {
                    "input_index": 3,
                    "llm_risk_score": 91,
                    "risk_level": "critical",
                    "reasons": ["Tie score."],
                    "key_factors": ["class overlap"],
                    "uncertainty": "low",
                },
            ],
        }
    )
    request = _request(
        results=[
            _candidate(name="low", application_no="low-app"),
            _candidate(name="top", application_no="top-app"),
            _candidate(name="tie", application_no="tie-app"),
        ]
    )

    response = await generate_search_risk_report_data(
        request=request,
        current_user=SimpleNamespace(id="user-1", organization_id="org-1"),
        database_factory=_DbContext,
        user_plan_getter=lambda db, user_id: {"plan_name": "starter", "display_name": "Starter"},
        report_eligibility_checker=lambda db, plan_name, org_id: {
            "eligible": True,
            "reports_used": 0,
            "reports_limit": 10,
            "reports_remaining": 10,
            "can_export": True,
        },
        report_usage_incrementer=lambda db, user_id, org_id, cost: True,
        report_usage_refunder=lambda db, user_id, org_id, cost: True,
        gemini_client_getter=lambda: client,
        report_persister=_noop_report_persister,
    )

    assert [item.input_index for item in response.results] == [2, 3, 1]
    assert [item.application_no for item in response.results] == ["top-app", "tie-app", "low-app"]
    assert response.highest_risk_application_no == "top-app"


def test_real_gemini_client_exposes_json_generation_method():
    from generative_ai.gemini_client import GeminiClient

    assert hasattr(GeminiClient, "generate_json")


@pytest.mark.asyncio
async def test_deepseek_client_uses_openai_sdk_json_mode_and_disabled_thinking():
    from generative_ai.deepseek_client import DeepSeekClient

    recorded = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"summary":"ok"}')
                    )
                ]
            )

    client = DeepSeekClient(
        settings=SimpleNamespace(
            deepseek_api_key="",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_text_model="deepseek-v4-pro",
            deepseek_timeout=30,
            deepseek_max_retries=0,
        )
    )
    client.api_key = "test-key"
    client._initialized = True
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )

    result = await client.generate_json(
        prompt="combined",
        system_prompt="system instructions",
        user_prompt="trademark data",
        max_output_tokens=123,
        temperature=0.1,
    )

    assert result == {"summary": "ok"}
    assert recorded["model"] == "deepseek-v4-pro"
    assert recorded["messages"] == [
        {"role": "system", "content": "system instructions"},
        {"role": "user", "content": "trademark data"},
    ]
    assert recorded["response_format"] == {"type": "json_object"}
    assert recorded["extra_body"] == {"thinking": {"type": "disabled"}}
    assert recorded["stream"] is False
    assert recorded["max_tokens"] == 123


def test_real_deepseek_client_exposes_json_generation_method():
    from generative_ai.deepseek_client import DeepSeekClient

    assert hasattr(DeepSeekClient, "generate_json")


@pytest.mark.asyncio
async def test_qwen_client_uses_openai_sdk_json_mode_and_disabled_thinking():
    from generative_ai.qwen_client import QwenClient

    recorded = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"summary":"ok"}')
                    )
                ]
            )

    client = QwenClient(
        settings=SimpleNamespace(
            qwen_api_key="",
            qwen_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            qwen_text_model="qwen-max",
            qwen_vl_model="qwen3-vl-plus",
            qwen_timeout=30,
            qwen_max_retries=0,
        )
    )
    client.api_key = "test-key"
    client._initialized = True
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )

    result = await client.generate_multimodal_json(
        system_prompt="Return JSON.",
        user_prompt="trademark data",
        images=[{"label": "query_logo", "data_url": "data:image/png;base64,abc"}],
        max_output_tokens=123,
        temperature=0.1,
    )

    assert result == {"summary": "ok"}
    assert recorded["model"] == "qwen3-vl-plus"
    assert recorded["messages"][0] == {"role": "system", "content": "Return JSON."}
    assert recorded["messages"][1]["role"] == "user"
    assert recorded["messages"][1]["content"][0] == {"type": "text", "text": "trademark data"}
    assert recorded["messages"][1]["content"][2] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc"},
    }
    assert recorded["response_format"] == {"type": "json_object"}
    assert recorded["extra_body"] == {"enable_thinking": False}
    assert recorded["stream"] is False
    assert recorded["max_tokens"] == 123


@pytest.mark.asyncio
async def test_qwen_client_supports_text_only_json_generation():
    from generative_ai.qwen_client import QwenClient

    recorded = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"summary":"ok"}')
                    )
                ]
            )

    client = QwenClient(
        settings=SimpleNamespace(
            qwen_api_key="",
            qwen_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            qwen_text_model="qwen-max",
            qwen_vl_model="qwen3-vl-plus",
            qwen_timeout=30,
            qwen_max_retries=0,
        )
    )
    client.api_key = "test-key"
    client._initialized = True
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )

    result = await client.generate_json(
        prompt="combined",
        system_prompt="system instructions",
        user_prompt="trademark data",
        max_output_tokens=123,
        temperature=0.1,
    )

    assert result == {"summary": "ok"}
    assert recorded["model"] == "qwen-max"
    assert recorded["messages"] == [
        {"role": "system", "content": "system instructions"},
        {"role": "user", "content": "trademark data"},
    ]
    assert recorded["response_format"] == {"type": "json_object"}
    assert recorded["extra_body"] == {"enable_thinking": False}
    assert recorded["stream"] is False
    assert recorded["max_tokens"] == 123


@pytest.mark.asyncio
async def test_qwen_client_caps_output_tokens_to_provider_limit():
    from generative_ai.qwen_client import QWEN_MAX_OUTPUT_TOKENS, QwenClient

    recorded = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"summary":"ok"}')
                    )
                ]
            )

    client = QwenClient(
        settings=SimpleNamespace(
            qwen_api_key="",
            qwen_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            qwen_text_model="qwen-max",
            qwen_vl_model="qwen3-vl-plus",
            qwen_timeout=30,
            qwen_max_retries=0,
        )
    )
    client.api_key = "test-key"
    client._initialized = True
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )

    result = await client.generate_json(
        prompt="combined",
        system_prompt="system instructions",
        user_prompt="trademark data",
        max_output_tokens=16384,
        temperature=0.1,
    )

    assert result == {"summary": "ok"}
    assert recorded["model"] == "qwen-max"
    assert recorded["max_tokens"] == QWEN_MAX_OUTPUT_TOKENS


def test_real_qwen_client_exposes_multimodal_json_generation_method():
    from generative_ai.qwen_client import QwenClient

    assert hasattr(QwenClient, "generate_multimodal_json")
    assert hasattr(QwenClient, "generate_json")


def test_risk_report_output_budget_covers_visible_result_limit():
    assert RISK_REPORT_MAX_OUTPUT_TOKENS >= 16384


def test_search_risk_report_route_delegates_to_service(client):
    payload = {
        "query": "les cafes sati",
        "selected_classes": [30],
        "language": "tr",
        "image_used": False,
        "results": [_candidate()],
    }
    response_payload = {
        "query": "les cafes sati",
        "selected_classes": [30],
        "image_used": False,
        "summary": "Advisory report",
        "overall_risk_score": 82,
        "highest_risk_application_no": "2026/011163",
        "results": [
            {
                "input_index": 1,
                "name": "les cafes sati",
                "application_no": "2026/011163",
                "image_url": "/api/v1/trademarks/2026-011163/logo",
                "llm_risk_score": 82,
                "risk_level": "high",
                "reasons": ["Exact token"],
                "key_factors": ["class overlap"],
                "uncertainty": "low",
            }
        ],
        "model": "gemini-test",
        "generated_at": "2026-05-05T12:00:00",
        "report_usage": {"reports_used": 1, "reports_limit": 10, "reports_remaining": 9},
        "report_id": "report-123",
        "report_download_url": "/api/v1/reports/report-123/download",
        "credits_remaining": None,
    }

    with patch(
        "agentic_search.generate_search_risk_report_data",
        new_callable=AsyncMock,
    ) as mock_generate:
        mock_generate.return_value = response_payload
        resp = client.post("/api/v1/search/risk-report", json=payload)

    assert resp.status_code == 200
    assert resp.json()["results"][0]["llm_risk_score"] == 82
    assert resp.json()["results"][0]["image_url"] == "/api/v1/trademarks/2026-011163/logo"
    assert resp.json()["report_download_url"] == "/api/v1/reports/report-123/download"
    assert mock_generate.await_count == 1


def test_public_search_risk_report_route_delegates_to_pending_service(client):
    payload = {
        "query": "les cafes sati",
        "selected_classes": [30],
        "language": "tr",
        "image_used": False,
        "results": [_candidate()],
    }
    response_payload = {
        "query": "les cafes sati",
        "selected_classes": [30],
        "image_used": False,
        "summary": "Advisory report",
        "overall_risk_score": 82,
        "highest_risk_application_no": "2026/011163",
        "results": [
            {
                "input_index": 1,
                "name": "les cafes sati",
                "application_no": "2026/011163",
                "image_url": "/api/v1/trademarks/2026-011163/logo",
                "llm_risk_score": 82,
                "risk_level": "high",
                "reasons": ["Exact token"],
                "key_factors": ["class overlap"],
                "uncertainty": "low",
            }
        ],
        "model": "qwen:qwen-max",
        "generated_at": "2026-05-05T12:00:00",
        "report_usage": None,
        "report_id": None,
        "report_download_url": None,
        "claim_token": "claim-token-12345678901234567890",
        "claim_expires_at": "2026-05-06T12:00:00Z",
        "is_pending": True,
        "credits_remaining": None,
    }

    with patch(
        "agentic_search.generate_pending_search_risk_report_data",
        new_callable=AsyncMock,
    ) as mock_generate:
        mock_generate.return_value = response_payload
        resp = client.post("/api/v1/search/risk-report/public", json=payload)

    assert resp.status_code == 200
    assert resp.json()["is_pending"] is True
    assert resp.json()["claim_token"] == "claim-token-12345678901234567890"
    assert resp.json()["report_id"] is None
    assert mock_generate.await_count == 1


def test_search_risk_report_claim_route_delegates_to_claim_service(client):
    with patch(
        "agentic_search.claim_pending_search_risk_report_data",
        new_callable=AsyncMock,
    ) as mock_claim:
        mock_claim.return_value = {
            "report_id": "report-123",
            "report_download_url": "/api/v1/reports/report-123/download",
            "report_usage": {"reports_used": 1, "reports_limit": 10, "reports_remaining": 9},
        }
        resp = client.post(
            "/api/v1/search/risk-report/claim",
            json={"claim_token": "claim-token-12345678901234567890"},
        )

    assert resp.status_code == 200
    assert resp.json()["report_id"] == "report-123"
    assert mock_claim.await_count == 1
    assert mock_claim.await_args.kwargs["claim_token"] == "claim-token-12345678901234567890"


def test_search_risk_report_route_accepts_multipart_query_image(client):
    payload = {
        "query": "les cafes sati",
        "selected_classes": [30],
        "language": "tr",
        "image_used": True,
        "results": [_candidate()],
    }
    response_payload = {
        "query": "les cafes sati",
        "selected_classes": [30],
        "image_used": True,
        "summary": "Advisory report",
        "overall_risk_score": 82,
        "highest_risk_application_no": "2026/011163",
        "results": [
            {
                "input_index": 1,
                "name": "les cafes sati",
                "application_no": "2026/011163",
                "image_url": "/api/v1/trademarks/2026-011163/logo",
                "llm_risk_score": 82,
                "risk_level": "high",
                "reasons": ["Exact token"],
                "key_factors": ["class overlap"],
                "uncertainty": "low",
            }
        ],
        "model": "qwen:qwen3-vl-plus",
        "generated_at": "2026-05-05T12:00:00",
        "report_usage": {"reports_used": 1, "reports_limit": 10, "reports_remaining": 9},
        "report_id": "report-123",
        "report_download_url": "/api/v1/reports/report-123/download",
        "credits_remaining": None,
    }

    with patch(
        "agentic_search.generate_search_risk_report_data",
        new_callable=AsyncMock,
    ) as mock_generate:
        mock_generate.return_value = response_payload
        resp = client.post(
            "/api/v1/search/risk-report",
            files={
                "payload": (None, json.dumps(payload), "application/json"),
                "query_image": ("query-logo.png", io.BytesIO(_png_bytes()), "image/png"),
            },
        )

    assert resp.status_code == 200
    assert resp.json()["model"] == "qwen:qwen3-vl-plus"
    assert mock_generate.await_count == 1
    kwargs = mock_generate.await_args.kwargs
    assert kwargs["request"].image_used is True
    assert kwargs["query_image_bytes"]
    assert kwargs["query_image_mime"] == "image/png"


def test_search_risk_report_route_rejects_empty_results(client):
    resp = client.post(
        "/api/v1/search/risk-report",
        json={"query": "les cafes sati", "language": "tr", "results": []},
    )

    assert resp.status_code == 422


def test_search_risk_report_route_rejects_more_than_twenty_results(client):
    resp = client.post(
        "/api/v1/search/risk-report",
        json={
            "query": "les cafes sati",
            "language": "tr",
            "results": [_candidate(name=f"candidate {i}", application_no=str(i)) for i in range(21)],
        },
    )

    assert resp.status_code == 422
