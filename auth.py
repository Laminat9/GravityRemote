import functools
import logging
import re
from datetime import datetime
from typing import Callable, Any
from telegram import Update
from telegram.ext import ContextTypes

import config

# Настройка системного логгера для вывода в консоль
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("GravityRemote")

# Регулярные выражения для маскировки чувствительных данных
_SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,40}\b"),  # Telegram Bot Token
    re.compile(r"(?i)(password|passwd|pwd|token|secret)\s*[:=]\s*['\"]?([^'\"\s]+)['\"]?"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)([A-Za-z0-9._-]+)")
]


def sanitize_text(text: str) -> str:
    """Удаляет или маскирует токены, пароли и секреты из строки."""
    if not text:
        return ""
    sanitized = str(text)
    for pattern in _SENSITIVE_PATTERNS:
        sanitized = pattern.sub(r"***REDACTED***", sanitized)
    return sanitized


def is_authorized(user_id: int | None) -> bool:
    """Проверяет, входит ли user_id в белый список разрешенных пользователей."""
    if not user_id or not config.ALLOWED_USER_ID:
        return False
    return user_id == config.ALLOWED_USER_ID


def log_action(user_id: int | None, action: str, result: str, is_allowed: bool = True) -> None:
    """Записывает действие в защищенный файл аудита bot_actions.log."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_action = sanitize_text(action)
    clean_result = sanitize_text(result)
    status_tag = "ALLOWED" if is_allowed else "DENIED"
    uid = user_id if user_id is not None else 0

    log_entry = f"[{timestamp}] [USER:{uid}] [{status_tag}] ACTION: {clean_action} | RESULT: {clean_result}\n"

    try:
        with open(config.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        logger.error(f"Не удалось записать в лог безопасности: {e}")


def restricted(func: Callable) -> Callable:
    """Декоратор для проверки прав доступа к хендлерам Telegram-бота."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> Any:
        user = update.effective_user
        user_id = user.id if user else None

        if not is_authorized(user_id):
            command_desc = update.message.text if update.message and update.message.text else (
                update.callback_query.data if update.callback_query else "Unknown"
            )
            log_action(user_id, command_desc, "Попытка несанкционированного доступа", is_allowed=False)
            logger.warning(f"Несанкционированная попытка доступа от ID {user_id}")

            refusal_text = (
                "⛔ <b>Доступ запрещён</b>\n\n"
                "Этот бот настроен исключительно для удалённого управления личным ПК владельца.\n"
                f"Ваш Telegram ID (<code>{user_id}</code>) записан в лог безопасности."
            )

            if update.callback_query:
                try:
                    await update.callback_query.answer("⛔ Доступ запрещён", show_alert=True)
                    await update.callback_query.message.reply_text(refusal_text, parse_mode="HTML")
                except Exception:
                    pass
            elif update.message:
                await update.message.reply_text(refusal_text, parse_mode="HTML")

            return

        return await func(update, context, *args, **kwargs)

    return wrapper
