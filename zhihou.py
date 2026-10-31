"""Minimal, local-only client for the ZhiHou third-party logistics API.

Credentials are deliberately not persisted here.  The workbench keeps only the
current access token in the running Python process, and never serializes it
back to the browser, logs, or the local .env file.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener


ZHIHOU_BASE_URL = "https://api-cn.zhfulfill.com/erp/thirdparty"


class ZhiHouApiError(RuntimeError):
    """A safe, user-facing ZhiHou API failure."""


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ZhiHouApiError("智猴接口未返回有效 JSON") from error
    if not isinstance(value, dict):
        raise ZhiHouApiError("智猴接口返回格式不正确")
    return value


class ZhiHouClient:
    """Calls documented ZhiHou endpoints directly, bypassing injected proxies."""

    def __init__(self, base_url: str = ZHIHOU_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def _post(self, path: str, *, token: str = "", payload: Any = None,
              form: bool = False) -> dict[str, Any]:
        if not path.startswith("/") or "?" in path:
            raise ValueError("智猴 API 路径不正确")
        if token and (not isinstance(token, str) or len(token) > 4096):
            raise ValueError("智猴登录状态不正确")
        payload = {} if payload is None else payload
        if not isinstance(payload, (dict, list)):
            raise ValueError("智猴请求参数不正确")
        if form:
            if not isinstance(payload, dict):
                raise ValueError("智猴表单参数不正确")
            body = urlencode({key: str(value) for key, value in payload.items()}).encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            content_type = "application/json"
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        if token:
            headers["x-access-token"] = token
        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method="POST")
        try:
            # The desktop shell may expose a loopback proxy which is unrelated
            # to ZhiHou.  The explicit empty proxy configuration keeps this
            # local integration on a direct HTTPS connection.
            with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
                return _json_object(response.read())
        except HTTPError as error:
            raise ZhiHouApiError(f"智猴接口返回 HTTP {error.code}") from error
        except URLError as error:
            raise ZhiHouApiError("无法连接智猴接口，请检查网络或稍后重试") from error

    @staticmethod
    def _successful(response: dict[str, Any]) -> bool:
        code = response.get("code")
        return code in (None, 0, "0", 200, "200")

    @staticmethod
    def _failure_message(response: dict[str, Any]) -> str:
        message = str(response.get("message") or "智猴接口请求未成功").strip()
        # Avoid reflecting a large or unexpected upstream response in the UI.
        return message[:240] or "智猴接口请求未成功"

    def login(self, account: str, password: str, expiry: str) -> str:
        response = self._post("/passport/loginV2", payload={
            "account": account,
            "password": password,
            "userTokenExpired": expiry,
        })
        if not self._successful(response):
            raise ZhiHouApiError(self._failure_message(response))
        data = response.get("data")
        token = data if isinstance(data, str) else data.get("token", "") if isinstance(data, dict) else ""
        if not isinstance(token, str) or not token.strip() or len(token) > 4096:
            raise ZhiHouApiError("智猴未返回有效登录令牌")
        return token.strip()

    def read(self, path: str, token: str, payload: dict[str, Any] | None = None,
             *, form: bool = False) -> Any:
        response = self._post(path, token=token, payload=payload, form=form)
        if not self._successful(response):
            raise ZhiHouApiError(self._failure_message(response))
        return response.get("data")

    def write(self, path: str, token: str, payload: list[dict[str, Any]]) -> Any:
        """Make exactly one confirmed write request to a documented endpoint.

        Callers are responsible for requiring a fresh, single-use local preview.
        This client intentionally has no retry path: a lost response can mean the
        remote operation completed, and must be checked by the operator instead
        of replaying a create, update, or cancellation.
        """
        if not isinstance(payload, list) or not payload or not all(isinstance(row, dict) for row in payload):
            raise ValueError("智猴写入参数不正确")
        response = self._post(path, token=token, payload=payload)
        if not self._successful(response):
            raise ZhiHouApiError(self._failure_message(response))
        return response.get("data")
