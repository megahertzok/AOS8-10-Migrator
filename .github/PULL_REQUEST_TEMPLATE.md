## What does this change?

## Why?

## Have you tested this against real hardware/Central?
- [ ] Yes — against a real AOS8 controller/AP
- [ ] Yes — against a real Central tenant
- [ ] No — logic/UI change only, verified locally without live equipment

## Checklist
- [ ] If you touched `proxy-agent/`, files that changed require rebuilding the
      downloadable zip:
      `git ls-files --cached --others --exclude-standard proxy-agent | zip -q docs/downloads/proxy-agent.zip -@`
- [ ] No credentials, tokens, or real hostnames/IPs from a customer site are in this diff
- [ ] Anything you weren't able to verify against real equipment is marked
      `UNVERIFIED` in code/docs, per this repo's existing convention (see
      `proxy-agent/endpoints.yaml` for examples)
