"""Optional local caller preferences and encrypted, explicitly disclosed profile fields.

No personal-profile model tool is registered. Sensitive data is never included
in a call unless the local operator explicitly unlocks and selects fields.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("PHONEDEX_DATA_DIR", str(Path.home() / ".phonedex"))).expanduser()
SETTINGS = ROOT / "calling-settings.json"
VAULT = ROOT / "calling-profile.enc.json"
FIELDS = {
    "full_name", "name_pronunciation", "date_of_birth", "phone", "email",
    "home_address", "health_insurance_carrier", "health_insurance_member_id",
    "dental_insurance_carrier", "dental_insurance_member_id", "vehicle", "vin",
    "frequent_flyer_numbers", "known_traveler_number", "tsa_redress_number",
}
PREFERENCES = {
    "assistant_name": 64, "caller_accent": 80, "answering_instructions": 700,
}
DEFAULTS = {
    "enabled": False, "assistant_name": "", "caller_accent": "",
    "answering_instructions": "",
}
MAX_VAULT_BYTES = 65536
E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")


class ProfileError(ValueError):
    pass


def _load_doc(path: Path, cap: int) -> dict:
    if not path.exists():
        return {}
    if path.stat().st_size > cap:
        raise ProfileError("Caller configuration exceeds its size limit")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError("Caller configuration is invalid") from exc
    if not isinstance(obj, dict):
        raise ProfileError("Caller configuration must be an object")
    return obj


def _store_doc(path: Path, data: dict):
    """Atomic owner-only local write; Windows permissions remain user/OS-managed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    blob = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(blob) > MAX_VAULT_BYTES:
        raise ProfileError("Profile is too large")
    fd, filename = tempfile.mkstemp(prefix=".phonedex-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(filename, 0o600)
        os.replace(filename, path)
    finally:
        try:
            os.unlink(filename)
        except FileNotFoundError:
            pass


def _settings_path(path: Path | None = None, agent: str | None = None) -> Path:
    if agent is None:
        return path or SETTINGS
    if path is not None:
        raise ProfileError("Cannot specify both agent and settings path")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", agent):
        raise ProfileError("Agent ID must be 1-32 lowercase letters, numbers, dash or underscore")
    return ROOT / "caller-agents" / (agent + ".json")


def load_settings(path: Path | None = None, *, agent: str | None = None) -> dict:
    cfg = _load_doc(_settings_path(path, agent), 8192)
    if set(cfg) - set(DEFAULTS):
        raise ProfileError("Unrecognized caller preference")
    result = dict(DEFAULTS)
    for name, value in cfg.items():
        if name == "enabled":
            if type(value) is not bool:
                raise ProfileError("enabled must be true or false")
        else:
            if not isinstance(value, str) or len(value) > PREFERENCES[name]:
                raise ProfileError("Invalid preference: " + name)
        result[name] = value
    return result


def update_setting(name: str, value: str, *, path: Path | None = None, agent: str | None = None) -> dict:
    destination = _settings_path(path, agent)
    settings = load_settings(destination)
    if name == "enabled":
        if value not in ("on", "off"):
            raise ProfileError("enabled expects on or off")
        settings[name] = value == "on"
    elif name in PREFERENCES:
        value = " ".join(value.split())
        if len(value) > PREFERENCES[name] or any(ord(x) < 32 for x in value):
            raise ProfileError("Preference is too long or contains control characters")
        settings[name] = value
    else:
        raise ProfileError("Unknown preference")
    _store_doc(destination, settings)
    return settings


def personalization_prompt(path: Path | None = None, *, agent: str | None = None) -> str:
    settings = load_settings(path, agent=agent)
    if not settings["enabled"]:
        return ""
    parts = ["Optional operator-configured phone assistant preferences:"]
    if settings["assistant_name"]:
        parts.append("You may introduce yourself as an AI assistant named " +
                     settings["assistant_name"] + ". Do not impersonate a human.")
    if settings["caller_accent"]:
        parts.append("Preferred voice/accent (only if the model supports it): " +
                     settings["caller_accent"] + ".")
    if settings["answering_instructions"]:
        parts.append("Operator's answering style: " + settings["answering_instructions"])
    parts.append("These preferences never authorize revealing profile data, payments, or transfers.")
    return "\n".join(parts)


def _key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise ProfileError("Use a vault passphrase of at least 12 characters")
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode("utf-8"))


def _aes():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ProfileError("Install cryptography to use the encrypted profile vault") from exc
    return AESGCM


def read_profile(passphrase: str, path: Path | None = None) -> dict:
    destination = path or VAULT
    if not destination.exists():
        return {}
    obj = _load_doc(destination, MAX_VAULT_BYTES)
    if obj.get("version") != 1 or set(obj) != {"version", "salt", "nonce", "ciphertext"}:
        raise ProfileError("Unsupported encrypted profile format")
    try:
        salt = base64.b64decode(obj["salt"], validate=True)
        nonce = base64.b64decode(obj["nonce"], validate=True)
        ct = base64.b64decode(obj["ciphertext"], validate=True)
        if len(salt) != 16 or len(nonce) != 12:
            raise ValueError("Invalid salt/nonce")
        raw = _aes()(_key(passphrase, salt)).decrypt(nonce, ct, b"phonedex-caller-profile-v1")
        profile = json.loads(raw)
        if not isinstance(profile, dict) or set(profile) - FIELDS:
            raise ValueError("Invalid profile fields")
        if any(not isinstance(v, str) or len(v) > 512 for v in profile.values()):
            raise ValueError("Invalid profile content")
        return profile
    except ProfileError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise ProfileError("Wrong passphrase or damaged encrypted profile") from exc
    except Exception as exc:
        from cryptography.exceptions import InvalidTag
        if isinstance(exc, InvalidTag):
            raise ProfileError("Wrong passphrase or damaged encrypted profile") from None
        raise


def save_profile_field(name: str, value: str, passphrase: str, *, path: Path | None = None):
    if name not in FIELDS:
        raise ProfileError("Unknown profile field")
    if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise ProfileError("Field is too long or contains control characters")
    if name == "phone" and value and not E164.fullmatch(value):
        raise ProfileError("Phone requires E.164 format, for example +17055550123")
    destination = path or VAULT
    old = read_profile(passphrase, destination)
    if value:
        old[name] = value
    else:
        old.pop(name, None)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    plain = json.dumps(old, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    cipher = _aes()(_key(passphrase, salt)).encrypt(nonce, plain, b"phonedex-caller-profile-v1")
    encode = lambda b: base64.b64encode(b).decode("ascii")
    _store_doc(destination, {"version": 1, "salt": encode(salt),
                              "nonce": encode(nonce), "ciphertext": encode(cipher)})


def scoped_disclosure(fields: list[str], passphrase: str, *, path: Path | None = None) -> str:
    """Only explicit fields; no silent scope expansion or direct model tool access."""
    if not fields or len(fields) > 10 or len(set(fields)) != len(fields):
        raise ProfileError("Select 1-10 distinct profile fields")
    if any(f not in FIELDS for f in fields):
        raise ProfileError("Unknown profile field requested")
    profile = read_profile(passphrase, path)
    missing = [f for f in fields if f not in profile]
    if missing:
        raise ProfileError("Requested profile fields are not set: " + ", ".join(missing))
    lines = ["Operator expressly authorized these personal details for this single call.",
             "Reveal them only to the intended party if needed for the call task.",
             "Never provide one-time codes, passwords, or payment card numbers:"]
    lines += [f.replace("_", " ") + ": " + profile[f] for f in fields]
    return "\n".join(lines)
