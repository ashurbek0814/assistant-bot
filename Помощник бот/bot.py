import os
import json
import asyncio
import logging
import re
import random
import html # Добавлен для экранирования HTML
import aiohttp
import aiofiles
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from deep_translator import GoogleTranslator # <--- Добавлено для перевода
from deep_translator.exceptions import LanguageNotSupportedException, TranslationNotFound # <--- Добавлено для обработки ошибок перевода

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEATHER_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
OWNER_TELEGRAM_ID = os.getenv("OWNER_TELEGRAM_ID")
EXCHANGERATE_API_KEY = os.getenv("EXCHANGERATE_API_KEY") # <--- Добавлен ключ API для валют
REMINDERS_FILE = "reminders.json"
USER_LINKS_FILE = "user_links.json" # Файл для хранения ссылок пользователей

# Константы для приложения
MAX_NEWS_ARTICLES_TO_SHOW = 3
TELEGRAM_MESSAGE_MAX_LENGTH = 4096
MESSAGE_CHUNK_SIZE = 4000 # Чуть меньше максимума для безопасности
DATETIME_FORMAT = "%d.%m.%Y %H:%M"
MIN_REMINDER_DELAY_FALLBACK_SECONDS = 0.1

# Проверка обязательных переменных
if not TELEGRAM_BOT_TOKEN:
    logging.error("Ошибка: TELEGRAM_BOT_TOKEN не установлен")
    exit()
if not WEATHER_API_KEY:
    logging.warning("Предупреждение: OPENWEATHERMAP_API_KEY не установлен. Функция погоды будет недоступна.")
if not NEWS_API_KEY:
    logging.warning("Предупреждение: NEWS_API_KEY не установлен. Функция новостей будет недоступна.")
if not EXCHANGERATE_API_KEY: # <--- Добавлена проверка ключа
    logging.warning("Предупреждение: EXCHANGERATE_API_KEY не установлен. Функция конвертации валют будет недоступна.")

owner_id_str = os.getenv("OWNER_TELEGRAM_ID")
if owner_id_str:
    try:
        OWNER_TELEGRAM_ID = int(owner_id_str)
    except ValueError:
        logging.error("Ошибка: OWNER_TELEGRAM_ID должен быть числом. Функция /ask_creator будет недоступна.")
        OWNER_TELEGRAM_ID = None # Explicitly set to None if conversion fails
else:
    logging.warning("Предупреждение: OWNER_TELEGRAM_ID не установлен. Функция /ask_creator будет недоступна.")
    OWNER_TELEGRAM_ID = None # Explicitly set to None if env var is not set

# Инициализация бота
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные
reminders = {}
user_links = {} # Для хранения ссылок пользователей {chat_id: {name: {"url": url, "description": desc}}}
active_reminder_tasks = {}
REPEAT_DAILY = "daily"
REPEAT_WEEKLY = "weekly"

# Игра
RPS_CHOICES = ["камень", "ножницы", "бумага"]

# --- Вспомогательные функции ---

def load_reminders():
    """Загрузка напоминаний из файла"""
    global reminders
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            reminders = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logging.warning(f"Файл {REMINDERS_FILE} не найден или поврежден. Будет создан новый.")
        reminders = {}

def load_user_links():
    """Загрузка пользовательских ссылок из файла"""
    global user_links
    try:
        with open(USER_LINKS_FILE, "r", encoding="utf-8") as f:
            loaded_data = json.load(f)
            user_links = {str(k): v for k, v in loaded_data.items()} # chat_id как строки
    except (FileNotFoundError, json.JSONDecodeError):
        logging.warning(f"Файл {USER_LINKS_FILE} не найден или поврежден. Будет создан новый.")
        user_links = {}

async def save_reminders():
    """Асинхронное сохранение напоминаний"""
    try:
        async with aiofiles.open(REMINDERS_FILE, "w", encoding="utf-8") as f:
            await f.write(json.dumps(reminders, indent=4, ensure_ascii=False))
    except IOError as e:
        logging.error(f"Ошибка сохранения напоминаний: {e}")

async def save_user_links():
    """Асинхронное сохранение пользовательских ссылок"""
    try:
        async with aiofiles.open(USER_LINKS_FILE, "w", encoding="utf-8") as f:
            await f.write(json.dumps(user_links, indent=4, ensure_ascii=False))
    except IOError as e:
        logging.error(f"Ошибка сохранения пользовательских ссылок: {e}")

async def get_weather(city: str):
    """Получение данных о погоде"""
    if not WEATHER_API_KEY:
        logging.warning("API ключ для погоды не установлен.")
        return None
        
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.error(f"Ошибка API погоды: {response.status} для города {city}. Ответ: {await response.text()}")
                    return None
    except aiohttp.ClientError as e:
        logging.error(f"Ошибка сети при запросе погоды: {e}")
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при запросе погоды: {e}", exc_info=True)
    return None

async def get_news(query: str):
    """Получение новостей"""
    if not NEWS_API_KEY:
        logging.warning("API ключ для новостей не установлен.")
        return None
        
    url = f"https://newsapi.org/v2/everything?q={query}&apiKey={NEWS_API_KEY}&language=ru&sortBy=publishedAt"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.error(f"Ошибка API новостей: {response.status} для запроса '{query}'. Ответ: {await response.text()}")
                    return None
    except aiohttp.ClientError as e:
        logging.error(f"Ошибка сети при запросе новостей: {e}")
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при запросе новостей: {e}", exc_info=True)
    return None

async def translate_text_async(text: str, target_lang: str, source_lang: str = 'auto'):
    """
    Асинхронно переводит текст с использованием GoogleTranslator.
    Работает в отдельном потоке, чтобы не блокировать основной цикл asyncio.
    """
    try:
        # GoogleTranslator синхронный, поэтому запускаем в потоке
        translated_text = await asyncio.to_thread(
            GoogleTranslator(source=source_lang, target=target_lang).translate,
            text
        )
        return translated_text
    except LanguageNotSupportedException:
        logging.warning(f"Язык '{target_lang}' или '{source_lang}' не поддерживается для перевода.")
        return f"Ошибка: Язык '{target_lang}' (или исходный язык) не поддерживается."
    except TranslationNotFound:
        logging.warning(f"Перевод для текста '{text[:50]}...' на язык '{target_lang}' не найден.")
        return f"Ошибка: Не удалось найти перевод для вашего текста на язык '{target_lang}'."
    except Exception as e:
        logging.error(f"Ошибка при переводе текста: {e}", exc_info=True)
        return "Произошла ошибка при попытке перевода."

