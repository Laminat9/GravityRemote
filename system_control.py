import ctypes
from ctypes import wintypes
import os
import socket
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple, Optional, List, Dict

import psutil
import mss
import requests
import pyperclip
import config

try:
    import comtypes
    from pycaw.pycaw import AudioUtilities
    HAS_PYCAW = True
except Exception:
    HAS_PYCAW = False


# ==========================================
# 📊 МОНИТОРИНГ СИСТЕМЫ
# ==========================================

def get_uptime_str() -> str:
    """Возвращает строку с текущим аптаймом ПК."""
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days > 0:
        parts.append(f"{days} дн.")
    if hours > 0 or days > 0:
        parts.append(f"{hours} ч.")
    parts.append(f"{minutes} мин.")
    return " ".join(parts)


def get_system_status() -> str:
    """Формирует подробный отчет о нагрузке CPU, памяти, дисках и времени работы."""
    # CPU
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_cores_phys = psutil.cpu_count(logical=False) or 0
    cpu_cores_log = psutil.cpu_count(logical=True) or 0

    # RAM
    ram = psutil.virtual_memory()
    ram_total_gb = ram.total / (1024 ** 3)
    ram_used_gb = ram.used / (1024 ** 3)
    ram_percent = ram.percent

    # Диски
    disk_lines = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or part.fstype == "":
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            total_gb = usage.total / (1024 ** 3)
            used_gb = usage.used / (1024 ** 3)
            disk_lines.append(
                f"   💾 <b>{part.device}</b> {used_gb:.1f}/{total_gb:.1f} ГБ ({usage.percent}%)"
            )
        except (PermissionError, OSError):
            continue

    disks_text = "\n".join(disk_lines) if disk_lines else "   💾 Информация о дисках недоступна"

    uptime_text = get_uptime_str()

    report = (
        "📊 <b>СТАТУС СИСТЕМЫ</b>\n\n"
        f"⚡ <b>CPU:</b> {cpu_percent}% ({cpu_cores_phys} физ. / {cpu_cores_log} лог. ядер)\n"
        f"🧠 <b>RAM:</b> {ram_used_gb:.1f} / {ram_total_gb:.1f} ГБ ({ram_percent}%)\n"
        f"💽 <b>Диски:</b>\n{disks_text}\n"
        f"⏱️ <b>Время работы (Uptime):</b> {uptime_text}"
    )
    return report


