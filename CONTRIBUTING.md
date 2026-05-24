# Contributing

Thanks for considering a contribution! This repo is the **engine + template** for an LLM wiki — the bot, the scripts, the method (`CLAUDE.md`), and the skill. The wiki *content* is personal to each user and lives in their own (usually private) copy, so contributions here target the **engine and docs**, never anyone's notes.

🇷🇺 [Русская версия ниже](#на-русском)

## Good first contributions

- 🐛 **Bug fixes** in the Telegram bot (`scripts/tg_bot.py`) or helper scripts.
- 🔌 **New intake sources** — beyond forwarded posts and YouTube (e.g. RSS, web articles, voice notes).
- 📝 **Docs** — fix typos, clarify setup, improve the method in `CLAUDE.md` / `SKILL.md`.
- 🌍 **Translations** — the project is RU/EN today; more languages welcome.
- 💡 **Ideas / questions** — open an Issue. Not every contribution is code.

## Dev setup

```bash
git clone https://github.com/AlexKorWeb/llm-wiki.git
cd llm-wiki/scripts
pip install -r requirements.txt
cp .env.example .env        # fill BOT_TOKEN + OWNER_USER_ID to test the bot
```

You also need [Claude Code](https://claude.com/claude-code) on your `PATH` (the bot calls `claude -p`).

## Pull request flow

1. **Fork** the repo and create a branch (`fix/youtube-timeout`, `feat/rss-intake`).
2. Make your change. Keep PRs focused — one thing per PR.
3. In the PR description, say **what** changed and **why**.
4. Open the PR against `main`. The maintainer reviews, may ask for tweaks, then merges.

## Guidelines

- **Never commit secrets or content.** No `.env`, no tokens, no personal `raw/`/`knowledge/` notes. `.gitignore` already blocks the obvious ones — keep it that way.
- **Keep `scripts/*.ps1` ASCII-only.** Windows PowerShell 5.1 mis-decodes non-ASCII in `.ps1` files without a BOM. Python scripts are UTF-8 and may contain Russian.
- **Respect the method's language.** `CLAUDE.md` prose is Russian + English code/terms by design; don't "fix" that unless the PR is a translation.
- **Match the existing style.** Small, readable, dependency-light. The whole point is *no infra*.
- **Test the bot manually** if you touched it: forward a post, confirm an article is created and committed.

> Note: the maintainer develops the bot in a private wiki and mirrors engine files here via `sync-engine.ps1`. That's fine — your merged PRs become the new upstream and flow back. Just send PRs against this repo as usual.

## Code of conduct

Be kind, be specific, assume good intent. That's it.

---

## На русском

Спасибо, что хочешь помочь! Этот репозиторий — **движок + шаблон** для LLM-вики (бот, скрипты, метод `CLAUDE.md`, скилл). *Контент* вики у каждого свой и живёт в его (обычно приватной) копии — поэтому вклад сюда касается **движка и документации**, а не чьих-то заметок.

### С чего начать

- 🐛 **Фиксы багов** в боте (`scripts/tg_bot.py`) или вспомогательных скриптах.
- 🔌 **Новые источники приёма** — кроме пересланных постов и YouTube (RSS, веб-статьи, голосовые).
- 📝 **Документация** — опечатки, понятность установки, улучшение метода в `CLAUDE.md` / `SKILL.md`.
- 🌍 **Переводы** — сейчас RU/EN, другие языки приветствуются.
- 💡 **Идеи / вопросы** — заведи Issue. Не всякий вклад — это код.

### Локальная разработка

```bash
git clone https://github.com/AlexKorWeb/llm-wiki.git
cd llm-wiki/scripts
pip install -r requirements.txt
cp .env.example .env        # впиши BOT_TOKEN + OWNER_USER_ID для теста бота
```

Ещё нужен [Claude Code](https://claude.com/claude-code) в `PATH` (бот вызывает `claude -p`).

### Как прислать правку (Pull Request)

1. Сделай **форк** и ветку (`fix/youtube-timeout`, `feat/rss-intake`).
2. Внеси изменение. Один PR — одна задача.
3. В описании PR укажи **что** поменял и **зачем**.
4. Открой PR в ветку `main`. Мейнтейнер проверит, возможно попросит правки, затем вольёт.

### Правила

- **Никогда не коммить секреты и контент.** Ни `.env`, ни токенов, ни личных заметок `raw/`/`knowledge/`.
- **Держи `scripts/*.ps1` в ASCII.** Windows PowerShell 5.1 ломается на кириллице в `.ps1` без BOM. Python-скрипты — UTF-8, кириллица в них допустима.
- **Уважай язык метода.** Проза `CLAUDE.md` — русский + английские код/термины намеренно; не «исправляй» это, если PR не про перевод.
- **Соблюдай стиль.** Просто, читаемо, без лишних зависимостей. Весь смысл — *никакой инфраструктуры*.
- **Протестируй бота** вручную, если трогал его: перешли пост, убедись, что статья создалась и закоммитилась.

### Кодекс поведения

Будь доброжелателен, конкретен, предполагай добрые намерения. Всё.
