# Optional Skills

This directory holds the **optional** (recommended add-on) skills that ship
with NekoClaw. Unlike the built-in skills under `nekoclaw/skills/internal/`,
these are not vendored in the repository — they are downloaded at build time
from an upstream project.

## Layout

- `build.py` — downloads the optional skill pack into `skills/`.
- `skills/` — the downloaded skills (one directory per skill, each with a
  `SKILL.md`). This directory is generated and git-ignored.

## Source

Skills are fetched from [zfscgy/ZhSkills](https://github.com/zfscgy/ZhSkills).
Every top-level directory in that repository containing a `SKILL.md` file is
treated as a skill.

## Building

Run the download script before packaging:

```bash
python resources/skills/build.py
```

Options:

- `--ref <branch-or-tag>` — download a specific ref (default: `main`).
- `--keep` — merge into the existing `skills/` directory instead of wiping it.

## Installation

`build/install.py` bundles whatever is in `skills/` into the app. On every
launch the gateway syncs each skill into the user's `workspace/skills/<name>/`
(see `nekoclaw.startup.sync_optional_skills`), where it becomes a normal,
editable, toggleable workspace skill. Already-installed skills are kept up to
date: if the bundled content changes, the workspace copy is refreshed in
place (whether it's currently enabled or disabled/zipped).