def get_top_processes(limit: int = 10) -> str:
    """Возвращает список топ процессов по потреблению CPU и RAM."""
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
        try:
            info = p.info
            name = info.get('name') or 'Unknown'
            # Пропускаем процесс бездействия системы
            if name.lower() in ('system idle process', 'idle'):
                continue
            mem_mb = (info.get('memory_info').rss / (1024 * 1024)) if info.get('memory_info') else 0
            procs.append({
                'pid': info['pid'],
                'name': name,
                'cpu': info.get('cpu_percent') or 0.0,
                'mem_mb': mem_mb
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if not procs:
        return "Не удалось получить список процессов."

    # Сортируем по суммарному интересу (потребление памяти)
    top_by_mem = sorted(procs, key=lambda x: x['mem_mb'], reverse=True)[:limit]

    lines = ["📑 <b>ТОП-10 ПРОЦЕССОВ ПО ПАМЯТИ И CPU:</b>\n"]
    for i, proc in enumerate(top_by_mem, 1):
        lines.append(
            f"<b>{i}.</b> <code>{proc['name']}</code> (PID: {proc['pid']})\n"
            f"   RAM: <b>{proc['mem_mb']:.1f} МБ</b> | CPU: <b>{proc['cpu']:.1f}%</b>"
        )

    return "\n".join(lines)


def take_screenshot() -> Path:
    """Делает скриншот экрана монитора и сохраняет во временную папку."""
    config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = config.TEMP_DIR / f"screenshot_{int(time.time())}.png"

    with mss.MSS() as sct:
        # Монитор 0 объединяет все дисплеи, монитор 1 - главный дисплей
        monitor = sct.monitors[0] if len(sct.monitors) > 0 else sct.monitors[1]
        sct.shot(mon=0 if len(sct.monitors) > 1 else 1, output=str(screenshot_path))

    return screenshot_path


def get_network_info() -> str:
    """Возвращает список локальных сетевых интерфейсов и внешний публичный IP."""
    # Локальные адреса
    local_addrs = []
    interfaces = psutil.net_if_addrs()
    for iface_name, addrs in interfaces.items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                local_addrs.append(f"• <b>{iface_name}</b>: <code>{addr.address}</code>")

    local_text = "\n".join(local_addrs) if local_addrs else "• Локальные IP не определены"

    # Внешний IP
    external_ip = "Не удалось определить (ошибка запроса)"
    for service_url in ["https://api.ipify.org", "https://icanhazip.com", "https://ifconfig.me/ip"]:
        try:
            resp = requests.get(service_url, timeout=3.5)
            if resp.status_code == 200:
                external_ip = resp.text.strip()
                break
        except Exception:
            continue

    hostname = socket.gethostname()

    return (
        "🌐 <b>СЕТЕВЫЕ ПОДКЛЮЧЕНИЯ И IP</b>\n\n"
        f"💻 <b>Имя ПК:</b> <code>{hostname}</code>\n"
        f"🌍 <b>Внешний публичный IP:</b> <code>{external_ip}</code>\n\n"
        f"🔌 <b>Локальные сетевые адреса:</b>\n{local_text}"
    )


def get_battery_info() -> str:
    """Возвращает информацию о заряде аккумулятора."""
    battery = psutil.sensors_battery()
    if battery is None:
        return "🖥️ <b>Стационарный ПК</b>: Аккумулятор не обнаружен (питание напрямую от электросети)."

    percent = battery.percent
    is_plugged = battery.power_plugged
    secs_left = battery.secsleft

    state = "🔌 Подключен к сети (заряжается)" if is_plugged else "🔋 Работает от батареи"
    time_str = ""
    if secs_left > 0 and secs_left != psutil.POWER_TIME_UNLIMITED:
        m, _ = divmod(secs_left, 60)
        h, m = divmod(m, 60)
        time_str = f"\n⏳ Оставшееся время: ~{h} ч. {m} мин."
    elif is_plugged and percent == 100:
        time_str = "\n✅ Батарея полностью заряжена"

    return (
        "🔋 <b>ИНФОРМАЦИЯ О БАТАРЕЕ</b>\n\n"
        f"Уровень заряда: <b>{percent}%</b>\n"
        f"Статус: <b>{state}</b>{time_str}"
    )


# ==========================================
# 🔊 УПРАВЛЕНИЕ ЗВУКОМ
# ==========================================

def _get_endpoint_volume():
    """Безопасный доступ к аудио-микшеру Windows через pycaw."""
    if not HAS_PYCAW:
        raise RuntimeError("Модуль pycaw не установлен или недоступен.")
    comtypes.CoInitialize()
    speakers = AudioUtilities.GetSpeakers()
    return speakers.EndpointVolume


def get_volume_info() -> dict:
    """Возвращает текущий уровень громкости и статус Mute."""
    try:
        endpoint = _get_endpoint_volume()
        current_scalar = endpoint.GetMasterVolumeLevelScalar()
        is_muted = bool(endpoint.GetMute())
        return {
            "percent": int(round(current_scalar * 100)),
            "is_muted": is_muted,
            "success": True
        }
    except Exception as e:
        return {"success": False, "error": str(e), "percent": 0, "is_muted": False}


def change_volume(delta_percent: int) -> dict:
    """Изменяет громкость на delta_percent (+10 / -10)."""
    try:
        endpoint = _get_endpoint_volume()
        current = endpoint.GetMasterVolumeLevelScalar()
        new_val = max(0.0, min(1.0, current + (delta_percent / 100.0)))
        endpoint.SetMasterVolumeLevelScalar(new_val, None)
        return {
            "percent": int(round(new_val * 100)),
            "is_muted": bool(endpoint.GetMute()),
            "success": True
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def toggle_mute() -> dict:
    """Включает или выключает звук (Toggle Mute)."""
    try:
        endpoint = _get_endpoint_volume()
        current_mute = bool(endpoint.GetMute())
        new_mute = not current_mute
        endpoint.SetMute(new_mute, None)
        current_scalar = endpoint.GetMasterVolumeLevelScalar()
        return {
            "percent": int(round(current_scalar * 100)),
            "is_muted": new_mute,
            "success": True
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==========================================
# ⚙️ ДЕЙСТВИЯ С ПК
# ==========================================

def ensure_desktop():
    """Обеспечивает доступ к интерактивному рабочему столу Windows."""
    try:
        user32 = ctypes.windll.user32
        desk = user32.OpenInputDesktop(0, False, 0x01FF)
        if not desk:
            desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if desk:
            user32.SetThreadDesktop(desk)
    except Exception:
        pass


def lock_workstation() -> Tuple[bool, str]:
    """Блокирует экран Windows (Win + L)."""
    try:
        res = ctypes.windll.user32.LockWorkStation()
        if res != 0:
            return True, "🔒 Экран ПК успешно заблокирован."
        return False, "Не удалось заблокировать экран (вызов LockWorkStation вернул 0)."
    except Exception as e:
        return False, f"Ошибка при блокировке экрана: {e}"



def minimize_all_windows() -> Tuple[bool, str]:
    """Сворачивает все открытые окна на рабочем столе Windows."""
    try:
        if HAS_PYCAW:
            import comtypes.client
            comtypes.CoInitialize()
            shell = comtypes.client.CreateObject("Shell.Application")
            shell.MinimizeAll()
            return True, "🗔 Все окна успешно свёрнуты."
    except Exception:
        pass

    try:
        # Резервный способ: эмуляция Win + D через user32
        user32 = ctypes.windll.user32
        user32.keybd_event(0x5B, 0, 0, 0)  # Win down
        user32.keybd_event(0x44, 0, 0, 0)  # D down
        user32.keybd_event(0x44, 0, 2, 0)  # D up
        user32.keybd_event(0x5B, 0, 2, 0)  # Win up
        return True, "🗔 Все окна успешно свёрнуты (Win + D)."
    except Exception as e:
        return False, f"Ошибка при сворачивании окон: {e}"


def suspend_pc() -> Tuple[bool, str]:
    """Переводит ПК в спящий режим."""
    try:
        # Вызов через powrprof.dll SetSuspendState(0, 1, 0)
        # 0: Standby (Sleep), 1: Hibernate = False, 0: Disable wake = False
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=False)
        return True, "🌙 ПК переведён в спящий режим."
    except Exception as e:
        return False, f"Ошибка при переводе в спящий режим: {e}"


def restart_pc() -> Tuple[bool, str]:
    """Перезагружает ПК с таймером в 5 секунд."""
    try:
        subprocess.run(["shutdown", "/r", "/t", "5"], check=True)
        return True, "🔄 Перезагрузка ПК инициирована (через 5 секунд)."
    except Exception as e:
        return False, f"Ошибка перезагрузки: {e}"


def shutdown_pc() -> Tuple[bool, str]:
    """Завершает работу ПК с таймером в 5 секунд."""
    try:
        subprocess.run(["shutdown", "/s", "/t", "5"], check=True)
        return True, "⏻ Завершение работы ПК инициировано (через 5 секунд)."
    except Exception as e:
        return False, f"Ошибка завершения работы: {e}"


def kill_process_by_name(process_name: str) -> Tuple[bool, str]:
    """Находит и принудительно завершает процесс по имени."""
    clean_name = process_name.strip()
    if not clean_name:
        return False, "Имя процесса не указано."

    target_name = clean_name.lower()
    if not target_name.endswith(".exe"):
        target_name_with_exe = f"{target_name}.exe"
    else:
        target_name_with_exe = target_name

    killed_count = 0
    errors = []

    for p in psutil.process_iter(['pid', 'name']):
        try:
            p_name = (p.info.get('name') or '').lower()
            if p_name == target_name or p_name == target_name_with_exe:
                p.terminate()
                killed_count += 1
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            errors.append(f"PID {p.pid}: Отказано в доступе (требуются права администратора)")
        except Exception as e:
            errors.append(f"PID {p.pid}: {e}")

    if killed_count > 0:
        msg = f"🛑 Успешно завершено копий процесса <b>{clean_name}</b>: {killed_count}."
        if errors:
            msg += f"\nПредупреждения: {'; '.join(errors)}"
        return True, msg
    elif errors:
        return False, f"Не удалось завершить процесс: {'; '.join(errors)}"
    else:
        return False, f"Процесс <code>{clean_name}</code> не найден среди запущенных."


# ==========================================
# 📋 БУФЕР ОБМЕНА
# ==========================================

def get_clipboard_text() -> str:
    """Возвращает текущее текстовое содержимое буфера обмена."""
    try:
        text = pyperclip.paste()
        if not text:
            return "Буфер обмена пуст или содержит нетекстовые данные (изображение, файл)."
        # Ограничиваем длину превью, если буфер гигантский
        if len(text) > 3500:
            preview = text[:3500] + f"\n\n<i>[...содержимое усечено, всего {len(text)} символов]</i>"
            return preview
        return text
    except Exception as e:
        return f"Ошибка при чтении буфера обмена: {e}"


def set_clipboard_text(text: str) -> Tuple[bool, str]:
    """Копирует присланный текст в буфер обмена Windows."""
    try:
        pyperclip.copy(text)
        return True, "✅ Текст успешно скопирован в буфер обмена ПК."
    except Exception as e:
        return False, f"Ошибка при записи в буфер обмена: {e}"


# ==========================================
# 🪟 ЗАПУЩЕННЫЕ ПРИЛОЖЕНИЯ И ОКНА
# ==========================================


def get_app_icon(proc_name: str) -> str:
    """Подбирает подходящий эмодзи-значок под тип приложения."""
    name = (proc_name or "").lower()
    if any(b in name for b in ("chrome", "duckduckgo", "firefox", "brave", "edge", "opera", "browser", "yandex")):
        return "🌐"
    elif "telegram" in name:
        return "💬"
    elif "discord" in name:
        return "🎧"
    elif any(d in name for d in ("code", "antigravity", "pycharm", "idea", "sublime", "studio", "visual")):
        return "💻"
    elif "explorer" in name:
        return "📁"
    elif any(m in name for m in ("music", "spotify", "aimp", "vlc", "player", "foobar")):
        return "🎵"
    elif any(g in name for g in ("steam", "epic", "game")):
        return "🎮"
    elif any(t in name for t in ("cmd", "powershell", "terminal", "bash", "mintty")):
        return "⌨️"
    elif "taskmgr" in name:
        return "📑"
    elif any(n in name for n in ("notepad", "word", "excel", "writer")):
        return "📝"
    return "📱"


def get_running_applications() -> List[Dict]:
    """
    Возвращает список активных оконных приложений пользователя.
    Исключает фоновые службы, невидимые и системные служебные окна.
    """
    ensure_desktop()
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    apps = []

    def enum_cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True

        # Проверка на свернутые UWP/фоновые окна (cloaked)
        DWMWA_CLOAKED = 14
        cloaked = ctypes.c_int(0)
        try:
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value != 0:
                return True
        except Exception:
            pass

        # Исключаем служебные tool windows
        ex_style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        if ex_style & 0x00000080:  # WS_EX_TOOLWINDOW
            return True

        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if (rect.right - rect.left) <= 0 or (rect.bottom - rect.top) <= 0:
            return True

        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()

        # Игнорируемые системные заголовки
        ignored = {
            "Program Manager",
            "Интерфейс ввода Windows",
            "Windows Input Experience",
            "NVIDIA GeForce Overlay",
            "Settings",
            "Параметры",
        }
        if not title or title in ignored or title.startswith("MSCTFIME UI"):
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        proc_name = "Приложение"
        mem_mb = 0.0
        try:
            proc = psutil.Process(pid.value)
            proc_name = proc.name()
            mem_mb = proc.memory_info().rss / (1024 * 1024)
        except Exception:
            pass

        is_minimized = bool(user32.IsIconic(hwnd))

        apps.append({
            "hwnd": int(hwnd),
            "title": title,
            "pid": int(pid.value),
            "proc_name": proc_name,
            "mem_mb": mem_mb,
            "is_minimized": is_minimized,
            "icon": get_app_icon(proc_name),
        })
        return True

    cb = WNDENUMPROC(enum_cb)
    desk = user32.OpenDesktopW("Default", 0, False, 0x0100)  # DESKTOP_ENUMERATE
    if desk:
        try:
            user32.EnumDesktopWindows(desk, cb, 0)
        finally:
            user32.CloseDesktop(desk)

    if not apps:
        user32.EnumWindows(cb, 0)

    return apps


def get_window_detail(hwnd: int) -> Optional[Dict]:
    """Возвращает детальную информацию по конкретному окну (по HWND)."""
    ensure_desktop()
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return None

    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value.strip() or "Без названия"

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    proc_name = "Неизвестно"
    mem_mb = 0.0
    cpu_percent = 0.0
    exe_path = ""
    try:
        proc = psutil.Process(pid.value)
        proc_name = proc.name()
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        cpu_percent = proc.cpu_percent(interval=0.05)
        exe_path = proc.exe()
    except Exception:
        pass

    is_minimized = bool(user32.IsIconic(hwnd))
    icon = get_app_icon(proc_name)

    return {
        "hwnd": hwnd,
        "title": title,
        "pid": pid.value,
        "proc_name": proc_name,
        "exe_path": exe_path,
        "mem_mb": mem_mb,
        "cpu": cpu_percent,
        "is_minimized": is_minimized,
        "icon": icon,
    }


def focus_window(hwnd: int) -> Tuple[bool, str]:
    """Разворачивает окно и выводит его на передний план на мониторе."""
    ensure_desktop()
    user32 = ctypes.windll.user32
    try:
        if not user32.IsWindow(hwnd):
            return False, "Окно больше не существует или было закрыто."

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW

        current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        fg_hwnd = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
        if fg_tid != current_tid:
            user32.AttachThreadInput(current_tid, fg_tid, True)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.AttachThreadInput(current_tid, fg_tid, False)
        else:
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)

        return True, "🔍 Окно успешно выведено на передний план на экране ПК."
    except Exception as e:
        return False, f"Ошибка при активации окна: {e}"


def minimize_window(hwnd: int) -> Tuple[bool, str]:
    """Сворачивает указанное окно."""
    ensure_desktop()
    user32 = ctypes.windll.user32
    try:
        if not user32.IsWindow(hwnd):
            return False, "Окно больше не существует или было закрыто."
        user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        return True, "🗕 Окно успешно свёрнуто."
    except Exception as e:
        return False, f"Ошибка при сворачивании окна: {e}"


def close_window(hwnd: int) -> Tuple[bool, str]:
    """Корректно закрывает окно (отправляет WM_CLOSE)."""
    ensure_desktop()
    user32 = ctypes.windll.user32
    try:
        if not user32.IsWindow(hwnd):
            return False, "Окно уже закрыто или не найдено."
        # WM_CLOSE = 0x0010
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True, "❌ Сигнал на закрытие окна успешно отправлен."
    except Exception as e:
        return False, f"Ошибка при закрытии окна: {e}"


def kill_process_by_pid(pid: int) -> Tuple[bool, str]:
    """Принудительно завершает процесс по PID."""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        proc.terminate()
        return True, f"🛑 Процесс <b>{name}</b> (PID: {pid}) успешно остановлен."
    except psutil.NoSuchProcess:
        return False, f"Процесс с PID {pid} уже не существует."
    except psutil.AccessDenied:
        return False, f"Отказано в доступе к PID {pid} (требуются права администратора)."
    except Exception as e:
        return False, f"Ошибка при завершении процесса: {e}"


def open_task_view() -> Tuple[bool, str]:
    """Вызывает системный экран переключения задач (Win + Tab)."""
    try:
        ensure_desktop()
        user32 = ctypes.windll.user32
        user32.keybd_event(0x5B, 0, 0, 0)  # Win down
        user32.keybd_event(0x09, 0, 0, 0)  # Tab down
        user32.keybd_event(0x09, 0, 2, 0)  # Tab up
        user32.keybd_event(0x5B, 0, 2, 0)  # Win up
        return True, "🗔 Экран переключения задач (Win + Tab) открыт на ПК."
    except Exception as e:
        return False, f"Ошибка вызова Win+Tab: {e}"


def open_task_manager() -> Tuple[bool, str]:
    """Запускает Диспетчер задач Windows."""
    try:
        subprocess.Popen(["taskmgr.exe"])
        return True, "📋 Диспетчер задач успешно запущен на ПК."
    except Exception as e:
        return False, f"Ошибка запуска taskmgr: {e}"
