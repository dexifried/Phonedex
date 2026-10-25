"""Phonedex: self-hosted Codex-subscription phone harness."""
from __future__ import annotations
import argparse,asyncio,ipaddress,logging,os,re,sys,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def load_config():
    """Parse simple KEY=VALUE local .env without evaluating executable content."""
    config=ROOT/".env"
    if not config.exists():return
    for line in config.read_text(encoding="utf-8-sig").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line:continue
        key,value=line.split("=",1)
        key=key.strip()
        if not re.fullmatch(r"PHONEDEX_[A-Z0-9_]+",key):continue
        value=value.strip()
        if value.startswith(("'",'"')) and len(value)>=2 and value[-1]==value[0]:
            value=value[1:-1]
        if value:os.environ.setdefault(key,value)

def doctor(mode='local'):
    from phonebridge.subscription_live import oauth_check
    from harness.transcript_store import transcript_mode, retention_days
    from phonebridge.calling_profile import load_settings, VAULT
    missing_deps=[p for p in ("aiortc","av","pylibsrtp","websockets") if importlib.util.find_spec(p) is None]
    print("PYTHON_DEPENDENCIES="+("ok" if not missing_deps else "missing:"+",".join(missing_deps)))
    print("PROJECT=Phonedex;BY=Dexifyz")
    print(oauth_check())
    required=["PHONEDEX_TERMINATION_URI","PHONEDEX_PUBLIC_IP","PHONEDEX_CALLER_ID",
              "PHONEDEX_SIP_USERNAME","PHONEDEX_SIP_PASSWORD"]
    if mode == "remote":
        required += ["PHONEDEX_GATEWAY_URL","PHONEDEX_GATEWAY_TOKEN"]
    print("CHECK_MODE="+mode)
    print("CLOUDFLARE_CONTROL="+("required" if mode=="remote" else "optional"))
    missing=[name for name in required if not os.environ.get(name)]
    print("CONFIG_MISSING="+(",".join(missing) if missing else "none"))
    try:
        target=os.environ.get("PHONEDEX_TERMINATION_URI","")
        if not (target.startswith("sips:") or target.startswith("sip:")):raise ValueError("SIP target")
        ip=ipaddress.ip_address(os.environ.get("PHONEDEX_PUBLIC_IP",""))
        assert ip.version==4 and ip.is_global
        print("SIP_TARGET_AND_PUBLIC_IP=valid")
    except (ValueError,AssertionError):
        print("SIP_TARGET_AND_PUBLIC_IP=not_configured")
    print("MEDIA_PORT_FORWARDED="+str(os.environ.get("PHONEDEX_MEDIA_PORT_FORWARDED")=="1"))
    allow=[a.strip() for a in os.environ.get("PHONEDEX_ALLOWED_RECIPIENTS","").split(",") if a.strip()]
    print("ALLOWED_RECIPIENT_COUNT="+str(len(allow)))
    try:
        privacy_mode=transcript_mode()
        print("TRANSCRIPT_DESTINATION="+privacy_mode)
        if privacy_mode in ("local","both"):
            print("TRANSCRIPT_LOCAL_RETENTION_DAYS="+str(retention_days()))
    except ValueError:
        print("TRANSCRIPT_DESTINATION=invalid_configuration")
    print("AGENTPHONE_TRANSCRIPT_SYNC="+
          ("enabled" if os.environ.get("PHONEDEX_AGENTPHONE_TRANSCRIPTS_ENABLED")=="1" else "off"))
    try:
        preferences=load_settings()
        print("CALLING_PERSONALIZATION="+("enabled" if preferences["enabled"] else "off"))
        print("ENCRYPTED_CALLING_PROFILE="+("present" if VAULT.exists() else "not_created"))
    except ValueError:
        print("CALLING_PERSONALIZATION=invalid_configuration")
    print("SECRETS_ARE_NEVER_DISPLAYED")

