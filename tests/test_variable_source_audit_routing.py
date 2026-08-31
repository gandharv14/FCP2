from __future__ import annotations

import hashlib
import io
import json
import urllib.error

from xl_task_build import DEFAULT_PROJECT_ID
from xl_variable_source_audit import (
    _http_error_details,
    _request_config_fingerprint,
)


def test_sol_gateway_uses_active_project() -> None:
    assert DEFAULT_PROJECT_ID == "cms6m4urm006n07z8ecxi1oi2"


def test_http_error_details_keep_only_bounded_safe_fields() -> None:
    body = json.dumps({
        "error": {
            "message": "Invalid or deleted project_id",
            "type": "invalid_request_error",
            "param": "x-labelbox-context",
            "code": 400,
            "ignored": "workbook data must not be retained",
        }
    }).encode()
    error = urllib.error.HTTPError(
        "https://example.test/chat/completions",
        400,
        "Bad Request",
        {},
        io.BytesIO(body),
    )

    assert _http_error_details(error) == {
        "status": 400,
        "reason": "Bad Request",
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "response_error": {
            "type": "invalid_request_error",
            "param": "x-labelbox-context",
            "code": 400,
            "message": "Invalid or deleted project_id",
        },
    }


def test_request_fingerprint_excludes_prompts_and_is_stable() -> None:
    first = _request_config_fingerprint(
        "https://litellm.labelbox.com/",
        "openai/gpt-5.6-sol",
        DEFAULT_PROJECT_ID,
    )
    second = _request_config_fingerprint(
        "https://litellm.labelbox.com",
        "openai/gpt-5.6-sol",
        DEFAULT_PROJECT_ID,
    )

    assert first == second
    assert len(first) == 64