async def get_conversion_rate(base_currency: str, target_currency: str, amount: float):
    """Получает курс конвертации и конвертированную сумму."""
    if not EXCHANGERATE_API_KEY:
        return "Функция конвертации валют недоступна: API ключ не настроен."

    # Используем эндпоинт /pair для прямого получения результата конвертации
    url = f"https://v6.exchangerate-api.com/v6/{EXCHANGERATE_API_KEY}/pair/{base_currency}/{target_currency}/{amount}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json() # Пытаемся парсить JSON в любом случае для сообщений об ошибках

                if response.status == 200 and data.get("result") == "success":
                    converted_amount = data.get("conversion_result")
                    rate = data.get("conversion_rate")
                    if converted_amount is not None and rate is not None:
                        return (f"💸 {amount:.2f} {base_currency} = <b>{converted_amount:.2f} {target_currency}</b> 💰\n"
                                f"<i>(1 {base_currency} = {rate:.4f} {target_currency})</i>")
                    else: # pragma: no cover
                        logging.error(f"Ответ API конвертации не содержит conversion_result или conversion_rate: {data}")
                        return "Ошибка: неполные данные от сервиса конвертации."
                else:
                    error_type = data.get("error-type", "unknown_error")
                    logging.error(f"Ошибка API конвертации валют ({base_currency}->{target_currency}, amount: {amount}): {error_type}, HTTP Status: {response.status}, Response: {data}")
                    
                    error_messages = {
                        "unsupported-code": f"😕 Ошибка: одна или обе валюты (<b>{base_currency}</b>, <b>{target_currency}</b>) не поддерживаются или неверно указаны.",
                        "malformed-request": "🤔 Ошибка: неверный запрос к API конвертации (возможно, неверный формат валюты или суммы).",
                        "invalid-key": "🔑 Ошибка конфигурации: недействительный API ключ для конвертации валют.",
                        "inactive-account": "😴 Ошибка конфигурации: аккаунт API для конвертации валют неактивен.",
                        "quota-reached": "⏳ Достигнут лимит запросов к API конвертации валют. Попробуйте позже."
                    }
                    return error_messages.get(error_type, f"Ошибка сервиса конвертации валют: {error_type}.")
    except aiohttp.ClientError as e:
        logging.error(f"Сетевая ошибка при запросе курса валют: {e}", exc_info=True)
        return "Сетевая ошибка при получении курса валют."
    except json.JSONDecodeError as e:
        # Логируем текст ответа, если он не смог быть декодирован как JSON
        response_text_for_log = "N/A"
        if 'response' in locals() and hasattr(response, 'text'):
            try:
                response_text_for_log = await response.text()
            except Exception: #  pragma: no cover
                pass # Если даже текст получить не удалось, оставим N/A
        logging.error(f"Ошибка декодирования JSON от API конвертации: {e}. Ответ: {response_text_for_log}", exc_info=True)
        return "🔧 Ошибка: не удалось обработать ответ от сервиса конвертации."
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при конвертации валют: {e}", exc_info=True)
        return "💥 Произошла ошибка при конвертации валют."

async def send_reminder(chat_id: int, text: str, delay: float, reminder_id: int, repeat: str = None):
    """Отправка напоминания"""
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        # Используем html.escape для текста напоминания, так как он от пользователя
        await bot.send_message(chat_id, f"⏰ <b>Напоминание:</b>\n<i>{html.escape(text)}</i>", parse_mode=ParseMode.HTML)
        
        chat_id_str = str(chat_id)
        if repeat and chat_id_str in reminders:
            target_reminder_data = None
            target_reminder_idx = -1
            if reminders.get(chat_id_str): # Проверяем, что список напоминаний для чата существует
                for idx, r_item in enumerate(reminders[chat_id_str]):
                    if r_item.get("id") == reminder_id:
                        target_reminder_data = r_item
                        target_reminder_idx = idx
                        break
            
            if target_reminder_data:
                now = datetime.now()
                try:
                    # Используем datetime из найденного напоминания
                    next_time = datetime.strptime(target_reminder_data["datetime"], DATETIME_FORMAT)
                except (ValueError, KeyError) as e:
                    logging.error(f"Ошибка данных/формата datetime для напоминания {reminder_id} в чате {chat_id_str} ({e}) при перепланировке send_reminder. Удаление.")
                    # Безопасное удаление
                    if target_reminder_idx != -1 and \
                       target_reminder_idx < len(reminders[chat_id_str]) and \
                       reminders[chat_id_str][target_reminder_idx].get("id") == reminder_id:
                        reminders[chat_id_str].pop(target_reminder_idx)
                    else: # Если индекс невалиден или элемент изменился, ищем по ID
                        reminders[chat_id_str] = [r for r in reminders[chat_id_str] if r.get("id") != reminder_id]
                    
                    if not reminders[chat_id_str]:
                        del reminders[chat_id_str]
                    await save_reminders()
                    return # Прекращаем обработку этого напоминания

                original_next_time = next_time 
                rescheduled = False
                while next_time <= now:
                    rescheduled = True
                    if repeat == REPEAT_DAILY:
                        next_time += timedelta(days=1)
                    elif repeat == REPEAT_WEEKLY:
                        next_time += timedelta(weeks=1)
                    else: 
                        logging.warning(f"Неизвестный тип повтора '{repeat}' для напоминания {reminder_id}. Повтор отменен.")
                        reminders[chat_id_str].pop(target_reminder_idx) # Используем сохраненный индекс
                        if not reminders[chat_id_str]:
                            del reminders[chat_id_str]
                        await save_reminders()
                        return
                
                if rescheduled: 
                    logging.info(f"Перепланирование напоминания {reminder_id} с {original_next_time.strftime(DATETIME_FORMAT)} на {next_time.strftime(DATETIME_FORMAT)}")

                new_delay = (next_time - now).total_seconds()
                if new_delay < 0: 
                    logging.warning(f"Рассчитана отрицательная задержка ({new_delay}s) для перепланированного напоминания {reminder_id} в send_reminder. Устанавливается значение {MIN_REMINDER_DELAY_FALLBACK_SECONDS}s.")
                    new_delay = MIN_REMINDER_DELAY_FALLBACK_SECONDS
                
                # Обновляем datetime в хранилище используя target_reminder_idx
                reminders[chat_id_str][target_reminder_idx]["datetime"] = next_time.strftime(DATETIME_FORMAT)
                await save_reminders()
                
                active_reminder_tasks.setdefault(chat_id_str, {})[reminder_id] = asyncio.create_task(
                    send_reminder(chat_id, text, new_delay, reminder_id, repeat)
                )
            else:
                logging.warning(f"Напоминание {reminder_id} для чата {chat_id_str} не найдено для перепланирования (возможно, уже удалено).")

        elif not repeat: 
            if chat_id_str in reminders:
                initial_len = len(reminders[chat_id_str])
                reminders[chat_id_str] = [r for r in reminders[chat_id_str] if r["id"] != reminder_id]
                if len(reminders[chat_id_str]) < initial_len: 
                    logging.info(f"Одноразовое напоминание {reminder_id} для чата {chat_id_str} отправлено и удалено.")
                if not reminders[chat_id_str]:
                    del reminders[chat_id_str]
                await save_reminders()
                
    except asyncio.CancelledError:
        logging.info(f"Напоминание {reminder_id} для чата {chat_id} было отменено.")
    except Exception as e:
        logging.error(f"Ошибка при отправке/перепланировании напоминания {reminder_id} для чата {chat_id}: {e}", exc_info=True)
    finally:
        chat_id_str = str(chat_id)
        if chat_id_str in active_reminder_tasks and reminder_id in active_reminder_tasks[chat_id_str]:
            del active_reminder_tasks[chat_id_str][reminder_id]
            if not active_reminder_tasks[chat_id_str]:
                del active_reminder_tasks[chat_id_str]

