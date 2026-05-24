#!/usr/bin/env python3
"""
compile.py — Сводка по дневным логам за последние N дней.

Использование:
    python wiki/scripts/compile.py          # за последние 7 дней
    python wiki/scripts/compile.py --days 30
"""

import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Сводка дневных логов вики")
    parser.add_argument("--days", type=int, default=7, help="Период в днях (по умолчанию 7)")
    args = parser.parse_args()

    wiki_root = Path(__file__).resolve().parent.parent
    daily_dir = wiki_root / "daily"
    knowledge_dir = wiki_root / "knowledge"

    if not daily_dir.exists():
        print("Папка daily/ не найдена.")
        return

    cutoff = datetime.now() - timedelta(days=args.days)
    total_entries = 0
    mentioned_files = set()
    daily_files_found = []

    for daily_file in sorted(daily_dir.glob("*.md")):
        # Извлекаем дату из имени файла
        try:
            file_date = datetime.strptime(daily_file.stem, "%Y-%m-%d")
        except ValueError:
            continue

        if file_date < cutoff:
            continue

        daily_files_found.append(daily_file.name)
        content = daily_file.read_text(encoding="utf-8")

        # Считаем записи (строки ###)
        entries = [line for line in content.splitlines() if line.startswith("### ")]
        total_entries += len(entries)

        # Извлекаем упомянутые файлы
        for match in re.finditer(r"`([^`]+\.(md|py|txt|json))`", content):
            mentioned_files.add(match.group(1))

    # Собираем существующие knowledge-страницы
    existing_pages = set()
    for category in ["concepts", "tools", "connections"]:
        cat_dir = knowledge_dir / category
        if cat_dir.exists():
            for f in cat_dir.glob("*.md"):
                existing_pages.add(f"knowledge/{category}/{f.name}")

    # Определяем, какие страницы стоит обновить
    pages_to_update = []
    for f in mentioned_files:
        if f.startswith("knowledge/") and f in existing_pages:
            pages_to_update.append(f)

    # Вывод
    print(f"=== Сводка за последние {args.days} дней ===\n")
    print(f"Дневных логов: {len(daily_files_found)}")
    print(f"Записей всего: {total_entries}")
    print(f"Упомянуто файлов: {len(mentioned_files)}")

    if daily_files_found:
        print(f"\nЛоги: {', '.join(daily_files_found)}")

    if pages_to_update:
        print(f"\nСтраницы knowledge/ для обновления:")
        for p in sorted(pages_to_update):
            print(f"  - {p}")
    elif existing_pages:
        print(f"\nВсе {len(existing_pages)} страниц knowledge/ актуальны.")
    else:
        print("\nСтраниц knowledge/ пока нет.")

    if mentioned_files - {p for p in mentioned_files if p.startswith("knowledge/")}:
        other = sorted(f for f in mentioned_files if not f.startswith("knowledge/"))
        print(f"\nПрочие затронутые файлы:")
        for f in other:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
