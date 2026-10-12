import asyncio
import html
import logging
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Optional
import psutil

from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

# Корректная поддержка UTF-8 и эмодзи в консоли Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import config
import auth
from auth import restricted, log_action
import system_control
import app_launcher
import file_manager

logger = logging.getLogger("GravityRemote.Bot")

# ==========================================
# Состояния ConversationHandler
# ==========================================
(
    STATE_APP_NAME,
    STATE_APP_PATH,
    STATE_FOLDER_LIST,
    STATE_FILE_DOWNLOAD,
    STATE_FILE_UPLOAD_FILE,
    STATE_FILE_UPLOAD_DEST,
    STATE_FOLDER_SIZE,
    STATE_KILL_PROC,
    STATE_CLIPBOARD_TEXT,
) = range(9)


# ==========================================
# 🎛️ ГЕНЕРАТОРЫ МЕНЮ И КНОПОК
# ==========================================

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню верхнего уровня."""
    keyboard = [
        [
            InlineKeyboardButton("📊 МОНИТОРИНГ", callback_data="menu:monitoring"),
            InlineKeyboardButton("📱 ЗАПУЩЕННЫЕ ПРИЛОЖЕНИЯ", callback_data="menu:running_apps"),
        ],
        [
            InlineKeyboardButton("🚀 ЯРЛЫКИ", callback_data="menu:apps"),
            InlineKeyboardButton("📁 ФАЙЛОВЫЙ МЕНЕДЖЕР", callback_data="menu:files"),
        ],
        [
            InlineKeyboardButton("🖥️ ДЕЙСТВИЯ С ПК", callback_data="menu:pc"),
            InlineKeyboardButton("🔔 ДОПОЛНИТЕЛЬНО", callback_data="menu:extra"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_monitoring_keyboard() -> InlineKeyboardMarkup:
    """Подменю: Мониторинг системы."""
    keyboard = [
        [
            InlineKeyboardButton("📈 Статус (CPU/RAM/Диски)", callback_data="mon:status"),
            InlineKeyboardButton("📑 Топ процессов", callback_data="mon:top_proc"),
        ],
        [
            InlineKeyboardButton("📱 Запущенные приложения", callback_data="menu:running_apps"),
            InlineKeyboardButton("📸 Скриншот экрана", callback_data="mon:screenshot"),
        ],
        [
            InlineKeyboardButton("🌐 Сеть и IP", callback_data="mon:network"),
            InlineKeyboardButton("🔋 Заряд батареи", callback_data="mon:battery"),
        ],
        [
            InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:main"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def format_running_apps_message(apps: list, page: int = 0, per_page: int = 5) -> str:
    """Формирует текстовое сообщение со списком активных оконных приложений."""
    total = len(apps)
    if total == 0:
        return (
            "📱 <b>ЗАПУЩЕННЫЕ ПРИЛОЖЕНИЯ И ОКНА</b>\n\n"
            "В данный момент активных оконных приложений не обнаружено.\n"
            "<i>(Фоновые службы и процессы можно посмотреть в разделе «Мониторинг → Топ процессов»)</i>"
        )

    start_idx = page * per_page
    end_idx = min(start_idx + per_page, total)
    total_pages = max(1, (total + per_page - 1) // per_page)
    current_page_apps = apps[start_idx:end_idx]

    lines = [
        f"📱 <b>ЗАПУЩЕННЫЕ ПРИЛОЖЕНИЯ И ОКНА</b> (Стр. {page + 1}/{total_pages})\n"
        f"Всего открыто программ: <b>{total}</b>\n"
    ]

    for i, app in enumerate(current_page_apps, start=start_idx + 1):
        icon = app.get("icon", "📱")
        proc_name = html.escape(app["proc_name"])
        title = html.escape(app["title"])
        if len(title) > 55:
            title = title[:52] + "..."
        state_icon = "🗕 Свёрнуто" if app["is_minimized"] else "🗖 На экране"
        mem_mb = app["mem_mb"]

        lines.append(
            f"<b>{i}. {icon} {proc_name}</b> (PID: <code>{app['pid']}</code>)\n"
            f"   📄 <i>{title}</i>\n"
            f"   💾 <b>{mem_mb:.1f} МБ</b> | {state_icon}\n"
        )

    lines.append("<i>Нажмите на кнопку с номером приложения ниже для управления его окном:</i>")
    return "\n".join(lines)


def get_running_apps_keyboard(apps: list, page: int = 0, per_page: int = 5) -> InlineKeyboardMarkup:
    """Генерирует инлайн-клавиатуру со списком запущенных приложений с пагинацией."""
    keyboard = []
    total_apps = len(apps)
    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_page_apps = apps[start_idx:end_idx]

    for i, app in enumerate(current_page_apps, start=start_idx + 1):
        proc_clean = app["proc_name"]
        if proc_clean.lower().endswith(".exe"):
            proc_clean = proc_clean[:-4]
        icon = app.get("icon", "📱")
        # Лаконичный текст кнопки
        btn_text = f"{i}. {icon} {proc_clean}"
        keyboard.append([
            InlineKeyboardButton(btn_text, callback_data=f"app_win:view:{app['hwnd']}:{page}")
        ])

    # Пагинация
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Предыдущая", callback_data=f"app_win:page:{page - 1}"))
    if end_idx < total_apps:
        nav_row.append(InlineKeyboardButton("Следующая ➡️", callback_data=f"app_win:page:{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    # Системные действия
    keyboard.append([
        InlineKeyboardButton("🔄 Обновить список", callback_data=f"app_win:refresh:{page}"),
        InlineKeyboardButton("🗔 Win+Tab на ПК", callback_data=f"pc:task_view:{page}"),
    ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:main")
    ])

    return InlineKeyboardMarkup(keyboard)


def format_window_detail_message(detail: dict) -> str:
    """Формирует подробное описание выбранного окна и процесса."""
    icon = detail.get("icon", "📱")
    proc_name = html.escape(detail["proc_name"])
    title = html.escape(detail["title"])
    state = "🗕 Свёрнуто в панель задач" if detail["is_minimized"] else "🗖 Отображается на рабочем столе"
    exe_path = html.escape(detail.get("exe_path") or "Неизвестно")

    return (
        f"{icon} <b>УПРАВЛЕНИЕ ОКНОМ: {proc_name}</b>\n\n"
        f"📄 <b>Заголовок:</b> <i>{title}</i>\n"
        f"🆔 <b>PID:</b> <code>{detail['pid']}</code> (HWND: <code>{detail['hwnd']}</code>)\n"
        f"💾 <b>Память (RAM):</b> <b>{detail['mem_mb']:.1f} МБ</b>\n"
        f"⚡ <b>CPU:</b> <b>{detail['cpu']:.1f}%</b>\n"
        f"🪟 <b>Состояние:</b> {state}\n"
        f"📁 <b>Путь:</b> <code>{exe_path}</code>\n\n"
        f"<i>Выберите действие для этого приложения:</i>"
    )


def get_window_control_keyboard(hwnd: int, pid: int, page: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура управления выбранным окном приложения."""
    keyboard = [
        [
            InlineKeyboardButton("🔍 На передний план", callback_data=f"app_win:focus:{hwnd}:{page}"),
            InlineKeyboardButton("🗕 Свернуть окно", callback_data=f"app_win:min:{hwnd}:{page}"),
        ],
        [
            InlineKeyboardButton("❌ Закрыть окно", callback_data=f"app_win:close:{hwnd}:{page}"),
            InlineKeyboardButton("🛑 Завершить процесс (Kill)", callback_data=f"app_win:kill_ask:{hwnd}:{pid}:{page}"),
        ],
        [
            InlineKeyboardButton("🔄 Обновить статус", callback_data=f"app_win:view:{hwnd}:{page}"),
            InlineKeyboardButton("⬅️ К списку приложений", callback_data=f"app_win:page:{page}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_apps_keyboard() -> InlineKeyboardMarkup:
    """Подменю: Ярлыки приложений."""
    apps = app_launcher.load_apps()
    keyboard = []

    # Динамически генерируем кнопки для каждого сохранённого приложения (по 2 в ряд)
    row = []
    for app_name in apps.keys():
        row.append(InlineKeyboardButton(f"▶️ {app_name}", callback_data=f"app:run:{app_name}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Управляющие кнопки
    keyboard.append([
        InlineKeyboardButton("➕ Добавить ярлык", callback_data="app:add"),
        InlineKeyboardButton("📋 Список ярлыков", callback_data="app:list"),
    ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(keyboard)


def get_files_keyboard() -> InlineKeyboardMarkup:
    """Подменю: Файловый менеджер."""
    keyboard = [
        [
            InlineKeyboardButton("📂 Содержимое папки", callback_data="file:list"),
            InlineKeyboardButton("📥 Скачать файл с ПК", callback_data="file:download"),
        ],
        [
            InlineKeyboardButton("📤 Загрузить файл на ПК", callback_data="file:upload"),
            InlineKeyboardButton("📊 Размер папки/диска", callback_data="file:size"),
        ],
        [
            InlineKeyboardButton("🗑️ Очистить корзину", callback_data="file:empty_bin"),
        ],
        [
            InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:main"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_pc_keyboard() -> InlineKeyboardMarkup:
    """Подменю: Действия с ПК."""
    keyboard = [
        [
            InlineKeyboardButton("🗔 Свернуть все окна", callback_data="pc:minimize_all"),
            InlineKeyboardButton("📑 Task View (Win+Tab)", callback_data="pc:task_view"),
        ],
        [
            InlineKeyboardButton("📱 Запущенные приложения", callback_data="menu:running_apps"),
            InlineKeyboardButton("🔒 Заблокировать экран", callback_data="pc:lock"),
        ],
        [
            InlineKeyboardButton("🛑 Завершить процесс", callback_data="pc:kill_proc"),
            InlineKeyboardButton("🌙 Спящий режим", callback_data="pc:sleep"),
        ],
        [
            InlineKeyboardButton("🔊 Управление звуком", callback_data="menu:volume"),
        ],
        [
            InlineKeyboardButton("🔄 Перезагрузить ПК", callback_data="pc:restart"),
            InlineKeyboardButton("⏻ Выключить ПК", callback_data="pc:shutdown"),
        ],
        [
            InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:main"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_volume_keyboard(vol_info: dict) -> InlineKeyboardMarkup:
    """Подменю управления звуком."""
    mute_label = "🔇 Включить звук" if vol_info.get("is_muted") else "🔈 Выключить звук (Mute)"
    keyboard = [
        [
            InlineKeyboardButton("🔉 -10%", callback_data="vol:down"),
            InlineKeyboardButton("🔊 +10%", callback_data="vol:up"),
        ],
        [
            InlineKeyboardButton(mute_label, callback_data="vol:mute_toggle"),
            InlineKeyboardButton("🔄 Обновить", callback_data="vol:refresh"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад в Действия с ПК", callback_data="menu:pc"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_extra_keyboard() -> InlineKeyboardMarkup:
    """Подменю: Дополнительно."""
    keyboard = [
        [
            InlineKeyboardButton("📋 Показать буфер", callback_data="extra:clip_read"),
            InlineKeyboardButton("✏️ Записать в буфер", callback_data="extra:clip_write"),
        ],
        [
            InlineKeyboardButton("⏱️ Аптайм ПК", callback_data="extra:uptime"),
            InlineKeyboardButton("❓ Справка /help", callback_data="extra:help"),
        ],
        [
            InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:main"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_back_button(target: str = "menu:main") -> InlineKeyboardMarkup:
    """Кнопка возврата в меню под результатом."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ В главное меню", callback_data=target)
    ]])


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Кнопка отмены для режимов ожидания ввода."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Отмена", callback_data="input:cancel")
    ]])


def get_confirmation_keyboard(confirm_action: str, cancel_action: str = "confirm:no") -> InlineKeyboardMarkup:
    """Кнопки подтверждения опасных действий."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm:yes:{confirm_action}"),
            InlineKeyboardButton("❌ Отмена", callback_data=cancel_action),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ==========================================
# 🚀 КОМАНДЫ БОТА (/start, /menu, /help)
# ==========================================

@restricted
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команд /start и /menu."""
    log_action(update.effective_user.id, "/start", "Открытие главного меню")
    welcome_text = (
        "👋 <b>Добро пожаловать в GravityRemote!</b>\n\n"
        "Личный центр управления вашим компьютером под Windows.\n"
        "Выберите интересующий раздел:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            welcome_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    elif update.message:
        await update.message.reply_text(
            welcome_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    log_action(update.effective_user.id, "/help", "Запрос справки")
    help_text = (
        "📖 <b>СПРАВКА ПО КОМАНДАМ И ВОЗМОЖНОСТЯМ:</b>\n\n"
        "<b>Команды в чате:</b>\n"
        "• <code>/start</code> или <code>/menu</code> — Открыть главное инлайн-меню\n"
        "• <code>/apps</code> — Список активных окон и управление ими\n"
        "• <code>/status</code> — Быстрая сводка состояния системы\n"
        "• <code>/lock</code> — Заблокировать экран ПК\n"
        "• <code>/cancel</code> — Прервать ожидание текущего ввода данных\n"
        "• <code>/help</code> — Показать эту справку\n\n"
        "<b>Разделы меню:</b>\n"
        "📊 <b>Мониторинг:</b> нагрузка CPU/RAM, диски, аптайм, топ процессов, скриншот, IP, батарея.\n"
        "📱 <b>Запущенные приложения:</b> просмотр всех окон, разворачивание, сворачивание, закрытие, завершение процессов.\n"
        "🚀 <b>Ярлыки:</b> быстрый запуск Discord, браузера, TgWsProxy, .bat скриптов, добавление новых.\n"
        "📁 <b>Файлы:</b> обзор папок, скачивание файлов до 50 МБ на телефон, загрузка файлов на ПК, размер директорий, очистка корзины.\n"
        "🖥️ <b>Управление ПК:</b> выключение, перезагрузка, спящий режим, блокировка экрана, Win+Tab, остановка процессов, громкость.\n"
        "🔔 <b>Дополнительно:</b> чтение и запись системного буфера обмена."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            help_text,
            reply_markup=get_back_button(),
            parse_mode="HTML"
        )
    elif update.message:
        await update.message.reply_text(
            help_text,
            reply_markup=get_back_button(),
            parse_mode="HTML"
        )


@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /status для быстрого просмотра нагрузки без меню."""
    log_action(update.effective_user.id, "/status", "Быстрый статус")
    status_text = system_control.get_system_status()
    await update.message.reply_text(
        status_text,
        reply_markup=get_back_button(),
        parse_mode="HTML"
    )


@restricted
async def running_apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /apps или /running для быстрого просмотра запущенных окон."""
    log_action(update.effective_user.id, "/apps", "Команда списка запущенных приложений")
    apps = system_control.get_running_applications()
    text = format_running_apps_message(apps, page=0)
    kb = get_running_apps_keyboard(apps, page=0)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


@restricted
async def lock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /lock для быстрой блокировки экрана."""
    user_id = update.effective_user.id
    log_action(user_id, "/lock", "Блокировка экрана")
    ok, msg = system_control.lock_workstation()
    await update.message.reply_text(
        msg,
        reply_markup=get_back_button("menu:pc"),
        parse_mode="HTML"
    )


# ==========================================
# 🧭 РОУТЕР ИНЛАЙН-МЕНЮ (Callback Queries)
# ==========================================

@restricted
async def handle_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Единый диспетчер для навигации по меню и мгновенных действий."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    user_id = update.effective_user.id
    await query.answer()

    # --- 1. Навигация по разделам меню ---
    if data == "menu:main":
        await query.message.edit_text(
            "🏠 <b>Главное меню:</b>\nВыберите категорию для управления ПК:",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
        return

    elif data == "menu:monitoring":
        await query.message.edit_text(
            "📊 <b>Раздел: Мониторинг системы</b>\nВыберите действие:",
            reply_markup=get_monitoring_keyboard(),
            parse_mode="HTML"
        )
        return

    elif data == "menu:apps":
        await query.message.edit_text(
            "🚀 <b>Раздел: Ярлыки приложений</b>\nНажмите на ярлык для запуска или добавьте новый:",
            reply_markup=get_apps_keyboard(),
            parse_mode="HTML"
        )
        return

    elif data == "menu:files":
        await query.message.edit_text(
            "📁 <b>Раздел: Файловый менеджер</b>\nВыберите операцию с файлами:",
            reply_markup=get_files_keyboard(),
            parse_mode="HTML"
        )
        return

    elif data == "menu:pc":
        await query.message.edit_text(
            "🖥️ <b>Раздел: Действия с ПК</b>\nВыберите операцию:",
            reply_markup=get_pc_keyboard(),
            parse_mode="HTML"
        )
        return

    elif data == "menu:volume" or data == "vol:refresh":
        vol_info = system_control.get_volume_info()
        vol_status = f"{vol_info['percent']}%" if vol_info['success'] else "Неизвестно"
        mute_status = " (🔇 Звук выключен)" if vol_info.get("is_muted") else ""
        text = (
            f"🔊 <b>УПРАВЛЕНИЕ ГРОМКОСТЬЮ ПК</b>\n\n"
            f"Текущий уровень: <b>{vol_status}</b>{mute_status}"
        )
        await query.message.edit_text(
            text,
            reply_markup=get_volume_keyboard(vol_info),
            parse_mode="HTML"
        )
        return

    elif data == "menu:extra":
        await query.message.edit_text(
            "🔔 <b>Раздел: Дополнительно</b>\nВыберите действие:",
            reply_markup=get_extra_keyboard(),
            parse_mode="HTML"
        )
        return

    # --- 1.1. Вкладка: Запущенные приложения и окна ---
    elif data == "menu:running_apps" or data.startswith("app_win:page:"):
        page = 0
        if data.startswith("app_win:page:"):
            try:
                page = int(data.split(":")[2])
            except (IndexError, ValueError):
                page = 0
        apps = system_control.get_running_applications()
        log_action(user_id, "menu:running_apps", f"Просмотр приложений (стр. {page + 1}, найдено {len(apps)})")
        text = format_running_apps_message(apps, page=page)
        kb = get_running_apps_keyboard(apps, page=page)
        try:
            await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await query.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        return

    elif data.startswith("app_win:refresh:"):
        page = 0
        try:
            page = int(data.split(":")[2])
        except (IndexError, ValueError):
            page = 0
        apps = system_control.get_running_applications()
        log_action(user_id, "app_win:refresh", f"Обновление списка (найдено {len(apps)})")
        text = format_running_apps_message(apps, page=page)
        kb = get_running_apps_keyboard(apps, page=page)
        try:
            await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
        return

    elif data.startswith("app_win:view:"):
        parts = data.split(":")
        hwnd = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
        detail = system_control.get_window_detail(hwnd)
        if not detail:
            await query.answer("⚠️ Окно уже закрыто или не найдено.", show_alert=True)
            apps = system_control.get_running_applications()
            text = format_running_apps_message(apps, page=page)
            kb = get_running_apps_keyboard(apps, page=page)
            await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        log_action(user_id, f"app_win:view:{hwnd}", f"Просмотр окна {detail['proc_name']}")
        text = format_window_detail_message(detail)
        kb = get_window_control_keyboard(hwnd, detail["pid"], page=page)
        await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        return

    elif data.startswith("app_win:focus:"):
        parts = data.split(":")
        hwnd = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
        ok, msg = system_control.focus_window(hwnd)
        log_action(user_id, f"app_win:focus:{hwnd}", msg)
        await query.answer(msg, show_alert=True)
        detail = system_control.get_window_detail(hwnd)
        if detail:
            text = format_window_detail_message(detail)
            kb = get_window_control_keyboard(hwnd, detail["pid"], page=page)
            await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        return

    elif data.startswith("app_win:min:"):
        parts = data.split(":")
        hwnd = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
        ok, msg = system_control.minimize_window(hwnd)
        log_action(user_id, f"app_win:min:{hwnd}", msg)
        await query.answer(msg, show_alert=True)
        detail = system_control.get_window_detail(hwnd)
        if detail:
            text = format_window_detail_message(detail)
            kb = get_window_control_keyboard(hwnd, detail["pid"], page=page)
            await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        return

    elif data.startswith("app_win:close:"):
        parts = data.split(":")
        hwnd = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
        ok, msg = system_control.close_window(hwnd)
        log_action(user_id, f"app_win:close:{hwnd}", msg)
        await query.answer(msg, show_alert=True)
        time.sleep(0.5)
        apps = system_control.get_running_applications()
        text = format_running_apps_message(apps, page=page)
        kb = get_running_apps_keyboard(apps, page=page)
        await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        return

    elif data.startswith("app_win:kill_ask:"):
        parts = data.split(":")
        hwnd = int(parts[2])
        pid = int(parts[3])
        page = int(parts[4]) if len(parts) > 4 else 0
        detail = system_control.get_window_detail(hwnd)
        pname = detail["proc_name"] if detail else f"PID {pid}"
        warn_text = (
            f"⚠️ <b>ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ</b>\n\n"
            f"Принудительно уничтожить процесс <code>{html.escape(pname)}</code> (PID: {pid})?\n"
            f"Все несохранённые данные в этом приложении будут утеряны!"
        )
        await query.message.edit_text(
            warn_text,
            reply_markup=get_confirmation_keyboard(
                confirm_action=f"kill_win_pid:{pid}:{page}",
                cancel_action=f"app_win:view:{hwnd}:{page}"
            ),
            parse_mode="HTML"
        )
        return

    # --- 2. Мониторинг системы ---
    elif data == "mon:status":
        status_text = system_control.get_system_status()
        log_action(user_id, "mon:status", "Успешно")
        await query.message.reply_text(status_text, reply_markup=get_back_button(), parse_mode="HTML")
        return

    elif data == "mon:top_proc":
        procs_text = system_control.get_top_processes()
        log_action(user_id, "mon:top_proc", "Успешно")
        await query.message.reply_text(procs_text, reply_markup=get_back_button(), parse_mode="HTML")
        return

    elif data == "mon:screenshot":
        log_action(user_id, "mon:screenshot", "Создание скриншота")
        status_msg = await query.message.reply_text("📸 Делаю снимок экрана, подождите...")
        try:
            screenshot_path = system_control.take_screenshot()
            with open(screenshot_path, "rb") as photo_file:
                await query.message.reply_photo(
                    photo=photo_file,
                    caption=f"📸 <b>Скриншот экрана ПК</b>\nВремя: {system_control.datetime.now().strftime('%H:%M:%S')}",
                    reply_markup=get_back_button(),
                    parse_mode="HTML"
                )
            screenshot_path.unlink(missing_ok=True)
            await status_msg.delete()
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка создания скриншота: {e}", reply_markup=get_back_button())
        return

    elif data == "mon:network":
        net_text = system_control.get_network_info()
        log_action(user_id, "mon:network", "Успешно")
        await query.message.reply_text(net_text, reply_markup=get_back_button(), parse_mode="HTML")
        return

    elif data == "mon:battery":
        battery_text = system_control.get_battery_info()
        log_action(user_id, "mon:battery", "Успешно")
        await query.message.reply_text(battery_text, reply_markup=get_back_button(), parse_mode="HTML")
        return

    # --- 3. Ярлыки приложений ---
    elif data.startswith("app:run:"):
        app_name = data[len("app:run:"):]
        log_action(user_id, f"app:run:{app_name}", "Запуск")
        ok, msg = app_launcher.launch_app(app_name)
        log_action(user_id, f"app:run:{app_name}", "Успех" if ok else f"Ошибка: {msg}")
        await query.message.reply_text(msg, reply_markup=get_back_button("menu:apps"), parse_mode="HTML")
        return

    elif data == "app:list":
        list_text = app_launcher.get_apps_list_text()
        log_action(user_id, "app:list", "Просмотр списка ярлыков")
        await query.message.reply_text(list_text, reply_markup=get_back_button("menu:apps"), parse_mode="HTML")
        return

    # --- 4. Управление звуком ---
    elif data in ["vol:up", "vol:down"]:
        delta = 10 if data == "vol:up" else -10
        res = system_control.change_volume(delta)
        log_action(user_id, data, f"Громкость: {res.get('percent')}%")
        vol_info = system_control.get_volume_info()
        vol_status = f"{vol_info['percent']}%" if vol_info['success'] else "Неизвестно"
        mute_status = " (🔇 Звук выключен)" if vol_info.get("is_muted") else ""
        text = (
            f"🔊 <b>УПРАВЛЕНИЕ ГРОМКОСТЬЮ ПК</b>\n\n"
            f"Текущий уровень: <b>{vol_status}</b>{mute_status}"
        )
        await query.message.edit_text(text, reply_markup=get_volume_keyboard(vol_info), parse_mode="HTML")
        return

    elif data == "vol:mute_toggle":
        res = system_control.toggle_mute()
        log_action(user_id, "vol:mute_toggle", f"Mute: {res.get('is_muted')}")
        vol_info = system_control.get_volume_info()
        vol_status = f"{vol_info['percent']}%" if vol_info['success'] else "Неизвестно"
        mute_status = " (🔇 Звук выключен)" if vol_info.get("is_muted") else ""
        text = (
            f"🔊 <b>УПРАВЛЕНИЕ ГРОМКОСТЬЮ ПК</b>\n\n"
            f"Текущий уровень: <b>{vol_status}</b>{mute_status}"
        )
        await query.message.edit_text(text, reply_markup=get_volume_keyboard(vol_info), parse_mode="HTML")
        return

    # --- 5. Безопасные действия с ПК ---
    elif data == "pc:minimize_all":
        log_action(user_id, "pc:minimize_all", "Сворачивание всех окон")
        ok, msg = system_control.minimize_all_windows()
        await query.message.reply_text(msg, reply_markup=get_back_button("menu:pc"), parse_mode="HTML")
        return

    elif data.startswith("pc:task_view"):
        log_action(user_id, "pc:task_view", "Вызов Win+Tab")
        ok, msg = system_control.open_task_view()
        await query.answer("🗔 Win + Tab вызван на экране ПК!", show_alert=False)
        return

    elif data == "pc:lock":
        log_action(user_id, "pc:lock", "Блокировка экрана")
        ok, msg = system_control.lock_workstation()
        await query.message.reply_text(msg, reply_markup=get_back_button("menu:pc"), parse_mode="HTML")
        return

    # --- 6. Опасные действия (запрос подтверждения) ---
    elif data == "pc:sleep":
        warn_text = "⚠️ <b>ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЯ</b>\n\nПеревести компьютер в спящий режим?"
        await query.message.edit_text(warn_text, reply_markup=get_confirmation_keyboard("sleep"), parse_mode="HTML")
        return

    elif data == "pc:restart":
        warn_text = "⚠️ <b>ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЯ</b>\n\nПерезагрузить компьютер сейчас?"
        await query.message.edit_text(warn_text, reply_markup=get_confirmation_keyboard("restart"), parse_mode="HTML")
        return

    elif data == "pc:shutdown":
        warn_text = "⚠️ <b>ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЯ</b>\n\nЗавершить работу компьютера (выключить) сейчас?"
        await query.message.edit_text(warn_text, reply_markup=get_confirmation_keyboard("shutdown"), parse_mode="HTML")
        return

    elif data == "file:empty_bin":
        warn_text = "⚠️ <b>ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЯ</b>\n\nБезвозвратно очистить Корзину Windows?"
        await query.message.edit_text(warn_text, reply_markup=get_confirmation_keyboard("empty_bin"), parse_mode="HTML")
        return

    # --- 7. Обработка подтверждения (confirm:yes / confirm:no) ---
    elif data.startswith("confirm:yes:"):
        action_type = data[len("confirm:yes:"):]
        if action_type == "sleep":
            log_action(user_id, "confirm:sleep", "Выполнение спящего режима")
            ok, msg = system_control.suspend_pc()
            await query.message.edit_text(msg, reply_markup=get_back_button(), parse_mode="HTML")
        elif action_type == "restart":
            log_action(user_id, "confirm:restart", "Инициирована перезагрузка ПК")
            ok, msg = system_control.restart_pc()
            await query.message.edit_text(msg, reply_markup=get_back_button(), parse_mode="HTML")
        elif action_type == "shutdown":
            log_action(user_id, "confirm:shutdown", "Инициировано выключение ПК")
            ok, msg = system_control.shutdown_pc()
            await query.message.edit_text(msg, reply_markup=get_back_button(), parse_mode="HTML")
        elif action_type == "empty_bin":
            log_action(user_id, "confirm:empty_bin", "Очистка корзины")
            ok, msg = file_manager.empty_recycle_bin()
            await query.message.edit_text(msg, reply_markup=get_back_button("menu:files"), parse_mode="HTML")
        elif action_type.startswith("kill_proc:"):
            proc_name = action_type[len("kill_proc:"):]
            log_action(user_id, f"confirm:kill_proc:{proc_name}", "Завершение процесса")
            ok, msg = system_control.kill_process_by_name(proc_name)
            await query.message.edit_text(msg, reply_markup=get_back_button("menu:pc"), parse_mode="HTML")
        elif action_type.startswith("kill_win_pid:"):
            parts = action_type.split(":")
            pid = int(parts[1])
            page = int(parts[2]) if len(parts) > 2 else 0
            log_action(user_id, f"confirm:kill_win_pid:{pid}", "Принудительное завершение окна/процесса")
            ok, msg = system_control.kill_process_by_pid(pid)
            time.sleep(0.5)
            apps = system_control.get_running_applications()
            text = f"{msg}\n\n" + format_running_apps_message(apps, page=page)
            kb = get_running_apps_keyboard(apps, page=page)
            await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        return

    elif data == "confirm:no":
        log_action(user_id, "confirm:no", "Отмена опасной операции")
        await query.message.edit_text(
            "❌ <b>Действие отменено.</b>",
            reply_markup=get_back_button(),
            parse_mode="HTML"
        )
        return

    # --- 8. Дополнительно (буфер обмена, uptime, help) ---
    elif data == "extra:clip_read":
        clip_content = system_control.get_clipboard_text()
        log_action(user_id, "extra:clip_read", "Чтение буфера обмена")
        escaped_text = html.escape(clip_content)
        await query.message.reply_text(
            f"📋 <b>СОДЕРЖИМОЕ БУФЕРА ОБМЕНА:</b>\n\n<code>{escaped_text}</code>",
            reply_markup=get_back_button("menu:extra"),
            parse_mode="HTML"
        )
        return

    elif data == "extra:uptime":
        uptime_str = system_control.get_uptime_str()
        log_action(user_id, "extra:uptime", f"Uptime: {uptime_str}")
        await query.message.reply_text(
            f"⏱️ <b>Время непрерывной работы ПК:</b>\n<b>{uptime_str}</b>",
            reply_markup=get_back_button("menu:extra"),
            parse_mode="HTML"
        )
        return

    elif data == "extra:help":
        await help_command(update, context)
        return


# ==========================================
# 💬 ДИАЛОГОВЫЕ ВВОДЫ (ConversationHandler)
# ==========================================

# --- Отмена диалога ---
@restricted
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сбрасывает диалоговое состояние и возвращает в главное меню."""
    context.user_data.clear()
    text = "❌ <b>Ввод данных отменён.</b>"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    return ConversationHandler.END


# --- 1. Добавление приложения ---
@restricted
async def prompt_add_app_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_text(
        "➕ <b>ДОБАВЛЕНИЕ НОВОГО ЯРЛЫКА</b>\n\n"
        "Шаг 1 из 2: Пришлите <b>название</b> приложения следующим сообщением:\n"
        "(Например: <code>Notepad++</code> или <code>Telegram</code>)",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    return STATE_APP_NAME


@restricted
async def handle_add_app_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    app_name = update.message.text.strip()
    if not app_name:
        await update.message.reply_text("Имя не может быть пустым. Попробуйте снова или нажмите Отмена.", reply_markup=get_cancel_keyboard())
        return STATE_APP_NAME

    context.user_data["new_app_name"] = app_name
    await update.message.reply_text(
        f"Шаг 2 из 2 для <b>{app_name}</b>:\n\n"
        "Пришлите <b>путь к исполняемому файлу</b> (.exe, .bat) или URL-ссылку:\n"
        "(Например: <code>C:\\Program Files\\Notepad++\\notepad++.exe</code>)",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    return STATE_APP_PATH


@restricted
async def handle_add_app_path(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    app_path = update.message.text.strip()
    app_name = context.user_data.get("new_app_name", "Новое приложение")
    user_id = update.effective_user.id

    ok, msg = app_launcher.add_app(app_name, app_path)
    log_action(user_id, f"add_app:{app_name}", "Успешно" if ok else f"Ошибка: {msg}")

    context.user_data.clear()
    await update.message.reply_text(msg, reply_markup=get_back_button("menu:apps"), parse_mode="HTML")
    return ConversationHandler.END


# --- 2. Содержимое папки ---
@restricted
async def prompt_folder_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_text(
        "📂 <b>СОДЕРЖИМОЕ ПАПКИ</b>\n\n"
        "Пришлите путь к директории следующим сообщением:\n"
        "(Например: <code>C:\\Users\\Username\\Desktop</code> или просто <code>.</code> для домашней папки):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    return STATE_FOLDER_LIST


@restricted
async def handle_folder_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    target_dir = update.message.text.strip()
    user_id = update.effective_user.id
    log_action(user_id, f"file:list:{target_dir}", "Запрос листинга папки")

    ok, result_text = file_manager.list_directory(target_dir)
    await update.message.reply_text(result_text, reply_markup=get_back_button("menu:files"), parse_mode="HTML")
    return ConversationHandler.END


# --- 3. Скачивание файла с ПК ---
@restricted
async def prompt_file_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_text(
        "📥 <b>СКАЧАТЬ ФАЙЛ С ПК</b>\n\n"
        "Пришлите полный путь к файлу на компьютере:\n"
        "(Например: <code>C:\\Users\\Username\\Downloads\\document.pdf</code>)\n\n"
        "<i>Лимит размера для Telegram Bot API: 50 МБ.</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    return STATE_FILE_DOWNLOAD


@restricted
async def handle_file_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_path_str = update.message.text.strip()
    user_id = update.effective_user.id
    log_action(user_id, f"file:download:{file_path_str}", "Запрос скачивания")

    ok, file_path, status_desc = file_manager.get_file_for_download(file_path_str)
    if not ok or not file_path:
        await update.message.reply_text(status_desc, reply_markup=get_back_button("menu:files"), parse_mode="HTML")
        return ConversationHandler.END

    status_msg = await update.message.reply_text("⏳ Отправляю файл в чат, пожалуйста, подождите...")
    try:
        with open(file_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=file_path.name,
                caption=f"📥 Файл: <code>{file_path.name}</code>\nРазмер: {file_manager.format_bytes(file_path.stat().st_size)}",
                reply_markup=get_back_button("menu:files"),
                parse_mode="HTML"
            )
        await status_msg.delete()
        log_action(user_id, f"file:download:{file_path.name}", "Успешно отправлен")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при отправке файла: {e}", reply_markup=get_back_button("menu:files"))
        log_action(user_id, f"file:download:{file_path.name}", f"Ошибка: {e}")

    return ConversationHandler.END


# --- 4. Загрузка файла на ПК ---
@restricted
async def prompt_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    default_dir_str = str(config.DEFAULT_DOWNLOAD_DIR)
    await query.message.edit_text(
        "📤 <b>ЗАГРУЗИТЬ ФАЙЛ НА ПК</b>\n\n"
        "Отправьте документ или фото прямо в этот чат.\n\n"
        f"<i>По умолчанию файл сохранится в:</i>\n<code>{default_dir_str}</code>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    return STATE_FILE_UPLOAD_FILE


@restricted
async def handle_received_upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Принимает файл от пользователя и сохраняет его во временную директорию."""
    document = update.message.document
    photo = update.message.photo
    user_id = update.effective_user.id

    if not document and not photo:
        await update.message.reply_text(
            "Пожалуйста, пришлите файл (документ или фото) или нажмите Отмена.",
            reply_markup=get_cancel_keyboard()
        )
        return STATE_FILE_UPLOAD_FILE

    status_msg = await update.message.reply_text("⏳ Загружаю файл на сервер бота...")

    try:
        if document:
            tg_file = await update.message.document.get_file()
            filename = document.file_name or "downloaded_file.bin"
        else:
            # Если отправлено фото (берем наибольшее разрешение)
            tg_file = await photo[-1].get_file()
            filename = f"photo_{int(system_control.time.time())}.jpg"

        temp_dest = config.TEMP_DIR / f"upload_{filename}"
        await tg_file.download_to_drive(str(temp_dest))

        context.user_data["upload_temp_path"] = str(temp_dest)
        context.user_data["upload_filename"] = filename

        await status_msg.edit_text(
            f"📥 Файл <b>{filename}</b> получен!\n\n"
            "Пришлите <b>путь к папке</b>, куда его сохранить на ПК, или отправьте <code>.</code> для сохранения в папку по умолчанию:\n"
            f"<code>{config.DEFAULT_DOWNLOAD_DIR}</code>",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return STATE_FILE_UPLOAD_DEST
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при приёме файла: {e}", reply_markup=get_back_button("menu:files"))
        return ConversationHandler.END


@restricted
async def handle_upload_dest_folder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text.strip()
    user_id = update.effective_user.id

    temp_path_str = context.user_data.get("upload_temp_path")
    filename = context.user_data.get("upload_filename", "file.bin")

    if not temp_path_str or not Path(temp_path_str).exists():
        await update.message.reply_text("Временный файл не найден. Попробуйте повторить загрузку.", reply_markup=get_back_button())
        context.user_data.clear()
        return ConversationHandler.END

    dest_dir = user_input if user_input and user_input != "." else str(config.DEFAULT_DOWNLOAD_DIR)

    ok, msg = file_manager.save_uploaded_file(Path(temp_path_str), dest_dir, filename)
    log_action(user_id, f"upload:{filename}", "Успешно" if ok else f"Ошибка: {msg}")

    context.user_data.clear()
    await update.message.reply_text(msg, reply_markup=get_back_button("menu:files"), parse_mode="HTML")
    return ConversationHandler.END


# --- 5. Размер папки/диска ---
@restricted
async def prompt_folder_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_text(
        "📊 <b>РАЗМЕР ПАПКИ ИЛИ ДИСКА</b>\n\n"
        "Пришлите путь к директории следующим сообщением:\n"
        "(Например: <code>C:\\Users\\Username\\Downloads</code>)",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    return STATE_FOLDER_SIZE


@restricted
async def handle_folder_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    folder_path = update.message.text.strip()
    user_id = update.effective_user.id
    log_action(user_id, f"file:size:{folder_path}", "Расчет размера")

    status_msg = await update.message.reply_text("⏳ Подсчитываю размер папки, это может занять несколько секунд...")
    ok, result_text = file_manager.get_folder_size(folder_path)
    await status_msg.edit_text(result_text, reply_markup=get_back_button("menu:files"), parse_mode="HTML")
    return ConversationHandler.END


# --- 6. Завершение процесса по имени ---
@restricted
async def prompt_kill_proc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_text(
        "🛑 <b>ЗАВЕРШИТЬ ПРОЦЕСС</b>\n\n"
        "Пришлите имя процесса следующим сообщением:\n"
        "(Например: <code>chrome.exe</code> или <code>notepad</code>)",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    return STATE_KILL_PROC


@restricted
async def handle_kill_proc_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    proc_name = update.message.text.strip()
    if not proc_name:
        await update.message.reply_text("Имя процесса не может быть пустым.", reply_markup=get_cancel_keyboard())
        return STATE_KILL_PROC

    # Запрашиваем подтверждение перед завершением
    warn_text = (
        f"⚠️ <b>ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ</b>\n\n"
        f"Вы действительно хотите принудительно завершить процесс <code>{html.escape(proc_name)}</code>?"
    )
    await update.message.reply_text(
        warn_text,
        reply_markup=get_confirmation_keyboard(f"kill_proc:{proc_name}"),
        parse_mode="HTML"
    )
    return ConversationHandler.END


# --- 7. Запись текста в буфер обмена ---
@restricted
async def prompt_clipboard_write(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_text(
        "✏️ <b>ЗАПИСЬ В БУФЕР ОБМЕНА</b>\n\n"
        "Пришлите текст следующим сообщением, который нужно скопировать в буфер обмена ПК:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    return STATE_CLIPBOARD_TEXT


@restricted
async def handle_clipboard_write(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text_to_copy = update.message.text
    user_id = update.effective_user.id

    ok, msg = system_control.set_clipboard_text(text_to_copy)
    log_action(user_id, "extra:clip_write", f"Записано {len(text_to_copy)} символов")

    await update.message.reply_text(msg, reply_markup=get_back_button("menu:extra"), parse_mode="HTML")
    return ConversationHandler.END


# ==========================================
# ⚠️ ОБРАБОТЧИК ОШИБОК
# ==========================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный перехват ошибок для стабильности бота."""
    logger.error("Непредвиденная ошибка при обработке апдейта:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        err_msg = html.escape(str(context.error))
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⚠️ <b>Произошла ошибка:</b>\n<code>{err_msg}</code>",
                reply_markup=get_back_button(),
                parse_mode="HTML"
            )
        except Exception:
            pass


# ==========================================
# 🏁 ИНИЦИАЛИЗАЦИЯ И ЗАПУСК
# ==========================================

def build_application():
    """Создаёт и конфигурирует экземпляр приложения python-telegram-bot."""
    errors = config.validate_config()
    if errors:
        print("\n" + "=" * 60)
        print("ОШИБКА КОНФИГУРАЦИИ GRAVITY REMOTE:")
        for err in errors:
            print(f" - {err}")
        print("Пожалуйста, заполните файл .env перед запуском бота.")
        print("=" * 60 + "\n")
        sys.exit(1)

    builder = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
    )
    if config.PROXY_URL:
        builder = builder.proxy(config.PROXY_URL).get_updates_proxy(config.PROXY_URL)
        logger.info(f"Используется прокси для Telegram: {config.PROXY_URL}")
    app = builder.build()

    # ConversationHandler для всех текстовых/файловых запросов
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(prompt_add_app_name, pattern="^app:add$"),
            CallbackQueryHandler(prompt_folder_list, pattern="^file:list$"),
            CallbackQueryHandler(prompt_file_download, pattern="^file:download$"),
            CallbackQueryHandler(prompt_file_upload, pattern="^file:upload$"),
            CallbackQueryHandler(prompt_folder_size, pattern="^file:size$"),
            CallbackQueryHandler(prompt_kill_proc, pattern="^pc:kill_proc$"),
            CallbackQueryHandler(prompt_clipboard_write, pattern="^extra:clip_write$"),
        ],
        states={
            STATE_APP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_app_name)],
            STATE_APP_PATH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_app_path)],
            STATE_FOLDER_LIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_folder_list)],
            STATE_FILE_DOWNLOAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_file_download)],
            STATE_FILE_UPLOAD_FILE: [MessageHandler((filters.Document.ALL | filters.PHOTO) & ~filters.COMMAND, handle_received_upload_file)],
            STATE_FILE_UPLOAD_DEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_upload_dest_folder)],
            STATE_FOLDER_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_folder_size)],
            STATE_KILL_PROC: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_kill_proc_name)],
            STATE_CLIPBOARD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_clipboard_write)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CommandHandler("start", cancel_conversation),
            CommandHandler("menu", cancel_conversation),
            CallbackQueryHandler(cancel_conversation, pattern="^input:cancel$"),
        ],
        per_chat=True,
        per_user=True,
    )

    app.add_handler(conv_handler)

    # Стандартные команды
    app.add_handler(CommandHandler(["start", "menu"], start_command))
    app.add_handler(CommandHandler(["apps", "running"], running_apps_command))
    app.add_handler(CommandHandler("lock", lock_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("cancel", cancel_conversation))

    # Общий диспетчер меню и инлайн-кнопок
    app.add_handler(CallbackQueryHandler(handle_callback_router))

    # Регистрация глобального обработчика ошибок
    app.add_error_handler(error_handler)

    return app


def ensure_single_instance():
    """Завершает старые зависшие копии bot.py, чтобы избежать конфликта 409 Conflict."""
    current_pid = os.getpid()
    try:
        parent_pid = os.getppid()
    except Exception:
        parent_pid = None

    try:
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.pid == current_pid or (parent_pid and p.pid == parent_pid):
                    continue
                p_name = (p.info.get("name") or "").lower()
                if not ("python" in p_name):
                    continue
                cmdline = p.info.get("cmdline") or []
                if len(cmdline) > 1 and any(arg.lower().endswith("bot.py") for arg in cmdline[1:]):
                    logger.warning(f"Завершение предыдущего процесса бота (PID: {p.pid})...")
                    p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass


def main():
    """Точка входа с автоматическим переподключением при обрывах связи и блокировках."""
    ensure_single_instance()
    logger.info("Запуск GravityRemote Telegram Bot...")
    while True:
        loop = None
        try:
            # Создаём свежий event loop для каждой попытки подключения
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            app = build_application()
            logger.info(f"Бот успешно инициализирован. Разрешённый Telegram ID: {config.ALLOWED_USER_ID}")
            app.run_polling(drop_pending_updates=True, close_loop=False)
            break
        except KeyboardInterrupt:
            logger.info("Работа бота остановлена пользователем.")
            break
        except Exception as e:
            err_msg = str(e)
            if "Conflict" in err_msg:
                print("\n" + "!" * 65)
                print("⚠️ КОНФЛИКТ ЭКЗЕМПЛЯРОВ (409 Conflict):")
                print("Был обнаружен другой экземпляр бота с тем же токеном.")
                print("Завершаем старые процессы и повторяем запуск...")
                print("!" * 65 + "\n")
                ensure_single_instance()
            elif any(term in err_msg for term in ["ConnectError", "Timed out", "NetworkError", "All connection attempts failed", "ReadError"]):
                print("\n" + "!" * 65)
                print("⚠️ НЕТ СВЯЗИ С СЕРВЕРАМИ TELEGRAM (api.telegram.org):")
                print(f"Причина: {err_msg}")
                print("\nВнимание: В РФ доступ к Telegram Bot API часто блокируется провайдером.")
                print("1. Убедитесь, что на ПК ВКЛЮЧЕН VPN, ЛИБО")
                print("2. Укажите локальный прокси в файле .env (PROXY_URL=...)")
                print("\nПовторная попытка подключения через 5 секунд (не закрывайте окно)...")
                print("!" * 65 + "\n")
            else:
                logger.error(f"Ошибка при работе бота: {e}", exc_info=True)
            time.sleep(5)
        finally:
            if loop and not loop.is_closed():
                try:
                    loop.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()