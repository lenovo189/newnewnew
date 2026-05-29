from fastapi import FastAPI, Request, Response
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Update
from pyrogram.raw.types import Update as RawUpdate  # For internal raw processing fallback
from supabase import create_client, Client as SupabaseClient
import os
import uvicorn
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv(dotenv_path=".env.local")

# --- CONFIGURATION ---
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# Admin state tracking
admin_states = {}

def parse_tg_link(link: str):
    try:
        parts = link.strip().split('/')
        return int(parts[-1])
    except:
        return None

# Initialize Supabase & Pyrogram Client
supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Client("movie_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, plugins=None)

# Initialize FastAPI
app = FastAPI()

MOVIES_PER_PAGE = 10

# --- PYPROGRAM HANDLERS ---

def get_movies_markup(page: int, total_count: int, movies: list):
    buttons = []
    row = []
    for i in range(len(movies)):
        row.append(InlineKeyboardButton(str(i + 1), callback_data=f"select_{movies[i]['id']}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row: buttons.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"page_{page-1}"))
    if (page + 1) * MOVIES_PER_PAGE < total_count:
        nav_row.append(InlineKeyboardButton("Keyingi ➡️", callback_data=f"page_{page+1}"))
    if nav_row:
        buttons.append(nav_row)
    return InlineKeyboardMarkup(buttons)

@bot.on_message(filters.command("start"))
async def start_command(client, message: Message):
    await message.reply_text(
        "👋 CineStream Botiga xush kelibsiz!\n\n"
        "Qidirish uchun film nomini yuboring yoki mavjud filmlarni ko'rish uchun /list bosing."
    )

@bot.on_message(filters.command("list"))
async def list_movies(client, message: Message, page: int = 0):
    try:
        count_res = supabase.table("movies").select("id", count="exact").eq("status", "published").execute()
        total_count = count_res.count if count_res.count is not None else 0
        
        offset = page * MOVIES_PER_PAGE
        response = supabase.table("movies").select("id, title").eq("status", "published").order("created_at", desc=True).range(offset, offset + MOVIES_PER_PAGE - 1).execute()
        movies = response.data
        
        if not movies:
            await message.reply_text("Hozircha filmlar mavjud emas.")
            return
        
        movie_text = f"🎬 **Katalog ({page + 1}-sahifa)**\n\n"
        for i, movie in enumerate(movies):
            movie_text += f"{i + 1}. **{movie['title']}**\n"
        
        movie_text += "\nKo'rish uchun raqamni tanlang yoki boshqalarni ko'rish uchun o'qlardan foydalaning:"
        markup = get_movies_markup(page, total_count, movies)
        await message.reply_text(movie_text, reply_markup=markup)
    except Exception as e:
        await message.reply_text("❌ Katalogni yuklashda xatolik yuz berdi.")

@bot.on_callback_query(filters.regex("^page_"))
async def handle_pagination(client, callback_query: CallbackQuery):
    page = int(callback_query.data.split("_")[1])
    try:
        count_res = supabase.table("movies").select("id", count="exact").eq("status", "published").execute()
        total_count = count_res.count
        offset = page * MOVIES_PER_PAGE
        response = supabase.table("movies").select("id, title").eq("status", "published").order("created_at", desc=True).range(offset, offset + MOVIES_PER_PAGE - 1).execute()
        movies = response.data

        movie_text = f"🎬 **Katalog ({page + 1}-sahifa)**\n\n"
        for i, movie in enumerate(movies):
            movie_text += f"{i + 1}. **{movie['title']}**\n"
        movie_text += "\nKo'rish uchun raqamni tanlang yoki boshqalarni ko'rish uchun o'qlardan foydalaning:"
        
        markup = get_movies_markup(page, total_count, movies)
        await callback_query.edit_message_text(movie_text, reply_markup=markup)
    except:
        await callback_query.answer("Sahifani yuklab bo'lmadi.")

async def send_movie_package(client, chat_id, movie):
    if movie.get("post_message_id"):
        await client.forward_messages(chat_id=chat_id, from_chat_id=CHANNEL_ID, message_ids=int(movie["post_message_id"]))
    
    buttons = []
    if movie.get("telegram_message_id"):
        buttons.append([InlineKeyboardButton("📺 To'liq ko'rish (MKV)", callback_data=f"full_{movie['id']}")])
    if movie.get("sample_message_id"):
        buttons.append([InlineKeyboardButton("🎞 Namunani ko'rish", callback_data=f"sample_{movie['id']}")])
    
    markup = InlineKeyboardMarkup(buttons)
    await client.send_message(chat_id=chat_id, text=f"🍿 **{movie['title']}**\n\nQuyidagi variantlardan birini tanlang:", reply_markup=markup)

@bot.on_callback_query(filters.regex("^select_"))
async def handle_selection(client, callback_query: CallbackQuery):
    movie_id = callback_query.data.split("_")[1]
    await callback_query.answer("Tayyorlanmoqda...")
    try:
        response = supabase.table("movies").select("*").eq("id", movie_id).single().execute()
        movie = response.data
        if movie:
            await send_movie_package(client, callback_query.message.chat.id, movie)
    except Exception as e:
        print(f"Selection Error: {e}")

@bot.on_callback_query(filters.regex("^full_"))
async def handle_full_request(client, callback_query: CallbackQuery):
    movie_id = callback_query.data.split("_")[1]
    await callback_query.answer("Film yuborilmoqda...")
    try:
        response = supabase.table("movies").select("telegram_message_id").eq("id", movie_id).single().execute()
        movie = response.data
        if movie and movie.get("telegram_message_id"):
            await client.forward_messages(chat_id=callback_query.message.chat.id, from_chat_id=CHANNEL_ID, message_ids=int(movie["telegram_message_id"]))
    except Exception as e:
        print(f"Full Video Error: {e}")

@bot.on_callback_query(filters.regex("^sample_"))
async def handle_sample_request(client, callback_query: CallbackQuery):
    movie_id = callback_query.data.split("_")[1]
    await callback_query.answer("Namuna yuborilmoqda...")
    try:
        response = supabase.table("movies").select("sample_message_id").eq("id", movie_id).single().execute()
        movie = response.data
        if movie and movie.get("sample_message_id"):
            await client.forward_messages(chat_id=callback_query.message.chat.id, from_chat_id=CHANNEL_ID, message_ids=int(movie["sample_message_id"]))
    except Exception as e:
        print(f"Sample Error: {e}")

@bot.on_message(filters.command("admin"))
async def admin_cmd(client, message: Message):
    args = message.text.split()
    if len(args) > 1 and args[1] == os.getenv("ADMIN_PASSWORD"):
        admin_states[message.from_user.id] = {"is_admin": True, "step": "menu"}
        await message.reply_text("✅ Admin tizimga kirdi! Yangi film qo'shish uchun /addmovie buyrug'ini bosing.")
    else:
        await message.reply_text("❌ Ruxsat berilmadi. Foydalanish: `/admin parolingiz`")

@bot.on_message(filters.command("addmovie"))
async def add_movie_start(client, message: Message):
    user_id = message.from_user.id
    if user_id not in admin_states or not admin_states[user_id].get("is_admin"):
        await message.reply_text("❌ Avval /admin parolingiz orqali tizimga kiring.")
        return
    admin_states[user_id]["step"] = "awaiting_title"
    await message.reply_text("📝 **Film qo'shish**\n\n1-bosqich: Film nomini yuboring (masalan: 'Avatar')")

@bot.on_message(filters.command("cancel"))
async def cancel_flow(client, message: Message):
    user_id = message.from_user.id
    if user_id in admin_states:
        admin_states[user_id]["step"] = "menu"
        await message.reply_text("🛑 Bekor qilindi. Admin menyusiga qaytilmoqda.")

@bot.on_message(filters.text & ~filters.command([]))
async def handle_all_text(client, message: Message):
    user_id = message.from_user.id
    state = admin_states.get(user_id)
    text = message.text.strip()

    if state and state.get("step") != "menu":
        step = state.get("step")
        if step == "awaiting_title":
            state["title"] = text
            state["step"] = "awaiting_post_link"
            await message.reply_text(f"✅ Nom o'rnatildi: **{text}**\n\n2-bosqich: Post linkini yuboring (masalan: https://t.me/channel/1)")
        
        elif step == "awaiting_post_link":
            msg_id = parse_tg_link(text)
            if msg_id:
                state["post_message_id"] = msg_id
                state["telegram_post_url"] = text
                state["step"] = "awaiting_sample_link"
                await message.reply_text(f"✅ Post linki qabul qilindi (ID: {msg_id})\n\n3-bosqich: Namuna video linkini yuboring (yoki 'yo'q' deb yozing)")
            else:
                await message.reply_text("❌ Link formati noto'g'ri. Iltimos shunday link yuboring: `https://t.me/channel/123`")
        
        elif step == "awaiting_sample_link":
            if text.lower() in ['yo\'q', "yo'q", 'none', 'yoq']:
                state["sample_message_id"] = None
                state["step"] = "awaiting_mkv_link"
                await message.reply_text("⏩ Namuna o'tkazib yuborildi.\n\n4-bosqich: Asosiy video (MKV) linkini yuboring")
            else:
                msg_id = parse_tg_link(text)
                if msg_id:
                    state["sample_message_id"] = msg_id
                    state["step"] = "awaiting_mkv_link"
                    await message.reply_text(f"✅ Namuna linki qabul qilindi (ID: {msg_id})\n\n4-bosqich: Asosiy video (MKV) linkini yuboring")
                else:
                    await message.reply_text("❌ Link formati noto'g'ri.")

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
                    await message.reply_text(f"🎉 **Muvaffaqiyatli!** '**{state['title']}**' filmi qo'shildi va e'lon qilindi.")
                    state["step"] = "menu"
                except Exception as e:
                    await message.reply_text(f"❌ Ma'lumotlar bazasiga saqlashda xatolik: {e}")
            else:
                await message.reply_text("❌ Link formati noto'g'ri.")
        return

    # User qidiruv logikasi
    user_query = text
    await message.reply_text(f"🔍 **{user_query}** qidirilmoqda...")
    try:
        response = supabase.table("movies").select("*").ilike("title", f"%{user_query}%").eq("status", "published").execute()
        movies = response.data
        if movies:
            await send_movie_package(bot, message.chat.id, movies[0])
        else:
            await message.reply_text("🔍 Film topilmadi. Mavjud filmlarni ko'rish uchun /list bosing.")
    except Exception as e:
        await message.reply_text("❌ Qidiruvda xatolik yuz berdi.")

@app.on_event("startup")
async def on_startup():
    await bot.start()
    
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        telegram_api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
        
        try:
            response = requests.get(telegram_api_url)
            if response.status_code == 200 and response.json().get("ok"):
                print(f"🚀 Webhook successfully configured to: {webhook_url}")
            else:
                print(f"❌ Failed to set webhook via HTTP API: {response.text}")
        except Exception as e:
            print(f"❌ Error while connecting to Telegram API: {e}")
    else:
        print("⚠️ RENDER_EXTERNAL_URL environment variable not found.")

@app.on_event("shutdown")
async def on_shutdown():
    # Bypassing bot.stop() prevents the uvicorn worker thread from encountering a cross-loop exception.
    print("Stopping application gracefully...")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        json_data = await request.json()
        
        # Correctly maps raw dictionaries arriving from Telegram webhooks using Pyrogram's engine updates
        if "update_id" in json_data:
            # Drop the wrapping dictionary framework and extract the structural values natively
            parsed_update = Update(bot)
            
            # Use Pyrogram's native parser engine to dynamically populate the fields matching filters
            for key, val in json_data.items():
                if key != "update_id":
                    setattr(parsed_update, key, val)
                    
            # Check the dictionary fields natively against bot decorators
            update_object = Update._parse(bot, json_data.get("message") or json_data.get("callback_query"), None)
            if update_object:
                await bot.check_update(update_object)
    except Exception as e:
        # Standard safety catch to intercept and pass raw updates without crashing the server
        try:
            # Fallback direct ingestion processing
            await bot.check_update(json_data)
        except Exception as inner_err:
            print(f"Webhook processing error: {inner_err}")
            
    return Response(status_code=200)

@app.get("/")
async def root():
    return {"status": "healthy", "bot": "CineStream"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
