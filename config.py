import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Базовая директория проекта
BASE_DIR = Path(__file__).resolve().parent

# Загрузка переменных окружения из .env с принудительным переопределением
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE, override=True)
else:
    load_dotenv(override=True)

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Белый список ID пользователей Telegram (числовой ID владельца)
raw_user_id = os.getenv("ALLOWED_USER_ID", "").strip()
try:
    ALLOWED_USER_ID = int(raw_user_id) if raw_user_id else 0
except ValueError:
    ALLOWED_USER_ID = 0

# Прокси для Telegram (опционально: socks5://127.0.0.1:10808 или http://127.0.0.1:8080)
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# Папка по умолчанию для скачивания/сохранения файлов
user_profile = os.getenv("USERPROFILE", str(Path.home()))
default_downloads = Path(user_profile) / "Downloads" / "FromTelegram"
env_download_dir = os.getenv("DEFAULT_DOWNLOAD_DIR", "").strip()
DEFAULT_DOWNLOAD_DIR = Path(env_download_dir) if env_download_dir else default_downloads

# Системные пути
LOG_FILE = BASE_DIR / "bot_actions.log"
CONFIG_APPS_FILE = BASE_DIR / "config_apps.json"
TEMP_DIR = BASE_DIR / "temp"

# Создание вспомогательных директорий
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def validate_config() -> list[str]:
    """Проверяет корректность ключевых параметров конфигурации."""
    errors = []
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        errors.append("Не задан BOT_TOKEN в файле .env (получите токен у @BotFather).")
    if not ALLOWED_USER_ID:
        errors.append("Не задан ALLOWED_USER_ID в файле .env (ваш числовой ID, узнайте у @userinfobot).")
    return errors
