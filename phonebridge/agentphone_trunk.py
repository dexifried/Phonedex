"""Phonedex SIP credentials from the local process environment."""
import os
def load_credentials():
    username=os.environ.get("PHONEDEX_SIP_USERNAME","")
    password=os.environ.get("PHONEDEX_SIP_PASSWORD","")
    if not username or not password:
        raise RuntimeError("Missing PHONEDEX_SIP_USERNAME / PHONEDEX_SIP_PASSWORD")
    return {"username":username,"password":password}
def status():
    # No secrets or trunk IDs returned or logged.
    record=load_credentials()
    return {"stored":True,"has_username":bool(record["username"]),"has_password":bool(record["password"])}
