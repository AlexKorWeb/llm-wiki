#!/usr/bin/env python3
"""
flush.py — Дописывает запись о сессии в daily/YYYY-MM-DD.md

Использование:
    python wiki/scripts/flush.py "Краткое описание сессии"
    python wiki/scripts/flush.py "Описание" --files file1.md file2.md
"""

import sys
import json
from datetime import datetime
from pathlib import Path


def main():
    wiki_root = Path(__file__).resolve().parent.parent
    daily_dir = wiki_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    # Получаем резюме
    if len(sys.argv) < 2:
        print("Использование: python flush.py \"Описание сессии\" [--files file1 file2 ...]")
        sys.exit(1)

    summary = sys.argv[1]

    # Получаем список файлов (опционально)
    changed_files = []
    if "--files" in sys.argv:
        idx = sys.argv.index("--files")
        changed_files = sys.argv[idx + 1:]

    # Если вызван через stdin (hook), пробуем прочитать JSON
    if not sys.stdin.isatty():
        try:
            hook_data = json.load(sys.stdin)
            # Извлекаем информацию о файлах из hook данных
            if isinstance(hook_data, dict):
                tool_input = hook_data.get("tool_input", {})
                if isinstance(tool_input, dict):
                    fp = tool_input.get("file_path", "")
                    if fp and fp not in changed_files:
                        changed_files.append(fp)
        except (json.JSONDecodeError, EOFError):
            pass

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    daily_file = daily_dir / f"{date_str}.md"

    # Если файл не существует — создаём заголовок
    if not daily_file.exists():
        daily_file.write_text(f"# Дневной лог: {date_str}\n\n", encoding="utf-8")

    # Формируем запись
    entry_lines = [f"### {time_str} — {summary}\n"]
    if changed_files:
        entry_lines.append("Затронутые файлы:\n")
        for f in changed_files:
            entry_lines.append(f"- `{f}`\n")
    entry_lines.append("\n")

    # Дописываем
    with daily_file.open("a", encoding="utf-8") as fh:
        fh.writelines(entry_lines)

    print(f"[flush] Запись добавлена в {daily_file.relative_to(wiki_root)}")


if __name__ == "__main__":
    main()
