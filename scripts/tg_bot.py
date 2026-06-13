#!/usr/bin/env python3
"""
tg_bot.py — Telegram-бот для приёма пересланных постов и автоматического INGEST в LLM-вики.

Поток:
1. Принимает только пересланные сообщения от OWNER_USER_ID
2. Сохраняет текст в raw/_inbox/<timestamp>_<source>.md
3. Скачивает картинки в raw/_inbox/assets/<slug>/
4. После DEBOUNCE_SECONDS бездействия запускает `claude -p` для INGEST всей пачки
5. После успеха Claude должен сам очистить обработанные файлы из raw/_inbox/

Запуск:
    pip install -r requirements.txt
    cp .env.example .env  # заполнить BOT_TOKEN и OWNER_USER_ID
    python tg_bot.py
"""

import asyncio
import json
import logging
import os
import re
import shutil
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _YT_AVAILABLE = True
except ImportError:
    _YT_AVAILABLE = False

WIKI_ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = WIKI_ROOT / "raw" / "_inbox"
ASSETS_DIR = INBOX_DIR / "assets"
KNOWLEDGE_DIR = WIKI_ROOT / "knowledge"
LOGS_DIR = WIKI_ROOT / "scripts" / "logs"

# CREATE_NO_WINDOW (0x08000000): дочерние консольные процессы (claude.exe, git.exe)
# не должны открывать своё окно консоли. Без этого флага, когда бот запущен
# безоконно (pythonw.exe), каждый подпроцесс всплывает отдельным окном, а закрытие
# этого окна шлёт Ctrl+C и прерывает операцию. На не-Windows флаг = 0 (no-op).
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

load_dotenv(WIKI_ROOT / "scripts" / ".env")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0") or "0")
DEBOUNCE_SECONDS = int(os.getenv("DEBOUNCE_SECONDS", "60") or "60")
MAX_TURNS = int(os.getenv("CLAUDE_MAX_TURNS", "60") or "60")
# Модель для `claude -p`. Если задана — передаём --model, чтобы INGEST НЕ зависел
# от глобального дефолта Claude Code (иначе смена модели через /model или временно
# недоступная модель ломает все INGEST). Пусто => наследуется дефолт claude.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "").strip()

if not BOT_TOKEN or not OWNER_USER_ID:
    raise SystemExit(
        "Установи BOT_TOKEN и OWNER_USER_ID в scripts/.env "
        "(скопируй из scripts/.env.example)"
    )

INBOX_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            LOGS_DIR / f"tg_bot_{datetime.now():%Y-%m-%d}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("tg_bot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

_ingest_task: asyncio.Task | None = None
_bot_started_at: datetime | None = None
STOP_CALLBACK = "bot:stop"
_STOP_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🛑 Остановить бот", callback_data=STOP_CALLBACK)]]
)
_last_chat_id: int | None = None


def _max_wiki_mtime() -> float:
    """Максимальное mtime среди knowledge/, log.md, index.md.

    log.md и index.md учитываются, потому что claude -p может корректно
    распознать дубликат и просто записать skip-строку в log.md, не трогая
    knowledge/. Это валидное завершение INGEST.
    """
    mtimes: list[float] = []
    if KNOWLEDGE_DIR.exists():
        mtimes.extend(f.stat().st_mtime for f in KNOWLEDGE_DIR.rglob("*.md"))
    for extra in (WIKI_ROOT / "log.md", WIKI_ROOT / "index.md"):
        if extra.exists():
            mtimes.append(extra.stat().st_mtime)
    return max(mtimes) if mtimes else 0.0


