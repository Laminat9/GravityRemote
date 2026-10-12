import json
import logging
import os
import shlex
import subprocess
import webbrowser
from pathlib import Path
from typing import Tuple, Dict

import config

logger = logging.getLogger("GravityRemote.Apps")


def load_apps() -> Dict[str, str]:
    """Загружает список сохранённых приложений из config_apps.json."""
    if not config.CONFIG_APPS_FILE.exists():
        default_apps = {
            "Discord": "%LOCALAPPDATA%\\Discord\\Update.exe --processStart Discord.exe",
            "DuckDuckGo": "https://duckduckgo.com",
            "Telegram": "%APPDATA%\\Telegram Desktop\\Telegram.exe",
            "Notepad": "notepad.exe"
        }
        save_apps(default_apps)
        return default_apps

    try:
        with open(config.CONFIG_APPS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка при чтении config_apps.json: {e}")
        return {}


def save_apps(apps: Dict[str, str]) -> bool:
    """Сохраняет словарь приложений в config_apps.json."""
    try:
        with open(config.CONFIG_APPS_FILE, "w", encoding="utf-8") as f:
            json.dump(apps, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка при записи config_apps.json: {e}")
        return False


def add_app(name: str, path_or_command: str) -> Tuple[bool, str]:
    """Добавляет новое приложение или команду в конфигурацию."""
    name = name.strip()
    path_or_command = path_or_command.strip()
    if not name or not path_or_command:
        return False, "Имя и путь приложения не могут быть пустыми."

    apps = load_apps()
    apps[name] = path_or_command
    if save_apps(apps):
        return True, f"✅ Ярлык <b>{name}</b> успешно сохранён!"
    return False, "Не удалось сохранить конфигурацию в файл."


def remove_app(name: str) -> Tuple[bool, str]:
    """Удаляет ярлык из конфигурации."""
    apps = load_apps()
    if name in apps:
        del apps[name]
        save_apps(apps)
        return True, f"🗑️ Ярлык <b>{name}</b> удалён."
    return False, f"Ярлык <b>{name}</b> не найден."


def get_apps_list_text() -> str:
    """Формирует текстовый список всех доступных приложений."""
    apps = load_apps()
    if not apps:
        return "Список приложений пуст."

    lines = ["📋 <b>СПИСОК СОХРАНЁННЫХ ЯРЛЫКОВ:</b>\n"]
    for i, (name, target) in enumerate(apps.items(), 1):
        lines.append(f"<b>{i}.</b> <code>{name}</code>\n   Путь: <code>{target}</code>")

    lines.append("\n<i>Нажмите соответствующую кнопку в меню для запуска.</i>")
    return "\n".join(lines)


def launch_app(name: str) -> Tuple[bool, str]:
    """
    Запускает приложение по имени из конфигурации.
    Использует pathlib.Path и аргументы в виде списка.
    Корректно обрабатывает кириллицу, пробелы, .bat и ссылки.
    """
    apps = load_apps()
    target = apps.get(name)

    if not target:
        return False, f"Приложение <b>{name}</b> не найдено в конфигурации."

    target = target.strip()

    # 1. Ссылка в браузере (URL)
    if target.startswith("http://") or target.startswith("https://"):
        try:
            webbrowser.open(target)
            return True, f"🌐 Ссылка открыта в браузере:\n<code>{target}</code>"
        except Exception as e:
            return False, f"Ошибка открытия ссылки в браузере: {e}"

    # 2. Развертывание переменных окружения Windows (например, %LOCALAPPDATA%)
    expanded = os.path.expandvars(target)

    # 3. Специальная обработка Discord
    if name.lower() == "discord" or "update.exe --processstart discord.exe" in expanded.lower():
        update_exe = Path(os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe"))
        if update_exe.exists():
            try:
                cmd_list = [str(update_exe), "--processStart", "Discord.exe"]
                subprocess.Popen(cmd_list, cwd=str(update_exe.parent))
                return True, "🎮 Discord успешно запущен через менеджер обновлений."
            except Exception as e:
                return False, f"Ошибка запуска Discord: {e}"

    # 4. Команды командной строки Windows (например: cmd start duckduckgo или cmd /c start ...)
    target_clean = expanded.strip()
    if target_clean.lower().startswith("cmd ") or target_clean.lower().startswith("cmd.exe "):
        cmd_body = target_clean[target_clean.find(" ") + 1:].strip()
        if not cmd_body.lower().startswith("/c ") and not cmd_body.lower().startswith("/k "):
            cmd_body = f"/c {cmd_body}"
        try:
            subprocess.Popen(f"cmd.exe {cmd_body}", shell=True)
            return True, f"🚀 Команда выполнена: <code>cmd.exe {cmd_body}</code>"
        except Exception as e:
            return False, f"Ошибка выполнения команды cmd: {e}"

    if target_clean.lower().startswith("start "):
        try:
            subprocess.Popen(f"cmd.exe /c {target_clean}", shell=True)
            return True, f"🚀 Запущено через cmd start: <code>{target_clean}</code>"
        except Exception as e:
            return False, f"Ошибка запуска start: {e}"

    # 5. Проверяем аргументы командной строки
    # Если путь заключен в кавычки или есть пробелы с флагами
    cmd_tokens = []
    try:
        cmd_tokens = [t.strip('"\'') for t in shlex.split(expanded, posix=False)]
    except Exception:
        cmd_tokens = [expanded.strip('"\'')]

    if not cmd_tokens:
        return False, "Некорректная команда запуска."

    exe_or_script_str = cmd_tokens[0]
    script_args = cmd_tokens[1:]
    target_path = Path(exe_or_script_str)

    # Если указан относительный путь или файл без пути, проверяем
    if not target_path.exists():
        # Возможно путь содержал пробелы, но не был взят в кавычки
        full_candidate = Path(expanded.strip('"\''))
        if full_candidate.exists():
            target_path = full_candidate
            script_args = []
        else:
            return False, f"Файл не найден по пути:\n<code>{target_path}</code>"

    working_dir = str(target_path.parent) if target_path.parent.exists() else None

    # 5. Пакетные файлы (.bat, .cmd)
    if target_path.suffix.lower() in [".bat", ".cmd"]:
        try:
            # На Windows bat-файлы запускаются через cmd.exe
            run_cmd = ["cmd.exe", "/c", str(target_path)] + script_args
            subprocess.Popen(run_cmd, cwd=working_dir)
            return True, f"⚡ Скрипт <b>{name}</b> успешно запущен."
        except Exception as e:
            return False, f"Ошибка запуска скрипта {name}: {e}"

    # 6. Исполняемые файлы (.exe)
    if target_path.suffix.lower() == ".exe":
        try:
            run_cmd = [str(target_path)] + script_args
            subprocess.Popen(run_cmd, cwd=working_dir)
            return True, f"🚀 Приложение <b>{name}</b> запущено."
        except Exception as e:
            return False, f"Ошибка запуска exe {name}: {e}"

    # 7. Другие зарегистрированные типы файлов (через системный вызов Windows)
    try:
        os.startfile(str(target_path))
        return True, f"📄 Файл <b>{name}</b> запущен через системную ассоциацию Windows."
    except Exception as e:
        return False, f"Ошибка запуска файла {name}: {e}"
