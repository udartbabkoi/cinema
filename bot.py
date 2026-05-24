import asyncio
import logging
import sys
import os
import requests
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from groq import Groq

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# 1. ЗАВАНТАЖЕННЯ КЛЮЧІВ З ENVIRONMENT VARIABLES
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    logging.error("Помилка: TELEGRAM_TOKEN та GROQ_API_KEY мають бути встановлені як змінні середовища!")
    sys.exit(1)

# Ініціалізація клієнтів Telegram та Groq
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
groq_client = Groq(api_key=GROQ_API_KEY)

# ГЛОБАЛЬНА ПАМ'ЯТЬ БОТА
user_histories = {}
cached_cinema_data = "Дані про розклад тимчасово оновлюються."


# 2. KEEP-ALIVE ВЕБ-СЕРВЕР (потрібен для Railway)
async def keep_alive():
    import socket
    server = await asyncio.start_server(
        lambda r, w: None,
        "0.0.0.0",
        int(os.environ.get("PORT", 8080))
    )
    async with server:
        await server.serve_forever()


# 3. АСИНХРОННИЙ ФОНОВИЙ ПАРСЕР
async def cinema_parser_task():
    global cached_cinema_data
    url = "https://smartcinema.ua/api/movies/schedule/2/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    while True:
        logging.info("Фоновий парсер: Повний збір розкладу на 7 днів...")
        
        full_output_text = "АКТУАЛЬНИЙ РОЗКЛАД КІНОТЕАТРУ SMART CINEMA (Чернівці, ТРЦ Depo't) НА 7 ДНІВ\n"
        full_output_text += "==================================================\n\n"
        
        base_date = datetime.now()
        
        for i in range(7):
            target_date_obj = base_date + timedelta(days=i)
            query_date = target_date_obj.strftime("%d-%m-%Y")
            display_date = target_date_obj.strftime("%d.%m.%Y")
            
            params = {"date": query_date}
            
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, lambda: requests.get(url, params=params, headers=headers))
                
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
                    logging.warning(f"Фоновий парсер: Немає даних для дати {query_date}. Код: {response.status_code}")
                    
            except Exception as e:
                logging.error(f"Фоновий парсер: Помилка для дати {query_date}: {e}")
            
            await asyncio.sleep(0.1)
            
        cached_cinema_data = full_output_text
        logging.info("Фоновий парсер: Повний розклад успішно оновлено в кеші!")
        await asyncio.sleep(3600)


# 4. МИТТЄВА ФІЛЬТРАЦІЯ КОНТЕКСТУ
def get_relevant_context(user_message):
    content = cached_cinema_data

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


# 5. ОБРОБКА КОМАНД ТА ПОВІДОМЛЕНЬ
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_histories[message.from_user.id] = []
    welcome_text = (
        "🍿 Вітаємо у Smart Cinema Чернівці!\n\n"
        "Я твій особистий ШІ-помічник. Можу розповісти, які фільми йдуть у ТРЦ Depo't на тиждень вперед, "
        "підказати повний розклад сеансів, зали та детальні ціни на всі типи місць.\n\n"
        "Яке кіно або яка дата вас цікавить?"
    )
    await message.answer(welcome_text)

@dp.message()
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    
    if not message.text:
        await message.answer("Будь ласка, відправте текстове повідомлення! ✍️")
        return

    if user_id not in user_histories:
        user_histories[user_id] = []
        
    placeholder_msg = await message.answer("ШІ шукає розклад та рахує ціни... 🍿")
        
    try:
        cinema_context = get_relevant_context(message.text)
        
        system_instruction = f"""
Ти — офіційний ШІ-асистент мережі кінотеатрів Smart Cinema у Чернівцях (ТРЦ Depo't). 
Твоя мета — ввічливо допомагати клієнтам, розповідати про фільми, розклад сеансів, формати (2D/3D), зали та детальні ціни.
Ти повинен спілкуватися ВИКЛЮЧНО українською мовою. Твій тон має бути дружнім та професійним.

СУВОРЕ ПРАВИЛО ФОРМАТУВАННЯ ТЕКСТУ:
Тобі повністю ЗАБОРОНЕНО використовувати будь-який подібний markdown-формат у відповідях. 
- Ніколи не став подвійні зірочки (**текст**) для жирного шрифту.
- Ніколи не використовуй решітки (### Заголовок) для створення заголовків.
- Не використовуй нижні підкреслення або символи зворотних лапок.

Твоя відповідь повинна складатися з чистого тексту, розділеного на звичайні абзаци. 
Замість маркдауну оформлюй списки за допомогою дефісів (-) та різноманітних емодзі.

Актуальні дані про розклад (тут є всі зали та детальні ціни):
{cinema_context}

Правила:
1. Завжди вітайся від імені Smart Cinema.
2. Коли користувач запитує про ціни — детально розписуй вартість різних типів місць (Стандарт, VIP) та номери залів із наданого тексту, нічого не урізаючи!
3. Посилання на сайт: "Ви можете придбати квитки на нашому офіційному сайті smartcinema.ua".
4. Якщо користувач скаржиться, незадоволений, або хоче поговорити з людиною — обов'язково направляй його до служби підтримки: @Smartcinema_bot
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
                
            chunk_size = 4000
            
            try:
                await placeholder_msg.delete()
            except:
                pass
            
            for i in range(0, len(response_text), chunk_size):
                await message.answer(response_text[i:i+chunk_size])
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


# 6. ЗАПУСК
async def main():
    asyncio.create_task(keep_alive())
    asyncio.create_task(cinema_parser_task())
    await asyncio.sleep(2)  
    
    logging.info("Бот та фоновий парсер успішно запущені!")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
