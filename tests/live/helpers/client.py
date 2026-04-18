from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency. Run: pip install requests") from exc

from tests.live.helpers.config import LiveConfig


RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)
GET_RETRY_ATTEMPTS = 3
GET_RETRY_DELAY_SECONDS = 2.0


@dataclass
class LiveClient:
    config: LiveConfig
    token: Optional[str] = None
    session: requests.Session = field(default_factory=requests.Session)

    def url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    def _headers(
        self,
        headers: Optional[dict] = None,
        *,
        token: bool = True,
        json_content: bool = False,
    ) -> dict:
        final = dict(headers or {})
        if token and self.token:
            final.setdefault("Authorization", f"Bearer {self.token}")
        if json_content and not any(key.lower() == "content-type" for key in final):
            final["Content-Type"] = "application/json"
        return final

    def _error_response(self, method: str, url: str, exc: Exception):
        response = requests.Response()
        response.status_code = 599
        response._content = str(exc).encode("utf-8", errors="replace")
        response.url = url
        response.reason = exc.__class__.__name__
        response.headers["Content-Type"] = "text/plain; charset=utf-8"
        response.request = requests.Request(method=method, url=url).prepare()
        return response

    def _request(
        self,
        method: str,
        path: str,
        *,
        retryable: bool = False,
        **kwargs,
    ):
        url = self.url(path)
        attempts = GET_RETRY_ATTEMPTS if retryable else 1
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                return self.session.request(
                    method,
                    url,
                    timeout=self.config.timeout,
                    **kwargs,
                )
            except RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                if attempt >= attempts:
                    break
                time.sleep(GET_RETRY_DELAY_SECONDS)

        assert last_exc is not None
        return self._error_response(method, url, last_exc)

    def get(
        self,
        path: str,
        *,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        token: bool = True,
    ):
        return self._request(
            "GET",
            path,
            headers=self._headers(headers, token=token),
            params=params,
            retryable=True,
        )

    def post(
        self,
        path: str,
        *,
        headers: Optional[dict] = None,
        json_data=None,
        data=None,
        files=None,
        token: bool = True,
    ):
        final_headers = self._headers(
            headers,
            token=token,
            json_content=(files is None and json_data is not None),
        )
        if files:
            final_headers = {
                key: value
                for key, value in final_headers.items()
                if key.lower() != "content-type"
            }
            return self._request(
                "POST",
                path,
                headers=final_headers,
                data=data,
                files=files,
            )
        body = json_data if json_data is not None else data
        return self._request(
            "POST",
            path,
            headers=final_headers,
            json=body,
        )

    def put(
        self,
        path: str,
        *,
        data=None,
        headers: Optional[dict] = None,
        token: bool = True,
    ):
        return self._request(
            "PUT",
            path,
            headers=self._headers(headers, token=token, json_content=True),
            json=data,
        )

    def delete(
        self,
        path: str,
        *,
        headers: Optional[dict] = None,
        token: bool = True,
    ):
        return self._request(
            "DELETE",
            path,
            headers=self._headers(headers, token=token),
            retryable=True,
        )
