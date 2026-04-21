#!/usr/bin/env python3
"""Convert CLIProxyAPI auth files into sub2api import payloads and optionally import them."""

from __future__ import annotations

import argparse
import base64
import json
import re
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request


SUPPORTED_PROVIDERS = {"codex", "gemini", "claude", "antigravity"}
DEFAULT_CONCURRENCY = 10
DEFAULT_PRIORITY = 1
DEFAULT_RATE_MULTIPLIER = 1
DEFAULT_LOAD_FACTOR = 10
ACCOUNT_NAME_MAX_LEN = 100
CONFIG_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_INPUT_DIR_NAME = "cpa_token"
DEFAULT_OUTPUT_DIR_NAME = "sub2api_token"
DEFAULT_MERGED_OUTPUT_NAME = "sub2api-merged.json"
DEFAULT_CONFIG_NAME = "config.json"


@dataclass
class ConvertedAccount:
    source_path: Path
    relative_path: Path
    provider: str
    account: dict[str, Any]


class ConversionError(ValueError):
    """Raised when a source file cannot be converted safely."""


class ConfigError(ValueError):
    """Raised when config.json is invalid."""


class ImportError(ValueError):
    """Raised when sub2api import fails."""


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def default_config_template() -> dict[str, Any]:
    """Build the default config.json template.

    @returns Default configuration structure written on first run
    """
    return {
        "version": CONFIG_VERSION,
        "sub2api": {
            "auto_import": False,
            "base_url": "",
            "auth_mode": "admin_api_key",
            "admin_api_key": "",
            "bearer_token": "",
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "verify_tls": True,
            "skip_default_group_bind": True,
        },
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments for the converter.

    @param argv Raw command-line arguments without the program name
    @returns Parsed argparse namespace
    """
    parser = argparse.ArgumentParser(
        description=(
            "Convert CLIProxyAPI auth JSON files from ./cpa_token into ./sub2api_token "
            "or a single merged sub2api import file, and optionally import them into sub2api."
        )
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        help="Optional input file or directory. Defaults to <script_dir>/cpa_token.",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        help="Output directory. Defaults to <script_dir>/sub2api_token.",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        help="Config file path. Defaults to <script_dir>/config.json.",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help=(
            "Merge all converted accounts into a single JSON file using the default output name "
            "under --output-dir."
        ),
    )
    parser.add_argument(
        "--merge-output",
        dest="merge_output_path",
        help=(
            "Write all converted accounts into a single JSON file and import that merged payload once. "
            "When set, per-file output under --output-dir is skipped."
        ),
    )
    parser.add_argument(
        "--file-regex",
        dest="file_regex",
        help=(
            "Only process JSON files whose relative path or file name matches this regular expression "
            "when the input path is a directory."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail immediately on the first invalid or unsupported input file.",
    )
    parser.add_argument(
        "--no-import",
        action="store_true",
        help="Skip the auto-import phase even if config.json enables it.",
    )
    return parser.parse_args(argv)


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path | None]:
    """Resolve all filesystem paths used by the converter.

    @param args Parsed command-line arguments
    @returns Input path, output directory, config path, and optional merged output path
    """
    root = script_dir()
    input_path = Path(args.input_path).expanduser().resolve() if args.input_path else (root / DEFAULT_INPUT_DIR_NAME)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else (root / DEFAULT_OUTPUT_DIR_NAME)
    config_path = Path(args.config_path).expanduser().resolve() if args.config_path else (root / DEFAULT_CONFIG_NAME)
    if args.merge_output_path:
        merge_output_path = Path(args.merge_output_path).expanduser().resolve()
    elif args.merge:
        merge_output_path = (output_dir / DEFAULT_MERGED_OUTPUT_NAME).resolve()
    else:
        merge_output_path = None
    return input_path, output_dir, config_path, merge_output_path


def truncate_account_name(name: str) -> str:
    """Trim and limit an account name for sub2api.

    @param name Raw account name candidate
    @returns Safe account name within the max length limit
    """
    trimmed = name.strip()
    if not trimmed:
        return "imported-account"
    return trimmed[:ACCOUNT_NAME_MAX_LEN]


def build_account_name(source_path: Path) -> str:
    """Build the exported account name from a source file path.

    @param source_path Source JSON file path
    @returns Normalized account name
    """
    return truncate_account_name(source_path.stem)


def payload_header(exported_at: str | None = None) -> dict[str, Any]:
    """Create the top-level sub2api export payload.

    @param exported_at Optional explicit export timestamp
    @returns Base payload dictionary for sub2api import/export files
    """
    return {
        "exported_at": exported_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "proxies": [],
        "accounts": [],
    }


def build_payload(*accounts: dict[str, Any], exported_at: str | None = None) -> dict[str, Any]:
    """Build a complete sub2api payload from converted accounts.

    @param accounts Account records to include
    @param exported_at Optional explicit export timestamp
    @returns Payload ready to write or import
    """
    payload = payload_header(exported_at=exported_at)
    payload["accounts"].extend(accounts)
    return payload


def build_extra(provider: str, source_path: Path, **extra: Any) -> dict[str, Any]:
    """Build the sub2api extra metadata block.

    @param provider Source provider name
    @param source_path Source file path
    @param extra Optional extra fields to merge
    @returns Extra metadata block
    """
    del provider
    del source_path
    payload: dict[str, Any] = {"load_factor": DEFAULT_LOAD_FACTOR}
    for key, value in extra.items():
        if value is not None and value != "":
            payload[key] = value
    return payload


def parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON string and require a top-level object.

    @param raw Raw JSON text
    @returns Parsed JSON object
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConversionError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ConversionError("top-level JSON must be an object")
    return data


def parse_datetime(value: Any) -> datetime | None:
    """Parse supported date-like values into a timezone-aware datetime.

    @param value Datetime, timestamp, or string representation
    @returns Parsed datetime or None when parsing fails
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(text), tz=timezone.utc)
            except ValueError:
                return None

    return None