# --- Обработчики команд ---
# Для более "стикерного" вида, рассмотрите возможность использования bot.send_sticker(chat_id, YOUR_STICKER_FILE_ID)
# в дополнение или вместо некоторых текстовых ответов, особенно для подтверждений.
# Например, после успешного выполнения команды.

@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer(f"Привет, {message.from_user.first_name}! Я твой личный помощник. 🤖👋\nНапиши /help для списка команд. ✨")
    # Consider sending a welcome sticker: await bot.send_sticker(message.chat.id, YOUR_WELCOME_STICKER_ID)

@dp.message(Command("help"))
async def help_command(message: Message):
    help_text = (
        "💡 <b>Доступные команды:</b>\n\n"
        "/start - Запустить бота\n"
        "/help - 📖 Список команд\n"
        "/remind [ДД.ММ.ГГГГ] [ЧЧ:ММ] текст [repeat daily/weekly] - ⏰ Установить напоминание.\n"
        "  <i>(дата и/или время опциональны)</i>\n"
        "/list - 📋 Список напоминаний\n"
        "/cancel ID - 🗑️ Отменить напоминание\n"
        "/ask_creator текст - 📬 Отправить сообщение создателю бота\n"
        "/weather город - 🌤️ Погода\n"
        "/news запрос - 📰 Новости\n"
        "/translate &lt;код_языка&gt; &lt;текст&gt; - 🌍 Перевести текст (например, /translate en Привет)\n"
        "/convert &lt;сумма&gt; &lt;ВАЛЮТА1&gt; [to] &lt;ВАЛЮТА2&gt; - 💸 Конвертировать валюту (напр., /convert 100 USD UZS)\n\n"
        "🔗 <b>Управление ссылками:</b>\n"
        "/addlink &lt;название&gt; &lt;ссылка&gt; [описание] - ✨ Добавить/обновить ссылку\n"
        "/links - 📑 Показать ваши сохраненные ссылки\n"
        "/dellink &lt;название_или_номер&gt; - ➖ Удалить сохраненную ссылку\n\n"
        "🎮 <b>Игры:</b>\n"
        "/rps [камень|ножницы|бумага] - 🗿✂️📄 Сыграть в Камень-Ножницы-Бумага\n\n"
        "<b>Примеры использования:</b>\n"
        "<code>/remind 15:30 Позвонить маме</code>\n"
        "<code>/remind 01.01.2025 С Новым Годом!</code>\n"
        f"<code>/remind Купить молоко repeat {REPEAT_DAILY}</code>\n"
        f"<code>/remind 10:00 Важная встреча repeat {REPEAT_WEEKLY}</code>\n"
        "<code>/rps камень</code>\n"
        "<code>/translate es Здравствуйте, как ваши дела?</code> (es - испанский)\n"
        "<code>/translate auto:ru Hello world</code> (перевести с автоопределения на русский)\n"
        "<code>/convert 100 USD to EUR</code>\n"
        "<code>/convert 5000 RUB UZS</code>"
    )
    await message.answer(help_text, parse_mode=ParseMode.HTML)

