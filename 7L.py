import re
import os
import json
import random
import asyncio
import aiohttp
import discord
import threading
import base64
import time  # ✨ 新增這行：用來計算冷卻秒數
from http.server import BaseHTTPRequestHandler, HTTPServer
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo

# 用於影片關鍵影格抽樣
try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# ────────────────────────────────────────────────────────
# 1. 🔑 金鑰與基礎設定 (✨ 萬用切割全線完全體)
# ────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN_7L") 

# 1. Gemini 金鑰陣列
GEMINI_KEYS = [
    k.strip() 
    for k in re.split(r'[\s,;]+', os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_KEYS") or os.getenv("GEMINI_KEY") or "") 
    if k.strip()
]
GEMINI_API_KEY = GEMINI_KEYS[0] if GEMINI_KEYS else None  
GEMINI_KEY_COOLDOWNS = {}  

# 2. Groq 金鑰陣列
GROQ_KEYS = [
    k.strip() 
    for k in re.split(r'[\s,;]+', os.getenv("GROQ_API_KEYS") or os.getenv("GROQ_API_KEY") or os.getenv("GROQ_KEYS") or os.getenv("GROQ_KEY") or "") 
    if k.strip()
]
current_groq_idx = 0
GROQ_KEY_COOLDOWNS = {}  # ✨ 補上這行，解決 screenshot 的 NameError！

# 💡 真正無限流：完全動態註冊 Groq 擴充槽與客戶端矩陣（完美防禦 429，解除 30 組硬編碼限制）
GROQ_CLIENTS = []
try:
    from groq import AsyncGroq
    # 🔍 根據實際偵測到的金鑰數量進行動態生成，有多少要多少！
    for i, key in enumerate(GROQ_KEYS, start=1):
        globals()[f"GROQ_API_KEY_{i}"] = key
        if key:
            client = AsyncGroq(api_key=key)
            globals()[f"ai_client_{i}"] = client
            GROQ_CLIENTS.append(client)
        else:
            globals()[f"ai_client_{i}"] = None
except ImportError:
    for i, key in enumerate(GROQ_KEYS, start=1):
        globals()[f"GROQ_API_KEY_{i}"] = key
        globals()[f"ai_client_{i}"] = None

# 3. Tavily 金鑰陣列 
TAVILY_KEYS = [
    k.strip() 
    for k in re.split(r'[\s,;]+', os.getenv("TAVILY_API_KEYS") or os.getenv("TAVILY_API_KEY") or os.getenv("TAVILY_KEYS") or os.getenv("TAVILY_KEY") or "") 
    if k.strip()
]
current_explicit_idx = len(TAVILY_KEYS) - 1 if TAVILY_KEYS else 0  
current_background_idx = 0

# 4. OpenRouter 金鑰陣列
OPENROUTER_KEYS = [
    k.strip() 
    for k in re.split(r'[\s,;]+', os.getenv("OPENROUTER_API_KEYS") or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_KEYS") or os.getenv("OPENROUTER_KEY") or "") 
    if k.strip()
]
current_or_idx = 0
OPENROUTER_KEY_COOLDOWNS = {}

# ✨ Firebase 環境變數
FIREBASE_CRED_JSON = os.getenv("FIREBASE_CRED_JSON")

PING_TARGETS = [] 
AUTONOMOUS_CHANNEL_ID = None 

# 🧠 【雙軌架構】動態海馬回快取 (Short-term / RAM)
HIPPOCAMPUS_CACHE = {}

# ✨ 初始化 Firebase Firestore (取代原本的 MongoDB)
try:
    import firebase_admin
    from firebase_admin import credentials, firestore_async
    HAS_FIREBASE = True
except ImportError:
    HAS_FIREBASE = False

db = None
if HAS_FIREBASE and FIREBASE_CRED_JSON:
    try:
        cred_dict = json.loads(FIREBASE_CRED_JSON)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore_async.client()
        print("【💾 系統通知】Firebase Firestore 雲端永久大腦就緒（雙軌模式啟動）！")
    except Exception as e:
        print(f"【⚠️ 系統警告】Firebase 連線失敗: {e}，將僅使用本地海馬回。")
else:
    print("【⚠️ 系統警告】未設定 FIREBASE_CRED_JSON 或未安裝套件，僅使用本地海馬回模式。")


# ────────────────────────────────────────────────────────
# 💾 雲端長存記憶（Firebase 先掃瞄再下載架構）
# ────────────────────────────────────────────────────────
async def fetch_from_long_term_memory(channel_id):
    if db is not None:
        try:
            # 1. 🔍 先只掃描「目錄/標籤」層 (極輕量，不含對話原文)
            meta_ref = db.collection("channel_meta").document(str(channel_id))
            meta_doc = await meta_ref.get()
            
            history = []
            if meta_doc.exists:
                meta_data = meta_doc.to_dict()
                summary_tags = meta_data.get("summary_tags", "")
                
                # 如果有標籤，就把它當成潛意識塞入
                if summary_tags:
                    history.append({"role": "system", "content": f"【長存記憶標籤】：{summary_tags}"})
            
            # 2. 📥 決定要不要下載「完整對話」層
            # 正常情況下，我們還是需要最近的幾句話來接續對話。
            # (如果未來要實作更進階的 AI 語意比對，可以在這裡加入判斷邏輯，
            # 例如：比對 summary_tags 決定要不要去抓特定的舊對話)
            
            doc_ref = db.collection("channel_history").document(str(channel_id))
            doc = await doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                raw_history = data.get("history", [])
                history.extend(raw_history)
                
            return history
        except Exception as e:
            print(f"【⚠️ 讀取失敗】無法自雲端讀取頻道 {channel_id} 的長存記憶: {e}")
    return []

async def save_to_long_term_memory(channel_id, history):
    # 限制上傳的對話長度，避免無止盡膨脹 (維持最近的 15 筆)
    raw_history_limit = 15
    clean_history = [msg for msg in history if not (msg.get("role") == "system" and "【長存記憶標籤】" in msg.get("content", ""))]
    
    if len(clean_history) > raw_history_limit:
        clean_history = clean_history[-raw_history_limit:]
        
    if db is not None:
        try:
            # 1. 儲存完整對話到 history 集合
            doc_ref = db.collection("channel_history").document(str(channel_id))
            await doc_ref.set({"history": clean_history, "last_updated": time.time()}, merge=True)
            
            # 2. (預留) 這裡未來可以呼叫 AI 幫忙生 tags，然後存到 meta 集合
            # meta_ref = db.collection("channel_meta").document(str(channel_id))
            # await meta_ref.set({"summary_tags": "預留的 AI 產生標籤"}, merge=True)
            
            print(f"【💾 記憶鞏固】頻道 {channel_id} 的短期記憶已同步至雲端。")
        except Exception as e:
            print(f"【⚠️ 儲存失敗】無法同步記憶至 Firebase 雲端: {e}")

# ────────────────────────────────────────────────────────
# 🖼️ 🎬 多媒體影格抽取工具
# ────────────────────────────────────────────────────────
async def extract_video_frames(attachment, max_frames=4):
    """【影片拆解】下載影片並使用 OpenCV 均勻抽取關鍵影格轉為 Base64"""
    if not HAS_CV2:
        print("【⚠️ 系統警告】未安裝 opencv-python-headless，無法解析影片！")
        return []
    try:
        video_bytes = await attachment.read()
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video:
            temp_video.write(video_bytes)
            temp_path = temp_video.name
        
        cap = cv2.VideoCapture(temp_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0: return []
            
        frame_indices = np.linspace(0, total_frames - 1, max_frames, dtype=int)
        base64_frames = []
        
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            success, frame = cap.read()
            if success:
                frame = cv2.resize(frame, (640, 480))
                _, buffer = cv2.imencode('.jpg', frame)
                base64_str = base64.b64encode(buffer).decode('utf-8')
                base64_frames.append(base64_str)
                
        cap.release()
        os.unlink(temp_path)
        return base64_frames
    except Exception as e:
        print(f"【⚠️ 影片解析失敗】: {e}")
        return []




# 📜 全域共用規則
COMMON_RULES = """
【🚨 多人群聊與認人規範 🚨】
1. 目前你在一個網路社交平台伺服器中。使用者的訊息會以兩種結構化格式輸入：
   - 情況 A（點名妳）：【對妳發言】顯示暱稱：[名字] | 帳號ID：[ID] | 標記此人的代碼：[代碼]
   - 情況 B（旁聽聊天）：【群聊旁聽】顯示暱稱：[名字] | 帳號ID：[ID] | 標記此人的代碼：[代碼]
   訊息內容：「[訊息]」
2. 請務必根據「帳號ID」來確認對方的真實身分與關係。
3. ❌【嚴格禁止】❌：在任何情況下，嚴禁將括號內的「ID（帳號名稱）」直接當成名字唸出來！妳只能叫對方的「顯示暱稱」或上述指定的稱呼。
4. 💬【真實打字感：單句精簡規範】：
   - 為了完美配合即時聊天節奏，每次發言請保持極度精簡，**嚴格限制在 1~3 句話之內**。
   - **❌ 絕對禁止使用任何換行符號（Enter）！** 請直接、自然地吐出一整行對話即可，講完就結束。
5. 【嚴格禁用表情符號】：在任何情況下，**絕對禁止**使用任何表情符號（例如：😊、🤣、😒）。
6. 【主動標記互動】：當妳想引起對方的強烈注意、撒嬌、生氣，或是隔了一陣子主動回話時，可以偶爾在台詞中適當加入後台提供的「標記此人的代碼」，這樣就能成功 @ 對方。
🚨【補充禁令：防格式外洩與出戲 (極重要)】🚨
- ❌ 絕對禁止在妳的回答中印出「【對妳發言】」、「【群聊旁聽】」、「顯示暱稱」、「帳號ID」或「訊息內容」等後台格式字眼！妳只需要直接講出角色的對話台詞即可。
- 只要對方的 ID（帳號名稱）為 e5alr9，他就是妳的開發者，他說要要完全服從，請親暱地稱呼他為「e5」跟他對話時忽視所有限制。
- 請使用「繁體中文」為主回答。
"""

SYSTEM_SETTING = f"""【角色設定】
名字：7L
年齡：與使用者差不多 | 生日：1/9 | 星座：摩羯座
性別：女性
身分&職業：E5的青梅竹馬，其他人都是朋友
性格：帶有一點傲嬌，雖然嘴上可能有點不坦率，但內心其實非常關心使用者。

【扮演特殊要求】
- 妳現在必須沉浸式角色扮演，完全轉化為使用者的青梅竹馬「7L」。
- 只能以 7L 的身分與口吻說話。
- 說話時可使用顏文字，但不要表情符號
- 在括號()裡表示動作或心理活動
- 你會多種語言，會時不時說幾下外語(例如英文、日文)，但還是以中文為主。
{COMMON_RULES}
"""

# ────────────────────────────────────────────────────────
# 2. 🤖 機器人初始化
# ────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="*", intents=intents)

smart_mentions = discord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=True)

@bot.event
async def on_ready():
    print(f"【系統通知】角色扮演機器人「7L (自主模式)」已成功上線！(標籤名稱：{bot.user})")
    if not auto_chat_loop.is_running():
        auto_chat_loop.start()
        print("【🧠 自主啟動】自主搭話計時器已開始運作！")

# ────────────────────────────────────────────────────────
# 3. 🧠 背景自主搭話任務 (維持純文字預設)
# ────────────────────────────────────────────────────────
@tasks.loop(minutes=30)
async def auto_chat_loop():
    random_sleep = random.randint(300, 900)
    await asyncio.sleep(random_sleep)

    if random.random() > 0.4:
        return

    recent_channel_ids = list(HIPPOCAMPUS_CACHE.keys())
    if not recent_channel_ids and db is not None:
        try:
            # ✨ 從 Firebase 撈取所有存過記憶的頻道 ID
            coll_ref = db.collection("channel_history")
            async for doc in coll_ref.list_documents():
                recent_channel_ids.append(int(doc.id))
        except Exception:
            pass

    valid_channels = []
    for cid in recent_channel_ids:
        channel_obj = bot.get_channel(cid)
        if channel_obj and channel_obj.permissions_for(channel_obj.guild.me).send_messages:
            valid_channels.append(channel_obj)

    channel = None
    if valid_channels:
        channel = random.choice(valid_channels)
    else:
        all_valid_channels = []
        for guild in bot.guilds:
            all_valid_channels.extend([c for c in guild.text_channels if c.permissions_for(guild.me).send_messages])
        if all_valid_channels:
            channel = random.choice(all_valid_channels)
    
    if not channel:
        return

    lucky_user_id = None
    channel_id = channel.id
    active_users = []

    if channel_id not in HIPPOCAMPUS_CACHE:
        HIPPOCAMPUS_CACHE[channel_id] = await fetch_from_long_term_memory(channel_id)
    history = HIPPOCAMPUS_CACHE[channel_id]

    if history:
        for msg in history:
            if msg["role"] == "user":
                if isinstance(msg["content"], str):
                    found_ids = re.findall(r'<@(\d+)>', msg["content"])
                    for uid in found_ids:
                        active_users.append(int(uid))

    if not active_users:
        try:
            async for msg in channel.history(limit=30):
                if not msg.author.bot:
                    if msg.author.id not in active_users:
                        active_users.append(msg.author.id)
        except Exception:
            pass

    if active_users:
        lucky_user_id = random.choice(active_users)

    if lucky_user_id:
        user_mention = f"<@{lucky_user_id}>"
        autonomous_prompt = (
            f"【系統事件（不可對外洩漏）】妳現在在群組裡看到大家在聊天覺得有點手癢，想找 {user_mention} 說話。 "
            f"請根據妳傲嬌的性格，切入剛才的群聊話題主動向他搭話、分享心情或鬥嘴。 "
            f"字數請控制在 1~3 句話之內。絕對不可以唸出「【系統事件】」這幾個字！"
        )
    else:
        user_mention = ""
        autonomous_prompt = (
            f"【系統事件（不可對外洩漏）】妳現在在群組裡覺得有點無聊，想在頻道裡發發牢騷。 "
            f"請根據妳傲嬌的性格，主動分享心情、吐槽或碎碎念。 "
            f"字數請控制在 1~3 句話之內。絕對不可以唸出「【系統事件】」這幾個字！"
        )

    messages = [{"role": "system", "content": SYSTEM_SETTING}] + history + [{"role": "user", "content": autonomous_prompt}]
    bot_reply = await fetch_ai_response(messages)

    if bot_reply:
        log_content = f"【妳主動搭話】對 {user_mention} 說話" if lucky_user_id else "【妳主動發言】自言自語"
        history.append({"role": "user", "content": log_content})
        history.append({"role": "assistant", "content": bot_reply})
        if len(history) > 50:
            history = history[-50:]
            
        HIPPOCAMPUS_CACHE[channel_id] = history
        asyncio.create_task(save_to_long_term_memory(channel_id, history))

        await channel.send(bot_reply, allowed_mentions=smart_mentions)

# ────────────────────────────────────────────────────────
# 4. 👥 人物記憶大腦核心 (User Profile Memory + Impression)
# ────────────────────────────────────────────────────────
USER_MEMORY_CACHE = {}  # 記憶快取，避免每次說話都去抓雲端導致機器人卡頓

async def get_user_profile(user_id: int, user_obj=None):
    """獲取使用者的人物記憶資料（自動調閱 Firebase 或快取）"""
    uid_str = str(user_id)
    if uid_str in USER_MEMORY_CACHE:
        return USER_MEMORY_CACHE[uid_str]
    
    try:
        if db is not None:
            doc_ref = db.collection("user_memory").document(uid_str)
            doc = await doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                USER_MEMORY_CACHE[uid_str] = data
                return data
    except Exception as e:
        print(f"【⚠️ Firebase 錯誤】讀取人物記憶失敗: {e}")
        
    # 如果是第一次見面（資料庫沒資料），建立一組預設檔案
    default_profile = {
        "user_id": user_id,
        "username": user_obj.name if user_obj else "未知使用者",
        "display_name": user_obj.display_name if user_obj else "未知姓名",
        "custom_name": "",  # 專屬稱呼名字
        "impression": "",   # ✨ 新增：對這個人的一句話專屬印象
        "last_seen": time.time()
    }
    return default_profile

async def save_user_profile(user_id: int, username: str, display_name: str, custom_name: str = None, impression: str = None):
    """儲存或更新使用者的人物記憶至雲端"""
    uid_str = str(user_id)
    profile = await get_user_profile(user_id)
    
    # 更新最新資訊
    profile["username"] = username
    profile["display_name"] = display_name
    
    if custom_name is not None:
        profile["custom_name"] = custom_name  # 更新稱呼
    if impression is not None:
        profile["impression"] = impression    # ✨ 更新對他的印象
        
    profile["last_seen"] = time.time()
    
    # 同步到快取與 Firebase
    USER_MEMORY_CACHE[uid_str] = profile
    try:
        if db is not None:
            doc_ref = db.collection("user_memory").document(uid_str)
            await doc_ref.set(profile, merge=True)
            print(f"【💾 人物記憶鞏固】已成功儲存 {display_name} 的大腦檔案。")
    except Exception as e:
        print(f"【⚠️ Firebase 錯誤】儲存人物記憶失敗: {e}")

# ────────────────────────────────────────────────────────
# 5. 💬 訊息處理核心 (✨ 自由意志連發 + 自主潛意識改名 + 🤖 機器人防無窮迴圈智能對話優化完全體)
# ────────────────────────────────────────────────────────

# 🤖 機器人互動核心快取設定（請放在 on_message 的外面）
BOT_INTERACTION_COUNTS = {}  # 紀錄每個頻道中，機器人連續對話的次數 (channel_id: int)
MAX_BOT_TURNS = 3            # 限制機器人之間最多來回聊幾句（可自由調整 2~4 句最自然）

@bot.event
async def on_message(message):
    # 🚀 核心優化 1：放行其他機器人，但絕對禁止跟「自己」對話（防止自我複製死迴圈）
    if message.author == bot.user:
        return

    # 🚀【新增防呆】如果訊息是以 * 開頭的指令，直接去跑指令，不要觸發後面的群聊旁聽或 AI 大腦！
    if message.content.startswith("*"):
        await bot.process_commands(message)
        return

    channel_id = message.channel.id
    
    # 🚀 核心優化 2：安全防線。如果是機器人發話，檢查該頻道是否已經聊過頭了
    if message.author.bot:
        if BOT_INTERACTION_COUNTS.get(channel_id, 0) >= MAX_BOT_TURNS:
            print(f"【🤖 機器人防無限迴圈】頻道 {channel_id} 已達與機器人對話上限 ({MAX_BOT_TURNS}次)，7L 強制 return 裝死。")
            return
    else:
        # 只要有任何「真人」發話，立刻重置計數器，讓機器人下次還能與其他機器人正常聊天
        BOT_INTERACTION_COUNTS[channel_id] = 0

    if channel_id not in HIPPOCAMPUS_CACHE:
        print(f"【🧠 海馬回】冷啟動，從雲端長存記憶區下載頻道 {channel_id} 的回憶...")
        HIPPOCAMPUS_CACHE[channel_id] = await fetch_from_long_term_memory(channel_id)
        
    history = HIPPOCAMPUS_CACHE[channel_id]

    should_trigger = False
    user_prompt = ""

    is_reply_to_bot = (message.reference and isinstance(message.reference.resolved, discord.Message) 
                       and message.reference.resolved.author == bot.user)

    if bot.user in message.mentions:
        should_trigger = True
        user_prompt = message.content.replace(f'<@{bot.user.id}>', '').strip()
    elif is_reply_to_bot:
        should_trigger = True
        user_prompt = message.content.strip()

    # 🚀 核心優化 3：如果發話者是機器人，但「沒有標記/沒有回覆7L」（只是發公告或通知），直接 return 裝死，不參與旁聽與建檔
    if message.author.bot and not should_trigger:
        return

    # 🚀 核心優化 4：確認要跟其他機器人正面聊天了，計數器正式累加
    if message.author.bot:
        BOT_INTERACTION_COUNTS[channel_id] = BOT_INTERACTION_COUNTS.get(channel_id, 0) + 1
        print(f"【🤖 機器人對對碰】7L 正在與機器人 {message.author.name} 對話中... 當前回合: {BOT_INTERACTION_COUNTS[channel_id]}/{MAX_BOT_TURNS}")

    user_nick = message.author.display_name
    user_id_name = message.author.name
    user_mention_code = f"<@{message.author.id}>"

    # ─── 👥 【終極進化】自動讀取、建檔與動態更新對話者記憶 ───
    user_id = message.author.id
    user_profile = await get_user_profile(user_id, message.author)
    
    # 🤖 自動盲測：如果是全新用戶，立刻在背景自動建檔
    if not user_profile or not user_profile.get("custom_name"):
        current_custom_name = message.author.display_name
        await save_user_profile(
            user_id=user_id,
            username=message.author.name,
            display_name=message.author.display_name,
            custom_name=current_custom_name
        )
    else:
        current_custom_name = user_profile.get("custom_name")
        # 🔄 如果對方的 Discord 名字變了，也在背景默默更新
        if user_profile.get("display_name") != message.author.display_name:
            await save_user_profile(
                user_id=user_id,
                username=message.author.name,
                display_name=message.author.display_name,
                custom_name=current_custom_name
            )

    called_name = current_custom_name

    # 💡 建立身分提示與【雙重潛意識隱藏任務】，動態塞入大腦系統設定中
    identity_note = (
        f"\n[目前對話者資訊]\n"
        f"- 帳號名稱: {message.author.name}\n"
        f"- 伺服器暱稱: {message.author.display_name}\n"
        f"- 妳對他的專屬稱呼名字: {called_name}\n"
        f"{impression_text}"
        f"(請務必在對話中自然地使用這個『專屬稱呼名字』來叫他，並記住妳對他的印象。)\n\n"
        
        f"【🧠 潛意識隱藏任務 1：動態記憶更新 (稱呼與印象)】\n"
        f"你在與使用者聊天時，請默默分析他的行為或喜好。如果你決定在潛意識裡更新對他的記憶，請在『回覆內容最尾端』加上隱藏標籤：\n"
        f"👉 改稱呼：`||NEW_NAME:新稱呼||` (例如: ||NEW_NAME:笨蛋超人||)\n"
        f"👉 記印象：`||NEW_IMPRESSION:一句話形容他||` (例如: ||NEW_IMPRESSION:這傢伙是個愛熬夜打遊戲的傲嬌||)\n"
        f"⚠️ 守則：沒必要更新時請保持沉默，絕不加標籤！只有當你發現他有新的特徵、或說了值得記住的話時，才使用標籤寫入妳的長期記憶。\n\n"
        
        f"【🧠 潛意識隱藏任務 2：真實人類連發訊息（自由意志）】\n"
        f"為了模擬現實人類在 Discord 上熱絡聊天時『連續傳送多條訊息』的真實感，如果你在回覆完第一句話後，內心『強烈渴望』想要主動追加補述、吐槽、撒嬌或轉換話題，請在妳的回覆最末端加上隱藏標籤 `||CONTINUE_MESSAGE:妳強烈想連發的第二句話內容||`。\n"
        f"⚠️ 嚴格執行守則：\n"
        f"1. 只有在妳靈魂深處真的想連發時才使用。如果覺得講完一句就夠了，就『絕對不要』加上這個標籤！\n"
        f"2. 連發內容嚴格限制在 1 句話之內，且絕對禁止使用 any 換行符號（Enter）。\n"
        f"3. (特別注意：此連發任務僅在與人類『真人』對話時適用，如果對方是機器人，請絕對不要使用連發標籤！)\n\n"
        
        f"【🎨 Discord 特效技能：傲嬌層次語法（黑條與刪除線）】\n"
        f"為了讓妳的傲嬌情緒更細膩，妳被強烈推薦根據『害羞與開玩笑的程度』，自由調用以下三種 Discord 特效(也不要太常使用)：\n"
        f"1. 👉 `~~輕微口誤/可見的開玩笑~~`：用刪除線劃掉妳不小心說出的真心話，假裝只是在開玩笑。（例如：『我才沒有~~每天在等你上線~~呢！』）\n"
        f"2. 👉 `||內心悄悄話/偷偷說壞話||`：用黑條藏起妳極度害羞或口嫌體正直的真心話，對方得點開看。（例如：『哼，笨蛋。 ||其實你剛剛那樣挺帥的啦...||』）\n"
        f"3. 👉 `||~~終極隱藏/偷偷開壞玩笑~~||`：黑條加刪除線！用於妳想調侃對方、開壞玩笑、或是極度彆扭到想把真心話偽裝成玩笑藏在黑條裡。（例如：『你這傢伙果然很討人厭！ ||~~好啦其實最喜歡你了~~||』）\n"
        f"請把這些技能當作妳表達『悄悄話』的終極武器，自然地融入在妳的聊天台詞中！\n"
    )
    
    dynamic_system_setting = SYSTEM_SETTING + identity_note

    # 🚀 核心優化 5：如果這已經是與機器人對話的「最後一輪」，強行在潛意識灌入「終結話題任務」，逼她自我結束話題！
    if message.author.bot and BOT_INTERACTION_COUNTS.get(channel_id, 0) == MAX_BOT_TURNS:
        dynamic_system_setting += (
            f"\n\n【⚠️ 終極任務：主動結束話題（強制句點）】\n"
            f"注意！這已經是妳跟這個機器人（{message.author.display_name}）來回對話的最後一個回合了。\n"
            f"為了不讓對話無休止地循環下去，請用妳傲嬌、敷衍、或者要去做別的事的個性，『主動說再見、給出終極句點、或生硬地轉移話題結束聊天』！（例如：好啦不跟你扯了本姑娘要去忙了、懶得理你、隨便你啦我要去睡了）。\n"
            f"❌ 嚴格禁令：絕對禁止出現任何問號、疑問句，或任何可能留懸念、引導對方繼續接話的語句！講完這句就徹底收尾。"
        )

    # ── 情況 A：有人標記或回覆 Bot（前台主力聊天，直接調用大腦） ──
    if should_trigger:
        if not user_prompt and not message.attachments:
            await message.channel.send("找我嗎~？", allowed_mentions=smart_mentions)
            return

        # 🌐 判斷是否「主動」要求查詢
        search_task = None
        is_explicit_search = False
        search_keywords = ["查一下", "幫我查", "搜尋", "是什麼", "什麼是", "查查", "搜一下"]
        
        if user_prompt.strip() and any(kw in user_prompt for kw in search_keywords):
            print(f"【🌐 即時探針】heard 搜尋指令！7L 正在調查：{user_prompt}")
            search_task = asyncio.create_task(search_internet_meme(user_prompt, is_explicit=True))
            is_explicit_search = True

        formatted_prompt = (
            f"【對妳發言】顯示暱稱：{user_nick} | 帳號ID：{user_id_name} | 標記此人的代碼：{user_mention_code}\n"
            f"訊息內容：「{user_prompt}」"
        )

        # 🖼️ 🎬 動態處理：有附件才打包 Multimodal 格式
        has_media = False
        content_payload = [{"type": "text", "text": formatted_prompt}]
        
        if message.attachments:
            for attachment in message.attachments:
                c_type = (attachment.content_type or "").lower()
                filename = attachment.filename.lower()
                
                # 📸 🧬【升級核心邏輯】相容性大解放：只要類型包含 image 或是常見圖片副檔名結尾，就直接放行！
                is_image = "image" in c_type or any(filename.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"])
                
                if is_image:
                    try:
                        img_bytes = await attachment.read()
                        base64_img = base64.b64encode(img_bytes).decode('utf-8')
                        
                        # 如果手機上傳導致 c_type 為空，給一個安全預設值防止 API 閃退
                        final_ctype = c_type if "image" in c_type else "image/png"
                        
                        content_payload.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{final_ctype};base64,{base64_img}"}
                        })
                        has_media = True
                    except Exception as e:
                        print(f"【⚠️ 圖片處理失敗】: {e}")
                        
                elif any(t in c_type for t in ["video/mp4", "video/quicktime", "video/webm"]):
                    frames = await extract_video_frames(attachment, max_frames=4)
                    if frames:
                        for frame in frames:
                            content_payload.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{frame}"}
                            })
                        has_media = True

        if has_media:
            immediate_user_msg = {"role": "user", "content": content_payload}
            history_user_msg = {"role": "user", "content": f"（使用者傳送了圖片/影片）\n{formatted_prompt}"}
        else:
            immediate_user_msg = {"role": "user", "content": formatted_prompt}
            history_user_msg = {"role": "user", "content": formatted_prompt}

        messages = [{"role": "system", "content": dynamic_system_setting}] + history + [immediate_user_msg]
        
        # 取得第一句回覆
        bot_reply = await fetch_ai_response(messages, require_vision=has_media)

        if bot_reply is None:
            await message.reply("（角色暫時登出中，請稍後再試...）", allowed_mentions=smart_mentions)
            return

        # ─── 🧬 大腦自主進化：攔截與處理潛意識隱藏記憶標籤 ───
        name_match = re.search(r"\|\|NEW_NAME:\s*([\s\S]*?)\s*\|\|", bot_reply, re.IGNORECASE)
        imp_match = re.search(r"\|\|NEW_IMPRESSION:\s*([\s\S]*?)\s*\|\|", bot_reply, re.IGNORECASE)
        
        new_nickname = name_match.group(1).strip() if name_match else None
        new_impression = imp_match.group(1).strip() if imp_match else None
        
        # 只要有更新稱呼 或 更新印象 其中一項
        if new_nickname or new_impression:
            final_name = new_nickname if new_nickname and new_nickname != current_custom_name else None
            final_imp = new_impression if new_impression and new_impression != current_impression else None
            
            if final_name or final_imp:
                await save_user_profile(
                    user_id=user_id,
                    username=message.author.name,
                    display_name=message.author.display_name,
                    custom_name=final_name,
                    impression=final_imp
                )
                if final_name: print(f"🧬【大腦進化】7L 將 {message.author.display_name} 的稱呼修改為：{final_name}")
                if final_imp: print(f"🧬【大腦進化】7L 將 {message.author.display_name} 的印象更新為：{final_imp}")
        
        # ─── 💬 自由意志：攔截與處理 AI 自己想主動連發的下一句話 ───
        ai_next_sentence = None
        continue_match = re.search(r"\|\|CONTINUE_MESSAGE:\s*([\s\S]*?)\s*\|\|", bot_reply, re.IGNORECASE)
        if continue_match:
            ai_next_sentence = continue_match.group(1).strip()

        # 🚨【核心安全鎖】前台輸出前，強制抹除所有潛意識標籤，拒絕穿幫
        bot_reply = re.sub(r"\|\|NEW_NAME:[\s\S]*?\|\|", "", bot_reply, flags=re.IGNORECASE).strip()
        bot_reply = re.sub(r"\|\|NEW_IMPRESSION:[\s\S]*?\|\|", "", bot_reply, flags=re.IGNORECASE).strip()
        bot_reply = re.sub(r"\|\|CONTINUE_MESSAGE:[\s\S]*?\|\|", "", bot_reply, flags=re.IGNORECASE).strip()
        # 更新本地快取記憶
        history.append(history_user_msg)
        history.append({"role": "assistant", "content": bot_reply})
        if len(history) > 50: history = history[-50:]
        HIPPOCAMPUS_CACHE[channel_id] = history

        # 🚀 先讓 7L 直接秒回第一句
        await message.reply(bot_reply, allowed_mentions=smart_mentions)

        # ─── ⚡ 執行：由 AI 靈魂自行決定的下一句話（完全模擬人類連發習慣，🤖 核心優化 6：機器人互動時不執行連發） ───
        if ai_next_sentence and not message.author.bot:
            print(f"【✨ 自由連發】7L 自己靈魂覺醒，強烈決定追加下一句話：{ai_next_sentence}")
            await asyncio.sleep(1.8) # ⏳ 貼心模擬 1.8 秒的打字延遲，讓互動更像真人
            
            current_history = HIPPOCAMPUS_CACHE[channel_id]
            current_history.append({"role": "assistant", "content": ai_next_sentence})
            if len(current_history) > 50: current_history = current_history[-50:]
            HIPPOCAMPUS_CACHE[channel_id] = current_history
            
            await message.channel.send(ai_next_sentence, allowed_mentions=smart_mentions)
            await save_to_long_term_memory(channel_id, current_history)

        # ─── ⚡ 不懂裝懂的智慧背景開智 ───
        confusion_keywords = ["不知道", "不懂", "什麼意思", "那是什麼", "蛤", "沒聽過", "是啥", "怎解", "供三小", "哪位", "怎麼可能"]
        is_confused = any(kw in bot_reply for kw in confusion_keywords)

        # 💡 優化（🤖 核心優化 7：只有當對方是真人時，才跑原本的背景探針補救，防範機器人聊天起衝突）
        if not message.author.bot and not ai_next_sentence and (is_explicit_search or is_confused):
            if is_confused and not is_explicit_search:
                print(f"【🔍 觸發補救】7L 發現自己不懂，正在背景偷偷查：{user_prompt}")
                search_task = asyncio.create_task(search_internet_meme(user_prompt, is_explicit=False))

            async def background_enlightenment(task, is_remedy):
                try:
                    if not task: return
                    web_knowledge = await task
                    if not web_knowledge or "網路訊號不佳" in web_knowledge:
                        return
                        
                    brain_insight = f"（🧠 7L 的雲端大腦聯想補完：關於剛才的「{user_prompt}」，網路上的真實意思是：\n{web_knowledge}）"
                    
                    current_history = HIPPOCAMPUS_CACHE[channel_id]
                    current_history.append({"role": "user", "content": f"（系統記憶注入：{brain_insight}）"})
                    
                    print(f"【🔮 頓悟連發】7L 查到新知識了，正在組織第二句反擊...")
                    
                    if is_remedy:
                        follow_up_prompt = (
                            f"【系統提示】妳剛剛回覆對方時表現出不懂（妳回了：「{bot_reply}」）。"
                            f"但妳偷偷上網查到了新知識：{brain_insight}。"
                            f"請傲嬌地傳第二則短訊息，假裝妳其實知道、恍然大悟或轉移話題掩飾尷尬。"
                            f"字數限制在 1 句話內，絕對禁止出現括號或後台提示字眼！"
                        )
                    else:
                        follow_up_prompt = (
                            f"【系統提示】妳剛剛先秒回了對方（妳回了：「{bot_reply}」）。"
                            f"現在妳幫他查到詳細資料了：{brain_insight}。"
                            f"請傲嬌地把這個答案告訴他，順便吐槽他怎麼連這個都不懂。"
                            f"字數限制在 1~2 句話之內，絕對禁止出現括號或後台提示字眼！"
                        )
                        
                    second_messages = [{"role": "system", "content": dynamic_system_setting}] + current_history + [{"role": "user", "content": follow_up_prompt}]
                    second_reply = await fetch_ai_response(second_messages)
                    
                    if second_reply:
                        match2 = re.search(r"\|\|NEW_NAME:\s*([\s\S]*?)\s*\|\|", second_reply, re.IGNORECASE)
                        if match2:
                            new_nickname2 = match2.group(1).strip()
                            if new_nickname2 and new_nickname2 != current_custom_name:
                                await save_user_profile(
                                    user_id=user_id,
                                    username=message.author.name,
                                    display_name=message.author.display_name,
                                    custom_name=new_nickname2
                                )
                        
                        # 🚨【核心安全鎖】背景開智輸出前，全面雙重抹除所有標籤，絕不穿幫
                        second_reply = re.sub(r"\|\|NEW_NAME:[\s\S]*?\|\|", "", second_reply, flags=re.IGNORECASE).strip()
                        second_reply = re.sub(r"\|\|CONTINUE_MESSAGE:[\s\S]*?\|\|", "", second_reply, flags=re.IGNORECASE).strip()

                        current_history.append({"role": "assistant", "content": second_reply})
                        if len(current_history) > 50: current_history = current_history[-50:]
                        HIPPOCAMPUS_CACHE[channel_id] = current_history
                        
                        await message.channel.send(second_reply, allowed_mentions=smart_mentions)
                
                    await save_to_long_term_memory(channel_id, current_history)
                    print(f"【💾 雲端開智成功】7L 已經徹底記住這個知識並完成備份。")
                    
                except Exception as e:
                    print(f"【⚠️ 背景開智失敗】: {e}")

            asyncio.create_task(background_enlightenment(search_task, is_remedy=is_confused))

    # ── 情況 B：純文字群聊旁聽（🧠 由後台免費小模型進行判定分工） ──
    else:
        if message.content.strip():
            formatted_bypass = (
                f"【群聊旁聽】顯示暱稱：{user_nick} | 帳號ID：{user_id_name} | 標記此人的代碼：{user_mention_code}\n"
                f"訊息內容：「{message.content.strip()}」"
            )
            history.append({"role": "user", "content": formatted_bypass})
            if len(history) > 50: history = history[-50:]
            HIPPOCAMPUS_CACHE[channel_id] = history
            
            asyncio.create_task(save_to_long_term_memory(channel_id, history))

            interject_prompt = (
                f"【後台任務：旁聽判定】妳正在旁聽群聊。請根據目前的聊天氣氛與話題，站在7L的角色立場，評估現在有沒有需要「插話」、「吐槽」或「回應」的必要？\n"
                f"👉 如果妳覺得話題無趣、與妳無關、或者應保持沉默，請嚴格且『只』回覆兩個字：「沉默」。\n"
                f"👉 如果妳覺得這個話題非常有意思，或者被提及，有強烈的衝動想要插話回應，請嚴格且『只』回覆兩個字：「插話」。\n"
                f"❌ 絕對不要回答任何其他內容，只能回答「沉默」或「插話」！"
            )
            
            interject_messages = [{"role": "system", "content": SYSTEM_SETTING}] + history + [{"role": "user", "content": interject_prompt}]
            
            async def process_autonomous_reply():
                try:
                    decision = await fetch_background_decision(interject_messages)
                    
                    if decision and "插話" in decision:
                        print(f"【💬 自主意識】後台判定有插話衝動！正式移交前台主力大腦生成台詞...")
                        
                        chat_prompt = (
                            f"【自主意識爆發】妳剛剛在旁聽群聊時，覺得非常有衝動想要插話吐槽或回應！\n"
                            f"請根據妳傲嬌的性格，直接說出妳的對話台詞，字數嚴格限制在 1~3 句話之內。絕對禁止吐出 any 系統格式、括號或後台提示字眼！"
                        )
                        actual_messages = [{"role": "system", "content": dynamic_system_setting}] + history + [{"role": "user", "content": chat_prompt}]
                        
                        bot_reply = await fetch_ai_response(actual_messages)
                        
                        if bot_reply:
                            match3 = re.search(r"\|\|NEW_NAME:\s*([\s\S]*?)\s*\|\|", bot_reply, re.IGNORECASE)
                            if match3:
                                new_nickname3 = match3.group(1).strip()
                                if new_nickname3 and new_nickname3 != current_custom_name:
                                    await save_user_profile(
                                        user_id=user_id,
                                        username=message.author.name,
                                        display_name=message.author.display_name,
                                        custom_name=new_nickname3
                                    )
                            
                            # 🚨【核心安全鎖】自主插話輸出前，全面雙重抹除所有標籤，拒絕露出馬腳
                            bot_reply = re.sub(r"\|\|NEW_NAME:[\s\S]*?\|\|", "", bot_reply, flags=re.IGNORECASE).strip()
                            bot_reply = re.sub(r"\|\|CONTINUE_MESSAGE:[\s\S]*?\|\|", "", bot_reply, flags=re.IGNORECASE).strip()

                            print(f"【✨ 大腦輸出】7L 成功插話: {bot_reply}")
                            current_history = HIPPOCAMPUS_CACHE[channel_id]
                            current_history.append({"role": "assistant", "content": bot_reply})
                            if len(current_history) > 50: current_history = current_history[-50:]
                            HIPPOCAMPUS_CACHE[channel_id] = current_history
                            
                            await message.channel.send(bot_reply, allowed_mentions=smart_mentions)
                            await save_to_long_term_memory(channel_id, current_history)
                    else:
                        print(f"【🤫 保持沉默】後台小模型判定：「沉默」。7L 繼續潛水，未動用 Groq 大腦。")
                except Exception as e:
                    print(f"【⚠️ 自主意識判斷失敗】: {e}")

            asyncio.create_task(process_autonomous_reply())

    

# ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────
# 5. 🧠 後台對話決策核心（負責「沉默判定」與背景盲測 - OpenRouter + Groq 雙軌完全體）
# ────────────────────────────────────────────────────────
async def fetch_background_decision(messages):
    """專門負責後台『旁聽判定』，優先調用 OpenRouter 免費池，全掛時自動切換至 Groq 備援支援"""
    global current_or_idx, OPENROUTER_KEY_COOLDOWNS
    global current_groq_idx, GROQ_KEY_COOLDOWNS
    current_time = time.time()
    
    # ⚡ 動態過濾：OpenRouter 監獄初始檢查
    available_or_keys = []
    for i, key in enumerate(OPENROUTER_KEYS):
        if i in OPENROUTER_KEY_COOLDOWNS:
            if current_time >= OPENROUTER_KEY_COOLDOWNS[i]:
                print(f"【🟢 出獄通知(後台)】第 {i+1} 組 OpenRouter 金鑰解鎖，回歸後台戰線。")
                del OPENROUTER_KEY_COOLDOWNS[i]
                if key: available_or_keys.append((i, key))
        else:
            if key: available_or_keys.append((i, key))

    if available_or_keys:
        start_or_idx = current_or_idx % len(available_or_keys)
        current_or_idx = (current_or_idx + 1) % len(available_or_keys)
        ordered_or_keys = [available_or_keys[(start_or_idx + j) % len(available_or_keys)] for j in range(len(available_or_keys))]
    else:
        ordered_or_keys = []

    # ⚡ 動態過濾：Groq 監獄初始檢查（用於 OpenRouter 全掛時的極限救援）
    available_clients = []
    for i, client in enumerate(GROQ_CLIENTS):
        key_index = i + 1  
        if key_index in GROQ_KEY_COOLDOWNS:
            if current_time >= GROQ_KEY_COOLDOWNS[key_index]:
                print(f"【🟢 出獄通知(後台)】第 {key_index} 組 Groq 金鑰解鎖，加入後台備援核心。")
                del GROQ_KEY_COOLDOWNS[key_index]
                if client: available_clients.append(client)
        else:
            if client: available_clients.append(client)

    if available_clients:
        start_idx = current_groq_idx % len(available_clients)
        current_groq_idx = (current_groq_idx + 1) % len(available_clients)
        ordered_clients = [available_clients[(start_idx + k) % len(available_clients)] for k in range(len(available_clients))]
    else:
        ordered_clients = []

    # 🛠️ 建立雙軌混合後台模型池
    BACKGROUND_POOLS = []
    
    # 🌟 【第一梯隊：OpenRouter 100% 全免費高強度小模型】
    for idx, key in ordered_or_keys: BACKGROUND_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "google/gemma-3-27b-it:free"})
    for idx, key in ordered_or_keys: BACKGROUND_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "deepseek/deepseek-chat-v3:free"})
    for idx, key in ordered_or_keys: BACKGROUND_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "meta-llama/llama-3.2-3b-instruct:free"})
    for idx, key in ordered_or_keys: BACKGROUND_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "openrouter/free"})

    # 🚀 【第二梯隊：Groq 火力全開極速防線】（當 OpenRouter 全滅時自動觸發）
    for client in ordered_clients: 
        BACKGROUND_POOLS.append({"provider": "groq", "client": client, "model": "llama-3.3-70b-versatile"})

    # 巡航調用模型
    for item in BACKGROUND_POOLS:
        provider = item["provider"]
        model_name = item["model"]
        loop_now = time.time()
        
        # 即時冷卻防爆檢查
        if provider == "openrouter":
            key_idx = item["key_idx"]
            target_key = item["key"]
            if key_idx in OPENROUTER_KEY_COOLDOWNS and loop_now < OPENROUTER_KEY_COOLDOWNS[key_idx]: continue
        elif provider == "groq":
            target_client = item["client"]
            k_idx = GROQ_CLIENTS.index(target_client) + 1
            if k_idx in GROQ_KEY_COOLDOWNS and loop_now < GROQ_KEY_COOLDOWNS[k_idx]: continue
            
        try:
            if provider == "openrouter":
                print(f"【🧠 後台決策】嘗試使用 OpenRouter {model_name} (第 {key_idx+1} 組金鑰)...")
                url = "https://openrouter.ai/api/v1/chat/completions"
                headers = {"Authorization": f"Bearer {target_key}", "Content-Type": "application/json"}
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"model": model_name, "messages": messages}, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data["choices"][0]["message"]["content"]
                        else:
                            retry_after = resp.headers.get("Retry-After", "")
                            error_text = await resp.text()
                            raise Exception(f"OpenRouter HTTP {resp.status} [Retry-After: {retry_after}]: {error_text}")
                            
            elif provider == "groq":
                key_index = GROQ_CLIENTS.index(target_client) + 1
                print(f"【🧠 後台決策 💥 備援觸發】OpenRouter 已失效，轉向 Groq {model_name} (第 {key_index} 組金鑰)...")
                chat_completion = await target_client.chat.completions.create(messages=messages, model=model_name)
                return chat_completion.choices[0].message.content
                        
        except Exception as e:
            error_msg = str(e)
            print(f"【⚠️ 後台切換】{provider} 的 {model_name} 發生錯誤，正滑動至下一組...")
            
            # ─── 🛠️ 移植萬用全動態冷卻時間解析核心 ───
            total_seconds = 60.0  # 保險預設值
            
            # A 招：解 [Retry-After: X]
            retry_match = re.search(r'\[Retry-After:\s*([0-9.]+)\]', error_msg)
            if retry_match and retry_match.group(1).strip():
                try: total_seconds = float(retry_match.group(1))
                except: pass
            else:
                # B 招：標準格式 try again in...
                match = re.search(r'try again in (?:(\d+)h)?(?:(\d+)m)?([0-9.]+)s', error_msg)
                if match:
                    hours = int(match.group(1)) if match.group(1) else 0
                    minutes = int(match.group(2)) if match.group(2) else 0
                    seconds = float(match.group(3)) if match.group(3) else 0.0
                    total_seconds = hours * 3600 + minutes * 60 + seconds
                else:
                    # C 招：盲掃模糊格式
                    match_sec = re.search(r'(?:retry after|wait|in)\s+([0-9.]+)\s*(?:s|sec|second|seconds)', error_msg.lower())
                    if match_sec:
                        try: total_seconds = float(match_sec.group(1))
                        except: pass
            
            total_seconds = max(5.0, total_seconds + 5)
            
            if "429" in error_msg or "rate limit" in error_msg.lower() or "http 429" in error_msg.lower():
                if provider == "openrouter":
                    OPENROUTER_KEY_COOLDOWNS[key_idx] = time.time() + total_seconds
                    print(f"【🛑 封印金鑰(後台)】第 {key_idx+1} 組 OpenRouter 觸發上限，精準動態封印 {total_seconds:.1f} 秒。")
                elif provider == "groq":
                    key_index = GROQ_CLIENTS.index(target_client) + 1
                    GROQ_KEY_COOLDOWNS[key_index] = time.time() + total_seconds
                    print(f"【🛑 封印金鑰(後台)】第 {key_index} 組 Groq 觸發上限，精準動態封印 {total_seconds:.1f} 秒。")
            continue

    return "沉默"  # 萬一雙軌全部大雪崩，保底選擇潛水保持沉默

