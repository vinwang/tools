#!/usr/bin/env python3
"""Scan Sub2API import JSON files for Codex account availability and quota state."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

DEFAULT_INCLUDE_PATTERN = "sub2api_accounts_import*.json"
DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_REFRESH_URL = "https://auth.openai.com/oauth/token"
DEFAULT_CLIENT_ID = os.environ.get("CODEX_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
DEFAULT_VERSION = "0.98.0"
DEFAULT_USER_AGENT = "codex_cli_rs/0.98.0 (python-port)"
DEFAULT_WORKERS = min(300, max(50, (os.cpu_count() or 1) * 20))
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF = 0.6
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_CYAN = "\033[36m"
ANSI_MAGENTA = "\033[35m"
UNLIMITED_TEXT_MARKERS = (
    "unlimited",
    "no limit",
    "no-limit",
    "without limit",
    "limitless",
    "不限额",
    "无限额",
    "无限制",
)
UNLIMITED_KEY_HINTS = ("unlimited", "no_limit", "nolimit", "limitless")
LIMIT_LIKE_KEY_HINTS = ("quota", "limit", "cap")
QUOTA_EXCEEDED_TEXT_MARKERS = (
    "usage_limit_reached",
    "usage limit has been reached",
    "quota exceeded",
    "limit exceeded",
    "超出配额",
    "额度已用完",
)


def _is_tty_stdout() -> bool:
    """Check whether stdout is attached to an interactive terminal.

    @returns True when stdout is a TTY
    """

    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _supports_color(disabled: bool) -> bool:
    """Determine whether ANSI color output should be enabled.

    @param disabled Whether color was disabled by CLI flag
    @returns True when ANSI color should be used
    """

    return (not disabled) and _is_tty_stdout() and ("NO_COLOR" not in os.environ)


def _paint(text: str, *codes: str, enabled: bool) -> str:
    """Wrap text with ANSI codes when color output is enabled.

    @param text Plain text to render
    @param codes ANSI codes to apply
    @param enabled Whether color output is enabled
    @returns Rendered text with or without ANSI codes
    """

    if not enabled or not codes:
        return text
    return "".join(codes) + text + ANSI_RESET


def _truncate(text: str, limit: int) -> str:
    """Shorten long text to a fixed width for terminal rendering.

    @param text Original text
    @param limit Maximum output length
    @returns Possibly truncated text
    """

    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 3:
        return "." * limit
    return text[: limit - 3] + "..."


class _ProgressDisplay:
    """Render a single-line terminal progress bar."""

    def __init__(self, enabled: bool) -> None:
        """Create a progress renderer.

        @param enabled Whether progress rendering is enabled
        @returns None
        """

        self.enabled = enabled
        self._last_len = 0
        self._finished = False

    def update(self, current: int, total: int, path: Path) -> None:
        """Refresh the progress bar with the current scan position.

        @param current Completed item count
        @param total Total item count
        @param path Path related to the latest completed item
        @returns None
        """

        if not self.enabled or total <= 0:
            return

        width = shutil.get_terminal_size(fallback=(100, 20)).columns
        bar_width = max(12, min(30, width - 52))
        percent = int((current * 100) / total)
        filled = int((current * bar_width) / total)
        bar = "#" * filled + "-" * (bar_width - filled)
        message = f"[{bar}] {current}/{total} {percent:>3}% {_truncate(path.name, 28)}"
        message = _truncate(message, max(10, width - 1))
        padding = " " * max(0, self._last_len - len(message))
        sys.stdout.write(f"\r{message}{padding}")
        sys.stdout.flush()
        self._last_len = len(message)

    def finish(self) -> None:
        """Terminate the progress bar and move to the next terminal line.

        @returns None
        """

        if not self.enabled or self._finished:
            return
        self._finished = True
        sys.stdout.write("\n")
        sys.stdout.flush()


def _build_probe_headers(access_token: str, account_id: str) -> dict[str, str]:
    """Build Codex probe headers for one account.

    @param access_token OpenAI access token
    @param account_id ChatGPT account ID
    @returns HTTP headers for the probe request
    """

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": DEFAULT_VERSION,
        "Openai-Beta": "responses=experimental",
        "User-Agent": DEFAULT_USER_AGENT,
        "Originator": "codex_cli_rs",
    }
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id
    return headers


def _build_probe_body(model: str) -> bytes:
    """Build the minimal Codex responses API probe payload.

    @param model Model name used for the probe
    @returns UTF-8 encoded JSON request body
    """

    payload = {
        "model": model,
        "stream": True,
        "store": False,
        "instructions": "",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "ping"}],
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _looks_unlimited_from_response(status_code: int | None, response_text: str) -> bool:
    """Infer whether a successful response indicates unlimited quota.

    @param status_code HTTP status code
    @param response_text Response body text
    @returns True when unlimited quota markers are present
    """

    if status_code is None or status_code < 200 or status_code >= 300:
        return False

    lowered = (response_text or "").lower()
    if any(marker in lowered for marker in UNLIMITED_TEXT_MARKERS):
        return True

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        return False

    stack: list[Any] = [parsed]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                key_lc = str(key).lower()
                if any(hint in key_lc for hint in UNLIMITED_KEY_HINTS):
                    if isinstance(value, bool) and value:
                        return True
                    if isinstance(value, (int, float)) and value == -1:
                        return True
                    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "unlimited", "no_limit", "nolimit"}:
                        return True
                if any(hint in key_lc for hint in LIMIT_LIKE_KEY_HINTS):
                    if value is None:
                        return True
                    if isinstance(value, (int, float)) and (value == -1 or value >= 9999):
                        return True
                    if isinstance(value, str) and value.strip().lower() in {"none", "null", "unlimited", "no limit", "no-limit", "无限", "不限额", "无限额"}:
                        return True
                if isinstance(value, (dict, list)):
                    stack.append(value)
                elif isinstance(value, str) and any(marker in value.lower() for marker in UNLIMITED_TEXT_MARKERS):
                    return True
        elif isinstance(current, list):
            stack.extend(current)
    return False


def _detect_quota_exceeded(response_text: str) -> tuple[bool, int | None]:
    """Extract quota exhaustion state and reset timestamp from a response body.

    @param response_text Response body text
    @returns Tuple of quota-exceeded flag and reset timestamp
    """

    if not response_text:
        return False, None

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict) and err.get("type") == "usage_limit_reached":
            resets_at = err.get("resets_at")
            if isinstance(resets_at, (int, float)):
                return True, int(resets_at)
            return True, None

    lowered = response_text.lower()
    if any(marker in lowered for marker in QUOTA_EXCEEDED_TEXT_MARKERS):
        return True, None
    return False, None


def _send_http_request(
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes]:
    """Send one HTTPS request with urllib and return status plus body.

    @param url Target URL
    @param method HTTP method
    @param headers HTTP headers
    @param body Optional request body
    @param timeout Request timeout in seconds
    @returns HTTP status code and raw response body
    """

    request = urllib.request.Request(url=url, data=body, headers=headers, method=method.upper())
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


async def _http_request_with_retry(
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
    retry_attempts: int,
    retry_backoff: float,
) -> tuple[int, bytes]:
    """Send an HTTP request with retry logic for transient network failures.

    @param url Target URL
    @param method HTTP method
    @param headers HTTP headers
    @param body Optional request body
    @param timeout Request timeout in seconds
    @param retry_attempts Total attempts allowed
    @param retry_backoff Base backoff in seconds
    @returns HTTP status code and raw response body
    """

    last_exc: Exception | None = None
    for attempt in range(1, retry_attempts + 1):
        try:
            return await asyncio.to_thread(_send_http_request, url, method, headers, body, timeout)
        except (TimeoutError, urllib.error.URLError, ssl.SSLError) as exc:
            last_exc = exc
            if attempt >= retry_attempts:
                break
            if retry_backoff > 0:
                await asyncio.sleep(retry_backoff * (2 ** (attempt - 1)))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("request failed without a captured exception")


def _refresh_access_token_sync(refresh_url: str, refresh_token: str, timeout: float) -> tuple[str, str]:
    """Refresh an OpenAI access token using a refresh token.

    @param refresh_url Token refresh endpoint
    @param refresh_token Existing refresh token
    @param timeout Request timeout in seconds
    @returns Tuple of new access token and refresh token
    """

    body = urllib.parse.urlencode(
        {
            "client_id": DEFAULT_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "openid profile email",
        }
    ).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    status, resp_body = _send_http_request(refresh_url, "POST", headers, body, timeout)
    if status != 200:
        message = resp_body.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"refresh failed with {status}: {message}")

    parsed = json.loads(resp_body.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise RuntimeError("refresh response is not valid JSON object")

    access_token = _pick_first_text([parsed.get("access_token")])
    next_refresh_token = _pick_first_text([parsed.get("refresh_token")])
    if not access_token:
        raise RuntimeError("refresh succeeded but access_token missing")
    return access_token, next_refresh_token


async def _refresh_access_token(refresh_url: str, refresh_token: str, timeout: float) -> tuple[str, str]:
    """Refresh an OpenAI access token asynchronously.

    @param refresh_url Token refresh endpoint
    @param refresh_token Existing refresh token
    @param timeout Request timeout in seconds
    @returns Tuple of new access token and refresh token
    """

    return await asyncio.to_thread(_refresh_access_token_sync, refresh_url, refresh_token, timeout)


@dataclass(frozen=True)
class AccountTarget:
    """Normalized Sub2API account fields extracted from one import entry.

    @param source_file Source Sub2API JSON file path
    @param account_index Zero-based index inside accounts array
    @param account_name Human-readable account name
    @param email Account email if present
    @param provider Logical provider label
    @param access_token OpenAI access token used for probing
    @param refresh_token OpenAI refresh token used for refresh flow
    @param account_id OpenAI ChatGPT account ID used in headers
    @param base_url Optional per-account probe base URL
    @returns Immutable account scan target
    """

    source_file: str
    account_index: int
    account_name: str
    email: str
    provider: str
    access_token: str
    refresh_token: str
    account_id: str
    base_url: str


@dataclass(frozen=True)
class Sub2ApiCheckResult:
    """Scan result for one Sub2API account entry.

    @param file Source Sub2API JSON file path
    @param account_index Zero-based index inside accounts array
    @param account_name Human-readable account name
    @param provider Logical provider label
    @param email Account email if present
    @param account_id OpenAI ChatGPT account ID
    @param status_code HTTP status returned by probe request
    @param unauthorized_401 Whether probe returned HTTP 401
    @param no_limit_unlimited Whether response indicates unlimited quota
    @param quota_exceeded Whether response indicates quota exhaustion
    @param quota_resets_at Reset timestamp from upstream response
    @param error Error summary if scan failed before response
    @param response_preview Response preview for debugging
    @returns Immutable result row
    """

    file: str
    account_index: int
    account_name: str
    provider: str
    email: str
    account_id: str
    status_code: int | None
    unauthorized_401: bool
    no_limit_unlimited: bool
    quota_exceeded: bool
    quota_resets_at: int | None
    error: str
    response_preview: str

    @classmethod
    def make_error(cls, target: AccountTarget, error_msg: str) -> "Sub2ApiCheckResult":
        """Build an error row for a failed account scan.

        @param target Normalized account target being scanned
        @param error_msg Failure message
        @returns Error result row
        """

        return cls(
            file=target.source_file,
            account_index=target.account_index,
            account_name=target.account_name,
            provider=target.provider,
            email=target.email,
            account_id=target.account_id,
            status_code=None,
            unauthorized_401=False,
            no_limit_unlimited=False,
            quota_exceeded=False,
            quota_resets_at=None,
            error=error_msg,
            response_preview="",
        )


def _load_json_object(path: Path) -> dict[str, Any]:
    """Load one JSON file and assert the root value is an object.

    @param path JSON file path to read
    @returns Parsed root object
    """

    raw = path.read_text(encoding="utf-8-sig")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("root JSON value is not an object")
    return obj


def _collect_input_files(input_path: Path, include_pattern: str, recursive: bool) -> list[Path]:
    """Collect candidate Sub2API import files from a file or directory input.

    @param input_path File or directory provided by CLI
    @param include_pattern Glob pattern used when input is a directory
    @param recursive Whether to scan directories recursively
    @returns Sorted list of JSON files to parse
    """

    resolved = input_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"input path not found: {resolved}")
    if resolved.is_file():
        return [resolved]
    if not resolved.is_dir():
        raise ValueError(f"input path is neither file nor directory: {resolved}")
    iterator = resolved.rglob(include_pattern) if recursive else resolved.glob(include_pattern)
    return sorted(path for path in iterator if path.is_file())


def _pick_first_text(candidates: list[Any]) -> str:
    """Return the first non-empty trimmed string from candidate values.

    @param candidates Candidate values in priority order
    @returns First usable string or empty string
    """

    for value in candidates:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return ""


def _extract_account_target(source_file: Path, account_index: int, account: dict[str, Any]) -> AccountTarget:
    """Normalize one Sub2API account record into scan-ready fields.

    @param source_file Source JSON file path
    @param account_index Zero-based index inside accounts array
    @param account Raw account object from Sub2API import file
    @returns Normalized account target
    """

    credentials = account.get("credentials")
    extra = account.get("extra")
    credentials_obj = credentials if isinstance(credentials, dict) else {}
    extra_obj = extra if isinstance(extra, dict) else {}

    account_name = _pick_first_text(
        [account.get("name"), extra_obj.get("email"), credentials_obj.get("email")]
    ) or f"account-{account_index + 1:03d}"
    email = _pick_first_text([extra_obj.get("email"), credentials_obj.get("email"), account.get("name")])
    provider = _pick_first_text([credentials_obj.get("type"), account.get("platform"), account.get("type")]) or "openai"
    account_id = _pick_first_text(
        [
            credentials_obj.get("chatgpt_account_id"),
            credentials_obj.get("account_id"),
            credentials_obj.get("accountId"),
        ]
    )
    base_url = _pick_first_text([credentials_obj.get("base_url"), credentials_obj.get("baseUrl")])

    return AccountTarget(
        source_file=str(source_file),
        account_index=account_index,
        account_name=account_name,
        email=email,
        provider=provider,
        access_token=_pick_first_text([credentials_obj.get("access_token"), credentials_obj.get("accessToken")]),
        refresh_token=_pick_first_text([credentials_obj.get("refresh_token"), credentials_obj.get("refreshToken")]),
        account_id=account_id,
        base_url=base_url,
    )


def _iter_account_targets(source_file: Path, payload: dict[str, Any]) -> list[AccountTarget]:
    """Extract all account scan targets from one Sub2API import payload.

    @param source_file Source JSON file path
    @param payload Parsed Sub2API import object
    @returns Account targets contained in accounts array
    """

    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        raise ValueError("missing accounts array")
    targets: list[AccountTarget] = []
    for index, account in enumerate(accounts):
        if not isinstance(account, dict):
            raise ValueError(f"accounts[{index}] is not an object")
        targets.append(_extract_account_target(source_file, index, account))
    return targets


async def _scan_account(
    target: AccountTarget,
    args: argparse.Namespace,
    probe_body: bytes,
) -> Sub2ApiCheckResult:
    """Probe one normalized Sub2API account against the Codex endpoint.

    @param target Normalized account target
    @param args Parsed CLI arguments
    @param probe_body Request body used for probe requests
    @returns Scan result for the account
    """

    access_token = target.access_token
    try:
        if args.refresh_before_check and target.refresh_token:
            access_token, _ = await _refresh_access_token(
                args.refresh_url,
                target.refresh_token,
                args.timeout,
            )
    except Exception as exc:  # noqa: BLE001
        return Sub2ApiCheckResult.make_error(target, str(exc))

    if not access_token:
        return Sub2ApiCheckResult.make_error(target, "missing access token")

    probe_base_url = target.base_url or args.base_url
    probe_url = probe_base_url.rstrip("/") + "/" + args.quota_path.lstrip("/")
    headers = _build_probe_headers(access_token, target.account_id)

    try:
        status, resp_body = await _http_request_with_retry(
            url=probe_url,
            method="POST",
            headers=headers,
            body=probe_body,
            timeout=args.timeout,
            retry_attempts=args.retry_attempts,
            retry_backoff=args.retry_backoff,
        )
    except Exception as exc:  # noqa: BLE001
        return Sub2ApiCheckResult.make_error(target, f"network error: {exc}")

    response_text = resp_body.decode("utf-8", errors="replace")
    quota_exceeded, quota_resets_at = _detect_quota_exceeded(response_text)
    return Sub2ApiCheckResult(
        file=target.source_file,
        account_index=target.account_index,
        account_name=target.account_name,
        provider=target.provider,
        email=target.email,
        account_id=target.account_id,
        status_code=status,
        unauthorized_401=(status == 401),
        no_limit_unlimited=_looks_unlimited_from_response(status, response_text),
        quota_exceeded=quota_exceeded,
        quota_resets_at=quota_resets_at,
        error="",
        response_preview=response_text[:300],
    )


async def _scan_targets(
    targets: list[AccountTarget],
    args: argparse.Namespace,
    probe_body: bytes,
    progress_callback: Callable[[int, int, Path], None] | None,
) -> list[Sub2ApiCheckResult]:
    """Scan multiple account targets concurrently with shared connection pooling.

    @param targets Account targets to scan
    @param args Parsed CLI arguments
    @param probe_body Request body used for probe requests
    @param progress_callback Optional progress callback
    @returns Ordered scan results
    """

    if not targets:
        return []

    total = len(targets)
    workers = min(args.workers, total)
    semaphore = asyncio.Semaphore(workers)
    completed = 0

    async def _worker(index: int, target: AccountTarget) -> tuple[int, Sub2ApiCheckResult]:
        """Run one target scan under the shared concurrency limit.

        @param index Stable result ordering index
        @param target Account target to scan
        @returns Ordering index and result row
        """

        nonlocal completed
        async with semaphore:
            result = await _scan_account(target, args, probe_body)
            completed += 1
            if progress_callback:
                progress_callback(completed, total, Path(target.source_file))
            return index, result

    tasks = [
        asyncio.create_task(_worker(index, target))
        for index, target in enumerate(targets, start=1)
    ]
    ordered = await asyncio.gather(*tasks)

    ordered.sort(key=lambda item: item[0])
    return [result for _, result in ordered]


def _status_label(item: Sub2ApiCheckResult, use_color: bool) -> str:
    """Render a compact status label for table output.

    @param item Result row to summarize
    @param use_color Whether ANSI color is enabled
    @returns Compact printable status label
    """

    if item.unauthorized_401:
        return _paint("401", ANSI_BOLD, ANSI_RED, enabled=use_color)
    if item.quota_exceeded:
        return _paint("LIM", ANSI_BOLD, ANSI_MAGENTA, enabled=use_color)
    if item.status_code is None:
        return _paint("ERR", ANSI_BOLD, ANSI_YELLOW, enabled=use_color)
    if 200 <= item.status_code < 300:
        return _paint(str(item.status_code), ANSI_GREEN, enabled=use_color)
    if item.status_code >= 500:
        return _paint(str(item.status_code), ANSI_RED, enabled=use_color)
    return _paint(str(item.status_code), ANSI_YELLOW, enabled=use_color)


def _print_table(results: list[Sub2ApiCheckResult], use_color: bool) -> None:
    """Print a concise human-readable summary and account-level findings.

    @param results Scan result rows
    @param use_color Whether ANSI color is enabled
    @returns None
    """

    if not results:
        print(_paint("No Sub2API accounts found.", ANSI_YELLOW, enabled=use_color))
        return

    unauthorized = [item for item in results if item.unauthorized_401]
    quota_exceeded = [item for item in results if item.quota_exceeded and not item.unauthorized_401]
    unlimited = [item for item in results if item.no_limit_unlimited]
    ok_count = sum(1 for item in results if item.status_code is not None and 200 <= item.status_code < 300)
    failed_count = len(results) - ok_count

    print(_paint("Scan Summary", ANSI_BOLD, ANSI_CYAN, enabled=use_color))
    print(f"  checked accounts    : {len(results)}")
    print(f"  unauthorized (401)  : {len(unauthorized)}")
    print(f"  quota-exceeded      : {len(quota_exceeded)}")
    print(f"  no-limit/unlimited  : {len(unlimited)}")
    print(f"  non-2xx or errors   : {failed_count}")
    print()

    for item in results:
        email_suffix = f" <{item.email}>" if item.email else ""
        location = f"{item.file}#{item.account_index + 1}"
        print(f"  [{_status_label(item, use_color)}] {location} {item.account_name}{email_suffix}")
        if item.error:
            print(f"      error: {item.error}")
        elif item.quota_exceeded and item.quota_resets_at:
            print(f"      resets_at: {item.quota_resets_at}")


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the Sub2API scanner.

    @returns Configured argument parser
    """

    parser = argparse.ArgumentParser(
        description="Scan Sub2API account import JSON files and detect Codex 401/quota/unlimited state."
    )
    parser.add_argument("--input", default=".", help="Sub2API import JSON file or directory (default: current directory)")
    parser.add_argument("--include", default=DEFAULT_INCLUDE_PATTERN, help=f"Glob used when input is a directory (default: {DEFAULT_INCLUDE_PATTERN})")
    parser.add_argument("--recursive", action="store_true", help="Scan directories recursively.")
    parser.add_argument("--base-url", default=DEFAULT_CODEX_BASE_URL, help=f"Codex base URL (default: {DEFAULT_CODEX_BASE_URL})")
    parser.add_argument("--quota-path", default="/responses", help="API path used for quota/auth probe (default: /responses)")
    parser.add_argument("--model", default="gpt-5", help="Model used in probe request body (default: gpt-5)")
    parser.add_argument("--timeout", type=float, default=20, help="HTTP timeout in seconds (default: 20)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Max concurrent async requests (default: {DEFAULT_WORKERS})")
    parser.add_argument("--retry-attempts", type=int, default=DEFAULT_RETRY_ATTEMPTS, help=f"Total attempts for network errors per account (default: {DEFAULT_RETRY_ATTEMPTS})")
    parser.add_argument("--retry-backoff", type=float, default=DEFAULT_RETRY_BACKOFF, help=f"Base seconds for exponential retry backoff (default: {DEFAULT_RETRY_BACKOFF})")
    parser.add_argument("--refresh-before-check", action="store_true", help="Refresh access token with refresh_token before probe.")
    parser.add_argument("--refresh-url", default=DEFAULT_REFRESH_URL, help=f"Token refresh endpoint (default: {DEFAULT_REFRESH_URL})")
    parser.add_argument("--output-json", action="store_true", help="Print full results as JSON instead of table view.")
    parser.add_argument("--no-progress", action="store_true", help="Disable live scan progress in terminal output.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output.")
    return parser


