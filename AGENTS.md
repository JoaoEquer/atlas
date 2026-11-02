# Working on Atlas

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before writing code. Its rules are not suggestions:
CI enforces them. Name new concepts in [docs/GLOSSARY.md](docs/GLOSSARY.md) first.

- **Language.** Code, comments, logs and these docs are in English. The roadmap
  ([ROADMAP.md](ROADMAP.md)) is in Portuguese.
- **The repository is public.** Every commit, its message included, is published the moment it
  is pushed, and `main` cannot be rewritten afterwards. Before a push, read the diff as a stranger
  would.
- **Nothing personal in the repository.** No real names, client names, e-mail addresses, home
  folders, tokens or conversation data in code, tests, fixtures, docs or commit messages; tests use
  `example.com` and made-up names. User state lives in `ATLAS_HOME`.
- **Nothing copied without its license.** Code or text taken from another project keeps that
  project's license in an SPDX header in the file (docs/ARCHITECTURE.md); otherwise it is written
  in Atlas's own words. Comments say what Atlas does and why, never which project it came from.
- **Porting from Atlas V3.** V3 is a separate private repository. Code comes over only through the
  gate at the end of the architecture document: read it, check it, port it in English while fixing
  it, and bring its tests.
- **Commits.** The subject is in English. Its last paragraph, with the `Co-Authored-By` line, also
  carries a `Notes-pt-BR:` line: the subject said in Portuguese, for the update dialog of the app
  in Portuguese, which reads it with the subjects (`atlas.installation.check`).

## Commands

```bash
uv sync                    # install (Python 3.13 is managed by uv)
uv run ruff check .        # lint
uv run ruff format .       # format
uv run pyright             # types, strict on src/
uv run lint-imports        # subject boundaries
uv run deptry src          # dependency hygiene
uv run pytest              # tests on every core, with the coverage floor (`-n0`: one process)
uv run atlas serve         # run the server on 127.0.0.1:18790
docker compose up --build  # run in a container
docker build -t atlas:local . && ATLAS_TEST_IMAGE=atlas:local uv run pytest tests/docker --no-cov
                           # the image's tests: an empty container, a key, a conversation
uv run python scripts/gen_protocol.py   # the app's TypeScript protocol, after a protocol change
uv run python scripts/gen_docs.py       # the documentation's commands and settings pages,
                                        # after a command or a setting changes
```

The documentation for whoever uses Atlas is in `src/atlas/docs/pages/`: a change to what a user
sees changes its page in the same commit (docs/ARCHITECTURE.md, The documentation).

The desktop app, in `apps/desktop/` (Node 24):

```bash
npm ci                     # install
npm run check              # tsc, eslint, prettier, vitest
npm run test:e2e           # build, then the real app, atlas serve and the fake model, end to end
npm run dev                # run the app from source
```

The Setup, in `apps/setup/` (Node 24), the file that installs Atlas without a terminal:

```bash
npm ci                     # install
npm run check              # tsc, eslint, prettier, vitest
npm run package            # build this system's Setup into dist/ (CI builds all three)
```

The website, in `site/` (Node 24):

```bash
npm ci                     # install
npm run check              # astro check, the Worker's types, eslint, prettier, vitest
npm run build              # build, then check every page and file agents read
npm run dev                # run the site from source on 127.0.0.1:4321
```

All of them must pass before a commit.