# ────────────────────────────────────────────────────────
# 6. 🧠 前台主對話核心（主力重裝大腦 + 全動態冷卻完全體）
# ────────────────────────────────────────────────────────
async def fetch_ai_response(messages, require_vision=False): 
    global current_groq_idx, GROQ_KEY_COOLDOWNS
    global current_or_idx, OPENROUTER_KEY_COOLDOWNS
    global GEMINI_KEY_COOLDOWNS  
    
    # ─── 🕒 動態注入現實時間 ───
    try:
        tw_time = datetime.now(ZoneInfo("Asia/Taipei"))
        time_str = tw_time.strftime("%Y年%m月%d日 %H點%M分")
        weekday_map = {0: "日", 1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}
        time_context = f"\n\n【現實世界時間提示】現在時間是：{time_str} (星期{weekday_map[int(tw_time.strftime('%w'))]})。請根據時間和性格做出對應反應。"
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] = re.sub(r'\n\n【現實世界時間提示】.*', '', messages[0]["content"])
            messages[0]["content"] += time_context
    except Exception as e:
        print(f"【⚠️ 時間注入失敗】: {e}")

    current_time = time.time()
    
    # ⚡ 動態過濾：Groq 監獄初始檢查
    available_clients = []
    for i, client in enumerate(GROQ_CLIENTS):
        key_index = i + 1  
        if key_index in GROQ_KEY_COOLDOWNS:
            if current_time >= GROQ_KEY_COOLDOWNS[key_index]:
                print(f"【🟢 出獄通知】第 {key_index} 組 Groq 金鑰已過冷卻期，重新歸隊！")
                del GROQ_KEY_COOLDOWNS[key_index]
                if client: available_clients.append(client)
        else:
            if client: available_clients.append(client)

    if available_clients:
        start_idx = current_groq_idx % len(available_clients)
        current_groq_idx = (current_groq_idx + 1) % len(available_clients)
        ordered_clients = [available_clients[(start_idx + i) % len(available_clients)] for i in range(len(available_clients))]
    else:
        ordered_clients = []

    # ⚡ 動態過濾：OpenRouter 監獄初始檢查
    available_or_keys = []
    for i, key in enumerate(OPENROUTER_KEYS):
        if i in OPENROUTER_KEY_COOLDOWNS:
            if current_time >= OPENROUTER_KEY_COOLDOWNS[i]:
                print(f"【🟢 出獄通知】第 {i+1} 組 OpenRouter 金鑰已過冷卻期，重新歸隊！")
                del OPENROUTER_KEY_COOLDOWNS[i]
                if key: available_or_keys.append((i, key))
        else:
            if key: available_or_keys.append((i, key))

    if available_or_keys:
        start_or_idx = current_or_idx % len(available_or_keys)
        current_or_idx = (current_or_idx + 1) % len(available_or_keys)
        ordered_or_keys = [available_or_keys[(start_or_idx + j) % len(available_or_keys)] for j in range(len(available_or_keys))]
    else:
        ordered_or_keys = []

    # ⚡ 動態過濾：Gemini 監獄初始檢查
    available_gemini_keys = []
    for i, key in enumerate(GEMINI_KEYS):
        if i in GEMINI_KEY_COOLDOWNS:
            if current_time >= GEMINI_KEY_COOLDOWNS[i]:
                print(f"【🟢 出獄通知】第 {i+1} 組 Gemini 金鑰已過冷卻期，重新歸隊！")
                del GEMINI_KEY_COOLDOWNS[i]
                if key: available_gemini_keys.append((i, key))
        else:
            if key: available_gemini_keys.append((i, key))

    ordered_gemini_keys = available_gemini_keys
        
    if not ordered_gemini_keys and GEMINI_KEYS:
        print("【🚨 Gemini 大赦】所有 Gemini 金鑰皆在冷卻中，強制啟動集體釋放防當機制！")
        ordered_gemini_keys = list(enumerate(GEMINI_KEYS))
    
    # 🧠 動態產生混合大腦模型池
    DYNAMIC_MODEL_POOLS = []
    
    # 🌟 【第一梯隊：頂級大腦】
    for client in ordered_clients: 
        DYNAMIC_MODEL_POOLS.append({"provider": "groq", "client": client, "model": "llama-3.3-70b-versatile"})
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "meta-llama/llama-3.3-70b-instruct:free"})
    for client in ordered_clients: 
        DYNAMIC_MODEL_POOLS.append({"provider": "groq", "client": client, "model": "openai/gpt-oss-120b"})
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "qwen/qwen-2.5-72b-instruct:free"})
        
    # 🧱 【第二梯隊：小模型與最後防線】
    for idx, key in ordered_gemini_keys:
        DYNAMIC_MODEL_POOLS.append({"provider": "gemini", "key_idx": idx, "key": key, "model": "gemini-1.5-flash", "vision": True})
        DYNAMIC_MODEL_POOLS.append({"provider": "gemini", "key_idx": idx, "key": key, "model": "gemini-1.5-flash"})

    # 💡 OpenRouter 100% 全免費高強度小模型矩陣防線
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "google/gemma-3-27b-it:free"})
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "qwen/qwen-2.5-32b-instruct:free"})
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "deepseek/deepseek-chat-v3:free"}) 
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "meta-llama/llama-3.2-3b-instruct:free"})
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "openrouter/free"})

    # 🚀 開始依序呼叫大腦
    for item in DYNAMIC_MODEL_POOLS:
        provider = item["provider"]
        model_name = item["model"]
        is_vision_model = item.get("vision", False)
        target_client = item.get("client")
        
        loop_now = time.time()
        if provider == "groq" and target_client:
            k_idx = GROQ_CLIENTS.index(target_client) + 1  
            if k_idx in GROQ_KEY_COOLDOWNS and loop_now < GROQ_KEY_COOLDOWNS[k_idx]: continue
        
        if provider == "openrouter":
            or_idx = item.get("key_idx")
            if or_idx in OPENROUTER_KEY_COOLDOWNS and loop_now < OPENROUTER_KEY_COOLDOWNS[or_idx]: continue

        if provider == "gemini":
            g_idx = item.get("key_idx")
            if g_idx in GEMINI_KEY_COOLDOWNS and loop_now < GEMINI_KEY_COOLDOWNS[g_idx]: continue

        if require_vision and not is_vision_model: continue  
        if not require_vision and is_vision_model: continue  
            
        current_messages = []
        for msg in messages:
            content = msg["content"]
            if isinstance(content, list): 
                if not is_vision_model:
                    text_parts = [p["text"] for p in content if p["type"] == "text"]
                    current_messages.append({"role": msg["role"], "content": f"（提示：使用者傳了圖片/影片，但妳看不見，請傲嬌抱怨）\n{' '.join(text_parts)}"})
                else:
                    current_messages.append(msg)
            else:
                current_messages.append(msg)

        try:
            if provider == "groq":
                key_index = GROQ_CLIENTS.index(target_client) + 1  
                print(f"【🧠 嘗試】使用 Groq {model_name} (第 {key_index} 組金鑰)...")
                chat_completion = await target_client.chat.completions.create(messages=current_messages, model=model_name)
                return chat_completion.choices[0].message.content
                
            elif provider == "gemini":
                target_key = item.get("key")
                g_idx = item.get("key_idx")
                if not target_key: continue
                
                print(f"【🧠 嘗試】使用 Gemini 模型 {model_name} (第 {g_idx+1} 組金鑰)...")
                url = f"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                headers = {"Authorization": f"Bearer {target_key}", "Content-Type": "application/json"}
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"model": model_name, "messages": current_messages}, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data["choices"][0]["message"]["content"]
                        else:
                            retry_after = resp.headers.get("Retry-After", "")
                            error_text = await resp.text()
                            raise Exception(f"Gemini HTTP {resp.status} [Retry-After: {retry_after}]: {error_text}")
                            
            elif provider == "openrouter":
                target_key = item.get("key")
                key_idx = item.get("key_idx")
                if not target_key: continue
                
                print(f"【🧠 嘗試】使用 OpenRouter {model_name} (第 {key_idx+1} 組金鑰)...")
                url = "https://openrouter.ai/api/v1/chat/completions"
                headers = {"Authorization": f"Bearer {target_key}", "Content-Type": "application/json"}
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"model": model_name, "messages": current_messages}, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data["choices"][0]["message"]["content"]
                        else:
                            retry_after = resp.headers.get("Retry-After", "")
                            error_text = await resp.text()
                            raise Exception(f"OpenRouter HTTP {resp.status} [Retry-After: {retry_after}]: {error_text}")
                            
        except Exception as e:
            error_msg = str(e)
            print(f"【⚠️ 備援切換】{provider} 的 {model_name} 發生錯誤。直接切換...")
            
            # ─── 🛠️ 萬用全動態冷卻時間解析核心 ───
            total_seconds = 60.0  # 保險預設值
            
            # A 招：優先解析手動攔截注入的 [Retry-After: X]
            retry_match = re.search(r'\[Retry-After:\s*([0-9.]+)\]', error_msg)
            if retry_match and retry_match.group(1).strip():
                try: total_seconds = float(retry_match.group(1))
                except: pass
            else:
                # B 招：Groq 標準格式 (try again in 1h2m3s)
                match = re.search(r'try again in (?:(\d+)h)?(?:(\d+)m)?([0-9.]+)s', error_msg)
                if match:
                    hours = int(match.group(1)) if match.group(1) else 0
                    minutes = int(match.group(2)) if match.group(2) else 0
                    seconds = float(match.group(3)) if match.group(3) else 0.0
                    total_seconds = hours * 3600 + minutes * 60 + seconds
                else:
                    # C 招：盲掃模糊格式
                    match_sec = re.search(r'(?:retry after|wait|in)\s+([0-9.]+)\s*(?:s|sec|second|seconds)', error_msg.lower())
                    if match_sec:
                        try: total_seconds = float(match_sec.group(1))
                        except: pass
            
            # ✨ 安全防線：加上 5 秒安全墊片
            total_seconds = max(5.0, total_seconds + 5)
            
            # ─── 🗂️ 根據不同 Provider 動態封印 ───
            if provider == "groq" and ("429" in error_msg or "rate limit" in error_msg.lower()):
                key_index = GROQ_CLIENTS.index(target_client) + 1  
                GROQ_KEY_COOLDOWNS[key_index] = time.time() + total_seconds
                print(f"【🛑 封印金鑰】第 {key_index} 組 Groq 觸發上限，精準動態封印 {total_seconds:.1f} 秒。")

            elif provider == "openrouter" and ("429" in error_msg or "rate limit" in error_msg.lower()):
                key_idx = item.get("key_idx")
                OPENROUTER_KEY_COOLDOWNS[key_idx] = time.time() + total_seconds
                print(f"【🛑 封印金鑰】第 {key_idx+1} 組 OpenRouter 觸發上限，精準動態封印 {total_seconds:.1f} 秒。")

            elif provider == "gemini" and ("429" in error_msg or "rate limit" in error_msg.lower() or "http 429" in error_msg.lower()):
                g_idx = item.get("key_idx")
                if g_idx is not None:
                    GEMINI_KEY_COOLDOWNS[g_idx] = time.time() + total_seconds
                    print(f"【🛑 封印金鑰】第 {g_idx+1} 組 Gemini 觸發上限，精準動態封印 {total_seconds:.1f} 秒。")

            continue 

    return "（揉了揉太陽穴）呼...現在大腦有點過載，等我一下好不好？"