@dp.message(Command("remind"))
async def remind_command(message: Message):
    try:
        command_text = message.text.split(maxsplit=1)
        if len(command_text) < 2 or not command_text[1].strip():
            await message.answer("📝 Пожалуйста, укажите текст напоминания и, опционально, дату/время.\n"
                                 "Формат: <code>/remind [ДД.ММ.ГГГГ] [ЧЧ:ММ] Текст [repeat daily/weekly]</code>",
                                 parse_mode=ParseMode.HTML)
            return

        args_str = command_text[1].strip()
        now = datetime.now()
        reminder_text_candidate = args_str 
        repeat = None

        repeat_match = re.search(r"\srepeat\s+(daily|weekly)$", reminder_text_candidate, re.IGNORECASE)
        if repeat_match:
            repeat_input = repeat_match.group(1).lower()
            if repeat_input in [REPEAT_DAILY, REPEAT_WEEKLY]:
                repeat = repeat_input
            else:
                await message.answer(f"🤔 Неизвестный тип повтора: {repeat_input}. Доступно: {REPEAT_DAILY}, {REPEAT_WEEKLY}.")
                return
            reminder_text_candidate = reminder_text_candidate[:repeat_match.start()].strip()

        date_str, time_str = None, None
        full_dt_match = re.match(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})\s+(.+)", reminder_text_candidate)
        if full_dt_match:
            date_str, time_str, actual_reminder_text = full_dt_match.groups()
        else:
            time_match = re.match(r"(\d{2}:\d{2})\s+(.+)", reminder_text_candidate)
            if time_match:
                time_str, actual_reminder_text = time_match.groups()
            else:
                date_match = re.match(r"(\d{2}\.\d{2}\.\d{4})\s+(.+)", reminder_text_candidate)
                if date_match:
                    date_str, actual_reminder_text = date_match.groups()
                else:
                    actual_reminder_text = reminder_text_candidate

        actual_reminder_text = actual_reminder_text.strip()
        if not actual_reminder_text:
            await message.answer("❌ Текст напоминания не может быть пустым.")
            return

        target_date = now.date() 
        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%d.%m.%Y").date()
            except ValueError:
                await message.answer("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ")
                return

        target_time_obj = None
        if time_str:
            try:
                target_time_obj = datetime.strptime(time_str, "%H:%M").time()
            except ValueError:
                await message.answer("❌ Неверный формат времени. Используйте ЧЧ:ММ")
                return
        elif not date_str and not time_str: 
            target_time_obj = (now + timedelta(minutes=1)).time().replace(second=0, microsecond=0)
        elif date_str and not time_str: 
            target_time_obj = datetime.min.time()
        # target_time_obj должен быть установлен одним из предыдущих условий, если парсинг прошел успешно

        remind_datetime = datetime.combine(target_date, target_time_obj)

        logging.info(f"DEBUG: date_str='{date_str}', time_str='{time_str}', remind_datetime='{remind_datetime.strftime(DATETIME_FORMAT)}', now='{now.strftime(DATETIME_FORMAT)}', condition_check: remind_datetime < now is {remind_datetime < now}")
        if not date_str and time_str and remind_datetime < now:
            logging.info(f"DEBUG: Time is in the past for today. Scheduling for tomorrow. remind_datetime was {remind_datetime.strftime(DATETIME_FORMAT)}, now is {now.strftime(DATETIME_FORMAT)}")
            remind_datetime += timedelta(days=1)
        
        if date_str and not time_str and remind_datetime < now and not repeat: 
             await message.answer(f"❌ Нельзя установить напоминание на прошедшее время (00:00 указанной даты): {remind_datetime.strftime(DATETIME_FORMAT)}")
             return

        if remind_datetime < now and not repeat:
            await message.answer(f"❌ Нельзя установить напоминание на прошедшее время: {remind_datetime.strftime(DATETIME_FORMAT)}")
            return
        
        if repeat and remind_datetime < now:
            temp_dt = remind_datetime
            logging.info(f"Исходное время для повторяющегося напоминания {temp_dt.strftime(DATETIME_FORMAT)} в прошлом. Вычисляем следующее.")
            while temp_dt <= now:
                if repeat == REPEAT_DAILY: temp_dt += timedelta(days=1)
                elif repeat == REPEAT_WEEKLY: temp_dt += timedelta(weeks=1)
            remind_datetime = temp_dt
            logging.info(f"Следующее время для повторяющегося напоминания установлено на {remind_datetime.strftime(DATETIME_FORMAT)}")

        chat_id_str = str(message.chat.id)
        reminders.setdefault(chat_id_str, []) 
            
        reminder_id = max((r["id"] for r in reminders[chat_id_str]), default=0) + 1
        final_datetime_str = remind_datetime.strftime(DATETIME_FORMAT)
        
        reminders[chat_id_str].append({
            "id": reminder_id,
            "datetime": final_datetime_str,
            "text": actual_reminder_text, 
            "repeat": repeat
        })
        await save_reminders()
        
        delay = (remind_datetime - now).total_seconds()
        if delay < 0: 
            logging.warning(f"Calculated negative delay ({delay}s) for reminder {reminder_id}. Setting to {MIN_REMINDER_DELAY_FALLBACK_SECONDS}s.")
            delay = MIN_REMINDER_DELAY_FALLBACK_SECONDS
            
        active_reminder_tasks.setdefault(chat_id_str, {}) 
            
        active_reminder_tasks[chat_id_str][reminder_id] = asyncio.create_task(
            send_reminder(message.chat.id, actual_reminder_text, delay, reminder_id, repeat))
            
        repeat_msg_part = f" (повтор: {repeat})" if repeat else ""
        # Используем html.escape для actual_reminder_text
        await message.answer( # type: ignore
            f"✅ Напоминание <b>#{reminder_id}</b> установлено на <b>{final_datetime_str}</b>: <i>{html.escape(actual_reminder_text)}</i>{repeat_msg_part} 🗓️",
            parse_mode=ParseMode.HTML
        )
        # Consider sending a confirmation sticker: await bot.send_sticker(message.chat.id, YOUR_REMINDER_SET_STICKER_ID)
    except Exception as e:
        logging.error(f"Ошибка в remind_command: {e}", exc_info=True)
        await message.answer("❌ Ой, ошибка при создании напоминания. Проверьте формат или загляните в логи, если вы мой создатель! 👨‍💻")

@dp.message(Command("list"))
async def list_command(message: Message):
    chat_id_str = str(message.chat.id)
    if chat_id_str in reminders and reminders[chat_id_str]:
        response_parts = ["📋 <b>Ваши напоминания:</b>\n"]
        try:
            sorted_reminders_list = sorted(
                reminders[chat_id_str], 
                key=lambda r: datetime.strptime(r['datetime'], DATETIME_FORMAT)
            )
        except ValueError as e:
            logging.error(f"Malformed datetime in reminders for chat {chat_id_str}: {e}. Displaying unsorted or partial list.")
            await message.answer("🔧 Ошибка в формате сохраненных напоминаний. Не могу отобразить список. 😕")
            return

        for reminder in sorted_reminders_list:
            repeat_info = f" (повтор: {reminder.get('repeat')})" if reminder.get('repeat') else ""
            reminder_id_str = str(reminder.get('id', 'N/A'))
            reminder_datetime_str = str(reminder.get('datetime', 'N/A'))
            reminder_text_str = str(reminder.get('text', ''))
            response_parts.append(f"#{reminder_id_str} - {reminder_datetime_str}: {html.escape(reminder_text_str)}{repeat_info}\n")
        
        full_response = "".join(response_parts)
        if len(full_response) > TELEGRAM_MESSAGE_MAX_LENGTH:
            await message.answer("📜 Слишком много напоминаний для одного сообщения. Показываю частями:", parse_mode=ParseMode.HTML)
            for i in range(0, len(full_response), MESSAGE_CHUNK_SIZE):
                await message.answer(full_response[i:i+MESSAGE_CHUNK_SIZE], parse_mode=ParseMode.HTML)
        else:
            await message.answer(full_response, parse_mode=ParseMode.HTML) 
    else:
        await message.answer("У вас нет активных напоминаний 🤷‍♀️\nМожет, создадим одно с помощью /remind? ✨")

