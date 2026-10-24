"""Conservative offline audit for private data before publishing a PhoneDex preview.

Checks committed filenames and text, optionally all reachable Git history.
This catches common leak patterns but is not a substitute for a manual review.
Never prints matched secret values.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAD_NAMES = re.compile(
    r"(^|/)(?:\.env(?:\..+)?|auth\.json|credentials\.json|business_profile\.json|"
    r".+\.(?:dpapi|pem|key|p12|pfx|sqlite3?|db|pcap|pcapng|wav|mp3|m4a|log|jsonl))$",
    re.IGNORECASE,
)
# The redacted .env.example and business_profile.example.json are permitted.
SAFE_NAMES = {".env.example", "harness/business_profile.example.json"}
PATTERNS = {
    "access-token": re.compile(r"(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|sk-proj-|sk-[A-Za-z0-9]{20})[A-Za-z0-9_-]{12,}"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    # Match generic kinds of personally identifying content instead of
    # embedding actual phone numbers, addresses, hostnames or emails here.
    "possible-personal-phone": re.compile(r"\+1(?:[\s().-]*[0-9]){10}\b"),
    "possible-personal-email": re.compile(r"\b[A-Za-z0-9._%+-]+@(?:gmail|hotmail|outlook|icloud|yahoo)\.com\b", re.I),
    "private-windows-home": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!Public[\\/])[\w.-]+[\\/]"),
    "specific-sip-host": re.compile(r"sips?:[a-z0-9-]+\.pstn\.agentphone\.ai\b|sip\.[0-9]{1,3}(?:-[0-9]{1,3}){3}\.sslip\.io\b", re.I),
    "hardcoded-bearer": re.compile(r"\bBearer\s+(?!<|\$\{|['\"]\s*\+|token\b|secret\b|YOUR_|placeholder\b|test\b)[A-Za-z0-9._=-]{28,}", re.I),
    "hardcoded-secret": re.compile(
        r"""(?i)(?:sip_password|api[_-]?key|access[_-]?token|bearer[_-]?token|client[_-]?secret)\s*[:=]\s*['"][A-Za-z0-9_./+=-]{24,}['"]"""
    ),
}

def git(*args: str) -> bytes:
    p = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    if p.returncode:
        raise RuntimeError("git command failed: " + " ".join(args[:3]))
    return p.stdout

def filenames(commit: str | None) -> list[str]:
    args = ["ls-files", "-z"] if commit is None else ["ls-tree", "-r", "--name-only", "-z", commit]
    return [x.decode("utf-8", "replace") for x in git(*args).split(b"\0") if x]

def blob(commit: str | None, name: str) -> bytes:
    if commit is None:
        return (ROOT / name).read_bytes()
    return git("show", f"{commit}:{name}")

def scan(commit: str | None, label: str) -> tuple[int, int]:
    scanned = issues = 0
    for name in filenames(commit):
        scanned += 1
        if name not in SAFE_NAMES and BAD_NAMES.search(name):
            print(f"{label}: sensitive filename: {name}")
            issues += 1
        payload = blob(commit, name)
        # Flag attached image EXIF metadata: preview assets should be metadata-free.
        low = name.lower()
        if low.endswith((".jpg", ".jpeg")) and b"Exif\x00\x00" in payload:
            print(f"{label}: embedded EXIF image metadata: {name}")
            issues += 1
        if low.endswith(".png") and b"eXIf" in payload:
            print(f"{label}: embedded PNG EXIF metadata: {name}")
            issues += 1
        if b"\0" in payload[:1024] or len(payload) > 2_000_000:
            continue
        text = payload.decode("utf-8", "replace")
        for kind, rx in PATTERNS.items():
            matches = list(rx.finditer(text))
            if kind == "possible-personal-phone":
                # NANP 555 exchanges are commonplace fictional test numbers.
                matches = [m for m in matches
                           if "".join(c for c in m.group() if c.isdigit())[4:7] != "555"]
            if matches:
                lines = sorted({text.count("\n", 0, m.start()) + 1 for m in matches})
                print(f"{label}: {kind}: {name} (line(s): {','.join(map(str, lines[:12]))})")
                issues += len(matches)
    return scanned, issues

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", action="store_true", help="inspect all reachable commits")
    ap.add_argument("--commit", help="inspect one Git revision for bounded local checks")
    args = ap.parse_args()
    commits: list[tuple[str | None, str]] = ([(args.commit, args.commit[:12])] if args.commit else [(None, "working-tree")])
    if args.history:
        revs = [x.decode("ascii") for x in git("rev-list", "--all").splitlines() if x]
        commits += [(rev, rev[:12]) for rev in revs]
    total_files = total_issues = 0
    for commit, label in commits:
        files, issues = scan(commit, label)
        total_files += files
        total_issues += issues
    print(f"RELEASE_AUDIT checked_sets={len(commits)} files={total_files} issues={total_issues}")
    print("Heuristic audit only; review source/history, third-party licenses and binary asset metadata separately.")
    return 1 if total_issues else 0

if __name__ == "__main__":
    sys.exit(main())
