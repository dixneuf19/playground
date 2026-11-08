# AGENTS.md

Guide for coding agents working in this monorepo. Project-specific guides live next to each project (for example `karaoke/AGENTS.md`).

## Adding a project

1. Put the project in its own top-level directory, as a uv project with `[tool.uv] exclude-newer = "3 days"` in its `pyproject.toml`. Lock with the oldest uv used in the Dockerfiles (`uvx uv@0.12 lock`).
2. Copy an existing pair of workflows (`vitry-auberge-bot-docker.yml` and `vitry-auberge-bot-test.yml`) to `<project>-docker.yml` and `<project>-test.yml`, then rename the paths filters, the image name and the GHA cache `scope`. A scope shared between projects makes their builds overwrite each other's cache, so every build starts cold.
3. Name the image `ghcr.io/dixneuf19/playground/<project>`, which is `IMAGE_NAME: ${{ github.repository }}/<project>` in the docker workflow.
4. Add `LABEL org.opencontainers.image.source=https://github.com/dixneuf19/playground` to the Dockerfile.
5. List the project in `README.md` and `CLAUDE.md`. The root `renovate.json` already covers every directory, so do not add a per-project Renovate config.

### Why the image goes under `playground/`

The first push from this repo's `GITHUB_TOKEN` creates the ghcr package, makes playground its admin, and gives it the repo's public visibility. Nothing has to be created or configured on ghcr by hand.

This only works when the package name is free. A bare `ghcr.io/dixneuf19/<project>` can already be taken by a package from an older standalone repo or a manual push. The push then fails with `denied: permission_denied: read_package`, and the fix is manual: grant playground access under "Manage Actions access" in the package settings, then make the package public. The `playground/` prefix avoids that collision. Neither the GitHub API nor its Terraform provider can change a package's Actions access or visibility, so this cannot be automated from brassberry-gitops.

karaoke, soundhoard and vitry-auberge-bot predate this convention and keep their bare names, because brassberry-gitops references them.

To check whether a name is free before the first push:

```bash
t=$(curl -s "https://ghcr.io/token?scope=repository:dixneuf19/playground/<project>:pull" | jq -r .token)
curl -s -H "Authorization: Bearer $t" https://ghcr.io/v2/dixneuf19/playground/<project>/tags/list
```

`NAME_UNKNOWN` means the name is free, and `UNAUTHORIZED` means a private package already exists.