def _cleanup_inbox(inbox_file: Path) -> None:
    """Удаляет обработанный inbox-файл и его asset-папку (если есть)."""
    slug = inbox_file.stem
    asset_dir = ASSETS_DIR / slug
    try:
        inbox_file.unlink(missing_ok=True)
        logger.info("Cleanup: удалён %s", inbox_file.name)
    except Exception as e:
        logger.error("Не удалось удалить inbox-файл %s: %s", inbox_file.name, e)
    if asset_dir.exists():
        try:
            shutil.rmtree(asset_dir)
            logger.info("Cleanup: удалена asset-папка %s", asset_dir.name)
        except Exception as e:
            logger.error("Не удалось удалить asset-папку %s: %s", asset_dir.name, e)


def slugify(text: str, maxlen: int = 30) -> str:
    """Безопасный slug для имени файла. Сохраняет кириллицу."""
    if not text:
        return "unknown"
    s = re.sub(r"[^\wЀ-ӿ]+", "-", text, flags=re.UNICODE)
    s = s.strip("-").lower()
    return s[:maxlen] or "unknown"


YOUTUBE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/|v/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


def extract_youtube_ids(text: str) -> list[str]:
    """Возвращает уникальные YouTube video IDs из текста, в порядке появления."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in YOUTUBE_URL_RE.finditer(text):
        vid = m.group(1)
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


def _fetch_youtube_sync(video_id: str) -> dict:
    """Получает метаданные через oembed + transcript (если доступен).

    Возвращает dict с ключами: video_id, url, title, author, author_url,
    transcript, transcript_lang, error (optional).
    """
    result: dict = {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "title": "",
        "author": "",
        "author_url": "",
        "transcript": "",
        "transcript_lang": "",
    }
    # 1) oembed для title + author
    try:
        oembed = (
            f"https://www.youtube.com/oembed"
            f"?url=https://www.youtube.com/watch?v={video_id}&format=json"
        )
        req = urllib.request.Request(oembed, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            meta = json.loads(r.read().decode("utf-8"))
            result["title"] = meta.get("title", "")
            result["author"] = meta.get("author_name", "")
            result["author_url"] = meta.get("author_url", "")
    except Exception as e:
        result["oembed_error"] = f"{type(e).__name__}: {e}"[:200]
    # 2) transcript
    if _YT_AVAILABLE:
        try:
            api = YouTubeTranscriptApi()
            t = api.fetch(video_id, languages=["ru", "en"])
            result["transcript"] = " ".join(s.text for s in t.snippets).strip()
            result["transcript_lang"] = t.language_code
        except Exception as e:
            result["transcript_error"] = f"{type(e).__name__}: {e}"[:200]
    else:
        result["transcript_error"] = "youtube-transcript-api не установлен"
    return result


async def fetch_youtube_data(video_id: str) -> dict:
    """Async-обёртка над _fetch_youtube_sync (выполняет в executor)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_youtube_sync, video_id)


def format_youtube_block(data: dict) -> list[str]:
    """Готовит markdown-блок про одно YouTube-видео для вставки в inbox-файл."""
    lines: list[str] = []
    title = data.get("title") or data["video_id"]
    lines.append(f"### YouTube: {title}")
    lines.append("")
    if data.get("author"):
        author_url = data.get("author_url") or ""
        if author_url:
            lines.append(f"- **Канал:** [{data['author']}]({author_url})")
        else:
            lines.append(f"- **Канал:** {data['author']}")
    lines.append(f"- **URL:** {data['url']}")
    if data.get("transcript"):
        lang = data.get("transcript_lang") or "?"
        lines.append(f"- **Transcript ({lang}):**")
        lines.append("")
        lines.append("```")
        lines.append(data["transcript"])
        lines.append("```")
    else:
        err = data.get("transcript_error") or "недоступен"
        lines.append(f"- **Transcript:** не удалось получить ({err})")
    lines.append("")
    return lines