async def async_main() -> int:
    """Run the CLI workflow and return a process exit code.

    @returns Process exit code
    """

    parser = _build_parser()
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.retry_attempts < 1:
        parser.error("--retry-attempts must be >= 1")
    if args.retry_backoff < 0:
        parser.error("--retry-backoff must be >= 0")

    use_color = _supports_color(args.no_color) and (not args.output_json)
    progress_enabled = _is_tty_stdout() and (not args.no_progress) and (not args.output_json)
    progress = _ProgressDisplay(progress_enabled)
    probe_body = _build_probe_body(args.model)

    try:
        input_files = _collect_input_files(Path(args.input), args.include, args.recursive)
        targets: list[AccountTarget] = []
        for file_path in input_files:
            payload = _load_json_object(file_path)
            targets.extend(_iter_account_targets(file_path, payload))
    except Exception as exc:  # noqa: BLE001
        progress.finish()
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if progress_enabled:
        print(_paint("Scanning Sub2API accounts...", enabled=use_color))

    try:
        results = await _scan_targets(
            targets,
            args,
            probe_body,
            progress.update if progress_enabled else None,
        )
    except Exception as exc:  # noqa: BLE001
        progress.finish()
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    progress.finish()

    if args.output_json:
        print(json.dumps({"results": [asdict(item) for item in results]}, ensure_ascii=False, indent=2))
    else:
        _print_table(results, use_color=use_color)
    return 1 if any(item.unauthorized_401 for item in results) else 0


def main() -> int:
    """Run the async entry point with platform-specific loop handling.

    @returns Process exit code
    """

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nScan interrupted by user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
