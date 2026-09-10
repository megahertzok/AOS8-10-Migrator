# Contributing

Thanks for working on this. A few things that keep this project coherent as more
people touch it:

## Getting set up

1. Clone the repo.
2. Read [README.md](README.md) first — it covers the architecture (GitHub Pages GUI +
   local proxy agent), the full migration workflow, and known gaps.
3. Run the proxy agent locally: `cd proxy-agent && ./start_proxy_agent.command`
   (or `.bat` on Windows). Open `http://127.0.0.1:8765/` to get the GUI served by
   your local proxy agent — no need to touch GitHub Pages while developing.

## The "UNVERIFIED" convention

Several pieces of this tool (the exact REST object names AOS8 uses for `ap convert`,
some `showcommand` response field names, the SSH rollback command's exact behavior)
couldn't be confirmed from public documentation and haven't been tested against every
firmware version. Those are explicitly marked `UNVERIFIED` in code comments and in
[`proxy-agent/endpoints.yaml`](proxy-agent/endpoints.yaml), with a note on what's
actually confirmed vs. best-effort.

**Keep this convention.** If you add something you haven't verified against real
equipment, mark it clearly rather than presenting it as confirmed — the next person
(possibly debugging a live migration) needs to know which parts to trust and which to
double-check first. If you *do* verify something previously marked `UNVERIFIED`
(e.g. against your own lab controller), please update the comment/doc — that's one of
the most valuable kinds of contribution here.

## Security rules — non-negotiable

- Never commit real credentials, tokens, hostnames, or IPs from a customer site.
- Never remove or weaken the credential redaction in `proxy-agent/debug_log.py`.
- The proxy agent binds to `127.0.0.1` by default — don't change that default or add
  a way to disable it without also adding authentication (there isn't any yet — see
  the open issue on this if you want to help).
- Anything added to `docs/` runs in the browser and is public — never put a secret,
  API key, or credential in client-side code.

## If you touch `proxy-agent/`

The GUI's "Download proxy agent (.zip)" link points at a **committed** file,
`docs/downloads/proxy-agent.zip` — not a live GitHub archive (GitHub's own archive
endpoint zips the whole repo, not just this folder). Regenerate it before opening a PR
that changes anything under `proxy-agent/`:

```bash
rm -f docs/downloads/proxy-agent.zip
git ls-files --cached --others --exclude-standard proxy-agent | zip -q docs/downloads/proxy-agent.zip -@
```

## Pull requests

- Keep PRs scoped to one change where reasonable.
- Fill out the PR template — the checklist exists because these are easy to forget.
- `main` is what GitHub Pages serves live, so a broken `docs/` change is user-facing
  immediately after merge. Test locally against the proxy agent first
  (`http://127.0.0.1:8765/`) before opening the PR.

## Reporting bugs / proposing features

Use GitHub Issues — templates are provided for both. Include Debug console output or
`proxy-agent/proxy_agent.log` lines where relevant (check the paste for anything
sensitive first, though credentials should already show as `***`).
