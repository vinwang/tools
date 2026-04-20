#!/usr/bin/env python3
"""Refresh Codex auth tokens through CLIProxyAPI management API."""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_MANAGEMENT_KEY = "123456"
DEFAULT_BASE_URL = "http://localhost:8317/v0/management"
DEFAULT_QUOTA_URL = "https://chatgpt.com/backend-api/wham/usage"
DEFAULT_REFRESH_URL = "https://auth.openai.com/oauth/token"
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_LOG_DIR = "logs"
DEFAULT_REFRESH_THRESHOLD_DAYS = 3


@dataclass(frozen=True)
class AuthCandidate:
    """Management API auth file entry selected for refresh.

    @param auth_id Auth file identifier returned by management API
    @param name Human-readable label
    @param path Local auth JSON path
    @param email Email associated with the auth file
    @param account_id ChatGPT account ID associated with the auth file
    @returns Immutable candidate object
    """

    auth_id: str
    name: str
    path: str
    email: str
    account_id: str


class Logger:
    """Minimal file and stdout logger."""

    def __init__(self, log_dir: Path) -> None:
        """Create a logger and the destination directory.

        @param log_dir Directory where log files are stored
        @returns None
        """

        log_dir.mkdir(parents=True, exist_ok=True)
        filename = f"refresh-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        self.log_path = log_dir / filename

    def write(self, message: str, level: str = "INFO") -> None:
        """Write one log line to file and stdout.

        @param message Log message body
        @param level Log level string
        @returns None
        """

        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{level}] {message}"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    @returns Parsed CLI namespace
    """

    parser = argparse.ArgumentParser(
        description="Refresh Codex auth tokens using CLIProxyAPI management API."
    )
    parser.add_argument("--management-key", default=DEFAULT_MANAGEMENT_KEY, help="Management API bearer token.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Management API base URL.")
    parser.add_argument("--quota-url", default=DEFAULT_QUOTA_URL, help="Quota probe URL.")
    parser.add_argument("--refresh-url", default=DEFAULT_REFRESH_URL, help="OAuth token refresh URL.")
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID, help="OAuth client_id used for refresh.")
    parser.add_argument("--auth-dir", default="", help="Optional auth directory. When provided, scan local JSON files first.")
    parser.add_argument(
        "--refresh-threshold-days",
        type=int,
        default=DEFAULT_REFRESH_THRESHOLD_DAYS,
        help="Only refresh tokens that expire within this many days (default: 3).",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC, help="HTTP timeout in seconds.")
    parser.add_argument("--preferred-auth-id", default="", help="Refresh only the specified auth ID or name.")
    parser.add_argument("--preferred-account", default="", help="Refresh only the specified account email or label.")
    parser.add_argument("--alert-webhook", default="", help="Optional webhook URL for summary notifications.")
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help="Directory used for log files.")
    return parser.parse_args()


def bool_from_value(value: Any) -> bool:
    """Normalize common truthy values used by management API.

    @param value Arbitrary API field value
    @returns Boolean interpretation
    """

    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def get_display_name(item: dict[str, Any]) -> str:
    """Return a stable human-readable label for one auth file entry.

    @param item Auth file dictionary from management API
    @returns Display name or empty string
    """

    for field_name in ("email", "account", "label", "name", "id"):
        value = str(item.get(field_name, "")).strip()
        if value:
            return value
    return ""


def decode_jwt_segment(segment: str) -> dict[str, Any]:
    """Decode one JWT payload segment into a JSON object.

    @param segment Base64url-encoded JWT segment
    @returns Decoded JSON object or empty dict
    """

    raw = str(segment or "").strip()
    if not raw:
        return {}
    padding = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + padding).encode("ascii"))
        parsed = json.loads(decoded.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def get_token_remaining_seconds(access_token: str) -> float:
    """Estimate remaining access token lifetime from JWT exp.

    @param access_token JWT access token
    @returns Remaining seconds or -1 when unavailable
    """

    parts = str(access_token or "").split(".")
    if len(parts) < 2:
        return -1
    payload = decode_jwt_segment(parts[1])
    exp_value = payload.get("exp")
    if not isinstance(exp_value, (int, float)):
        return -1
    return float(exp_value) - time.time()


def parse_expired_time(expired_text: str) -> float:
    """Parse the optional expired field into remaining seconds.

    @param expired_text Expiry timestamp string
    @returns Remaining seconds or -1 when unavailable
    """

    value = str(expired_text or "").strip()
    if not value:
        return -1
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        expiry = datetime.fromisoformat(normalized)
    except ValueError:
        return -1
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return (expiry - datetime.now(timezone.utc)).total_seconds()


def get_auth_remaining_seconds(auth_data: dict[str, Any]) -> float:
    """Estimate remaining auth lifetime from expired field or access token exp.

    @param auth_data Parsed auth JSON object
    @returns Remaining seconds or -1 when unavailable
    """

    expired_remaining = parse_expired_time(str(auth_data.get("expired") or ""))
    if expired_remaining != -1:
        return expired_remaining
    access_token = str(
        extract_string_property_recursive(auth_data, ["access_token", "accessToken"]) or ""
    ).strip()
    if not access_token:
        return -1
    return get_token_remaining_seconds(access_token)


def should_refresh_auth_data(
    auth_data: dict[str, Any],
    refresh_threshold_days: int,
) -> tuple[bool, str]:
    """Determine whether one auth file should perform OAuth refresh.

    @param auth_data Parsed auth JSON object
    @param refresh_threshold_days Refresh threshold in days
    @returns Tuple of refresh decision and reason string
    """

    refresh_token = str(
        extract_string_property_recursive(auth_data, ["refresh_token", "refreshToken"]) or ""
    ).strip()
    if not refresh_token:
        return False, "missing refresh token"

    access_token = str(
        extract_string_property_recursive(auth_data, ["access_token", "accessToken"]) or ""
    ).strip()
    if not access_token:
        return True, "missing access token"

    threshold_seconds = max(0, refresh_threshold_days) * 86400
    remaining_seconds = get_auth_remaining_seconds(auth_data)
    if remaining_seconds == -1:
        return True, "unknown expiry"
    if remaining_seconds <= threshold_seconds:
        return True, f"expires within threshold ({remaining_seconds:.0f}s)"
    return False, f"expires beyond threshold ({remaining_seconds:.0f}s)"


def is_codex_like_payload(payload: dict[str, Any]) -> bool:
    """Determine whether a parsed JSON object looks like a Codex auth file.

    @param payload Parsed auth JSON object
    @returns True when the object matches Codex auth characteristics
    """

    provider = str(payload.get("type") or payload.get("provider") or "").strip().lower()
    if provider == "codex":
        return True
    access_token = extract_string_property_recursive(payload, ["access_token", "accessToken"])
    refresh_token = extract_string_property_recursive(payload, ["refresh_token", "refreshToken"])
    account_id = extract_string_property_recursive(
        payload,
        ["account_id", "accountId", "chatgpt_account_id", "chatgptAccountId"],
    )
    return bool(access_token and (refresh_token or account_id))


def is_candidate_enabled(payload: dict[str, Any]) -> bool:
    """Check whether a local auth payload is eligible for refresh.

    @param payload Parsed auth JSON object
    @returns True when the payload is active and not disabled
    """

    status = str(payload.get("status") or "").strip().lower()
    if status and status != "active":
        return False
    if bool_from_value(payload.get("disabled")):
        return False
    if bool_from_value(payload.get("unavailable")):
        return False
    return True


def build_local_auth_candidate(path: Path, payload: dict[str, Any]) -> AuthCandidate | None:
    """Build one refresh candidate from a local auth JSON file.

    @param path Local auth file path
    @param payload Parsed auth JSON object
    @returns Candidate object or None when file should be skipped
    """

    if not is_codex_like_payload(payload) or not is_candidate_enabled(payload):
        return None
    email = str(payload.get("email") or payload.get("account") or payload.get("label") or "").strip()
    name = get_display_name(payload) or path.stem
    account_id = str(
        payload.get("account_id")
        or payload.get("accountId")
        or extract_string_property_recursive(payload, ["chatgpt_account_id", "chatgptAccountId"])
        or ""
    ).strip()
    auth_id = str(payload.get("id") or payload.get("name") or path.stem).strip() or path.stem
    return AuthCandidate(
        auth_id=auth_id,
        name=name,
        path=str(path),
        email=email,
        account_id=account_id,
    )


def apply_candidate_filters(
    candidates: list[AuthCandidate],
    preferred_auth_id: str,
    preferred_account: str,
) -> list[AuthCandidate]:
    """Filter refresh candidates by auth ID or account selector.

    @param candidates Candidate list before filtering
    @param preferred_auth_id Optional auth ID or display name
    @param preferred_account Optional email or display name
    @returns Filtered candidate list
    """

    filtered = list(candidates)
    if preferred_auth_id:
        filtered = [
            item for item in filtered if item.auth_id == preferred_auth_id or item.name == preferred_auth_id
        ]
    if preferred_account:
        filtered = [
            item for item in filtered if item.email == preferred_account or item.name == preferred_account
        ]
    return filtered


def filter_codex_candidates(
    files: list[dict[str, Any]],
    preferred_auth_id: str,
    preferred_account: str,
) -> list[AuthCandidate]:
    """Filter management API entries down to active Codex auth files.

    @param files Raw auth file entries
    @param preferred_auth_id Optional target auth ID or name
    @param preferred_account Optional target account email or label
    @returns Selected refresh candidates
    """

    candidates = []
    for item in files:
        if not isinstance(item, dict):
            continue
        is_codex = item.get("type") == "codex" or item.get("provider") == "codex"
        is_active = not item.get("status") or item.get("status") == "active"
        is_enabled = not bool_from_value(item.get("disabled"))
        is_available = not bool_from_value(item.get("unavailable"))
        if not (is_codex and is_active and is_enabled and is_available):
            continue
        candidate = AuthCandidate(
            auth_id=str(item.get("id", "")).strip(),
            name=get_display_name(item),
            path=str(item.get("path", "")).strip(),
            email=str(item.get("email", "")).strip(),
            account_id=str(item.get("account_id", "") or item.get("accountId", "")).strip(),
        )
        candidates.append(candidate)
    return apply_candidate_filters(candidates, preferred_auth_id, preferred_account)


def send_json_request(
    url: str,
    method: str,
    headers: dict[str, str],
    timeout: int,
    body: bytes | None = None,
) -> tuple[int, bytes]:
    """Send one HTTP request and return status code and raw body.

    @param url Target request URL
    @param method HTTP method
    @param headers HTTP request headers
    @param timeout Request timeout in seconds
    @param body Optional request body
    @returns Response status code and raw bytes
    """

    request = urllib.request.Request(url=url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def request_json(
    url: str,
    method: str,
    headers: dict[str, str],
    timeout: int,
    body_obj: Any | None = None,
    form_encoded: bool = False,
) -> Any:
    """Send an HTTP request and decode the response JSON body.

    @param url Target request URL
    @param method HTTP method
    @param headers HTTP request headers
    @param timeout Request timeout in seconds
    @param body_obj Optional request body object
    @param form_encoded Whether to encode body as form data
    @returns Parsed JSON response
    """

    body = None
    merged_headers = dict(headers)
    if body_obj is not None:
        if form_encoded:
            body = urllib.parse.urlencode(body_obj).encode("utf-8")
            merged_headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
            merged_headers["Content-Type"] = "application/json; charset=utf-8"
    status, payload = send_json_request(url, method, merged_headers, timeout, body)
    text = payload.decode("utf-8", errors="replace")
    if status < 200 or status >= 300:
        raise RuntimeError(f"HTTP {status}: {text[:500]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"response is not valid JSON: {exc}") from exc


def load_json_file(path: Path) -> dict[str, Any]:
    """Load and normalize one auth JSON file.

    @param path Local auth file path
    @returns Parsed JSON object
    """

    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        raise ValueError(f"auth file is empty: {path}")
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]
    raise ValueError(f"auth file root is not a supported object: {path}")


def extract_string_property_recursive(input_object: Any, names: list[str]) -> str | None:
    """Recursively locate the first non-empty string value by field names.

    @param input_object Nested dict/list structure to search
    @param names Candidate field names
    @returns First matching non-empty string or None
    """

    if input_object is None or isinstance(input_object, (str, int, float, bool)):
        return None
    if isinstance(input_object, dict):
        lowered_names = {name.lower() for name in names}
        for key, value in input_object.items():
            if str(key).lower() in lowered_names and isinstance(value, str) and value.strip():
                return value.strip()
        for value in input_object.values():
            result = extract_string_property_recursive(value, names)
            if result:
                return result
        return None
    if isinstance(input_object, list):
        for item in input_object:
            result = extract_string_property_recursive(item, names)
            if result:
                return result
    return None


def get_access_token_from_auth_data(auth_data: dict[str, Any], path: Path) -> str:
    """Extract access token from a loaded auth JSON object.

    @param auth_data Parsed auth JSON object
    @param path Source file path for error messages
    @returns Access token string
    """

    token = extract_string_property_recursive(
        auth_data,
        [
            "access_token",
            "accessToken",
            "chatgpt_access_token",
            "chatgptAccessToken",
            "bearer_token",
            "bearerToken",
            "auth_token",
            "authToken",
        ],
    )
    if not token:
        auth_header = extract_string_property_recursive(auth_data, ["authorization"])
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise ValueError(f"access token not found in auth file: {path}")
    return token


def update_auth_file(
    path: Path,
    access_token: str,
    id_token: str,
    new_refresh_token: str | None,
) -> None:
    """Write refreshed OAuth fields back into an auth JSON file.

    @param path Local auth file path
    @param access_token Refreshed access token
    @param id_token Refreshed ID token
    @param new_refresh_token Optional refreshed refresh token
    @returns None
    """

    data = load_json_file(path)
    token_container = data.get("tokens") if isinstance(data.get("tokens"), dict) else data
    token_container["access_token"] = access_token
    token_container["id_token"] = id_token
    if new_refresh_token:
        token_container["refresh_token"] = new_refresh_token
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collect_local_auth_candidates(
    auth_dir: Path,
    preferred_auth_id: str,
    preferred_account: str,
) -> list[AuthCandidate]:
    """Collect refresh candidates from a local auth directory.

    @param auth_dir Local auth directory path
    @param preferred_auth_id Optional auth ID or display name
    @param preferred_account Optional email or display name
    @returns Candidate list discovered in the directory
    """

    resolved = auth_dir.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"auth directory not found: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"auth directory is not a directory: {resolved}")

    candidates: list[AuthCandidate] = []
    for path in sorted(resolved.rglob("*.json")):
        try:
            payload = load_json_file(path)
        except Exception:
            continue
        candidate = build_local_auth_candidate(path, payload)
        if candidate is not None:
            candidates.append(candidate)
    return apply_candidate_filters(candidates, preferred_auth_id, preferred_account)


def refresh_oauth_token(
    refresh_url: str,
    client_id: str,
    refresh_token: str,
    timeout: int,
) -> dict[str, Any]:
    """Refresh OAuth tokens through the OpenAI auth endpoint.

    @param refresh_url OAuth refresh endpoint
    @param client_id OAuth client ID
    @param refresh_token Existing refresh token
    @param timeout Request timeout in seconds
    @returns Refresh response JSON
    """

    return request_json(
        url=refresh_url,
        method="POST",
        headers={},
        timeout=timeout,
        body_obj={
            "redirect_uri": DEFAULT_REDIRECT_URI,
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        },
        form_encoded=False,
    )


def probe_quota(quota_url: str, access_token: str, account_id: str, timeout: int) -> Any:
    """Send quota probe request with a bearer token.

    @param quota_url Quota endpoint URL
    @param access_token Access token used for authorization
    @param account_id ChatGPT account ID used for authorization routing
    @param timeout Request timeout in seconds
    @returns Parsed quota response
    """

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    }
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id
    return request_json(
        url=quota_url,
        method="GET",
        headers=headers,
        timeout=timeout,
    )


def send_webhook_alert(webhook_url: str, title: str, text: str, timeout: int, logger: Logger) -> None:
    """Post summary text to an optional webhook endpoint.

    @param webhook_url Webhook URL, ignored when empty
    @param title Alert title
    @param text Alert text
    @param timeout Request timeout in seconds
    @param logger Logger used for warning output
    @returns None
    """

    if not webhook_url.strip():
        return
    try:
        request_json(
            url=webhook_url,
            method="POST",
            headers={},
            timeout=timeout,
            body_obj={"title": title, "text": text},
            form_encoded=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.write(f"Webhook failed: {exc}", "WARN")


def fetch_management_files(base_url: str, management_key: str, timeout: int) -> list[dict[str, Any]]:
    """Load auth file metadata from CLIProxyAPI management API.

    @param base_url Management API base URL
    @param management_key Management API bearer token
    @param timeout Request timeout in seconds
    @returns Auth file entry list
    """

    response = request_json(
        url=base_url.rstrip("/") + "/auth-files",
        method="GET",
        headers={"Authorization": f"Bearer {management_key}"},
        timeout=timeout,
    )
    if not isinstance(response, dict):
        raise RuntimeError("management API response root is not an object")
    files = response.get("files")
    if not isinstance(files, list):
        raise RuntimeError("management API response does not contain files list")
    return [item for item in files if isinstance(item, dict)]


def load_auth_candidates(
    auth_dir: Path | None,
    preferred_auth_id: str,
    preferred_account: str,
    management_loader: Callable[[], list[dict[str, Any]]],
) -> tuple[str, list[AuthCandidate]]:
    """Load candidates from auth directory first, then fallback to management API.

    @param auth_dir Optional local auth directory path
    @param preferred_auth_id Optional auth ID or display name
    @param preferred_account Optional email or display name
    @param management_loader Callback that loads management API entries
    @returns Tuple of source label and candidate list
    """

    if auth_dir is not None:
        local_candidates = collect_local_auth_candidates(auth_dir, preferred_auth_id, preferred_account)
        if local_candidates:
            return "auth-dir", local_candidates
    management_files = management_loader()
    management_candidates = filter_codex_candidates(
        management_files,
        preferred_auth_id,
        preferred_account,
    )
    return "management", management_candidates


def refresh_candidate(candidate: AuthCandidate, args: argparse.Namespace, logger: Logger) -> tuple[bool, str]:
    """Refresh one candidate auth file and probe quota endpoint.

    @param candidate Selected auth file candidate
    @param args Parsed CLI arguments
    @param logger Logger instance
    @returns Tuple of success flag and display name
    """

    path = Path(candidate.path).expanduser()
    auth_data = load_json_file(path)
    refresh_token = str(
        extract_string_property_recursive(auth_data, ["refresh_token", "refreshToken"]) or ""
    ).strip()
    should_refresh, refresh_reason = should_refresh_auth_data(
        auth_data,
        args.refresh_threshold_days,
    )
    if should_refresh:
        logger.write(
            f"Refreshing OAuth token: {candidate.name or candidate.auth_id} | reason={refresh_reason}"
        )
        token_response = refresh_oauth_token(args.refresh_url, args.client_id, refresh_token, args.timeout)
        access_token = str(token_response.get("access_token", "")).strip()
        id_token = str(token_response.get("id_token", "")).strip()
        next_refresh_token = str(token_response.get("refresh_token", "")).strip() or None
        if not access_token or not id_token:
            raise RuntimeError("refresh response missing access_token or id_token")
        update_auth_file(path, access_token, id_token, next_refresh_token)
    else:
        logger.write(
            f"Skip OAuth refresh: {candidate.name or candidate.auth_id} | reason={refresh_reason}"
        )
        access_token = get_access_token_from_auth_data(auth_data, path)
    account_id = candidate.account_id or str(
        extract_string_property_recursive(
            auth_data,
            ["account_id", "accountId", "chatgpt_account_id", "chatgptAccountId"],
        )
        or ""
    ).strip()
    probe_quota(args.quota_url, access_token, account_id, args.timeout)
    logger.write(f"OK: {candidate.name or candidate.auth_id}")
    return True, candidate.name or candidate.auth_id


def build_summary(total: int, success_count: int, fail_count: int, log_path: Path) -> str:
    """Build summary text for console and webhook output.

    @param total Total candidate count
    @param success_count Success count
    @param fail_count Failure count
    @param log_path Log file path
    @returns Summary text
    """

    return f"Refresh done. Total={total} Success={success_count} Fail={fail_count}\nLog={log_path}"


def main() -> int:
    """Run the token refresh workflow.

    @returns Process exit code
    """

    args = parse_args()
    logger = Logger(Path(args.log_dir))
    try:
        logger.write("Started refresh workflow.")
        auth_dir = Path(args.auth_dir).expanduser() if args.auth_dir.strip() else None
        source, candidates = load_auth_candidates(
            auth_dir=auth_dir,
            preferred_auth_id=args.preferred_auth_id,
            preferred_account=args.preferred_account,
            management_loader=lambda: fetch_management_files(
                args.base_url,
                args.management_key,
                args.timeout,
            ),
        )
        if auth_dir is not None:
            if source == "auth-dir":
                logger.write(f"Using local auth directory: {auth_dir.expanduser().resolve()}")
            else:
                logger.write(
                    f"No valid local auth files found in {auth_dir.expanduser().resolve()}, falling back to management API.",
                    "WARN",
                )
        else:
            logger.write("Using management API candidate source.")
        if not candidates:
            raise RuntimeError("no active Codex auth file found")

        success_count = 0
        fail_count = 0
        failed_names: list[str] = []
        for candidate in candidates:
            logger.write(f"Refreshing: account={candidate.name}; id={candidate.auth_id}")
            try:
                refresh_candidate(candidate, args, logger)
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                fail_count += 1
                failed_name = candidate.name or candidate.auth_id
                failed_names.append(failed_name)
                logger.write(f"FAIL: {failed_name} | {exc}", "WARN")

        summary = build_summary(len(candidates), success_count, fail_count, logger.log_path)
        logger.write(summary)
        if failed_names:
            logger.write("Failed accounts: " + ", ".join(failed_names), "WARN")
        send_webhook_alert(
            args.alert_webhook,
            "OK" if fail_count == 0 else "ALARM",
            summary if not failed_names else summary + "\nFailed=" + ", ".join(failed_names),
            args.timeout,
            logger,
        )
        return 0 if fail_count == 0 else 1
    except Exception as exc:  # noqa: BLE001
        logger.write(str(exc), "ERROR")
        send_webhook_alert(args.alert_webhook, "ALARM", str(exc), args.timeout, logger)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
