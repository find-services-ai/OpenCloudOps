---
name: catalog
description: List the infrastructure patterns OpenCloudOps is allowed to build and the production lessons it enforces on generated code.
disable-model-invocation: true
metadata:
  plugin: opencloudops
---

Run via Bash:

```
occ list patterns
occ list lessons
```

Present both as short tables. This is the honest boundary of what the tool
can propose -- if someone asks for something not on the patterns list, say
plainly that it isn't approved yet rather than emitting something close.
