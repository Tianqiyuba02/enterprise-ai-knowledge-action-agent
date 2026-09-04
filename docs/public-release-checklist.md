# Public Release Checklist

Run this checklist again at the final release gate, immediately before changing repository visibility.

## Source and history

- Confirm the reviewed release-candidate commit and clean working tree.
- Confirm `main`, historical annotated tags, GitHub Releases, and the historical stash have not been rewritten.
- Confirm no local absolute paths, generated runtime data, temporary databases, build output, or populated environment files are tracked.
- Review the full reachable file-name history and deleted paths for accidental artifacts.
- Verify the accepted author metadata; do not rewrite history.

## Full-history secret scan

Install a current reputable `gitleaks` release through its verified package channel, then run from the repository root:

```bash
gitleaks git . --redact --no-banner --exit-code 1
```

The scan must cover all reachable commits, branches, and tags. Investigate every finding against the source commit. Do not add broad allowlists for credential-shaped text. Synthetic demo identifiers may be documented narrowly only if the scanner produces a demonstrated false positive.

The checked-in allowlist is intentionally limited to known SHA-256 business request keys and exact test-source literals. Do not broaden it to credential patterns; investigate every new finding.

Also inspect tracked files directly:

```bash
git ls-files -z | xargs -0 rg -n -i \
  'BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|AIza[0-9A-Za-z_-]{30,}|gh[pousr]_[0-9A-Za-z]{30,}|sk-[0-9A-Za-z]{20,}'
```

An empty direct scan does not replace the full-history scanner.

## Dependency and build evidence

- `uvx pip-audit`
- `cd ui && npm audit --audit-level=high`
- backend and frontend production Docker builds
- Render Blueprint validation against the official schema
- deterministic default, PostgreSQL integration, frontend, and browser gates

Record findings accurately. Do not blindly upgrade core dependencies during the release gate.

## Publication sequence

1. Obtain Controller approval for the exact commit.
2. Merge through the approved branch sequence without rewriting history.
3. Verify `main`, then create annotated tag `v1.0.0` from that exact commit.
4. Create the GitHub Release from the tag using reviewed release notes.
5. Change repository visibility only after GitHub confirms the intended public target.
6. Verify anonymous access to README, LICENSE, SECURITY, source history, and Release.
7. Configure the verified anonymous repository URL for the About-page source CTA.
8. Verify the CTA through the real public demo.
9. Add a real demo-video URL only after the video exists and has been reviewed.
10. Set the GitHub repository website to the public demo URL.

Never publish a placeholder or private URL.
