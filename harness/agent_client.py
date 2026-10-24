"""Portable outbound HTTPS relay for Phonedex.
Use an independent gateway token; never share your operator bearer with the PC.
"""
from __future__ import annotations
import json,os,ssl
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

class AgentGatewayClient:
    def __init__(self,token=None,base=None):
        self.base=(base or os.environ.get("PHONEDEX_GATEWAY_URL","")).rstrip("/")
        if not self.base.startswith("https://") or not self.base.endswith("/agent/gateway"):
            raise ValueError("PHONEDEX_GATEWAY_URL must be an HTTPS URL ending in /agent/gateway")
        self.token=token or os.environ.get("PHONEDEX_GATEWAY_TOKEN","")
        if len(self.token)<32:raise ValueError("Configure PHONEDEX_GATEWAY_TOKEN with a long random secret")
    def request(self,method,path,payload=None):
        if not path.startswith("/") or ("?" in path and not path.startswith("/poll?")):
            raise ValueError("Invalid gateway request path")
        body=None if payload is None else json.dumps(payload,separators=(",",":")).encode("utf-8")
        if body is not None and len(body)>12000:raise ValueError("Gateway event too large")
        headers={"Authorization":"Bearer "+self.token,"Accept":"application/json",
                 "User-Agent":"Phonedex/0.1"}
        if body is not None:headers["Content-Type"]="application/json"
        request=Request(self.base+path,data=body,headers=headers,method=method)
        try:
            with urlopen(request,timeout=8,context=ssl.create_default_context()) as response:
                return json.loads(response.read(100000))
        except HTTPError as exc:
            raise RuntimeError("Gateway returned HTTP "+str(exc.code)) from None
        except (URLError,TimeoutError,ssl.SSLError):
            raise RuntimeError("Gateway transport unavailable") from None
    def poll(self,active=None):
        if active is not None and (not active.startswith("call_") or len(active)>90):
            raise ValueError("Invalid call ID")
        return self.request("GET","/poll"+("?active="+active if active else ""))
    def event(self,body):
        return self.request("POST","/events",body)