def to_unix_seconds_string(value: Any) -> str | None:
    """Convert a date-like value into Unix timestamp seconds as a string.

    @param value Source date-like value
    @returns Unix timestamp string or None when conversion fails
    """
    dt = parse_datetime(value)
    if dt is None:
        return None
    return str(int(dt.timestamp()))


def to_rfc3339_string(value: Any) -> str | None:
    """Convert a date-like value into an RFC3339 timestamp string.

    @param value Source date-like value
    @returns RFC3339 string or None when conversion fails
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and parse_datetime(stripped) is not None:
            return stripped
    dt = parse_datetime(value)
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_string(mapping: dict[str, Any], key: str) -> str:
    """Return a trimmed string value from a mapping.

    @param mapping Source mapping to read from
    @param key Key name to fetch
    @returns Trimmed string value or an empty string when the value is not a string
    """
    value = mapping.get(key)
    if isinstance(value, str):
        return value.strip()
    return ""


def join_scope(value: Any) -> str:
    """Normalize scope values into a single space-delimited string.

    @param value Raw scope value from source data
    @returns Normalized scope string or an empty string when unsupported
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        scopes = [str(item).strip() for item in value if str(item).strip()]
        return " ".join(scopes)
    return ""


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode the payload section of a JWT token.

    @param token JWT token string
    @returns Parsed JWT payload claims object
    """
    token = token.strip()
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        raise ConversionError("invalid JWT format")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConversionError("failed to decode JWT payload") from exc
    if not isinstance(claims, dict):
        raise ConversionError("JWT payload is not an object")
    return claims


def build_account_record(
    *,
    name: str,
    platform: str,
    account_type: str,
    credentials: dict[str, Any],
    extra: dict[str, Any],
    concurrency: int = DEFAULT_CONCURRENCY,
    priority: int = DEFAULT_PRIORITY,
    rate_multiplier: int | float = DEFAULT_RATE_MULTIPLIER,
    auto_pause_on_expired: bool = True,
) -> dict[str, Any]:
    """Build a sub2api account record.

    @param name Account display name
    @param platform Target platform name for sub2api
    @param account_type Account type value for sub2api
    @param credentials Credentials payload to import
    @param extra Extra metadata stored alongside the account
    @param concurrency Account concurrency limit
    @param priority Account priority value
    @param rate_multiplier Rate multiplier for the account
    @param auto_pause_on_expired Flag to pause expired accounts automatically
    @returns Account record ready for inclusion in the import payload
    """
    return {
        "name": name,
        "platform": platform,
        "type": account_type,
        "credentials": credentials,
        "extra": extra,
        "concurrency": concurrency,
        "priority": priority,
        "rate_multiplier": rate_multiplier,
        "auto_pause_on_expired": auto_pause_on_expired,
    }


def convert_codex(data: dict[str, Any], source_path: Path) -> dict[str, Any]:
    """Convert a CLIProxyAPI Codex account into a sub2api import account.

    @param data Parsed CPA source payload
    @param source_path Source file path used for naming and trace metadata
    @returns Converted sub2api account record
    """
    access_token = ensure_string(data, "access_token")
    refresh_token = ensure_string(data, "refresh_token")
    if not access_token:
        raise ConversionError("codex access_token is required")
    if not refresh_token:
        raise ConversionError("codex refresh_token is required")

    id_token = ensure_string(data, "id_token")
    claims: dict[str, Any] = {}
    auth_claims: dict[str, Any] = {}
    if id_token:
        try:
            claims = decode_jwt_payload(id_token)
            nested = claims.get("https://api.openai.com/auth")
            if isinstance(nested, dict):
                auth_claims = nested
        except ConversionError:
            claims = {}
            auth_claims = {}

    email = ensure_string(data, "email") or str(claims.get("email", "")).strip()
    organization_id = str(auth_claims.get("organization_id", "")).strip()
    if not organization_id:
        organizations = auth_claims.get("organizations")
        if isinstance(organizations, list) and organizations:
            first = organizations[0]
            if isinstance(first, dict):
                organization_id = str(first.get("id", "")).strip()

    credentials: dict[str, Any] = {
        "refresh_token": refresh_token,
    }
    if access_token:
        credentials["access_token"] = access_token
    if id_token:
        credentials["id_token"] = id_token
    if email:
        credentials["email"] = email

    expires_at = to_rfc3339_string(data.get("expired"))
    if expires_at:
        credentials["expires_at"] = expires_at

    chatgpt_account_id = str(auth_claims.get("chatgpt_account_id", "")).strip() or ensure_string(data, "account_id")
    if chatgpt_account_id:
        credentials["chatgpt_account_id"] = chatgpt_account_id

    chatgpt_user_id = str(auth_claims.get("chatgpt_user_id", "")).strip()
    if chatgpt_user_id:
        credentials["chatgpt_user_id"] = chatgpt_user_id

    if organization_id:
        credentials["organization_id"] = organization_id

    plan_type = str(auth_claims.get("chatgpt_plan_type", "")).strip()
    if plan_type:
        credentials["plan_type"] = plan_type

    extra = build_extra(
        "codex",
        source_path,
        privacy_mode=data.get("privacy_mode"),
    )

    return build_account_record(
        name=build_account_name(source_path),
        platform="openai",
        account_type="oauth",
        credentials=credentials,
        extra=extra,
    )


def convert_gemini(data: dict[str, Any], source_path: Path) -> dict[str, Any]:
    """Convert a CLIProxyAPI Gemini account into a sub2api import account.

    @param data Parsed CPA source payload
    @param source_path Source file path used for naming and trace metadata
    @returns Converted sub2api account record
    """
    token = data.get("token")
    if not isinstance(token, dict):
        raise ConversionError("gemini token object is required")

    access_token = ensure_string(token, "access_token")
    refresh_token = ensure_string(token, "refresh_token")
    if not access_token:
        raise ConversionError("gemini token.access_token is required")
    if not refresh_token:
        raise ConversionError("gemini token.refresh_token is required")

    project_id = ensure_string(data, "project_id")
    if not project_id:
        raise ConversionError("gemini project_id is required")
    normalized_project = project_id.upper()
    if normalized_project == "GOOGLE_ONE" or normalized_project == "ALL" or "," in project_id:
        raise ConversionError(f"unsupported gemini project_id shape: {project_id}")

    credentials: dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "project_id": project_id,
        "token_type": ensure_string(token, "token_type") or "Bearer",
    }

    expires_at = to_unix_seconds_string(token.get("expiry"))
    if not expires_at:
        expires_at = to_unix_seconds_string(token.get("expires_at"))
    if expires_at:
        credentials["expires_at"] = expires_at

    scope = join_scope(token.get("scope"))
    if not scope:
        scope = join_scope(token.get("scopes"))
    if scope:
        credentials["scope"] = scope

    extra = build_extra(
        "gemini",
        source_path,
        source_auto=data.get("auto"),
        source_checked=data.get("checked"),
    )

    return build_account_record(
        name=build_account_name(source_path),
        platform="gemini",
        account_type="oauth",
        credentials=credentials,
        extra=extra,
    )


def convert_claude(data: dict[str, Any], source_path: Path) -> dict[str, Any]:
    """Convert a CLIProxyAPI Claude account into a sub2api import account.

    @param data Parsed CPA source payload
    @param source_path Source file path used for naming and trace metadata
    @returns Converted sub2api account record
    """
    access_token = ensure_string(data, "access_token")
    refresh_token = ensure_string(data, "refresh_token")
    if not access_token:
        raise ConversionError("claude access_token is required")
    if not refresh_token:
        raise ConversionError("claude refresh_token is required")

    credentials: dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": ensure_string(data, "token_type") or "Bearer",
    }

    expires_at = to_unix_seconds_string(data.get("expired"))
    if expires_at:
        credentials["expires_at"] = expires_at

    extra = build_extra(
        "claude",
        source_path,
        source_last_refresh=data.get("last_refresh"),
    )
    email = ensure_string(data, "email")
    if email:
        extra["email_address"] = email

    return build_account_record(
        name=build_account_name(source_path),
        platform="anthropic",
        account_type="oauth",
        credentials=credentials,
        extra=extra,
    )


def convert_antigravity(data: dict[str, Any], source_path: Path) -> dict[str, Any]:
    """Convert a CLIProxyAPI Antigravity account into a sub2api import account.

    @param data Parsed CPA source payload
    @param source_path Source file path used for naming and trace metadata
    @returns Converted sub2api account record
    """
    access_token = ensure_string(data, "access_token")
    refresh_token = ensure_string(data, "refresh_token")
    if not access_token:
        raise ConversionError("antigravity access_token is required")
    if not refresh_token:
        raise ConversionError("antigravity refresh_token is required")

    credentials: dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": ensure_string(data, "token_type") or "Bearer",
    }

    email = ensure_string(data, "email")
    if email:
        credentials["email"] = email

    project_id = ensure_string(data, "project_id")
    if project_id:
        credentials["project_id"] = project_id

    expires_at = to_unix_seconds_string(data.get("expired"))
    if not expires_at:
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)):
            expires_at = str(int((datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))).timestamp()))
        elif isinstance(expires_in, str):
            expires_text = expires_in.strip()
            if expires_text:
                try:
                    expires_at = str(
                        int((datetime.now(timezone.utc) + timedelta(seconds=float(expires_text))).timestamp())
                    )
                except ValueError:
                    expires_at = None
    if expires_at:
        credentials["expires_at"] = expires_at

    extra = build_extra(
        "antigravity",
        source_path,
        source_timestamp=data.get("timestamp"),
    )

    return build_account_record(
        name=build_account_name(source_path),
        platform="antigravity",
        account_type="oauth",
        credentials=credentials,
        extra=extra,
    )


CONVERTERS: dict[str, Callable[[dict[str, Any], Path], dict[str, Any]]] = {
    "codex": convert_codex,
    "gemini": convert_gemini,
    "claude": convert_claude,
    "antigravity": convert_antigravity,
}


def relative_output_path(source_file: Path, input_path: Path) -> Path:
    """Compute the output path relative to the chosen input path.

    @param source_file Source JSON file path
    @param input_path Input file or directory path
    @returns Relative output path used under the output directory
    """
    if input_path.is_file():
        return Path(source_file.name)
    return source_file.relative_to(input_path)


def is_hidden_relative_path(path: Path) -> bool:
    """Detect whether a relative path contains hidden segments.

    @param path Relative path to inspect
    @returns True when any path segment starts with a dot
    """
    return any(part.startswith(".") for part in path.parts)


def compile_file_regex(pattern: str | None) -> re.Pattern[str] | None:
    """Compile the optional file selection regular expression.

    @param pattern Regular expression text from CLI arguments
    @returns Compiled regex pattern or None when no pattern is provided
    """
    if pattern is None:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid --file-regex: {exc}") from exc


def matches_file_regex(file_path: Path, input_path: Path, file_regex: re.Pattern[str] | None) -> bool:
    """Check whether a source file matches the optional selection regex.

    @param file_path Candidate source file path
    @param input_path Input file or directory path
    @param file_regex Optional compiled regular expression
    @returns True when the file should be processed
    """
    if file_regex is None:
        return True
    relative_path = file_path.name if input_path.is_file() else file_path.relative_to(input_path).as_posix()
    return bool(file_regex.search(relative_path) or file_regex.search(file_path.name))


def iter_input_files(
    input_path: Path,
    excluded_paths: Iterable[Path] = (),
    file_regex: re.Pattern[str] | None = None,
) -> Iterable[Path]:
    """Iterate source JSON files under the selected input path.

    @param input_path Input file or directory path
    @param excluded_paths Paths that must not be processed
    @param file_regex Optional compiled regex used to filter directory entries
    @returns Iterable of source JSON files to convert
    """
    excluded = {path.resolve() for path in excluded_paths}
    if input_path.is_file():
        if input_path.resolve() not in excluded and matches_file_regex(input_path, input_path, file_regex):
            yield input_path
        return
    for path in sorted(input_path.rglob("*.json")):
        if (
            path.is_file()
            and path.resolve() not in excluded
            and not is_hidden_relative_path(path.relative_to(input_path))
            and matches_file_regex(path, input_path, file_regex)
        ):
            yield path


def convert_source_file(source_path: Path) -> tuple[str, dict[str, Any]]:
    """Convert a single CPA source file into a provider name and account record.

    @param source_path Source JSON file path
    @returns Provider name and converted sub2api account record
    """
    raw = source_path.read_text(encoding="utf-8")
    data = parse_json_object(raw)

    provider = ensure_string(data, "type")
    if not provider:
        raise ConversionError("provider type is required")
    if provider not in SUPPORTED_PROVIDERS:
        raise ConversionError(f"unsupported provider type: {provider}")

    converter = CONVERTERS[provider]
    account = converter(data, source_path)
    return provider, account


def write_payload(output_path: Path, payload: dict[str, Any]) -> None:
    """Write a sub2api payload to disk as formatted JSON.

    @param output_path Destination JSON file path
    @param payload Payload dictionary to write
    @returns None
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def create_config_template(config_path: Path) -> dict[str, Any]:
    """Create a default config.json file on disk.

    @param config_path Destination config file path
    @returns The config structure that was written
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    template = default_config_template()
    config_path.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return template


def load_config(config_path: Path) -> tuple[dict[str, Any], bool]:
    """Load config.json, creating a template when it does not exist.

    @param config_path Config file path
    @returns Loaded config and a flag indicating whether a template was created
    """
    if not config_path.exists():
        return create_config_template(config_path), True

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid config.json: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise ConfigError("config.json top-level value must be an object")

    template = default_config_template()
    merged = {
        "version": data.get("version", template["version"]),
        "sub2api": dict(template["sub2api"]),
    }
    sub2api = data.get("sub2api")
    if sub2api is not None and not isinstance(sub2api, dict):
        raise ConfigError("config.json field sub2api must be an object")
    if isinstance(sub2api, dict):
        merged["sub2api"].update(sub2api)
    return merged, False


def validate_import_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize auto-import configuration.

    @param config Loaded config structure
    @returns Normalized import settings ready for HTTP requests
    """
    sub2api = config.get("sub2api")
    if not isinstance(sub2api, dict):
        raise ConfigError("config.json field sub2api must be an object")

    auth_mode = str(sub2api.get("auth_mode", "admin_api_key")).strip().lower()
    if auth_mode not in {"admin_api_key", "bearer_token"}:
        raise ConfigError("sub2api.auth_mode must be admin_api_key or bearer_token")

    base_url = str(sub2api.get("base_url", "")).strip()
    if not base_url:
        raise ConfigError("sub2api.base_url is required when auto_import is enabled")

    timeout_raw = sub2api.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    try:
        timeout_seconds = float(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError("sub2api.timeout_seconds must be a number") from exc
    if timeout_seconds <= 0:
        raise ConfigError("sub2api.timeout_seconds must be > 0")

    verify_tls = bool(sub2api.get("verify_tls", True))
    skip_default_group_bind = bool(sub2api.get("skip_default_group_bind", True))
    admin_api_key = str(sub2api.get("admin_api_key", "")).strip()
    bearer_token = str(sub2api.get("bearer_token", "")).strip()
    effective_auth_mode = auth_mode
    auth_fallback_reason: str | None = None

    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "cpa-sub2api-importer/1.0"}
    if auth_mode == "admin_api_key":
        if admin_api_key:
            headers["x-api-key"] = admin_api_key
        elif bearer_token:
            effective_auth_mode = "bearer_token"
            auth_fallback_reason = "sub2api.admin_api_key empty, fallback to bearer_token"
            headers["Authorization"] = f"Bearer {bearer_token}"
        else:
            raise ConfigError(
                "sub2api.admin_api_key is required for admin_api_key mode unless sub2api.bearer_token is set"
            )
    else:
        if not bearer_token:
            raise ConfigError("sub2api.bearer_token is required for bearer_token mode")
        headers["Authorization"] = f"Bearer {bearer_token}"

    return {
        "base_url": base_url.rstrip("/"),
        "headers": headers,
        "timeout_seconds": timeout_seconds,
        "verify_tls": verify_tls,
        "skip_default_group_bind": skip_default_group_bind,
        "effective_auth_mode": effective_auth_mode,
        "auth_fallback_reason": auth_fallback_reason,
    }


