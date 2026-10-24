"""Offline, opt-in PhoneDex setup wizard. Never places calls or deploys services."""
from __future__ import annotations

import getpass
import importlib.util
import ipaddress
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Callable
from urllib.parse import urlsplit

MINIMUM_PYTHON = (3, 11)
DEPS = ("aiortc", "av", "pylibsrtp", "websockets")
NUMBER = re.compile(r"\+[1-9][0-9]{6,14}\Z")
SIP_URI = re.compile(r"sips?:[^\s<>\\\"']+\Z", re.I)
ENV_ORDER = (
    "PHONEDEX_TERMINATION_URI", "PHONEDEX_PUBLIC_IP", "PHONEDEX_MEDIA_PORT",
    "PHONEDEX_MEDIA_PORT_FORWARDED", "PHONEDEX_CALLER_ID",
    "PHONEDEX_SIP_USERNAME", "PHONEDEX_SIP_PASSWORD",
    "PHONEDEX_ALLOWED_RECIPIENTS", "PHONEDEX_MAX_CALLS_PER_DAY",
    "PHONEDEX_GATEWAY_URL", "PHONEDEX_GATEWAY_TOKEN",
)


def e164(value: str) -> str:
    value = value.strip()
    if not NUMBER.fullmatch(value):
        raise ValueError("Enter an E.164 number, such as +12025550123")
    return value


def approved_recipients(value: str) -> str:
    if not value.strip():
        raise ValueError("Provide at least one approved number; calling is disabled by default")
    numbers = [e164(x) for x in value.split(",")]
    return ",".join(dict.fromkeys(numbers))


def public_ipv4(value: str) -> str:
    try:
        result = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ValueError("Enter your public IPv4 address") from exc
    if result.version != 4 or not result.is_global:
        raise ValueError("Address must be a publicly routable IPv4, not a LAN or placeholder IP")
    return str(result)


def sip_target(value: str) -> str:
    value = value.strip()
    if not SIP_URI.fullmatch(value) or "example.invalid" in value.lower():
        raise ValueError("Enter the provider's SIP or SIPS termination URI")
    return value


def checked_field(value: str) -> str:
    if not value or value != value.strip() or any(c in value for c in ("\n", "\r", "\0")):
        raise ValueError("Value cannot be empty, contain newlines, or begin/end with whitespace")
    if value.startswith(('"', "'")) or value.endswith(('"', "'")):
        raise ValueError("Value cannot begin/end with quote characters")
    return value


def worker_endpoint(value: str) -> str:
    value = checked_field(value)
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Enter a valid HTTPS Worker gateway URL") from exc
    if (parsed.scheme != "https" or not parsed.hostname or
            not parsed.hostname.count(".") or
            "your-worker" in parsed.hostname or
            parsed.username or parsed.password or
            parsed.path != "/agent/gateway" or parsed.query or parsed.fragment):
        raise ValueError("Use a deployed HTTPS Worker URL ending in /agent/gateway")
    return value


def missing_deps() -> list[str]:
    return [p for p in DEPS if importlib.util.find_spec(p) is None]


def codex_oauth_ready() -> bool:
    # Reuse PhoneDex's own read-only diagnostic; never log tokens or auth files.
    from phonebridge.subscription_live import oauth_check
    return oauth_check() == "CODEX_OAUTH_ACCOUNT_READY"


def readiness(mode: str, config: dict[str, str] | None = None) -> list[tuple[str, bool, str]]:
    if mode not in ("local", "remote"):
        raise ValueError("mode must be local or remote")
    cfg = os.environ if config is None else config
    items = [
        ("Python 3.11+", sys.version_info >= MINIMUM_PYTHON, "Install Python 3.11+ from python.org"),
        ("Python packages", not missing_deps(), "Run python -m pip install -r requirements.txt"),
        ("Codex CLI", shutil.which("codex") is not None, "Install Codex CLI; then run codex login"),
        ("Codex login", codex_oauth_ready(), "Run codex login using your own authorized account"),
    ]
    def valid(field: str, validator: Callable[[str], str]) -> bool:
        try:
            validator(cfg.get(field, ""))
            return True
        except (ValueError, TypeError):
            return False
    items += [
        ("SIP termination", valid("PHONEDEX_TERMINATION_URI", sip_target), "Get your provider's termination URI"),
        ("Public IPv4", valid("PHONEDEX_PUBLIC_IP", public_ipv4), "Set your externally reachable IPv4 address"),
        ("Caller ID", valid("PHONEDEX_CALLER_ID", e164), "Set your provider-approved caller ID"),
        ("SIP credentials", bool(cfg.get("PHONEDEX_SIP_USERNAME") and cfg.get("PHONEDEX_SIP_PASSWORD")), "Provide your SIP username and password"),
        ("Dial allowlist", valid("PHONEDEX_ALLOWED_RECIPIENTS", approved_recipients), "List only numbers you own or may call"),
        ("SRTP UDP forwarding", cfg.get("PHONEDEX_MEDIA_PORT_FORWARDED") == "1", "Manually verify UDP forwarding, then update .env; not automatically tested"),
    ]
    if mode == "remote":
        items += [
            ("Cloudflare Worker URL", valid("PHONEDEX_GATEWAY_URL", worker_endpoint), "Deploy your own Worker and set its /agent/gateway HTTPS URL"),
            ("Worker gateway token", bool(cfg.get("PHONEDEX_GATEWAY_TOKEN")), "Create a unique secret and configure the matching Worker secret"),
        ]
    return items