def get_source_name(message) -> str:
    """Достаёт имя канала/пользователя-источника из forward_origin."""
    fo = getattr(message, "forward_origin", None)
    if fo is None:
        return "unknown"
    type_name = type(fo).__name__
    if "Channel" in type_name:
        return getattr(fo.chat, "title", None) or "channel"
    if "HiddenUser" in type_name:
        return getattr(fo, "sender_user_name", None) or "hidden"
    if "User" in type_name:
        u = getattr(fo, "sender_user", None)
        return (u.full_name if u else None) or "user"
    if "Chat" in type_name:
        return getattr(fo.sender_chat, "title", None) or "chat"
    return "unknown"


async def schedule_ingest(application: Application) -> None:
    """Планирует INGEST после DEBOUNCE_SECONDS бездействия (debounce)."""
    global _ingest_task
    if _ingest_task and not _ingest_task.done():
        _ingest_task.cancel()

    async def run_after_delay():
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
            await run_ingest(application)
        except asyncio.CancelledError:
            pass

    _ingest_task = asyncio.create_task(run_after_delay())


async def run_ingest(application: Application) -> None:
    """Запускает `claude -p` ОТДЕЛЬНО для каждого файла в raw/_inbox/.

    Так у Claude каждый раз свежий бюджет ходов и он успевает завершить
    полный INGEST (raw → knowledge → index → log → cleanup) для одной темы.
    """
    files = sorted(INBOX_DIR.glob("*.md"))
    if not files:
        return

    claude_cmd = shutil.which("claude")
    if not claude_cmd:
        await _notify(application, "❌ Не найдена команда `claude`. Установи Claude Code и проверь PATH.")
        return

    await _notify(application, f"🔄 Начинаю INGEST: {len(files)} файлов поочерёдно...")

    successes: list[str] = []
    failures: list[str] = []

    for idx, inbox_file in enumerate(files, start=1):
        rel_path = f"raw/_inbox/{inbox_file.name}"
        slug = inbox_file.stem
        prompt = (
            f"Обработай ОДИН входящий файл: {rel_path}\n\n"
            "Действуй строго по шагам, не пропуская ни одного:\n\n"
            "ШАГ 1. Прочитай файл, определи тему. В пересланном посте могут быть "
            "упоминания инструментов/репозиториев/сайтов БЕЗ прямых ссылок (Telegram "
            "часто их вырезает). Извлеки названия упомянутых сущностей.\n\n"
            "ШАГ 2. ОБОГАТИ контент через WebSearch и WebFetch:\n"
            "  - Для каждого упомянутого инструмента/проекта найди канонические URL "
            "(GitHub-репо, официальный сайт, документация, npm-пакет, Chrome Web Store).\n"
            "  - Кратко зафетчи 1-2 ключевых URL чтобы достать инструкции по установке, "
            "лицензию, ключевые фичи. Не пытайся читать всю документацию — только важное.\n"
            "  - Сохрани собранные URL — они ОБЯЗАТЕЛЬНО войдут в raw/-файл.\n\n"
            "ШАГ 3. Прочитай index.md и сравни с существующими файлами в raw/. "
            "Реши: новая ли это тема или дополнение к существующему raw/<тема>.md.\n\n"
            "ШАГ 4a (если дополнение): добавь раздел в конец существующего raw/-файла "
            "(текст поста + найденные URL + ключевые детали из WebFetch), отметь дату и источник.\n\n"
            "ШАГ 4b (если новая тема): создай ПОДРОБНУЮ raw/<kebab-case-имя>.md статью. "
            "Структура обязательна:\n"
            "  # <Название>\n"
            "  **Источники:** все найденные URL (GitHub, официальный сайт, доки, "
            "Telegram-канал откуда пришёл пост)\n"
            "  **Лицензия:** если применимо\n"
            "  ---\n"
            "  ## Что это (развёрнутое описание, не 2 строки)\n"
            "  ## Установка (точные команды из доков)\n"
            "  ## Использование (примеры, флаги, кейсы)\n"
            "  ## Сравнение с альтернативами (если уместно)\n"
            "  ## Когда применять / Применение в LLM-вики\n"
            "Имя файла — короткое, описательное, на латинице.\n\n"
            "ШАГ 5. Если в raw/_inbox/assets/<slug>/ есть картинки — перенеси их "
            "в raw/_assets/<имя-темы>/ и обнови markdown-пути на _assets/<имя-темы>/imgN.jpg.\n\n"
            "ШАГ 6. ОБЯЗАТЕЛЬНО создай или обнови knowledge/-страницу. Выбор папки:\n"
            "  - knowledge/skills/ — ТОЛЬКО Claude Code Skills (пакет SKILL.md в "
            "~/.claude/skills/). Признак: устанавливается через `git clone ... ~/.claude/skills/`, "
            "`/plugin install`, или явно описан как «skill для Claude Code».\n"
            "  - knowledge/tools/ — утилиты, библиотеки, CLI, плагины, сервисы, "
            "платформы (всё, что не Skill).\n"
            "  - knowledge/concepts/ — понятия, методики, техники.\n"
            "  - knowledge/connections/ — связи между темами.\n"
            "Краткая, но с указанием URL-источников сразу после заголовка. "
            "YAML frontmatter (title, tags, created, updated, sources, related). "
            "Если дополнение — обнови updated и добавь раздел в конце.\n\n"
            "ШАГ 7. Обнови related-связи в 2-3 смежных knowledge-страницах "
            "(найти по index.md те, к которым тема относится).\n\n"
            "ШАГ 8. Обнови index.md — добавь запись в Skills/Tools/Concepts/Connections и Sources. "
            "Skills и Tools — отдельные секции, не путай.\n\n"
            "ШАГ 9. Дополни log.md новой записью СВЕРХУ (сразу после заголовка "
            "`# Wiki Log` и подсказки про порядок), не в конец файла. Формат:\n"
            "## [YYYY-MM-DD] ingest | <название>\n"
            "- Источник: raw/_inbox/<файл> + найденные URL\n"
            "- Создано/обновлено: <путь>\n\n"
            "Критические правила:\n"
            "- raw/-файл должен быть ПОДРОБНЫЙ (не 2-строчное резюме). knowledge-файл "
            "может быть короче, но ОБЯЗАН ссылаться на URL-источники.\n"
            "- НИКОГДА не теряй найденные ссылки на GitHub/документацию.\n"
            "- Не пропускай шаги 6-9 — они критичны. Если шаг 6 не сделан, "
            "INGEST не считается завершённым.\n"
            "- НЕ удаляй файл из raw/_inbox/ — это сделает бот после проверки твоей работы."
        )

        logger.info("[%d/%d] claude -p %s", idx, len(files), inbox_file.name)
        # Снимок mtime вики ДО — для верификации что что-то изменилось
        before_mtime = _max_wiki_mtime()

        claude_args = ["-p", prompt, "--max-turns", str(MAX_TURNS)]
        if CLAUDE_MODEL:
            claude_args += ["--model", CLAUDE_MODEL]
        try:
            proc = await asyncio.create_subprocess_exec(
                claude_cmd,
                *claude_args,
                cwd=str(WIKI_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_NO_WINDOW,
            )
            stdout, stderr = await proc.communicate()

            # Логируем полный вывод в файл для отладки
            log_file = LOGS_DIR / f"claude_p_{slug}_{datetime.now():%H%M%S}.log"
            log_file.write_text(
                f"=== EXIT CODE: {proc.returncode} ===\n\n"
                f"=== STDOUT ===\n{stdout.decode('utf-8', errors='replace')}\n\n"
                f"=== STDERR ===\n{stderr.decode('utf-8', errors='replace')}\n",
                encoding="utf-8",
            )

            after_mtime = _max_wiki_mtime()
            wiki_changed = after_mtime > before_mtime

            if proc.returncode == 0 and wiki_changed:
                # Успех — claude завершил, вики изменилась (knowledge/, log.md
                # или index.md). Запись в log.md о skip-дубле тоже валидна.
                _cleanup_inbox(inbox_file)
                successes.append(inbox_file.name)
            elif proc.returncode == 0 and not wiki_changed:
                # Claude вернул 0, но ни один трекаемый файл не тронут —
                # подозрительно, оставляем для retry.
                failures.append(
                    f"{inbox_file.name}: exit 0, но вики не изменилась "
                    f"(см. {log_file.name}, файл оставлен для retry)"
                )
                logger.warning("вики не изменилась, оставляю %s", inbox_file.name)
            else:
                err = stderr.decode("utf-8", errors="replace").strip()[-200:]
                failures.append(
                    f"{inbox_file.name}: exit {proc.returncode}, "
                    f"{err or f'см. {log_file.name}'}"
                )
        except Exception as e:
            failures.append(f"{inbox_file.name}: {e}")
            logger.exception("Сбой при обработке %s", inbox_file.name)

    # Итоговое сообщение
    summary = [f"✅ INGEST завершён: {len(successes)}/{len(files)} файлов"]
    if failures:
        summary.append("\n❌ Ошибки:")
        summary.extend(f"- {f[:200]}" for f in failures[:5])

    git_status = await git_commit_push()
    summary.append(f"\n{git_status}")

    await _notify(application, "\n".join(summary), with_stop_button=True)


async def _run_git(*args: str) -> tuple[int, str, str]:
    """Helper: run git command, return (code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(WIKI_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def git_commit_push() -> str:
    """Auto add+commit+push после успешного INGEST. Возвращает статус для чата."""
    code, _, err = await _run_git("add", "-A")
    if code != 0:
        return f"⚠️ git add failed: {err.strip()[:200]}"

    # Есть ли что коммитить?
    code, _, _ = await _run_git("diff", "--cached", "--quiet")
    if code == 0:
        return "ℹ️ Нет изменений для коммита"

    msg = f"INGEST via tg-bot: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    code, _, err = await _run_git("commit", "-m", msg)
    if code != 0:
        return f"⚠️ git commit failed: {err.strip()[:200]}"

    code, _, err = await _run_git("push")
    if code != 0:
        return f"⚠️ git push failed: {err.strip()[:200]}"

    return "📤 Запушено на GitHub"


async def _notify(
    application: Application,
    text: str,
    *,
    with_stop_button: bool = False,
) -> None:
    # Fallback на OWNER_USER_ID, если ещё не было входящих сообщений
    # (нужно после рестарта бота с непустым inbox — иначе статус INGEST уходит
    # только в лог, и пользователь думает, что бот завис).
    chat_id = _last_chat_id or OWNER_USER_ID
    if not chat_id:
        logger.info(text)
        return
    try:
        # Telegram limit: 4096 chars
        await application.bot.send_message(
            chat_id=chat_id,
            text=text[:4000],
            reply_markup=_STOP_KEYBOARD if with_stop_button else None,
        )
    except Exception as e:
        logger.error("Не удалось отправить сообщение: %s", e)


async def _shutdown(application: Application, source: str) -> None:
    """Корректная остановка бота: отмена ingest-задачи + выход из polling."""
    global _ingest_task
    logger.info("Остановка по запросу: %s", source)
    if _ingest_task and not _ingest_task.done():
        _ingest_task.cancel()
    application.stop_running()


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _last_chat_id
    user = update.effective_user
    if not user or user.id != OWNER_USER_ID:
        return
    message = update.effective_message
    if message:
        _last_chat_id = message.chat_id
        await message.reply_text(
            "🛑 Останавливаю бот. Чтобы поднять снова — запусти `python scripts/tg_bot.py`."
        )
    await _shutdown(context.application, "command /stop")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _last_chat_id
    user = update.effective_user
    if not user or user.id != OWNER_USER_ID:
        return
    message = update.effective_message
    if not message:
        return
    _last_chat_id = message.chat_id

    pending = sorted(INBOX_DIR.glob("*.md"))
    pending_names = [p.name for p in pending]
    ingest_state = "running" if (_ingest_task and not _ingest_task.done()) else "idle"
    uptime = ""
    if _bot_started_at:
        delta = datetime.now() - _bot_started_at
        hh, rem = divmod(int(delta.total_seconds()), 3600)
        mm, ss = divmod(rem, 60)
        uptime = f"{hh}h {mm}m {ss}s"

    lines = [
        f"📊 Статус бота",
        f"- Uptime: {uptime or 'n/a'}",
        f"- Debounce: {DEBOUNCE_SECONDS}s",
        f"- Inbox: {len(pending_names)} файлов",
        f"- Ingest task: {ingest_state}",
    ]
    if pending_names:
        lines.append("")
        lines.append("Очередь:")
        lines.extend(f"- {n}" for n in pending_names[:10])
        if len(pending_names) > 10:
            lines.append(f"- ... (+{len(pending_names) - 10})")
    await message.reply_text("\n".join(lines), reply_markup=_STOP_KEYBOARD)


async def on_stop_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    user = update.effective_user
    if not user or user.id != OWNER_USER_ID:
        await query.answer("Только owner может остановить бот.", show_alert=True)
        return
    if query.data != STOP_CALLBACK:
        return
    await query.answer("Останавливаю…")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await context.application.bot.send_message(
        chat_id=query.message.chat_id,
        text="🛑 Останавливаю бот. Чтобы поднять снова — запусти `python scripts/tg_bot.py`.",
    )
    await _shutdown(context.application, "inline button")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _last_chat_id
    message = update.effective_message
    if not message:
        return

    user = update.effective_user
    if not user or user.id != OWNER_USER_ID:
        logger.warning(
            "Игнор от %s (id=%s)",
            user.full_name if user else "?",
            user.id if user else "?",
        )
        return

    is_forwarded = getattr(message, "forward_origin", None) is not None
    text = message.text or message.caption or ""
    yt_ids = extract_youtube_ids(text)

    # Принимаем форварды и любые прямые сообщения от owner
    # (фильтр user_id уже отсекает чужих в main()).
    if not text and not message.photo:
        # Определяем тип медиа для понятной диагностики
        media_kind = None
        if getattr(message, "video", None):
            media_kind = "video"
        elif getattr(message, "animation", None):
            media_kind = "GIF/анимация"
        elif getattr(message, "video_note", None):
            media_kind = "видео-кружок"
        elif getattr(message, "voice", None):
            media_kind = "voice-сообщение"
        elif getattr(message, "audio", None):
            media_kind = "audio"
        elif getattr(message, "document", None):
            media_kind = "document"
        elif getattr(message, "sticker", None):
            media_kind = "стикер"
        if media_kind:
            await message.reply_text(
                f"⚠️ {media_kind} без подписи — пока не распознаю. "
                "Если в видео есть полезное — пришли YouTube-ссылку или текст отдельно."
            )
        else:
            await message.reply_text("⚠️ Нет ни текста, ни фото — пропускаю.")
        return

    _last_chat_id = message.chat_id

    if is_forwarded:
        source = get_source_name(message)
    elif yt_ids:
        source = "youtube-direct"
    else:
        source = "direct"
    source_slug = slugify(source, maxlen=30)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    slug = f"{timestamp}_{source_slug}"
    inbox_file = INBOX_DIR / f"{slug}.md"
    asset_dir = ASSETS_DIR / slug

    image_lines: list[str] = []
    if message.photo:
        asset_dir.mkdir(parents=True, exist_ok=True)
        try:
            photo = message.photo[-1]
            tg_file = await photo.get_file()
            image_path = asset_dir / "img1.jpg"
            await tg_file.download_to_drive(custom_path=str(image_path))
            image_lines.append(f"![]({Path('assets') / slug / 'img1.jpg'})".replace("\\", "/"))
        except Exception as e:
            logger.error("Не удалось скачать фото: %s", e)

    content = [
        f"# {source} — {timestamp}",
        "",
        f"**Источник:** {source}",
        f"**Получено:** {datetime.now().isoformat(timespec='seconds')}",
        "",
        "---",
        "",
    ]
    if text:
        content.append(text)
        content.append("")
    if image_lines:
        content.extend(image_lines)
        content.append("")

    # YouTube: вытаскиваем transcript + метаданные по каждой ссылке
    if yt_ids:
        content.append("---")
        content.append("")
        content.append("## YouTube-видео из поста")
        content.append("")
        for vid in yt_ids:
            try:
                data = await fetch_youtube_data(vid)
                content.extend(format_youtube_block(data))
                if data.get("transcript"):
                    logger.info(
                        "YouTube %s: transcript %s, %d симв.",
                        vid, data.get("transcript_lang"), len(data["transcript"]),
                    )
                else:
                    logger.warning(
                        "YouTube %s: transcript недоступен (%s)",
                        vid, data.get("transcript_error"),
                    )
            except Exception as e:
                logger.exception("Сбой при обработке YouTube %s", vid)
                content.append(f"### YouTube: {vid}")
                content.append(f"- **URL:** https://youtu.be/{vid}")
                content.append(f"- Ошибка: {e}")
                content.append("")

    inbox_file.write_text("\n".join(content), encoding="utf-8")
    logger.info("Сохранён: %s", inbox_file.relative_to(WIKI_ROOT))

    await message.reply_text(
        f"📥 Принято: {inbox_file.name}\n"
        f"⏳ INGEST через {DEBOUNCE_SECONDS}с после последнего поста."
    )

    await schedule_ingest(context.application)


async def _post_init_scan_inbox(application: Application) -> None:
    """При старте бота: если в inbox/ лежат файлы — планируем INGEST через debounce.

    Это полезно, если бот был перезапущен после сбоя или ингест прервали.
    Также регистрирует список slash-команд для меню Telegram.
    """
    global _bot_started_at
    _bot_started_at = datetime.now()
    try:
        await application.bot.set_my_commands(
            [
                BotCommand("status", "Статус бота и очередь inbox"),
                BotCommand("stop", "Остановить бот"),
            ]
        )
    except Exception as e:
        logger.warning("Не удалось установить bot commands: %s", e)

    pending = sorted(INBOX_DIR.glob("*.md"))
    if pending:
        names = ", ".join(p.name for p in pending[:5])
        if len(pending) > 5:
            names += f", ... (+{len(pending) - 5})"
        logger.info(
            "Обнаружены незавершённые inbox-файлы (%d): %s. "
            "INGEST через %ss.",
            len(pending), names, DEBOUNCE_SECONDS,
        )
        await schedule_ingest(application)


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).post_init(_post_init_scan_inbox).build()
    # Команды регистрируем ПЕРВЫМИ, чтобы они не попадали в общий handle_message.
    owner_filter = filters.User(user_id=OWNER_USER_ID)
    app.add_handler(CommandHandler("stop", cmd_stop, filters=owner_filter))
    app.add_handler(CommandHandler("status", cmd_status, filters=owner_filter))
    app.add_handler(CallbackQueryHandler(on_stop_button, pattern=f"^{STOP_CALLBACK}$"))
    app.add_handler(
        MessageHandler(owner_filter & ~filters.COMMAND, handle_message)
    )
    logger.info(
        "Бот запущен. Owner ID: %s. Inbox: %s. Debounce: %ss.",
        OWNER_USER_ID,
        INBOX_DIR.relative_to(WIKI_ROOT),
        DEBOUNCE_SECONDS,
    )
    # drop_pending_updates=False: после рестарта бот подтянет накопленные
    # за время простоя сообщения. Telegram хранит их ~24 часа.
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
