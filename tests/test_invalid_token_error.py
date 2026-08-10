"""Regression tests for F7003 "Invalid OAuth token" handling.

WorkDrive reports an expired access token two different ways: as a plain 401
on some endpoints, and as a 500 carrying {"errors":[{"id":"F7003"}]} on
others. Both mean the same thing and both are recoverable by refreshing the
token, so neither may be classified as a permanent application error -- doing
so aborts the folder walk mid-scan and the sync is skipped until the local
expiry clock happens to roll over.
"""

import json

import pytest
import requests

from workdrive_sync.api import WorkDriveAPI

INVALID_TOKEN_BODY = '{"errors":[{"id":"F7003","title":"Invalid OAuth token."}]}'
VALIDATION_ERROR_BODY = '{"errors":[{"id":"F000","title":"LESS_THAN_MIN_OCCURANCE"}]}'


class _RefreshingAuth:
    """Mint a fresh token whenever the cached one is cleared, as ZohoAuth does."""

    def __init__(self):
        self._access_token = "tok-1"
        self.refreshes = 0

    def get_access_token(self):
        if self._access_token is None:
            self.refreshes += 1
            self._access_token = f"tok-{self.refreshes + 1}"
        return self._access_token


class _Resp:
    def __init__(self, status_code, reason="", text=""):
        self.status_code = status_code
        self.reason = reason
        self.text = text
        self.ok = 200 <= status_code < 300
        self.headers = {}

    def json(self):
        return json.loads(self.text) if self.text else {}

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(str(self.status_code), response=self)


def test_invalid_token_500_refreshes_and_retries(monkeypatch):
    auth = _RefreshingAuth()
    api = WorkDriveAPI(auth)
    api.MIN_SLEEP = 0
    seen_tokens = []

    def fake_request(method, url, headers=None, **kwargs):
        seen_tokens.append(headers["Authorization"])
        if len(seen_tokens) == 1:
            return _Resp(500, text=INVALID_TOKEN_BODY)
        return _Resp(200, text='{"data": []}')

    monkeypatch.setattr("workdrive_sync.api.requests.request", fake_request)

    resp = api._request("GET", "https://workdrive.zoho.eu/api/v1/files/abc/files")

    assert resp.status_code == 200
    assert len(seen_tokens) == 2, "F7003 must refresh and retry, not fail permanently"
    assert auth.refreshes == 1
    assert seen_tokens[0] != seen_tokens[1], "the retry must carry the refreshed token"


def test_persistently_invalid_token_gives_up_without_infinite_retry(monkeypatch):
    auth = _RefreshingAuth()
    api = WorkDriveAPI(auth)
    api.MIN_SLEEP = 0
    api.MAX_SLEEP = 0
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return _Resp(500, text=INVALID_TOKEN_BODY)

    monkeypatch.setattr("workdrive_sync.api.requests.request", fake_request)

    with pytest.raises(requests.HTTPError):
        api._request("GET", "https://workdrive.zoho.eu/api/v1/files/abc/files")

    assert 1 < calls["n"] <= 10, "must retry a token error but stay bounded"


def test_validation_error_still_fails_fast(monkeypatch):
    auth = _RefreshingAuth()
    api = WorkDriveAPI(auth)
    api.MIN_SLEEP = 0
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return _Resp(500, text=VALIDATION_ERROR_BODY)

    monkeypatch.setattr("workdrive_sync.api.requests.request", fake_request)

    with pytest.raises(requests.HTTPError):
        api._request("POST", "https://workdrive.zoho.eu/api/v1/files")

    assert calls["n"] == 1, "a permanent validation error must not be retried"
    assert auth.refreshes == 0, "a validation error must not touch the token"


def test_permanent_classifier_excludes_token_errors():
    assert WorkDriveAPI._is_permanent_api_error(_Resp(500, text=VALIDATION_ERROR_BODY))
    assert not WorkDriveAPI._is_permanent_api_error(_Resp(500, text=INVALID_TOKEN_BODY))
    assert not WorkDriveAPI._is_permanent_api_error(_Resp(500, text=""))


def test_invalid_token_classifier():
    assert WorkDriveAPI._is_invalid_token_error(_Resp(500, text=INVALID_TOKEN_BODY))
    assert not WorkDriveAPI._is_invalid_token_error(_Resp(500, text=VALIDATION_ERROR_BODY))
    assert not WorkDriveAPI._is_invalid_token_error(_Resp(200, text='{"data": []}'))