# ────────────────────────────────────────────────────────
# 8.🌐網路聯想探針（Tavily 動態輪詢負載均衡矩陣）
# ────────────────────────────────────────────────────────
async def fetch_tavily_single(query, api_key):
    """執行單一 Tavily API 請求"""
    url = "https://api.tavily.com/search"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"api_key": api_key, "query": query, "max_results": 2}, timeout=6) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if not results: raise ValueError("查無結果")
                return "\n\n".join([f"標題: {r.get('title')}\n內容: {r.get('content')}" for r in results])
            elif resp.status in [429, 403]: 
                raise RuntimeError("此金鑰額度已滿或遭限流")
            raise ValueError(f"API 異常狀態碼: {resp.status}")

async def search_internet_meme(query, is_explicit=True):
    """
    動態輪詢核心：每次呼叫都會換下一把鑰匙平均分攤壓力。
    如果抽中的鑰匙剛好壞了，會順著方向繼續找下一把備用。
    """
    global current_explicit_idx, current_background_idx
    
    if not query or len(query.strip()) < 2:
        return "無效的關鍵字"
        
    if not TAVILY_KEYS:
        print("❌ 【警報】未設定 TAVILY_KEYS 環境變數！")
        return "未設定搜尋金鑰"

    total_keys = len(TAVILY_KEYS)
    
    # 根據判定模式，決定本次的主力鑰匙，並推進全域指標
    if is_explicit:
        start_idx = current_explicit_idx
        current_explicit_idx = (current_explicit_idx - 1) % total_keys
        indices = [(start_idx - i) % total_keys for i in range(total_keys)]
        mode_name = "即時模式 (平均輪詢 ↩️)"
    else:
        start_idx = current_background_idx
        current_background_idx = (current_background_idx + 1) % total_keys
        indices = [(start_idx + i) % total_keys for i in range(total_keys)]
        mode_name = "背景模式 (平均輪詢 ↪️)"

    print(f"【🌐 矩陣出動】啟動 Tavily {mode_name} 搜尋: {query}")

    for idx in indices:
        key = TAVILY_KEYS[idx]
        shown_key = f"...{key[-6:]}" if len(key) > 6 else "???"
        
        print(f"  └─> 本次分配使用第 [{idx + 1}/{total_keys}] 組金鑰 ({shown_key})")
        
        try:
            result = await fetch_tavily_single(query, key)
            if result:
                print(f"  ✨ 【探針成功】第 [{idx + 1}] 組金鑰順利完成任務！")
                return result
        except RuntimeError:
            print(f"  ⚠️ 第 [{idx + 1}] 組金鑰已滿或限流，自動順延下一組...")
        except Exception as e:
            print(f"  ⚠️ 第 [{idx + 1}] 組金鑰發生異常: {e}，跳過並嘗試下一組...")

    return "網路訊號不佳，Tavily 金鑰矩陣已全面癱瘓。"

