"""Optional Windows DPAPI bridge for the existing JevResearch TypeSafe vault.

Never read or expose vault content through a remote management connector.
Credential is decrypted inside the local PhoneDex process only when explicitly
opted in. No files, shell history entries, or logs receive the plaintext key.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess


def vault_path() -> Path:
    roaming = os.environ.get("APPDATA")
    if not roaming:
        raise RuntimeError("Windows roaming application data unavailable")
    return Path(roaming) / "JevResearch" / "provider-key.dpapi"


def existing_vault_available() -> bool:
    if os.name != "nt":
        return False
    try:
        return vault_path().is_file()
    except OSError:
        return False


def load_jev_research_key_locally() -> str:
    """Decrypt Windows-user SecureString using the caller\u0027s Windows token.

    The decryption process returns the secret to the *local* PhoneDex process.
    It is never returned to a remote tool result or written to stdout by PhoneDex.
    This code is intentionally disabled unless explicitly requested by config.
    """
    if os.name != "nt" or not existing_vault_available():
        raise RuntimeError("Local JevResearch DPAPI vault unavailable")
    script = (
        "$ErrorActionPreference=\u0027Stop\u0027;"
        "$p=Join-Path (Join-Path $env:APPDATA \u0027JevResearch\u0027) \u0027provider-key.dpapi\u0027;"
        "$encoded=[IO.File]::ReadAllText($p).Trim();" 
        "$secure=ConvertTo-SecureString -String $encoded;"
        "$b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure);"
        "try{[Console]::Out.Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($b))}"
        "finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)}"
    )
    try:
        outcome = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("JevResearch DPAPI vault unavailable to this Windows user") from None
    secret = outcome.stdout.strip()
    if outcome.returncode != 0 or not 16 <= len(secret) <= 4096:
        raise RuntimeError("JevResearch DPAPI vault could not be unlocked")
    return secret