def _prompt_secret(confirm_new: bool = False) -> str:
    import getpass
    word=getpass.getpass("Encrypted calling profile passphrase: ")
    if len(word)<12:
        raise SystemExit("Vault passphrase must be at least 12 characters")
    if confirm_new and getpass.getpass("Repeat new passphrase: ")!=word:
        raise SystemExit("Passphrases did not match")
    return word

def settings_command(args):
    from phonebridge.calling_profile import load_settings, update_setting, ProfileError
    try:
        if args.settings_action=="show":
            import json
            print(json.dumps(load_settings(agent=args.agent),indent=2))
        elif args.settings_action=="set":
            setting=update_setting(args.name,args.value,agent=args.agent)
            print("PREFERENCE_SAVED="+args.name)
            print("CALLING_PERSONALIZATION="+("enabled" if setting["enabled"] else "off"))
    except ProfileError as exc:
        raise SystemExit(str(exc)) from None

def profile_command(args):
    from phonebridge.calling_profile import (FIELDS, VAULT, ProfileError,
                                             read_profile, save_profile_field)
    try:
        if args.profile_action=="fields":
            print("PROFILE_FIELDS="+",".join(sorted(FIELDS)))
            print("VAULT="+("present" if VAULT.exists() else "not_created"))
            return
        passphrase=_prompt_secret(confirm_new=(not VAULT.exists() and args.profile_action=="set"))
        if args.profile_action=="set":
            import getpass
            value=getpass.getpass("New value (blank deletes field): ")
            save_profile_field(args.field,value,passphrase)
            print("ENCRYPTED_FIELD_SAVED="+args.field)
        elif args.profile_action=="show":
            import json
            print(json.dumps(read_profile(passphrase),indent=2))
    except ProfileError as exc:
        raise SystemExit(str(exc)) from None

def dial(opts):
    from sip_outbound import dial as sip_dial
    from types import SimpleNamespace
    target=opts.to
    if not re.fullmatch(r"\+[1-9]\d{6,14}",target):
        raise SystemExit("Invalid recipient: use E.164")
    allowed={x.strip() for x in os.environ.get("PHONEDEX_ALLOWED_RECIPIENTS","").split(",") if x.strip()}
    if target not in allowed:
        raise SystemExit("Recipient blocked by PHONEDEX_ALLOWED_RECIPIENTS")
    if opts.share_fields and not opts.place_call:
        raise SystemExit("--share-fields requires --place-call; profile sharing is never part of a dry run")
    if not opts.place_call:
        print("PHONEDEX_DRY_RUN_OK=true; no call placed")
        return
    if os.environ.get("PHONEDEX_MEDIA_PORT_FORWARDED")!="1":
        raise SystemExit("SRTP port forwarding is not confirmed")
    required=("PHONEDEX_TERMINATION_URI","PHONEDEX_PUBLIC_IP","PHONEDEX_CALLER_ID",
              "PHONEDEX_SIP_USERNAME","PHONEDEX_SIP_PASSWORD")
    for field in required:
        if not os.environ.get(field):raise SystemExit("Missing "+field)
    prompt=opts.prompt
    if opts.share_fields:
        from phonebridge.calling_profile import ProfileError,scoped_disclosure
        fields=[f.strip() for f in opts.share_fields.split(",") if f.strip()]
        confirm=input("Share exactly these profile fields in this ONE call ("+", ".join(fields)+")? Type SHARE: ")
        if confirm!="SHARE":
            raise SystemExit("Sharing cancelled")
        try:
            disclosure=scoped_disclosure(fields,_prompt_secret())
        except ProfileError as exc:
            raise SystemExit(str(exc)) from None
        prompt+="\n"+disclosure
    config=SimpleNamespace(
        termination_uri=os.environ["PHONEDEX_TERMINATION_URI"],
        public_ip=os.environ["PHONEDEX_PUBLIC_IP"],
        media_port=int(os.environ.get("PHONEDEX_MEDIA_PORT","41002")),
        to=target,from_number=os.environ["PHONEDEX_CALLER_ID"],
        duration=opts.duration,mode=opts.mode,call_prompt=prompt,
        model_wake_file=opts.model_wake_file or os.environ.get('PHONEDEX_MODEL_WAKE_FILE') or None,
        voicemail_mode=opts.voicemail_mode,greeting_file=None,agent=opts.agent)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(sip_dial(config))

