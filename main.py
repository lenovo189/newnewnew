import asyncio
import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
import uvicorn

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from supabase import create_client, Client as SupabaseClient

load_dotenv(dotenv_path=".env.local")

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
WEBHOOK_PATH = "/webhook"
MOVIES_PER_PAGE = 10

# --- INIT ---
supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()
app = FastAPI()

admin_states = {}

# --- HELPERS ---
def parse_tg_link(link: str):
    try:
        return int(link.strip().split('/')[-1])
    except:
        return None

def get_movies_markup(page: int, total_count: int, movies: list) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for i, movie in enumerate(movies):
        row.append(InlineKeyboardButton(text=str(i + 1), callback_data=f"select_{movie['id']}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"page_{page - 1}"))
    if (page + 1) * MOVIES_PER_PAGE < total_count:
        nav_row.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"page_{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def send_movie_package(chat_id: int, movie: dict):
    if movie.get("post_message_id"):
        await bot.forward_message(chat_id=chat_id, from_chat_id=CHANNEL_ID, message_id=int(movie["post_message_id"]))

    buttons = []
    if movie.get("telegram_message_id"):
        buttons.append([InlineKeyboardButton(text="📺 To'liq ko'rish (MKV)", callback_data=f"full_{movie['id']}")])
    if movie.get("sample_message_id"):
        buttons.append([InlineKeyboardButton(text="🎞 Namunani ko'rish", callback_data=f"sample_{movie['id']}")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await bot.send_message(chat_id=chat_id, text=f"🍿 **{movie['title']}**\n\nQuyidagi variantlardan birini tanlang:", reply_markup=markup)

# --- COMMAND HANDLERS ---

@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "👋 CineStream Botiga xush kelibsiz!\n\n"
        "Qidirish uchun film nomini yuboring yoki mavjud filmlarni ko'rish uchun /list bosing."
    )

@dp.message(Command("list"))
async def list_movies(message: Message):
    await send_movie_list(message.chat.id, page=0, reply_to=message)

async def send_movie_list(chat_id: int, page: int, reply_to: Message = None):
    try:
        count_res = supabase.table("movies").select("id", count="exact").eq("status", "published").execute()
        total_count = count_res.count or 0

        offset = page * MOVIES_PER_PAGE
        response = supabase.table("movies").select("id, title").eq("status", "published").order("created_at", desc=True).range(offset, offset + MOVIES_PER_PAGE - 1).execute()
        movies = response.data

        if not movies:
            if reply_to:
                await reply_to.answer("Hozircha filmlar mavjud emas.")
            return

        movie_text = f"🎬 **Katalog ({page + 1}-sahifa)**\n\n"
        for i, movie in enumerate(movies):
            movie_text += f"{i + 1}. **{movie['title']}**\n"
        movie_text += "\nKo'rish uchun raqamni tanlang yoki boshqalarni ko'rish uchun o'qlardan foydalaning:"

        markup = get_movies_markup(page, total_count, movies)
        if reply_to:
            await reply_to.answer(movie_text, reply_markup=markup)
    except Exception as e:
        print(f"List error: {e}")
        if reply_to:
            await reply_to.answer("❌ Katalogni yuklashda xatolik yuz berdi.")

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    args = message.text.split()
    if len(args) > 1 and args[1] == ADMIN_PASSWORD:
        admin_states[message.from_user.id] = {"is_admin": True, "step": "menu"}
        await message.answer("✅ Admin tizimga kirdi! Yangi film qo'shish uchun /addmovie buyrug'ini bosing.")
    else:
        await message.answer("❌ Ruxsat berilmadi. Foydalanish: `/admin parolingiz`")

@dp.message(Command("addmovie"))
async def add_movie_start(message: Message):
    user_id = message.from_user.id
    if user_id not in admin_states or not admin_states[user_id].get("is_admin"):
        await message.answer("❌ Avval /admin parolingiz orqali tizimga kiring.")
        return
    admin_states[user_id]["step"] = "awaiting_title"
    await message.answer("📝 **Film qo'shish**\n\n1-bosqich: Film nomini yuboring (masalan: 'Avatar')")

@dp.message(Command("cancel"))
async def cancel_flow(message: Message):
    user_id = message.from_user.id
    if user_id in admin_states:
        admin_states[user_id]["step"] = "menu"
    await message.answer("🛑 Bekor qilindi. Admin menyusiga qaytilmoqda.")

# --- CALLBACK HANDLERS ---

@dp.callback_query(F.data.startswith("page_"))
async def handle_pagination(callback_query: CallbackQuery):
    page = int(callback_query.data.split("_")[1])
    try:
        count_res = supabase.table("movies").select("id", count="exact").eq("status", "published").execute()
        total_count = count_res.count or 0
        offset = page * MOVIES_PER_PAGE
        response = supabase.table("movies").select("id, title").eq("status", "published").order("created_at", desc=True).range(offset, offset + MOVIES_PER_PAGE - 1).execute()
        movies = response.data

        movie_text = f"🎬 **Katalog ({page + 1}-sahifa)**\n\n"
        for i, movie in enumerate(movies):
            movie_text += f"{i + 1}. **{movie['title']}**\n"
        movie_text += "\nKo'rish uchun raqamni tanlang yoki boshqalarni ko'rish uchun o'qlardan foydalaning:"

        markup = get_movies_markup(page, total_count, movies)
        await callback_query.message.edit_text(movie_text, reply_markup=markup)
    except Exception as e:
        await callback_query.answer("Sahifani yuklab bo'lmadi.")

@dp.callback_query(F.data.startswith("select_"))
async def handle_selection(callback_query: CallbackQuery):
    movie_id = callback_query.data.split("_")[1]
    await callback_query.answer("Tayyorlanmoqda...")
    try:
        response = supabase.table("movies").select("*").eq("id", movie_id).single().execute()
        movie = response.data
        if movie:
            await send_movie_package(callback_query.message.chat.id, movie)
    except Exception as e:
        print(f"Selection error: {e}")

@dp.callback_query(F.data.startswith("full_"))
async def handle_full_request(callback_query: CallbackQuery):
    movie_id = callback_query.data.split("_")[1]
    await callback_query.answer("Film yuborilmoqda...")
    try:
        response = supabase.table("movies").select("telegram_message_id").eq("id", movie_id).single().execute()
        movie = response.data
        if movie and movie.get("telegram_message_id"):
            await bot.forward_message(chat_id=callback_query.message.chat.id, from_chat_id=CHANNEL_ID, message_id=int(movie["telegram_message_id"]))
    except Exception as e:
        print(f"Full video error: {e}")

@dp.callback_query(F.data.startswith("sample_"))
async def handle_sample_request(callback_query: CallbackQuery):
    movie_id = callback_query.data.split("_")[1]
    await callback_query.answer("Namuna yuborilmoqda...")
    try:
        response = supabase.table("movies").select("sample_message_id").eq("id", movie_id).single().execute()
        movie = response.data
        if movie and movie.get("sample_message_id"):
            await bot.forward_message(chat_id=callback_query.message.chat.id, from_chat_id=CHANNEL_ID, message_id=int(movie["sample_message_id"]))
    except Exception as e:
        print(f"Sample error: {e}")

# --- TEXT HANDLER (admin flow + search) ---

@dp.message(F.text)
async def handle_all_text(message: Message):
    user_id = message.from_user.id
    state = admin_states.get(user_id)
    text = message.text.strip()

    if state and state.get("step") != "menu":
        step = state["step"]

        if step == "awaiting_title":
            state["title"] = text
            state["step"] = "awaiting_post_link"
            await message.answer(f"✅ Nom o'rnatildi: **{text}**\n\n2-bosqich: Post linkini yuboring (masalan: https://t.me/channel/1)")

        elif step == "awaiting_post_link":
            msg_id = parse_tg_link(text)
            if msg_id:
                state["post_message_id"] = msg_id
                state["telegram_post_url"] = text
                state["step"] = "awaiting_sample_link"
                await message.answer(f"✅ Post linki qabul qilindi (ID: {msg_id})\n\n3-bosqich: Namuna video linkini yuboring (yoki 'yo'q' deb yozing)")
            else:
                await message.answer("❌ Link formati noto'g'ri. Iltimos shunday link yuboring: `https://t.me/channel/123`")

        elif step == "awaiting_sample_link":
            if text.lower() in ["yo'q", "yoq", "none"]:
                state["sample_message_id"] = None
                state["step"] = "awaiting_mkv_link"
                await message.answer("⏩ Namuna o'tkazib yuborildi.\n\n4-bosqich: Asosiy video (MKV) linkini yuboring")
            else:
                msg_id = parse_tg_link(text)
                if msg_id:
                    state["sample_message_id"] = msg_id
                    state["step"] = "awaiting_mkv_link"
                    await message.answer(f"✅ Namuna linki qabul qilindi (ID: {msg_id})\n\n4-bosqich: Asosiy video (MKV) linkini yuboring")
                else:
                    await message.answer("❌ Link formati noto'g'ri.")

        elif step == "awaiting_mkv_link":
            msg_id = parse_tg_link(text)
            if msg_id:
                state["telegram_message_id"] = msg_id
                try:
                    movie_data = {
                        "title": state["title"],
                        "telegram_message_id": state["telegram_message_id"],
                        "post_message_id": state.get("post_message_id"),
                        "sample_message_id": state.get("sample_message_id"),
                        "telegram_post_url": state.get("telegram_post_url"),
                        "status": "published"
                    }
                    supabase.table("movies").insert(movie_data).execute()
                    await message.answer(f"🎉 **Muvaffaqiyatli!** '**{state['title']}**' filmi qo'shildi va e'lon qilindi.")
                    state["step"] = "menu"
                except Exception as e:
                    await message.answer(f"❌ Ma'lumotlar bazasiga saqlashda xatolik: {e}")
            else:
                await message.answer("❌ Link formati noto'g'ri.")
        return

    # Search
    await message.answer(f"🔍 **{text}** qidirilmoqda...")
    try:
        response = supabase.table("movies").select("*").ilike("title", f"%{text}%").eq("status", "published").execute()
        movies = response.data
        if movies:
            await send_movie_package(message.chat.id, movies[0])
        else:
            await message.answer("🔍 Film topilmadi. Mavjud filmlarni ko'rish uchun /list bosing.")
    except Exception as e:
        await message.answer("❌ Qidiruvda xatolik yuz berdi.")

# --- FASTAPI ROUTES ---

@app.on_event("startup")
async def on_startup():
    webhook_url = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
    print(f"🚀 Webhook set to: {webhook_url}")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await bot.session.close()

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    from aiogram.types import Update
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return Response(status_code=200)

@app.get("/")
async def root():
    return {"status": "healthy", "bot": "CineStream"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
