import asyncio
import logging
import os
import sys
import requests
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from groq import Groq

logging.basicConfig(level=logging.INFO)

LOCATIONS = {
    "Вінниця (ТЦ SkyPark)":       {"id": 1, "city": "Вінниця"},
    "Чернівці (ТРЦ Depo't)":      {"id": 2, "city": "Чернівці"},
    "Кропивницький (ТЦ Depo't)":  {"id": 3, "city": "Кропивницький"},
}

def load_tokens():
    token = os.environ.get("TELEGRAM_TOKEN")
    groq_key = os.environ.get("GROQ_API_KEY")
    if not token or not groq_key:
        logging.error("Помилка: TELEGRAM_TOKEN або GROQ_API_KEY не задано!")
        sys.exit(1)
    return token, groq_key

TELEGRAM_TOKEN, GROQ_API_KEY = load_tokens()
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
groq_client = Groq(api_key=GROQ_API_KEY)

user_histories = {}
user_locations = {}
cached_cinema_data = {name: "Дані про розклад тимчасово оновлюються." for name in LOCATIONS}

async def parse_location(location_name: str, location_id: int) -> str:
    url = f"https://smartcinema.ua/api/movies/schedule/{location_id}/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    loop = asyncio.get_event_loop()
    full_output_text = f"АКТУАЛЬНИЙ РОЗКЛАД КІНОТЕАТРУ SMART CINEMA ({location_name}) НА 7 ДНІВ\n"
    full_output_text += "==================================================\n\n"
    base_date = datetime.now()
    for i in range(7):
        target_date_obj = base_date + timedelta(days=i)
        query_date = target_date_obj.strftime("%d-%m-%Y")
        display_date = target_date_obj.strftime("%d.%m.%Y")
        try:
            response = await loop.run_in_executor(
                None,
                lambda d=query_date: requests.get(url, params={"date": d}, headers=headers, timeout=10)
            )
            if response.status_code == 200:
                movies = response.json()
                if movies and isinstance(movies, list):
                    day_text = f"📅 ДАТА: {display_date}\n"
                    day_text += "--------------------------------------------------\n"
                    has_showtimes_this_day = False
                    for movie in movies:
                        title = movie.get("title")
                        age = movie.get("age")
                        duration = movie.get("duration")
                        genres = ", ".join([g["name"] for g in movie.get("genre", [])])
                        showtimes = []
                        for fmt in ["2D", "3D"]:
                            for session in movie.get("showtime_list", {}).get(fmt, []):
                                start_time = datetime.fromisoformat(session["start"]).strftime("%H:%M")
                                session_info = (
                                    f"  - Час: {start_time} ({fmt}) | {session['hall']}\n"
                                    f"    Ціни: Стандарт — {session['standard_price']} грн, "
                                    f"VIP-місця — {session['vipseats_price']} грн, "
                                    f"VIP-ложі — {session['vip_price']} грн"
                                )
                                showtimes.append(session_info)
                        if showtimes:
                            has_showtimes_this_day = True
                            output_movie_text = f"🎬 Фільм: {title} ({age}+)\n"
                            if genres:
                                output_movie_text += f"Жанр: {genres}\n"
                            if duration:
                                output_movie_text += f"Тривалість: {duration} хв.\n"
                            output_movie_text += "Сеанси:\n"
                            output_movie_text += "\n".join(showtimes)
                            output_movie_text += "\n\n"
                            day_text += output_movie_text
                    day_text += "==================================================\n\n"
                    if has_showtimes_this_day:
                        full_output_text += day_text
            else:
                logging.warning(f"[{location_name}] Немає даних для {query_date}. Код: {response.status_code}")
        except Exception as e:
            logging.error(f"[{location_name}] Помилка для {query_date}: {e}")
        await asyncio.sleep(0.1)
    return full_output_text

async def cinema_parser_task():
    global cached_cinema_data
    while True:
        logging.info("Фоновий парсер: починаю збір розкладу для всіх локацій...")
        tasks = {}
        for name, info in LOCATIONS.items():
            if info["id"] is not None:
                tasks[name] = asyncio.create_task(parse_location(name, info["id"]))
        for name, task in tasks.items():
            try:
                result = await task
                cached_cinema_data[name] = result
                logging.info(f"Фоновий парсер: '{name}' — розклад оновлено ✓")
            except Exception as e:
                logging.error(f"Фоновий парсер: помилка для '{name}': {e}")
        logging.info("Фоновий парсер: всі локації оброблено. Наступне оновлення через 1 годину.")
        await asyncio.sleep(3600)

def get_relevant_context(user_message: str, location_name: str) -> str:
    content = cached_cinema_data.get(location_name, "Розклад для цієї локації ще завантажується.")
    if "📅 ДАТА:" not in content:
        return content
    blocks = content.split("📅 ДАТА: ")
    header = blocks[0]
    base_date = datetime.now()
    dates_to_check = []
    msg_lower = user_message.lower()
    if "сьогодн" in msg_lower:
        dates_to_check.append(base_date.strftime("%d.%m.%Y"))
    if "завтр" in msg_lower:
        dates_to_check.append((base_date + timedelta(days=1)).strftime("%d.%m.%Y"))
    if "післязавтр" in msg_lower:
        dates_to_check.append((base_date + timedelta(days=2)).strftime("%d.%m.%Y"))
    for i in range(7):
        d = base_date + timedelta(days=i)
        d_str = d.strftime("%d.%m.%Y")
        short_d_str = d.strftime("%d.%m")
        if short_d_str in msg_lower or d_str in msg_lower:
            dates_to_check.append(d_str)
    if not dates_to_check:
        dates_to_check.append(base_date.strftime("%d.%m.%Y"))
        dates_to_check.append((base_date + timedelta(days=1)).strftime("%d.%m.%Y"))
    relevant_text = header + "\n"
    for block in blocks[1:]:
        block_date = block.split("\n")[0].strip()
        if any(target_date in block_date for target_date in dates_to_check):
            relevant_text += "📅 ДАТА: " + block
    return relevant_text

