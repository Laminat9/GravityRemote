import ctypes
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Tuple, List

import config


def format_bytes(size_bytes: int) -> str:
    """Форматирует байты в человекочитаемый вид (Б, КБ, МБ, ГБ)."""
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} КБ"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} МБ"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} ГБ"


def list_directory(dir_path_str: str) -> Tuple[bool, str]:
    """
    Возвращает красивый текстовый список файлов и папок в указанной директории.
    """
    clean_str = dir_path_str.strip().strip('"\'')
    if not clean_str:
        target_path = Path.home()
    else:
        target_path = Path(os.path.expandvars(clean_str)).expanduser().resolve()

    if not target_path.exists():
        return False, f"❌ Путь не существует:\n<code>{target_path}</code>"

    if not target_path.is_dir():
        # Если передан путь к файлу
        try:
            stat = target_path.stat()
            size = format_bytes(stat.st_size)
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            return True, (
                f"📄 <b>Файл:</b> <code>{target_path.name}</code>\n"
                f"📁 <b>Папка:</b> <code>{target_path.parent}</code>\n"
                f"⚖️ <b>Размер:</b> {size}\n"
                f"🕒 <b>Изменён:</b> {mtime}"
            )
        except Exception as e:
            return False, f"Ошибка чтения файла: {e}"

    try:
        entries = list(os.scandir(target_path))
    except PermissionError:
        return False, f"⛔ Отказано в доступе к папке:\n<code>{target_path}</code>"
    except Exception as e:
        return False, f"Ошибка при открытии папки: {e}"

    dirs: List[os.DirEntry] = []
    files: List[os.DirEntry] = []

    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                dirs.append(entry)
            else:
                files.append(entry)
        except (PermissionError, OSError):
            continue

    dirs.sort(key=lambda x: x.name.lower())
    files.sort(key=lambda x: x.name.lower())

    total_items = len(dirs) + len(files)
    limit = 35  # Лимит строк, чтобы не выйти за 4096 символов сообщения

    lines = [
        f"📁 <b>СОДЕРЖИМОЕ ПАПКИ:</b>\n<code>{target_path}</code>\n",
        f"Всего объектов: {total_items} (папок: {len(dirs)}, файлов: {len(files)})\n"
    ]

    shown = 0
    for d in dirs:
        if shown >= limit:
            break
        lines.append(f"📁 <code>{d.name}/</code>")
        shown += 1

    for f in files:
        if shown >= limit:
            break
        try:
            sz = format_bytes(f.stat().st_size)
            lines.append(f"📄 <code>{f.name}</code> ({sz})")
        except OSError:
            lines.append(f"📄 <code>{f.name}</code>")
        shown += 1

    if shown < total_items:
        lines.append(f"\n<i>... и ещё {total_items - shown} скрытых объектов</i>")

    return True, "\n".join(lines)


def get_file_for_download(file_path_str: str) -> Tuple[bool, Path | None, str]:
    """
    Проверяет файл перед отправкой пользователю в Telegram.
    Проверяет ограничение Telegram Bot API (50 МБ).
    """
    clean_str = file_path_str.strip().strip('"\'')
    if not clean_str:
        return False, None, "Путь к файлу не указан."

    target_path = Path(os.path.expandvars(clean_str)).expanduser().resolve()

    if not target_path.exists():
        return False, None, f"❌ Файл не найден:\n<code>{target_path}</code>"

    if not target_path.is_file():
        return False, None, f"❌ Указанный путь является папкой, а не файлом:\n<code>{target_path}</code>"

    try:
        size = target_path.stat().st_size
        max_bytes = 50 * 1024 * 1024  # 50 МБ лимит отправки через Telegram Bot API
        if size > max_bytes:
            return False, None, (
                f"⚠️ Файл слишком большой для отправки ботом:\n"
                f"Размер: <b>{format_bytes(size)}</b> (максимум для Telegram Bot API — 50 МБ)."
            )
        return True, target_path, f"Файл готов к отправке ({format_bytes(size)})."
    except Exception as e:
        return False, None, f"Ошибка доступа к файлу: {e}"


def save_uploaded_file(temp_file_path: Path, dest_dir_str: str, original_filename: str) -> Tuple[bool, str]:
    """
    Сохраняет загруженный из Telegram файл в указанную папку на ПК.
    """
    clean_dest = dest_dir_str.strip().strip('"\'')
    if clean_dest:
        dest_dir = Path(os.path.expandvars(clean_dest)).expanduser().resolve()
    else:
        dest_dir = config.DEFAULT_DOWNLOAD_DIR

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        target_path = dest_dir / original_filename

        # Если файл уже существует, добавляем суффикс
        if target_path.exists():
            stem = target_path.stem
            suffix = target_path.suffix
            counter = 1
            while target_path.exists():
                target_path = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.move(str(temp_file_path), str(target_path))
        return True, f"✅ Файл успешно сохранён на ПК:\n<code>{target_path}</code>"
    except Exception as e:
        return False, f"❌ Ошибка при сохранении файла: {e}"


def get_folder_size(folder_path_str: str) -> Tuple[bool, str]:
    """
    Рекурсивно вычисляет суммарный размер папки или диска.
    """
    clean_str = folder_path_str.strip().strip('"\'')
    if not clean_str:
        return False, "Путь к папке не указан."

    target_path = Path(os.path.expandvars(clean_str)).expanduser().resolve()

    if not target_path.exists():
        return False, f"❌ Путь не существует:\n<code>{target_path}</code>"

    if target_path.is_file():
        sz = target_path.stat().st_size
        return True, f"📄 Файл: <code>{target_path.name}</code>\nРазмер: <b>{format_bytes(sz)}</b>"

    total_size = 0
    file_count = 0
    folder_count = 0

    try:
        for root, dirs, files in os.walk(str(target_path)):
            folder_count += len(dirs)
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    total_size += os.path.getsize(fp)
                    file_count += 1
                except (OSError, PermissionError):
                    continue
    except Exception as e:
        return False, f"Ошибка при сканировании папки: {e}"

    return True, (
        f"📊 <b>РАЗМЕР ПАПКИ:</b>\n<code>{target_path}</code>\n\n"
        f"⚖️ Суммарный объём: <b>{format_bytes(total_size)}</b>\n"
        f"📄 Всего файлов: <b>{file_count}</b>\n"
        f"📁 Подпапок: <b>{folder_count}</b>"
    )


def empty_recycle_bin() -> Tuple[bool, str]:
    """
    Очищает Корзину Windows без всплывающих диалогов.
    """
    try:
        # Флаги: SHERB_NOCONFIRMATION (0x1) | SHERB_NOPROGRESSUI (0x2) | SHERB_NOSOUND (0x4) = 7
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 7)
        # S_OK = 0, или если корзина уже пуста (0x80041006 / -2147418113 / 0x80004005)
        if result == 0 or (result & 0xFFFFFFFF) in [0, 0x80041006, 0x80004005]:
            return True, "🗑️ Корзина Windows успешно очищена!"
        return True, f"🗑️ Команда очистки корзины выполнена (код: {hex(result & 0xFFFFFFFF)})."
    except Exception as e:
        return False, f"❌ Ошибка очистки корзины: {e}"