@dp.message(Command("cancel"))
async def cancel_command(message: Message):
    try:
        args = message.text.split() 
        if len(args) < 2 or not args[1].isdigit():
            await message.answer("🔢 Формат: /cancel ID_напоминания (ID должен быть числом)")
            return
            
        reminder_id_to_cancel = int(args[1])
        chat_id_str = str(message.chat.id)
        
        reminder_found_in_storage = False 
        if chat_id_str in reminders and reminders[chat_id_str]:
            initial_len = len(reminders[chat_id_str])
            reminders[chat_id_str] = [r for r in reminders[chat_id_str] if r.get("id") != reminder_id_to_cancel]
            if len(reminders[chat_id_str]) < initial_len:
                reminder_found_in_storage = True
            if not reminders.get(chat_id_str): 
                del reminders[chat_id_str]
            if reminder_found_in_storage: 
                 await save_reminders()
            
        task_cancelled = False
        if chat_id_str in active_reminder_tasks and reminder_id_to_cancel in active_reminder_tasks[chat_id_str]:
            try:
                active_reminder_tasks[chat_id_str][reminder_id_to_cancel].cancel()
                task_cancelled = True
            except asyncio.CancelledError:
                 task_cancelled = True 
            except Exception as e_cancel:
                 logging.error(f"Error trying to cancel task {reminder_id_to_cancel} for chat {chat_id_str}: {e_cancel}")
        
        if reminder_found_in_storage or task_cancelled:
            await message.answer(f"🗑️ Напоминание #{reminder_id_to_cancel} отменено.")
            # Consider sending a confirmation sticker: await bot.send_sticker(message.chat.id, YOUR_CANCEL_STICKER_ID)
        else:
            await message.answer(f"🤷 Напоминание #{reminder_id_to_cancel} не найдено.")
            
    except ValueError: 
        await message.answer("🔢 ID напоминания должен быть числом.")
    except Exception as e:
        logging.error(f"Ошибка отмены напоминания: {e}", exc_info=True)
        await message.answer("❌ Ой, ошибка при отмене напоминания.")

@dp.message(Command("weather"))
async def weather_command(message: Message):
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2 or not args[1].strip():
            await message.answer("🏙️ Укажите город после команды /weather")
            return
        city = args[1].strip()
        weather_data = await get_weather(city)
        
        if weather_data and "main" in weather_data and "weather" in weather_data and weather_data["weather"]:
            temp = weather_data["main"].get("temp", "N/A")
            feels_like = weather_data["main"].get("feels_like", "N/A")
            desc = weather_data["weather"][0].get("description", "N/A")
            humidity = weather_data["main"].get("humidity", "N/A")
            wind_speed = weather_data.get("wind", {}).get("speed", "N/A") 
            
            response_text = (f"🌤️ <b>Погода в городе {html.escape(city)}:</b>\n"
                             f"🌡️ Температура: <b>{temp}°C</b> (ощущается как {feels_like}°C)\n"
                             f"📝 Описание: <i>{html.escape(desc.capitalize())}</i>\n"
                             f"💧 Влажность: {humidity}%\n"
                             f"💨 Ветер: {wind_speed} м/с")
            await message.answer(response_text, parse_mode=ParseMode.HTML)
        else:
            await message.answer(f"😥 Не удалось получить погоду для {html.escape(city)} или данные неполные.")
            
    except Exception as e:
        logging.error(f"Ошибка в команде /weather: {e}", exc_info=True)
        await message.answer("❌ Ой, ошибка при запросе погоды.")

