import json
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import asyncio
from datetime import datetime, timedelta
import aiohttp
import re  # Импортируем модуль для регулярных выражений

# --- Настройки ---
TOKEN = ""  # Замените на свой токен
WEATHER_API_KEY = "c529daafe7ba8400e57973d17aab48e4"  # Замените на свой API-ключ OpenWeatherMap
NEWS_API_KEY = "08e9a5e7b28c402cb2a5656124e82611"  # Замените на свой API-ключ NewsAPI

# --- Инициализация ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

REMINDERS_FILE = "reminders.json"
reminders = {}

# --- Вспомогательные функции ---

def load_reminders():
    """Загружает напоминания из файла."""
    global reminders
    try:
        with open(REMINDERS_FILE, "r") as f:
            reminders = json.load(f)
    except FileNotFoundError:
        reminders = {}

def save_reminders():
    """Сохраняет напоминания в файл."""
    with open(REMINDERS_FILE, "w") as f:
        json.dump(reminders, f, indent=4, ensure_ascii=False)

async def get_weather(city: str):
    """Получает данные о погоде из OpenWeatherMap."""
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                elif response.status == 404:
                    return None  # Возвращаем None, если город не найден
                else:
                    print(f"Ошибка при запросе погоды: {response.status} - {await response.text()}")
                    return None
    except aiohttp.ClientError as e:
        print(f"Ошибка клиента aiohttp: {e}")
        return None
    except Exception as e:
        print(f"Непредвиденная ошибка при запросе погоды: {e}")
        return None

async def get_news(query: str):
    """Получает новости из NewsAPI."""
    url = f"https://newsapi.org/v2/everything?q={query}&apiKey={NEWS_API_KEY}&language=ru&sortBy=publishedAt"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    print(f"Ошибка при запросе новостей: {response.status} - {await response.text()}")
                    return None
    except aiohttp.ClientError as e:
        print(f"Ошибка клиента aiohttp: {e}")
        return None
    except Exception as e:
        print(f"Непредвиденная ошибка при запросе новостей: {e}")
        return None

async def send_reminder(chat_id, text, delay, remind_datetime_str, reminder_id, repeat):
    """Отправляет напоминание и обрабатывает повторения."""
    await asyncio.sleep(delay)
    try:
        await bot.send_message(chat_id, f"⏰ Напоминание: {text}")
        if repeat:
            now = datetime.now()
            remind_datetime = datetime.strptime(remind_datetime_str, "%d.%m.%Y %H:%M")
            if repeat == "daily":
                remind_datetime += timedelta(days=1)
            elif repeat == "weekly":
                remind_datetime += timedelta(weeks=1)
            delay = (remind_datetime - now).total_seconds()
            chat_id_str = str(chat_id)
            if chat_id_str not in reminders:
                reminders[chat_id_str] = []
            new_reminder_id = len(reminders[chat_id_str]) + 1
            reminders[chat_id_str].append({"id": new_reminder_id, "datetime": remind_datetime.strftime("%d.%m.%Y %H:%M"), "text": text, "repeat": repeat})
            save_reminders()
            asyncio.create_task(send_reminder(chat_id, text, delay, remind_datetime.strftime("%d.%m.%Y %H:%M"), new_reminder_id, repeat))
        else:
            chat_id_str = str(chat_id)
            if chat_id_str in reminders:
                reminders[chat_id_str] = [r for r in reminders[chat_id_str] if not (r["id"] == reminder_id and r["datetime"] == remind_datetime_str and r["text"] == text)]
                save_reminders()
    except Exception as e:
        print(f"Ошибка при отправке напоминания: {e}")

# --- Обработчики команд ---

@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer("Привет! Я твой личный помощник! Напиши /help, чтобы увидеть список команд.")