def main():
    load_config()
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    doc=sub.add_parser("doctor",help="Report readiness without showing any credential")
    doc.add_argument("--mode",choices=("local","remote"),default="local",
                     help="check local SIP calling (default) or remote Worker control")
    setup=sub.add_parser("setup",help="Guided, offline configuration; no calls or deployment")
    setup.add_argument("--mode",choices=("local","remote"),default="local",help="local SIP calling or remote Worker control")
    setup.add_argument("--check",action="store_true",help="Read-only readiness checklist; safe for automation")
    sub.add_parser("controller",help="Run the outbound HTTPS polling controller")
    prefs=sub.add_parser("settings",help="Optional local AI caller preferences (off by default)")
    prefs.add_argument("--agent",default=None,help="Optional named agent configuration, e.g. muse")
    prefs_sub=prefs.add_subparsers(dest="settings_action",required=True)
    prefs_sub.add_parser("show",help="View non-sensitive caller settings")
    pref_set=prefs_sub.add_parser("set",help="Set a non-sensitive preference")
    pref_set.add_argument("name",choices=("enabled","assistant_name","caller_accent","answering_instructions"))
    pref_set.add_argument("value")
    profile=sub.add_parser("profile",help="Encrypted personal calling profile; never shared by default")
    profile_sub=profile.add_subparsers(dest="profile_action",required=True)
    profile_sub.add_parser("fields",help="List accepted field names, not your private values")
    profile_set=profile_sub.add_parser("set",help="Enter a private field without putting it in shell history")
    profile_set.add_argument("field",choices=tuple(sorted(__import__(
        "phonebridge.calling_profile",fromlist=["FIELDS"]).FIELDS)))
    profile_sub.add_parser("show",help="Locally display unlocked profile details")
    dialing=sub.add_parser("dial",help="Dry-run an allowed destination unless --place-call is set")
    dialing.add_argument("--to",required=True)
    dialing.add_argument("--mode",choices=("normal","business"),default="normal")
    dialing.add_argument("--duration",type=int,default=60)
    dialing.add_argument("--prompt",default="")
    dialing.add_argument("--place-call",action="store_true")
    dialing.add_argument("--agent",default=None,help="Optional named AI caller personality")
    dialing.add_argument("--share-fields",default="",
        help="Explicit one-call encrypted profile disclosure; prompts before sharing")
    dialing.add_argument("--voicemail-mode",choices=("auto","off"),
        default=os.environ.get("PHONEDEX_VOICEMAIL_MODE","auto"),
        help="outbound answer screen: stay silent until a voicemail beep")
    dialing.add_argument("--model-wake-file",default=None,
        help="local 24-kHz mono PCM16 WAV to prime GPT Live after answer screening")
    args=p.parse_args()
    if args.action=="doctor":doctor(args.mode)
    elif args.action=="setup":
        from onboarding import print_readiness,run_setup
        if args.check:print_readiness(args.mode)
        elif not run_setup(ROOT,args.mode):raise SystemExit(2)
    elif args.action=="controller":
        os.environ["PHONEDEX_AGENT_HARNESS"]="1"
        from harness.agent_controller import main as controller
        controller()
    elif args.action=="settings":settings_command(args)
    elif args.action=="profile":profile_command(args)
    else:
        if args.duration<20 or args.duration>300:
            raise SystemExit("Call duration must be 20..300 seconds")
        dial(args)
if __name__=="__main__":
    main()