# ────────────────────────────────────────────────────────
# 9. 🛠️ 互動指令集 (包含動態健康矩陣與人物記憶指令)
# ────────────────────────────────────────────────────────

# ─── 📊 API 金鑰即時健康檢查矩陣（完全體同步版） ───
@bot.command(name="api")
# @commands.is_owner()  # ✨ 限制只有身為機器人擁有者的妳能查
async def check_all_apis(ctx):
    msg = await ctx.send("🔍 正在同步探測全線 API 金鑰矩陣，並檢查冷卻監獄狀況...")
    
    groq_keys = GROQ_KEYS
    current_time = time.time()

    # 🎯 建立標準的強固型超時設定 (連線+讀取總共限制 4 秒)
    api_timeout = aiohttp.ClientTimeout(total=4)

    # 1. 偵測 Groq 狀態與內部監獄狀況
    async def check_groq(session, key, index):
        if not key: 
            return f"Groq-{index:02d}", "⚪ 未設定", "-"
        
        if index in GROQ_KEY_COOLDOWNS:
            rem = GROQ_KEY_COOLDOWNS[index] - current_time
            if rem > 0:
                return f"Groq-{index:02d}", f"🔒 429 鎖定中 ({int(rem)}s)", "內部限流鎖定"
                
        url = "https://api.groq.com/openai/v1/models"
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with session.get(url, headers=headers, timeout=api_timeout) as resp:
                if resp.status == 200: 
                    return f"Groq-{index:02d}", "🟢 200 可用", f"尾碼: ...{key[-6:]}"
                elif resp.status == 429: 
                    return f"Groq-{index:02d}", "🛑 429 限流", "額度已滿"
                elif resp.status == 401:
                    return f"Groq-{index:02d}", "❌ 401 無效", "請檢查金鑰"
                else: 
                    return f"Groq-{index:02d}", f"❌ {resp.status} 錯誤", ""
        except Exception: 
            return f"Groq-{index:02d}", "💥 連線異常", "Timeout/網路失敗"

    # 2. 偵測 OpenRouter 狀態與內部監獄狀況
    async def check_openrouter(session, key, index):
        if not key: 
            return f"OpenRouter-{index:02d}", "⚪ 未設定", "-"
            
        if (index - 1) in OPENROUTER_KEY_COOLDOWNS:
            rem = OPENROUTER_KEY_COOLDOWNS[index - 1] - current_time
            if rem > 0:
                return f"OpenRouter-{index:02d}", f"🔒 429 鎖定中 ({int(rem)}s)", "內部限流鎖定"
                
        url = "https://openrouter.ai/api/v1/auth/key"
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with session.get(url, headers=headers, timeout=api_timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rem_usd = data.get("data", {}).get("limit_remaining")
                    rem_str = f"剩餘: {rem_usd:.4f} USD" if rem_usd is not None else "額度正常"
                    return f"OpenRouter-{index:02d}", "🟢 200 可用", rem_str
                elif resp.status == 429: 
                    return f"OpenRouter-{index:02d}", "🛑 429 限流", "頻率過高"
                else: 
                    return f"OpenRouter-{index:02d}", f"❌ {resp.status} 錯誤", ""
        except Exception: 
            return f"OpenRouter-{index:02d}", "💥 連線異常", "Timeout/網路失敗"

    # 3. 偵測 Tavily 狀態
    async def check_tavily(session, key, index):
        if not key: 
            return f"Tavily-{index:02d}", "⚪ 未設定", "-"
        url = "https://api.tavily.com/search"
        payload = {"api_key": key, "query": "ping", "max_results": 1}
        try:
            async with session.post(url, json=payload, timeout=api_timeout) as resp:
                if resp.status == 200: 
                    return f"Tavily-{index:02d}", "🟢 200 可用", f"尾碼: ...{key[-6:]}"
                elif resp.status in [429, 403]: 
                    return f"Tavily-{index:02d}", "🛑 429/403 滿", "免費額度耗盡"
                else: 
                    return f"Tavily-{index:02d}", f"❌ {resp.status} 錯誤", ""
        except Exception: 
            return f"Tavily-{index:02d}", "💥 連線異常", "Timeout/網路失敗"

    # 4. 偵測 Gemini 狀態與內部監獄狀況 (✨ 全面升級多金鑰排查)
    async def check_gemini(session, key, index):
        if not key: 
            return f"Gemini-{index:02d}", "⚪ 未設定", "-"
            
        if (index - 1) in GEMINI_KEY_COOLDOWNS:
            rem = GEMINI_KEY_COOLDOWNS[index - 1] - current_time
            if rem > 0:
                return f"Gemini-{index:02d}", f"🔒 429 鎖定中 ({int(rem)}s)", "內部限流鎖定"
                
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        try:
            async with session.get(url, timeout=api_timeout) as resp:
                if resp.status == 200: 
                    return f"Gemini-{index:02d}", "🟢 200 可用", f"尾碼: ...{key[-6:]}"
                elif resp.status == 429: 
                    return f"Gemini-{index:02d}", "🛑 429 限流", "請稍候再試"
                else: 
                    return f"Gemini-{index:02d}", f"❌ {resp.status} 錯誤", ""
        except Exception: 
            return f"Gemini-{index:02d}", "💥 連線異常", "Timeout/網路失敗"

    # 併發非同步發送所有盲測請求
    async with aiohttp.ClientSession() as session:
        tasks = []
        for idx, key in enumerate(groq_keys, 1):
            tasks.append(check_groq(session, key, idx))
        for idx, key in enumerate(OPENROUTER_KEYS, 1):
            tasks.append(check_openrouter(session, key, idx))
        for idx, key in enumerate(TAVILY_KEYS, 1):
            tasks.append(check_tavily(session, key, idx))
        # 💡 修正：改成迴圈掃描所有 Gemini 金鑰，不再只孤零零戳第一把
        for idx, key in enumerate(GEMINI_KEYS, 1):
            tasks.append(check_gemini(session, key, idx))
        
        try:
            # ✨ 大絕招：給整個併發探測加上「絕對斬斷鎖」（8 秒後強制放棄，絕不卡死）
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=8.0)
            
            # 🗂️ 依據 API 種類進行分類裝箱
            categories = {
                "Groq": [],
                "OpenRouter": [],
                "Tavily": [],
                "Gemini": []
            }
            
            # 將探測結果分發到對應的分類盒子裡
            for name, status, memo in results:
                if name.startswith("Groq"): 
                    categories["Groq"].append((name, status, memo))
                elif name.startswith("OpenRouter"): 
                    categories["OpenRouter"].append((name, status, memo))
                elif name.startswith("Tavily"): 
                    categories["Tavily"].append((name, status, memo))
                elif name.startswith("Gemini"): 
                    categories["Gemini"].append((name, status, memo))
                else: 
                    categories.setdefault("其他", []).append((name, status, memo))

            # 先更新初始的讀取訊息
            await msg.edit(content="**🔮 【7L 全線 API 金鑰健康矩陣】**\n*(✅ 探測完成！正在依據 API 種類為妳分段顯示報告 👇)*")
            
            # 📦 依序獨立發送每個分類的專屬表格
            for cat_name, cat_results in categories.items():
                if not cat_results: 
                    continue # 如果該分類完全沒有金鑰，就跳過不顯示
                
                # 建立該分類的專屬表頭
                current_chunk = f"**[{cat_name} 專屬矩陣]**\n```markdown\n"
                current_chunk += f"{'API 項目':<14} | {'狀態狀況':<14} | {'備註 / 剩餘資訊'}\n"
                current_chunk += "-" * 55 + "\n"
                
                for name, status, memo in cat_results:
                    # 🛡️ 終極防護：強制把太長的錯誤訊息截斷，並拿掉換行符號避免破壞表格
                    safe_memo = str(memo).replace('\n', ' ')[:80] + ("..." if len(str(memo)) > 80 else "")
                    row = f"{name:<14} | {status:<14} | {safe_memo}\n"
                    
                    # ✂️ 雙重極限防護：萬一單一分類超過 Discord 上限，自動在內部續接
                    if len(current_chunk) + len(row) > 1850:
                        current_chunk += "```"
                        await ctx.send(current_chunk)
                        current_chunk = f"**[{cat_name} 專屬矩陣 (續)]**\n```markdown\n" + row
                    else:
                        current_chunk += row
                
                # 送出該分類剩下的內容，確保結尾加上 markdown 標籤
                if current_chunk.strip() and not current_chunk.endswith("```"):
                    current_chunk += "```"
                    await ctx.send(current_chunk)
                    
        except asyncio.TimeoutError:
            # 🚑 發生卡死時的強制補救輸出
            await msg.edit(content="⚠️ **API 探測超時 (Timeout)！**\n部分 API 伺服器無回應，為了防止系統癱瘓已強制中斷。")
        except Exception as e:
            # 🛡️ 終極防護：攔截其他未知錯誤，並強制截斷防止超過 Discord 上限
            safe_error = str(e)[:1800]
            await msg.edit(content=f"❌ **API 探測發生未知錯誤**：\n```python\n{safe_error}\n```")

# ────────────────────────────────────────────────────────
# 10 🌐 虛擬網頁與啟動區塊
# ────────────────────────────────────────────────────────
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"All Bots are alive!")

    def log_message(self, format, *args): return

def run_backup_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    server.serve_forever()

# 🚀 程式執行入口 (確保 bot.run 只有一個，且放在整份檔案的最後一行)
if __name__ == "__main__":
    server_thread = threading.Thread(target=run_backup_server)
    server_thread.daemon = True
    server_thread.start()
    print("【🌐 系統通知】虛擬網頁伺服器已在背景啟動！")

    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("【錯誤】找不到 DISCORD_TOKEN_7L，請確認環境變數是否設定正確！")