@dp.message(Command("help"))
async def help_command(message: Message):
    text = (
        "📌 Доступные команды:\n"
        "/start - Запустить бота\n"
        "/help - Список команд\n"
        "/remind DD.MM.YYYY HH:MM текст - Установить напоминание (дата опциональна)\n"
        "/remind DD.MM.YYYY HH:MM текст repeat - Установить повторяющееся напоминание (daily, weekly)\n"
        "/list - Показать список напоминаний\n"
        "/cancel ID - Отменить напоминание по ID\n"
        "/weather город - Узнать погоду в городе\n"
        "/news запрос - Узнать новости по запросу\n"
        "Просто напиши сообщение, и я попробую ответить!"
    )
    await message.answer(text)

@dp.message(Command("remind"))
async def remind_command(message: Message):
    try:
        args = message.text.split(" ", 1)
        if len(args) < 2:
            await message.answer("⏰ Используй формат: /remind DD.MM.YYYY HH:MM Текст напоминания (дата опциональна)")
            return

        datetime_and_text = args[1].split(" ", 1)
        if len(datetime_and_text) < 2:
            await message.answer("⏰ Используй формат: /remind DD.MM.YYYY HH:MM Текст напоминания (дата опциональна)")
            return

        datetime_str = datetime_and_text[0]
        text_and_repeat = datetime_and_text[1].split(" ", 1)
        reminder_text = text_and_repeat[0]
        repeat = text_and_repeat[1] if len(text_and_repeat) > 1 else None

        remind_datetime = None
        try:
            remind_datetime = datetime.strptime(datetime_str[:16], "%d.%m.%Y %H:%M")
        except ValueError:
            try:
                remind_datetime = datetime.strptime(datetime_str[:5], "%H:%M")
                remind_datetime = datetime.combine(datetime.now().date(), remind_datetime.time())
                if remind_datetime < datetime.now():
                    remind_datetime += timedelta(days=1)
            except ValueError:
                try:
                    remind_datetime = datetime.strptime(datetime_str[:10], "%d.%m.%Y")
                    remind_datetime = datetime.combine(remind_datetime.date(), datetime.now().time())
                except ValueError:
                    await message.answer("❌ Неверный формат даты и времени. Используй DD.MM.YYYY HH:MM, HH:MM или DD.MM.YYYY")
                    return

        if not reminder_text:
            await message.answer("❌ Текст напоминания не может быть пустым.")
            return

        now = datetime.now()
        if remind_datetime < now:
            await message.answer("❌ Нельзя установить напоминание на прошедшее время.")
            return

        delay = (remind_datetime - now).total_seconds()
        chat_id = str(message.chat.id)
        if chat_id not in reminders:
            reminders[chat_id] = []
        reminder_id = len(reminders[chat_id]) + 1
        reminders[chat_id].append({"id": reminder_id, "datetime": remind_datetime.strftime("%d.%m.%Y %H:%M"), "text": reminder_text, "repeat": repeat})
        save_reminders()

        asyncio.create_task(send_reminder(message.chat.id, reminder_text, delay, remind_datetime.strftime("%d.%m.%Y %H:%M"), reminder_id, repeat))
        await message.answer(f"✅ Напоминание установлено на {remind_datetime.strftime('%d.%m.%Y %H:%M')}: {reminder_text}")

    except Exception as e:
        print(f"Ошибка: {e}")
        await message.answer("❌ Ошибка! Используй формат: /remind DD.MM.YYYY HH:MM Текст напоминания (дата опциональна)")

@dp.message(Command("list"))
async def list_command(message: Message):
    chat_id = str(message.chat.id)
    if chat_id in reminders and reminders[chat_id]:
        reminder_list = ""
        for reminder in reminders[chat_id]:
            reminder_list += f"- ID: {reminder['id']}, {reminder['datetime']}: {reminder['text']}\n"
        await message.answer(f"Ваши напоминания:\n{reminder_list}")
    else:
        await message.answer("У вас нет активных напоминаний.")

@dp.message(Command("cancel"))
async def cancel_command(message: Message):
    try:
        args = message.text.split(" ", 1)
        if len(args) < 2:
            await message.answer("Используй формат: /cancel ID")
            return
        reminder_id = int(args[1])
        chat_id = str(message.chat.id)
        if chat_id in reminders:
            reminders[chat_id] = [r for r in reminders[chat_id] if r["id"] != reminder_id]
            save_reminders()
            await message.answer(f"Напоминание с ID {reminder_id} отменено.")
        else:
            await message.answer("У вас нет активных напоминаний.")
    except ValueError:
        await message.answer("Неверный формат ID. Используй число.")

