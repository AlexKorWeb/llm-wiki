---
name: llm-wiki
description: Build and maintain a Karpathy-style LLM knowledge wiki. Run INGEST (turn a raw note, forwarded post, or URL into a detailed source file plus a cross-linked knowledge page), QUERY (answer from the wiki and save good answers as new pages), and LINT (health-check the wiki for broken links, orphans, contradictions, stale pages). Trigger when the user says "ingest this", "add to wiki", "сделай статью", "обработай заметку", "query the wiki", "что у нас по...", "lint the wiki", "проверь вики", or drops a file in raw/.
---

# LLM Wiki — Skill

A self-maintaining knowledge base where the LLM, not a vector DB, is the index. Inspired by Andrej Karpathy's note-taking method. Works standalone in any repo that has the wiki layout below; pairs with the Telegram ingest bot (`scripts/tg_bot.py`) for a hands-free pipeline.

## Wiki layout this skill expects

```
raw/                  # source notes — READ ONLY, never edit or delete
  _inbox/             # incoming forwarded posts (bot drops them here)
  _assets/            # images extracted from processed posts
knowledge/
  concepts/           # ideas, methods, techniques
  tools/              # utilities, libraries, plugins, services, CLIs, platforms (NOT skills)
  skills/             # Claude Code Skills only (SKILL.md packages in ~/.claude/skills/)
  connections/        # links between topics (X vs Y, X + Y, patterns)
daily/                # auto session logs
index.md              # navigation index — updated on every INGEST
log.md                # append-only operation history (newest on top)
```

If `CLAUDE.md` exists at repo root, read it first — it holds the authoritative schema and language preference.

## Naming & format (non-negotiable)

- File names: **kebab-case**, `.md`, Latin only, no spaces (`claude-code-hooks.md`).
- Every `knowledge/` page starts with YAML frontmatter:
  ```yaml
  ---
  title: "..."
  tags: [..]
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  sources: []   # raw/ paths or URLs
  related: []   # other knowledge/ pages
  ---
  ```
- **Never lose source URLs.** If a note lacks links, find canonical ones via WebSearch/WebFetch before writing.

## Operation: INGEST

For each raw note or `raw/_inbox/` file, in order — do not skip steps:

1. **Read** the file; identify the topic and every mentioned entity (tools, repos, sites).
2. **Enrich** via WebSearch/WebFetch: find canonical URLs (GitHub, official site, docs, npm, Chrome Web Store); fetch 1–2 key URLs for install commands, license, core features.
3. **New or update?** Compare against `index.md` and existing `raw/` files.
4. **Write a DETAILED `raw/<kebab>.md`** (or append a dated section to the existing one). Structure: Sources → What it is → Install → Usage → Comparison → When to use. Raw files are thorough, not 2-line summaries.
5. **Move images** from `_inbox/assets/<slug>/` to `raw/_assets/<topic>/`, fix markdown paths.
6. **Create/update a `knowledge/` page** in the right folder (concepts / tools / skills / connections). It may be shorter than the raw file but MUST cite source URLs right after the title.
7. **Update `related:`** in 2–3 neighbouring pages.
8. **Update `index.md`** (correct section + Sources).
9. **Prepend to `log.md`** (newest entry on top):
   ```
   ## [YYYY-MM-DD] ingest | <title>
   - Источник / Source: raw/<file> + URLs
   - Создано / Created: knowledge/<category>/<page>.md
   - Обновлены связи / Links: [files]
   ```

Folder choice for step 6: `skills/` only for genuine Claude Code Skills (installed via `git clone … ~/.claude/skills/` or `/plugin install`); everything else tool-like → `tools/`; ideas/methods → `concepts/`; relationships → `connections/`.

Limit: ≤10–15 files per INGEST run.

## Operation: QUERY

1. Read `index.md` to navigate.
2. Find relevant `knowledge/` pages.
3. Answer with quotes and links to specific pages.
4. If the answer contains new, reusable knowledge — save it as a new page and update `index.md`.

## Operation: LINT

Report (don't silently fix unless asked):
1. **Broken links** — every `related:`/`sources:` points to an existing file.
2. **Orphans** — `knowledge/` pages missing from `index.md`.
3. **Contradictions** — conflicting info across pages.
4. **Stale** — pages not updated in >30 days.

## Hard rules

- `raw/` is the user's source of truth: never edit or delete it (the bot handles `_inbox/` cleanup).
- On conflict between `raw/` and `knowledge/`, `raw/` wins.
- `log.md` is append-only, newest on top; never remove existing entries.
- Respect the language set in `CLAUDE.md` (default: Russian prose, English code/terms).