def print_readiness(mode: str, *, printer: Callable[[str], None] = print) -> bool:
    printer(f"PhoneDex readiness: {mode} outbound mode (no phone calls placed)")
    all_ready = True
    for name, ready, action in readiness(mode):
        printer(f"  {'OK ' if ready else 'TODO'}  {name}" + ("" if ready else f": {action}"))
        all_ready &= ready
    printer("Notice: configuration checks cannot verify real carrier compatibility, open firewall ports, or actual call success.")
    return all_ready


def save_env(root: Path, values: dict[str, str]) -> Path:
    """Create a new, private file atomically; never overwrite existing settings."""
    config = root / ".env"
    if config.exists():
        raise FileExistsError(".env already exists; setup will not overwrite your configuration")
    for name, value in values.items():
        if name not in ENV_ORDER:
            raise ValueError("Unsupported config field")
        if value:
            checked_field(value)
    lines = [
        "# PhoneDex local config: PRIVATE; never commit or share",
        "# Port forwarding must be manually verified before setting to 1.",
    ]
    lines += [f"{key}={values.get(key, '')}" for key in ENV_ORDER]
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    fd = os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        config.unlink(missing_ok=True)
        raise
    return config


def _ask(label: str, validator: Callable[[str], str], *, secret=False,
         input_fn=input, getpass_fn=None) -> str:
    if getpass_fn is None:
        getpass_fn = getpass.getpass
    while True:
        try:
            raw = getpass_fn(label + ": ") if secret else input_fn(label + ": ")
        except (KeyboardInterrupt, EOFError):
            raise KeyboardInterrupt from None
        try:
            return validator(raw)
        except ValueError as err:
            print(f"  {err}")


def run_setup(root: Path, mode: str, *, input_fn=input) -> bool:
    """Interactive only, never installs packages, logs into services, or deploys."""
    if not sys.stdin.isatty():
        print("Interactive setup requires a terminal. Run: python phonedex.py setup --check")
        return False
    if (root / ".env").exists():
        print(".env already exists. No settings changed. Use setup --check to inspect readiness.")
        return False
    print("PhoneDex guided setup")
    print("No calls, package installs, network changes, or deployments will occur.")
    print("You'll need SIP provider credentials and a public IP before actual phone calls.")
    print("This stores the SIP password in a local .env file; protect your Windows/OS account.")
    try:
        confirm = input_fn("Save configuration locally in .env? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Cancelled without changing anything.")
            return False
        values = {
            "PHONEDEX_TERMINATION_URI": _ask("SIP termination URI", sip_target, input_fn=input_fn),
            "PHONEDEX_PUBLIC_IP": _ask("Public IPv4", public_ipv4, input_fn=input_fn),
            "PHONEDEX_MEDIA_PORT": "41002",
            "PHONEDEX_MEDIA_PORT_FORWARDED": "0",
            "PHONEDEX_CALLER_ID": _ask("Approved caller ID (+countrycode...)", e164, input_fn=input_fn),
            "PHONEDEX_SIP_USERNAME": _ask("SIP username", checked_field, input_fn=input_fn),
            "PHONEDEX_SIP_PASSWORD": _ask("SIP password (hidden)", checked_field, secret=True),
            "PHONEDEX_ALLOWED_RECIPIENTS": _ask("Approved outbound recipients (comma-separated E.164)", approved_recipients, input_fn=input_fn),
            "PHONEDEX_MAX_CALLS_PER_DAY": "12",
        }
        if mode == "remote":
            print("Remote agent control requires a separately deployed Cloudflare Worker.")
            values["PHONEDEX_GATEWAY_URL"] = _ask("Worker gateway HTTPS URL", worker_endpoint, input_fn=input_fn)
            values["PHONEDEX_GATEWAY_TOKEN"] = _ask("Worker gateway secret (hidden)", checked_field, secret=True)
        created = save_env(root, values)
        print(f"Configuration saved to {created.name}. No real calls or deployments made.")
        print("NEXT: install dependencies, run codex login, manually verify UDP forwarding.")
        if mode == "remote":
            print("NEXT: deploy your own Worker, set AGENT_CONTROL_TOKEN and VOICE_GATEWAY_TOKEN.")
            print("      VOICE_GATEWAY_TOKEN must match PHONEDEX_GATEWAY_TOKEN in .env.")
        print(f"Then run python phonedex.py setup --mode {mode} --check")
        return True
    except (KeyboardInterrupt, EOFError, ValueError, FileExistsError) as exc:
        print("Setup stopped; no configuration overwritten." if isinstance(exc, KeyboardInterrupt) else str(exc))
        return False
