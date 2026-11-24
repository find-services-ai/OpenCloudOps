---
name: setup
description: Check for or install the occ CLI that every other OpenCloudOps command in this plugin depends on.
disable-model-invocation: true
metadata:
  plugin: opencloudops
---

## Current state

!`command -v occ >/dev/null 2>&1 && echo "occ is installed: $(occ version 2>/dev/null | head -1)" || echo "occ is not installed"`

If it's installed, say so and stop -- don't reinstall.

If it isn't: OpenCloudOps isn't published on PyPI yet, so it installs from a
checkout. Ask the user whether they already have the
[find-services-ai/OpenCloudOps](https://github.com/find-services-ai/OpenCloudOps)
repository cloned somewhere:

- **Yes** -- run `pip install -e "<path>[all]"` from that checkout, then
  confirm with `occ version`.
- **No** -- offer to clone it (ask where), then run the same install command.

After installing, run `occ version` and `occ list patterns` once to confirm
the platform contract and pattern catalog actually load -- a successful
`pip install` doesn't guarantee `platform/platform.yaml` and `patterns/`
were included; if either command errors, say exactly what failed rather than
declaring setup complete.