def build_location_keyboard() -> ReplyKeyboardMarkup:
    buttons = [[KeyboardButton(text=name)] for name in LOCATIONS.keys()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    user_histories[user_id] = []
    user_locations.pop(user_id, None)
    await message.answer(
        "🍿 Вітаємо у Smart Cinema!\n\nОберіть ваш кінотеатр:",
        reply_markup=build_location_keyboard()
    )

@dp.message(F.text.in_(LOCATIONS.keys()))
async def location_selected(message: types.Message):
    user_id = message.from_user.id
    chosen = message.text
    user_locations[user_id] = chosen
    user_histories[user_id] = []
    city = LOCATIONS[chosen]["city"]
    await message.answer(
        f"✅ Обрано: {chosen}\n\nЯ твій особистий ШІ-помічник кінотеатру Smart Cinema у {city}. "
        f"Можу розповісти які фільми йдуть, підказати розклад сеансів, зали та ціни на тиждень вперед.\n\n"
        "Яке кіно або яка дата вас цікавить?",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message()
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    if not message.text:
        await message.answer("Будь ласка, відправте текстове повідомлення! ✍️")
        return
    if user_id not in user_locations:
        await message.answer("Спочатку оберіть кінотеатр 👇", reply_markup=build_location_keyboard())
        return
    location_name = user_locations[user_id]
    if user_id not in user_histories:
        user_histories[user_id] = []
    placeholder_msg = await message.answer("ШІ шукає розклад та рахує ціни... 🍿")
    try:
        cinema_context = get_relevant_context(message.text, location_name)
        city = LOCATIONS[location_name]["city"]
        system_instruction = f"""
Ти — офіційний ШІ-асистент кінотеатру Smart Cinema у місті {city} ({location_name}).
Твоя мета — ввічливо допомагати клієнтам, розповідати про фільми, розклад сеансів, формати (2D/3D), зали та детальні ціни.
Ти повинен спілкуватися ВИКЛЮЧНО українською мовою. Твій тон має бути дружнім та професійним.

СУВОРЕ ПРАВИЛО ФОРМАТУВАННЯ ТЕКСТУ:
Тобі повністю ЗАБОРОНЕНО використовувати будь-який markdown-формат у відповідях.
- Ніколи не став подвійні зірочки (**текст**) для жирного шрифту.
- Ніколи не використовуй решітки (### Заголовок) для створення заголовків.
- Не використовуй нижні підкреслення або символи зворотних лапок.

Твоя відповідь повинна складатися з чистого тексту, розділеного на звичайні абзаци.
Замість маркдауну оформлюй списки за допомогою дефісів (-) та різноманітних емодзі.

Актуальні дані про розклад (тут є всі зали та детальні ціни):
{cinema_context}

Правила:
1. Завжди вітайся від імені Smart Cinema {city}.
2. Коли користувач запитує про ціни — детально розписуй вартість різних типів місць (Стандарт, VIP) та номери залів із наданого тексту, нічого не урізаючи!
3. Посилання на сайт: "Ви можете придбати квитки на нашому офіційному сайті smartcinema.ua".
4. Скарга або людина -> підтримка: "@smart_support_bot".
"""
        messages = [{"role": "system", "content": system_instruction}]
        for msg in user_histories[user_id]:
            messages.append(msg)
        messages.append({"role": "user", "content": message.text})
        loop = asyncio.get_event_loop()
        completion = await loop.run_in_executor(
            None,
            lambda: groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=messages,
                temperature=0.3,
                max_tokens=1024
            )
        )
        response_text = completion.choices[0].message.content
        if response_text:
            user_histories[user_id].append({"role": "user", "content": message.text})
            user_histories[user_id].append({"role": "assistant", "content": response_text})
            if len(user_histories[user_id]) > 10:
                user_histories[user_id] = user_histories[user_id][-10:]
            try:
                await placeholder_msg.delete()
            except:
                pass
            chunk_size = 4000
            for i in range(0, len(response_text), chunk_size):
                await message.answer(response_text[i:i + chunk_size])
                await asyncio.sleep(0.1)
        else:
            await placeholder_msg.edit_text("Не вдалося отримати відповідь від ШІ. Спробуйте ще раз!")
    except Exception as e:
        logging.error(f"Помилка API або Telegram: {e}")
        try:
            await placeholder_msg.delete()
        except:
            pass
        await message.answer("Перепрошую, виникла помилка при обробці запиту нейромережею. Спробуйте пізніше!")

async def main():
    asyncio.create_task(cinema_parser_task())
    await asyncio.sleep(2)
    logging.info("Бот запущено з підтримкою кількох локацій!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