@dp.message(Command("weather"))
async def weather_command(message: Message):
    try:
        city = message.text.split(" ", 1)[1]
        weather_data = await get_weather(city)
        if weather_data:
            temperature = weather_data["main"]["temp"]
            feels_like = weather_data["main"]["feels_like"]
            description = weather_data["weather"][0]["description"]
            humidity = weather_data["main"]["humidity"]
            wind_speed = weather_data["wind"]["speed"]

            weather_message = (
                f"Погода в городе {city}:\n"
                f"Температура: {temperature}°C\n"
                f"Ощущается как: {feels_like}°C\n"
                f"Описание: {description}\n"
                f"Влажность: {humidity}%\n"
                f"Скорость ветра: {wind_speed} м/с"
            )
            await message.answer(weather_message)
        else:
            await message.answer(f"Не удалось найти город '{city}' или получить данные о погоде для него.")
    except IndexError:
        await message.answer("Укажите город после команды /weather")
    except Exception as e:
        print(f"Ошибка при получении погоды: {e}")
        await message.answer("Произошла ошибка при получении данных о погоде.")

@dp.message(Command("news"))
async def news_command(message: Message):
    try:
        query = message.text.split(" ", 1)[1]
        news_data = await get_news(query)
        if news_data and news_data["articles"]:
            for article in news_data["articles"]:
                news_message = (
                    f"📰 {article['title']}\n"
                    f"{article['description']}\n"
                    f"Источник: {article['source']['name']}\n"
                    f"Ссылка: {article['url']}\n\n"
                )
                await message.answer(news_message)
        elif news_data and not news_data["articles"]:
            await message.answer(f"По запросу '{query}' ничего не найдено.")
        else:
            await message.answer(f"Не удалось получить новости по запросу '{query}'.")
    except IndexError:
        await message.answer("Укажите запрос после команды /news")
    except Exception as e:
        print(f"Ошибка при получении новостей: {e}")
        await message.answer("Произошла ошибка при получении новостей.")

@dp.message()
async def handle_message(message: Message):
    text = message.text.lower()

    # Приветствия
    if re.search(r"(привет|здравствуй|добр(ое|ый) (утро|день|вечер)|хай|ку)", text):
        await message.answer("Привет! Рад тебя видеть!")

    # Как дела?
    elif re.search(r"(как (дела|ты)|как поживаешь|что нового)", text):
        await message.answer("У меня всё отлично, спасибо! А у тебя как?")

    # Что ты умеешь?
    elif re.search(r"(что ты умеешь|какие у тебя функции|что ты можешь)", text):
        await message.answer("Я могу устанавливать напоминания, показывать погоду, искать новости и отвечать на простые вопросы. Напиши /help, чтобы увидеть список команд.")

    # Прощания
    elif re.search(r"(пока|до свидания|прощай|увидимся|до скорого)", text):
        await message.answer("Пока! Хорошего дня!")

    # Погода
    elif re.search(r"(погода в|какая погода в|температура в) ([\w\s]+)", text):
        city = re.search(r"(погода в|какая погода в|температура в) ([\w\s]+)", text).group(2)
        await weather_command(message)

    # Новости
    elif re.search(r"(новости о|новости про|новости|что нового в мире) ([\w\s]+)", text):
        query = re.search(r"(новости о|новости про|новости|что нового в мире) ([\w\s]+)", text).group(2)
        await news_command(message)

    # Спасибо
    elif re.search(r"(спасибо|благодарю)", text):
        await message.answer("Пожалуйста! Обращайся, если что.")

    # Как тебя зовут?
    elif re.search(r"(как тебя зовут|как твое имя|ты кто)", text):
        await message.answer("Я - твой личный помощник!")

    # Не понял
    else:
        await message.answer("Я не совсем понял, что ты имеешь в виду. Попробуй перефразировать.")

# --- Запуск ---

async def main():
    load_reminders()
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