@dp.message(Command("news"))
async def news_command(message: Message):
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2 or not args[1].strip():
            await message.answer("🔍 Укажите запрос для новостей после команды /news")
            return
        query = args[1].strip()
        news_data = await get_news(query)
        
        if news_data and news_data.get("articles"):
            articles = news_data["articles"][:MAX_NEWS_ARTICLES_TO_SHOW]
            if not articles:
                await message.answer(f"🤷 Новости по запросу '{html.escape(query)}' не найдены.")
                return

            response_parts = [f"<b>Новости по запросу '{html.escape(query)}':</b>\n"]
            for article in articles:
                title = html.escape(article.get('title', 'Без заголовка'))
                url = article.get('url', '') 
                source_name = html.escape(article.get('source', {}).get('name', 'Неизвестный источник'))
                response_parts.append(f"\n📰 <b>{title}</b>\n<i>Источник: {source_name}</i>\n<a href=\"{url}\">{url}</a>\n") 
            
            await message.answer("".join(response_parts), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        elif news_data and news_data.get("status") == "error":
            logging.error(f"News API error for query '{query}': {news_data.get('message')}")
            await message.answer(f"🔧 Ошибка при получении новостей: {html.escape(news_data.get('message', 'Неизвестная ошибка API'))}")
        else:
            await message.answer(f"🤷 Новости по запросу '{html.escape(query)}' не найдены или произошла ошибка API.")
            
    except Exception as e:
        logging.error(f"Ошибка в команде /news: {e}", exc_info=True)
        await message.answer("❌ Ой, ошибка при запросе новостей.")

@dp.message(Command("ask_creator"))
async def ask_creator_command(message: Message):
    if not OWNER_TELEGRAM_ID: 
        await message.answer("😔 К сожалению, функция связи с моим создателем сейчас недоступна.")
        return

    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2 or not command_parts[1].strip():
        await message.answer("Пожалуйста, напишите ваше сообщение создателю после команды.\n"
                             "Пример: <code>/ask_creator Привет! У меня есть вопрос.</code>", parse_mode=ParseMode.HTML)
        return

    user_message_to_creator = command_parts[1].strip()
    safe_user_message = html.escape(user_message_to_creator)

    sender_info = f"Пользователь: {html.escape(message.from_user.full_name)}"
    if message.from_user.username:
        sender_info += f" (@{html.escape(message.from_user.username)})"
    sender_info += f"\nID: {message.from_user.id}" 
    chat_info = f"Чат ID: {message.chat.id} (Тип: {html.escape(str(message.chat.type))})"

    message_for_owner = (
        f"🔔 Новое обращение к вам от бота!\n\n"
        f"<b>От:</b> {sender_info}\n" 
        f"<b>В чате:</b> {chat_info}\n\n" 
        f"<b>Сообщение:</b>\n{safe_user_message}"
    )

    try:
        await bot.send_message(OWNER_TELEGRAM_ID, message_for_owner, parse_mode=ParseMode.HTML) 
        await message.answer("✅ Ваше сообщение было отправлено моему создателю! 📬")
        # Consider sending a confirmation sticker: await bot.send_sticker(message.chat.id, YOUR_MESSAGE_SENT_STICKER_ID)
        logging.info(f"Сообщение для создателя от {message.from_user.id} было отправлено.")
    except Exception as e: 
        logging.error(f"Не удалось отправить сообщение создателю (ID: {OWNER_TELEGRAM_ID}): {e}", exc_info=True)
        await message.answer("❌ Ой, произошла ошибка при отправке вашего сообщения. Пожалуйста, попробуйте позже. 🛠️")

# --- Команды для управления ссылками ---
@dp.message(Command("addlink"))
async def add_link_command(message: Message):
    command_text = message.text.split(maxsplit=1)
    if len(command_text) < 2 or not command_text[1].strip():
        await message.answer("📝 Формат: /addlink <название> <ссылка> [описание]\n"
                             "Пример: <code>/addlink Google https://google.com Поисковик</code>", parse_mode=ParseMode.HTML)
        return
    
    args_str = command_text[1].strip()
    parts = args_str.split(maxsplit=2) 
    if len(parts) < 2: 
        await message.answer("Формат: /addlink <название> <ссылка> [описание]\n"
                             "❗️ Необходимо указать как минимум название и ссылку.", parse_mode=ParseMode.HTML)
        return

    link_name = parts[0].strip()
    link_url = parts[1].strip()
    link_description = parts[2].strip() if len(parts) > 2 else ""

    if not link_name:
        await message.answer("🚫 Название ссылки не может быть пустым.")
        return

    if not (link_url.startswith("http://") or link_url.startswith("https://")):
        await message.answer("🔗 Неверный формат ссылки. Ссылка должна начинаться с http:// или https://")
        return

    chat_id_str = str(message.chat.id)
    user_links.setdefault(chat_id_str, {}) 

    action_text = "добавлена"
    if link_name in user_links[chat_id_str]:
        action_text = "обновлена"
        await message.answer(f"ℹ️ Ссылка с названием '{html.escape(link_name)}' уже существует. Она будет перезаписана.")

    user_links[chat_id_str][link_name] = {"url": link_url, "description": link_description}
    await save_user_links()
    await message.answer(f"✅ Ссылка '{html.escape(link_name)}' {action_text}! 🔗")
    # Consider sending a confirmation sticker: await bot.send_sticker(message.chat.id, YOUR_LINK_ADDED_STICKER_ID)

@dp.message(Command("links"))
async def links_command(message: Message):
    chat_id_str = str(message.chat.id)
    if chat_id_str not in user_links or not user_links[chat_id_str]: # pragma: no branch
        await message.answer("У вас пока нет сохраненных ссылок 🤷‍♂️\nИспользуйте <code>/addlink</code> для добавления! ✨", parse_mode=ParseMode.HTML)
        return

    response_parts = ["<b>Ваши сохраненные ссылки:</b>\n"]
    sorted_link_names = sorted(user_links[chat_id_str].keys())
    
    for i, name in enumerate(sorted_link_names, 1):
        link_data = user_links[chat_id_str][name]
        url = link_data.get("url", "") 
        description = link_data.get("description", "")
        
        safe_name = html.escape(name)
        safe_description = html.escape(description)
        
        link_entry = f"{i}. <a href=\"{html.escape(url)}\">{safe_name}</a> (<code>{safe_name}</code>)"
        if safe_description:
            link_entry += f"\n   <i>Описание:</i> {safe_description}"
        response_parts.append(link_entry)
    
    await message.answer("\n".join(response_parts), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@dp.message(Command("dellink"))
async def delete_link_command(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("🔢 Формат: /dellink <название_ссылки_или_номер>\n"
                             "Чтобы увидеть номера, используйте команду /links.")
        return

    identifier = args[1].strip()
    chat_id_str = str(message.chat.id)

    if chat_id_str not in user_links or not user_links[chat_id_str]: # pragma: no branch
        await message.answer("У вас нет сохраненных ссылок для удаления.")
        return

    link_to_delete_name = None
    if identifier.isdigit():
        try:
            link_index = int(identifier) - 1 
            sorted_link_names = sorted(user_links[chat_id_str].keys())
            if 0 <= link_index < len(sorted_link_names):
                link_to_delete_name = sorted_link_names[link_index]
            else:
                await message.answer(f"🔢❌ Неверный номер ссылки. Используйте /links, чтобы увидеть доступные номера.")
                return
        except ValueError: 
            pass 

    if link_to_delete_name is None: 
        link_to_delete_name = identifier

    if link_to_delete_name in user_links[chat_id_str]:
        del user_links[chat_id_str][link_to_delete_name]
        if not user_links[chat_id_str]: 
            del user_links[chat_id_str]
        await save_user_links()
        await message.answer(f"🗑️ Ссылка '{html.escape(link_to_delete_name)}' удалена.")
        # Consider sending a confirmation sticker: await bot.send_sticker(message.chat.id, YOUR_LINK_DELETED_STICKER_ID)
    else:
        await message.answer(f"🤷 Ссылка с названием или номером '{html.escape(identifier)}' не найдена.")

# --- Команда для перевода ---
@dp.message(Command("translate", "tr")) 
async def translate_command(message: Message):
    args = message.text.split(maxsplit=2) 
    
    if len(args) < 3 or not args[1] or not args[2]:
        await message.answer(
            "🤔 Неверный формат команды. Используйте:\n"
            "<code>/translate &lt;код_целевого_языка&gt; &lt;текст для перевода&gt;</code>\n"
            "Пример: <code>/translate en Привет, мир!</code>\n"
            "Или для указания исходного языка (необязательно, по умолчанию 'auto'):\n"
            "<code>/translate &lt;исходный_код&gt;:&lt;целевой_код&gt; &lt;текст&gt;</code>\n"
            "Пример: <code>/translate ru:fr Как дела?</code> (с русского на французский)",
            parse_mode=ParseMode.HTML
        )
        return

    lang_param = args[1].strip()
    text_to_translate = args[2].strip()
    
    source_lang = 'auto'
    target_lang = lang_param

    if ':' in lang_param:
        parts = lang_param.split(':', 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            source_lang = parts[0]
            target_lang = parts[1]
        else:
            await message.answer(
                "🔄 Неверный формат указания языков (например, 'en:ru'). "
                "Используйте <code>/translate &lt;код_целевого_языка&gt; &lt;текст&gt;</code> "
                "или <code>/translate &lt;исходный&gt;:&lt;целевой&gt; &lt;текст&gt;</code>.",
                parse_mode=ParseMode.HTML
            )
            return
            
    if not text_to_translate:
        await message.answer("✍️ Пожалуйста, укажите текст для перевода.")
        return

    if not target_lang: 
        await message.answer("🎯 Пожалуйста, укажите целевой язык для перевода.")
        return

    MAX_TRANSLATE_TEXT_LENGTH = 1000 
    if len(text_to_translate) > MAX_TRANSLATE_TEXT_LENGTH:
        await message.answer(f"📏 Текст для перевода слишком длинный. Максимальная длина: {MAX_TRANSLATE_TEXT_LENGTH} символов.")
        return

    await message.answer("<i>Перевожу...</i> 🌐 Пожалуйста, подождите немного.", parse_mode=ParseMode.HTML)

    translated_text = await translate_text_async(text_to_translate, target_lang, source_lang)

    if translated_text:
        response = f"🌍 <b>Перевод ({source_lang} → {target_lang}):</b>\n{html.escape(translated_text)}"
        await message.answer(response, parse_mode=ParseMode.HTML)

# --- Команда для конвертации валют ---
@dp.message(Command("convert"))
async def convert_command(message: Message):
    command_parts = message.text.split(maxsplit=1)
    
    default_help_message = (
        "💰 Используйте формат: <code>/convert &lt;сумма&gt; &lt;ИЗ_ВАЛЮТЫ&gt; [to] &lt;В_ВАЛЮТУ&gt;</code>\n"
        "Например: <code>/convert 100 USD EUR</code> или <code>/convert 10 EUR to RUB</code>\n"
        "Валюты должны быть 3-буквенными кодами (например, USD, EUR, RUB)."
    )

    if len(command_parts) < 2 or not command_parts[1].strip():
        await message.answer(default_help_message, parse_mode=ParseMode.HTML)
        return
    
    args_str = command_parts[1].strip()

    # Регулярное выражение для захвата: сумма, валюта1, (опционально "to"), валюта2
    # Позволяет форматы "100 USD EUR", "100 USD to EUR"
    match = re.fullmatch(r"(\d+\.?\d*)\s+([A-Za-z]{3})\s+(?:to\s+)?([A-Za-z]{3})", args_str, re.IGNORECASE)

    if not match:
        await message.answer(default_help_message, parse_mode=ParseMode.HTML)
        return

    try:
        amount_str = match.group(1)
        # Проверка на слишком большое число или некорректный ввод до float конвертации
        if len(amount_str) > 20: # Ограничение на длину строки числа
             await message.answer("📈 Сумма слишком большая или некорректно введена.", parse_mode=ParseMode.HTML)
             return
        amount = float(amount_str)
        if amount <= 0:
            await message.answer("➕ Сумма для конвертации должна быть положительным числом.", parse_mode=ParseMode.HTML)
            return
        base_currency = match.group(2).upper()
        target_currency = match.group(3).upper()
    except ValueError: # pragma: no cover (regex should prevent this, but as a safeguard)
        await message.answer("🔢 Сумма должна быть корректным числом.", parse_mode=ParseMode.HTML)
        return
    
    if base_currency == target_currency:
        await message.answer(f"🤔 {amount:.2f} {base_currency} = {amount:.2f} {target_currency} (валюты одинаковы)", parse_mode=ParseMode.HTML)
        return

    await message.answer(f"<i>Конвертирую {amount:.2f} {base_currency} в {target_currency}... ⏳</i>", parse_mode=ParseMode.HTML)
    result = await get_conversion_rate(base_currency, target_currency, amount)
    await message.answer(result, parse_mode=ParseMode.HTML)

# --- Команда для игры ---
@dp.message(Command("rps")) 
async def rps_command(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer(f"👉 Пожалуйста, сделайте ваш выбор: /rps [{'/'.join(RPS_CHOICES)}]")
        return

    user_choice = args[1].strip().lower()
    if user_choice not in RPS_CHOICES:
        await message.answer(f"🚫 Неверный выбор. Пожалуйста, выберите из: {', '.join(RPS_CHOICES)}.")
        return

    bot_choice = random.choice(RPS_CHOICES)

    winner = None 
    if user_choice == bot_choice:
        winner = None
    elif (user_choice == "камень" and bot_choice == "ножницы") or \
         (user_choice == "ножницы" and bot_choice == "бумага") or \
         (user_choice == "бумага" and bot_choice == "камень"):
        winner = True
    else:
        winner = False

    # user_choice и bot_choice из RPS_CHOICES, безопасны для прямого использования в HTML
    result_text = (f"👤 Ваш выбор: <b>{user_choice.capitalize()}</b>\n"
                   f"🤖 Выбор бота: <b>{bot_choice.capitalize()}</b>\n\n")
    if winner is None:
        result_text += "Ничья! 🤝"
    elif winner:
        result_text += "Вы победили! 🎉"
    else:
        result_text += "Бот победил! 🤖"
    await message.answer(result_text, parse_mode=ParseMode.HTML)

# --- Обработчики для простого общения ---

@dp.message() 
async def handle_conversation(message: Message):
    if not message.text: 
        return 

    lower_text = message.text.lower()

    greeting_keywords = ["привет", "здравствуй", "hello", "hi", "ку", "даров"]
    if any(keyword in lower_text for keyword in greeting_keywords):
        user_name = message.from_user.first_name
        greetings = [
            f"Привет, {user_name}! 👋 Чем могу помочь?",
            f"Здравствуйте, {user_name}! 😊 Рад вас видеть. Слушаю вас!",
            "Привет! ✨ Что нового? Как настроение?",
        ]
        await message.answer(random.choice(greetings))
        return 

    how_are_you_keywords = ["как дела", "как ты", "че как", "что как"]
    if any(keyword in lower_text for keyword in how_are_you_keywords):
        responses = [
            "Всё отлично, работаю в полную силу! 🚀 А у вас как?",
            "Лучше всех! 💪 Готов к новым задачам.",
            "Спасибо, хорошо! 😊 Чем могу быть полезен сегодня?",
        ]
        await message.answer(random.choice(responses))
        return 

    thank_you_keywords = ["спасибо", "благодарю", "спс", "благодыр"]
    if any(keyword in lower_text for keyword in thank_you_keywords):
        responses = [
            "Пожалуйста! Рад помочь. 🤗",
            "Не за что! Обращайтесь. 😉",
            "Всегда к вашим услугам! 👍 Рад был быть полезным!",
        ]
        await message.answer(random.choice(responses))
        return 

    bye_keywords = ["пока", "до свидания", "bye", "бб", "досвидос"]
    if any(keyword in lower_text for keyword in bye_keywords):
        await message.answer("До свидания! Если что, я здесь. 👋😊")
        return 

# --- Основные функции ---

async def reschedule_reminders():
    """Перепланирование напоминаний при запуске"""
    logging.info("Перепланировка напоминаний при запуске...")
    now = datetime.now()
    reminders_to_save_needed = False 

    for chat_id_str in list(reminders.keys()):
        current_chat_reminders_list = list(reminders.get(chat_id_str, []))
        actual_reminders_for_chat_after_reschedule = [] 
        tasks_to_create_for_this_chat = [] 

        for reminder_data in current_chat_reminders_list:
            try:
                if not all(k in reminder_data for k in ["datetime", "text", "id"]):
                    logging.error(f"Malformed reminder data in chat {chat_id_str}: {reminder_data}. Skipping.")
                    reminders_to_save_needed = True
                    continue

                remind_datetime_from_storage = datetime.strptime(reminder_data["datetime"], DATETIME_FORMAT)
                text = reminder_data["text"]
                reminder_id = reminder_data["id"]
                repeat = reminder_data.get("repeat") 
                
                current_remind_datetime_for_scheduling = remind_datetime_from_storage
                calculated_delay = -1 

                if current_remind_datetime_for_scheduling > now: 
                    calculated_delay = (current_remind_datetime_for_scheduling - now).total_seconds()
                    actual_reminders_for_chat_after_reschedule.append(reminder_data) 
                elif repeat: 
                    original_time_for_log = current_remind_datetime_for_scheduling
                    rescheduled_this_one = False
                    while current_remind_datetime_for_scheduling <= now:
                        rescheduled_this_one = True
                        if repeat == REPEAT_DAILY:
                            current_remind_datetime_for_scheduling += timedelta(days=1)
                        elif repeat == REPEAT_WEEKLY:
                            current_remind_datetime_for_scheduling += timedelta(weeks=1)
                        else: 
                            logging.warning(f"Удаление напоминания {reminder_id} в чате {chat_id_str} с неизвестным типом повтора '{repeat}' при перепланировке.")
                            reminders_to_save_needed = True
                            current_remind_datetime_for_scheduling = None 
                            break 
                    
                    if current_remind_datetime_for_scheduling: 
                        if rescheduled_this_one:
                             logging.info(f"Перепланировано повторяющееся напоминание {reminder_id} с {original_time_for_log.strftime(DATETIME_FORMAT)} на {current_remind_datetime_for_scheduling.strftime(DATETIME_FORMAT)}")
                        
                        calculated_delay = (current_remind_datetime_for_scheduling - now).total_seconds()
                        updated_reminder_data = reminder_data.copy()
                        new_datetime_str = current_remind_datetime_for_scheduling.strftime(DATETIME_FORMAT)
                        if updated_reminder_data["datetime"] != new_datetime_str:
                            updated_reminder_data["datetime"] = new_datetime_str
                            reminders_to_save_needed = True
                        actual_reminders_for_chat_after_reschedule.append(updated_reminder_data)
                else: 
                    logging.info(f"Удаление прошлого одноразового напоминания {reminder_id} для чата {chat_id_str} при перепланировке.")
                    reminders_to_save_needed = True
                    continue 
                
                if calculated_delay < 0 and current_remind_datetime_for_scheduling : 
                     logging.warning(f"Calculated negative delay for reminder {reminder_id} in chat {chat_id_str} during reschedule. Delay: {calculated_delay}. Time: {current_remind_datetime_for_scheduling.strftime(DATETIME_FORMAT)}. Skipping task creation.")
                     continue

                if calculated_delay >= 0: 
                    tasks_to_create_for_this_chat.append({
                        "chat_id": int(chat_id_str), 
                        "text": text, 
                        "delay": calculated_delay, 
                        "reminder_id": reminder_id, 
                        "repeat": repeat
                    })
            except ValueError as e_parse: 
                logging.error(f"Ошибка парсинга даты для напоминания при запуске: {reminder_data} в чате {chat_id_str}. Ошибка: {e_parse}. Напоминание удалено.")
                reminders_to_save_needed = True 
            except KeyError as e_key: 
                 logging.error(f"Отсутствует ключ в данных напоминания {reminder_data} в чате {chat_id_str}. Ошибка: {e_key}. Напоминание пропущено.")
                 reminders_to_save_needed = True
        
        if actual_reminders_for_chat_after_reschedule:
            reminders[chat_id_str] = actual_reminders_for_chat_after_reschedule
        elif chat_id_str in reminders: 
            del reminders[chat_id_str]
            reminders_to_save_needed = True 
        
        for task_params in tasks_to_create_for_this_chat:
            active_reminder_tasks.setdefault(str(task_params["chat_id"]), {})
            active_reminder_tasks[str(task_params["chat_id"])][task_params["reminder_id"]] = asyncio.create_task(
                send_reminder(**task_params)
            )

    if reminders_to_save_needed:
        await save_reminders()
    logging.info("Перепланировка напоминаний завершена.")

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s",
    )
    
    load_reminders()
    load_user_links() 
    await reschedule_reminders() 
    
    logging.info("Бот запускается...")
    try:
        await dp.start_polling(bot)
    except Exception as e: 
        logging.critical(f"Критическая ошибка при запуске polling или во время работы бота: {e}", exc_info=True)
    finally:
        logging.info("Закрытие сессии бота...")
        if bot.session: 
             await bot.session.close()
        logging.info("Бот остановлен.")

if __name__ == "__main__":
    asyncio.run(main())