def import_request_body(payload: dict[str, Any], skip_default_group_bind: bool) -> bytes:
    """Build the HTTP request body for a sub2api import call.

    @param payload Sub2api payload to import
    @param skip_default_group_bind Whether default group binding should be skipped
    @returns UTF-8 encoded JSON request body
    """
    body = {
        "data": payload,
        "skip_default_group_bind": skip_default_group_bind,
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def parse_error_message(raw_body: bytes) -> str:
    """Extract a readable error message from an HTTP response body.

    @param raw_body Raw HTTP response body
    @returns Best-effort error message string
    """
    if not raw_body:
        return "empty response"
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return raw_body.decode("utf-8", errors="replace").strip()

    if isinstance(payload, dict):
        for key in ("message", "detail", "error", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested_data = payload.get("data")
        if isinstance(nested_data, dict):
            for key in ("message", "detail", "error"):
                value = nested_data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return json.dumps(payload, ensure_ascii=False)


def import_payload(payload: dict[str, Any], import_settings: dict[str, Any]) -> dict[str, Any]:
    """Import a payload into sub2api and validate the response.

    @param payload Sub2api payload to import
    @param import_settings Normalized import settings
    @returns Response data object from sub2api
    """
    url = import_settings["base_url"] + "/api/v1/admin/accounts/data"
    body = import_request_body(payload, import_settings["skip_default_group_bind"])
    request = urllib_request.Request(url, data=body, headers=import_settings["headers"], method="POST")
    expected_account_created = len(payload.get("accounts", []))

    context = None
    if not import_settings["verify_tls"] and url.lower().startswith("https://"):
        context = ssl._create_unverified_context()

    try:
        with urllib_request.urlopen(request, timeout=import_settings["timeout_seconds"], context=context) as response:
            status_code = getattr(response, "status", 200)
            raw_body = response.read()
    except urllib_error.HTTPError as exc:
        raise ImportError(f"HTTP {exc.code}: {parse_error_message(exc.read())}") from exc
    except urllib_error.URLError as exc:
        raise ImportError(f"network error: {exc.reason}") from exc

    if status_code != 200:
        raise ImportError(f"HTTP {status_code}: unexpected status code")

    try:
        body_json = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ImportError("invalid JSON response from sub2api") from exc

    if not isinstance(body_json, dict):
        raise ImportError("unexpected response format from sub2api")

    if body_json.get("code") != 0:
        raise ImportError(parse_error_message(raw_body))

    data = body_json.get("data")
    if not isinstance(data, dict):
        raise ImportError("sub2api response missing data object")

    proxy_failed = int(data.get("proxy_failed", 0))
    account_created = int(data.get("account_created", 0))
    account_failed = int(data.get("account_failed", 0))
    if account_created != expected_account_created or account_failed != 0 or proxy_failed != 0:
        extras: list[str] = []
        if expected_account_created != account_created:
            extras.append(f"expected_account_created={expected_account_created}")
        if proxy_failed != 0:
            extras.append(f"proxy_failed={proxy_failed}")
        suffix = f", {', '.join(extras)}" if extras else ""
        raise ImportError(
            f"unexpected import result: account_created={account_created}, account_failed={account_failed}{suffix}"
        )

    return data


def emit_conversion_summary(
    *,
    converted: int,
    skipped: int,
    failed: int,
    details: list[str],
    output_dir: Path,
) -> None:
    """Print the conversion summary to stderr.

    @param converted Number of successfully converted files
    @param skipped Number of skipped files
    @param failed Number of failed files
    @param details Detail lines for skipped or failed conversions
    @param output_dir Output directory path
    @returns None
    """
    print(
        f"conversion converted={converted} skipped={skipped} failed={failed} output_dir={output_dir}",
        file=sys.stderr,
    )
    for detail in details:
        print(f"[convert] {detail}", file=sys.stderr)


def emit_import_summary(
    *,
    enabled: bool,
    skipped_reason: str | None,
    success: int,
    failed: int,
    details: list[str],
) -> None:
    """Print the import summary to stderr.

    @param enabled Whether import mode was enabled
    @param skipped_reason Reason import was skipped
    @param success Number of successful imports
    @param failed Number of failed imports
    @param details Detail lines for import outcomes
    @returns None
    """
    if not enabled:
        print(f"import enabled=false reason={skipped_reason}", file=sys.stderr)
        return

    print(f"import enabled=true success={success} failed={failed}", file=sys.stderr)
    for detail in details:
        print(f"[import] {detail}", file=sys.stderr)


def run(argv: Sequence[str]) -> int:
    """Run the converter CLI workflow end to end.

    @param argv Raw command-line arguments without the program name
    @returns Process exit code
    """
    args = parse_args(argv)
    input_path, output_dir, config_path, merge_output_path = resolve_paths(args)
    try:
        file_regex = compile_file_regex(args.file_regex)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        config, created = load_config(config_path)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if created:
        print(f"created config template: {config_path}", file=sys.stderr)

    if not input_path.exists():
        print(f"input path does not exist: {input_path}", file=sys.stderr)
        return 1
    if not input_path.is_file() and not input_path.is_dir():
        print(f"input path must be a file or directory: {input_path}", file=sys.stderr)
        return 1

    excluded_paths = [merge_output_path] if merge_output_path is not None else []
    files = list(iter_input_files(input_path, excluded_paths=excluded_paths, file_regex=file_regex))
    if not files:
        if args.file_regex:
            print(
                f"no JSON files matched --file-regex in input path: {input_path} regex={args.file_regex}",
                file=sys.stderr,
            )
        else:
            print(f"no JSON files found in input path: {input_path}", file=sys.stderr)
        return 1

    exported_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    converted_accounts: list[ConvertedAccount] = []
    conversion_details: list[str] = []
    converted_count = 0
    skipped_count = 0
    failed_count = 0

    for source_file in files:
        try:
            provider, account = convert_source_file(source_file)
            relative_path = relative_output_path(source_file, input_path)
            converted_accounts.append(
                ConvertedAccount(
                    source_path=source_file,
                    relative_path=relative_path,
                    provider=provider,
                    account=account,
                )
            )
            converted_count += 1
        except ConversionError as exc:
            failed_count += 1
            detail = f"{source_file.name}: {exc}"
            conversion_details.append(detail)
            if input_path.is_file() or args.strict:
                emit_conversion_summary(
                    converted=converted_count,
                    skipped=skipped_count,
                    failed=failed_count,
                    details=conversion_details,
                    output_dir=output_dir,
                )
                emit_import_summary(enabled=False, skipped_reason="conversion_failed", success=0, failed=0, details=[])
                return 1
            skipped_count += 1
            failed_count -= 1

    emit_conversion_summary(
        converted=converted_count,
        skipped=skipped_count,
        failed=failed_count,
        details=conversion_details,
        output_dir=output_dir,
    )

    if converted_count == 0:
        emit_import_summary(enabled=False, skipped_reason="no_converted_files", success=0, failed=0, details=[])
        return 1

    if merge_output_path is None:
        for item in converted_accounts:
            output_path = output_dir / item.relative_path
            write_payload(output_path, build_payload(item.account, exported_at=exported_at))
    else:
        write_payload(
            merge_output_path,
            build_payload(*(item.account for item in converted_accounts), exported_at=exported_at),
        )

    auto_import = bool(config.get("sub2api", {}).get("auto_import", False))
    if args.no_import:
        emit_import_summary(enabled=False, skipped_reason="cli_disabled", success=0, failed=0, details=[])
        return 0
    if not auto_import:
        emit_import_summary(enabled=False, skipped_reason="config_disabled", success=0, failed=0, details=[])
        return 0

    try:
        import_settings = validate_import_config(config)
    except ConfigError as exc:
        emit_import_summary(enabled=True, skipped_reason=None, success=0, failed=1, details=[f"config: {exc}"])
        return 1

    import_success = 0
    import_failed = 0
    import_details: list[str] = []
    fallback_reason = import_settings.get("auth_fallback_reason")
    if isinstance(fallback_reason, str) and fallback_reason:
        import_details.append(f"config: {fallback_reason}")
    if merge_output_path is not None:
        merged_payload = build_payload(*(item.account for item in converted_accounts), exported_at=exported_at)
        try:
            result = import_payload(merged_payload, import_settings)
            import_success += 1
            import_details.append(
                f"{merge_output_path.name}: ok account_created={result.get('account_created', 0)}"
            )
        except ImportError as exc:
            import_failed += 1
            import_details.append(f"{merge_output_path.name}: {exc}")
    else:
        for item in converted_accounts:
            payload = build_payload(item.account, exported_at=exported_at)
            try:
                result = import_payload(payload, import_settings)
                import_success += 1
                import_details.append(
                    f"{item.relative_path.as_posix()}: ok account_created={result.get('account_created', 0)}"
                )
            except ImportError as exc:
                import_failed += 1
                import_details.append(f"{item.relative_path.as_posix()}: {exc}")

    emit_import_summary(
        enabled=True,
        skipped_reason=None,
        success=import_success,
        failed=import_failed,
        details=import_details,
    )
    return 1 if import_failed > 0 else 0


def main() -> None:
    """Program entry point.

    @returns None
    """
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
