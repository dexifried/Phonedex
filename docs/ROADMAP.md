# PhoneDex roadmap

PhoneDex is an experimental open-source project. Milestones below are planned,
not promises of production support or completion dates.

## Next: first-run usability

- [x] Create an opt-in, project-local Python dependency bootstrap.
- [x] Separate local SIP setup from optional Cloudflare remote control.
- [ ] Test the documented installation process from a completely clean machine.
- [ ] Improve guided provider credentials, firewall checks and actionable setup errors.

## Linux compatibility and deployment validation

**Status: planned — not yet validated.**

Use a dedicated self-hosted Linux test environment to bring the existing
Python SIP/TLS, SRTP/WebRTC, and GPT Live bridge to Linux without relying
on Windows-specific launchers or local assumptions.

- [ ] Start validation on an existing **EndeavourOS (Arch Linux-based)**
      test host, documenting the actual OS version, Python version and CPU
      architecture. Reproduce setup on a clean Arch-based installation before
      claiming compatibility.
- [ ] Verify native dependencies (`aiortc`, `av`, `pylibsrtp`, and codecs),
      Codex CLI authentication, file permissions and project-local virtualenv setup.
- [ ] Replace any Windows-only service management, credential-loading or path
      assumptions with portable equivalents; add a sample `systemd` service
      with explicit opt-in and least-privilege settings.
- [ ] Validate SIP over TLS, SDES-SRTP/PCMU, RTP networking, inbound/outbound
      call lifecycle, voicemail detection and clean session teardown.
- [ ] Run offline tests on Linux in CI; perform a separately authorized,
      low-cost carrier call test before claiming end-to-end support.
- [ ] Publish step-by-step Linux installation, deployment, logging,
      updates and troubleshooting documentation.

**Done when:** a new contributor can follow the Linux instructions on a clean
host, pass the automated tests, and demonstrate one authorized inbound or
outbound call without applying undocumented local fixes.

Linux compatibility will be described as *verified* only after those tests,
not merely because the current Python source imports on Linux.

## Later

- [ ] Harden inbound SIP transaction handling and broader carrier support.
- [ ] Validate human call transfer on real carriers, with safe failure handling.
- [ ] Improve private storage, secure configuration and optional transcript encryption.
- [ ] Offer optional native installers and simpler service management across platforms.

No milestone requires exposing production tokens, personal phone numbers, call
transcripts, or internal machine names in the public repository.
