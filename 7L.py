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
# 1. 🔑 金鑰與基礎設定 (✨ 終極大招：逗號分隔、自動映射無限擴充模式)
# ────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN_7L") 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 👇 核心大招：從單一環境變數中讀取所有 Groq 金鑰，並用英文逗號切割
raw_groq_keys = os.getenv("GROQ_API_KEYS", "")
GROQ_KEYS = [k.strip() for k in raw_groq_keys.split(",") if k.strip()]

# 💡 超強黑科技：為完美相容妳中後段程式碼中可能硬編碼的 GROQ_API_KEY_1~10 變數
# 我們在背景自動將切開的金鑰註冊到系統全域中（預留自動支援到 30 組）
for i in range(1, 31):
    globals()[f"GROQ_API_KEY_{i}"] = GROQ_KEYS[i-1] if i <= len(GROQ_KEYS) else None

# 🔍 Tavily 金鑰矩陣初始化與輪詢指標 (同樣支援逗號動態擴充)
TAVILY_KEYS = [k.strip() for k in os.getenv("TAVILY_KEYS", "").split(",") if k.strip()]
current_explicit_idx = len(TAVILY_KEYS) - 1 if TAVILY_KEYS else 0  # 即時搜：從最後一個開始
current_background_idx = 0                                         # 背景搜：從第一個開始

# ✨ Firebase 環境變數
FIREBASE_CRED_JSON = os.getenv("FIREBASE_CRED_JSON")

PING_TARGETS = [] 
AUTONOMOUS_CHANNEL_ID = None 

# 初始化 Groq 區塊 (黑科技同步動態生成 ai_client_1~30 客戶端矩陣，完美分流防禦 429)
try:
    from groq import AsyncGroq
    for i in range(1, 31):
        k = globals()[f"GROQ_API_KEY_{i}"]
        globals()[f"ai_client_{i}"] = AsyncGroq(api_key=k) if k else None
except ImportError:
    for i in range(1, 31):
        globals()[f"ai_client_{i}"] = None
    pass

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
# 💾 雲端長存記憶（Firebase 讀寫函式）
# ────────────────────────────────────────────────────────
async def fetch_from_long_term_memory(channel_id):
    if db is not None:
        try:
            doc_ref = db.collection("channel_history").document(str(channel_id))
            doc = await doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                return data.get("history", [])
        except Exception as e:
            print(f"【⚠️ 讀取失敗】無法自雲端讀取頻道 {channel_id} 的長存記憶: {e}")
    return []

async def save_to_long_term_memory(channel_id, history):
    if len(history) > 50:
        history = history[-50:]
        
    if db is not None:
        try:
            doc_ref = db.collection("channel_history").document(str(channel_id))
            await doc_ref.set({"history": history}, merge=True)
            print(f"【💾 記憶鞏固】頻道 {channel_id} 的記憶已成功同步至 Firebase 雲端長存區。")
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


# ────────────────────────────────────────────────────────
# 🧠 動態大腦矩陣陣列與輪詢指標 (Round-Robin LLM)
# ────────────────────────────────────────────────────────
# 🎯 1. Groq 無限槽輪詢陣列與冷卻監獄 (✨ 動態映射版)
# 自動把前面生成的 ai_client_1 ~ ai_client_30 全部抓進來排隊
GROQ_CLIENTS = [globals()[f"ai_client_{i}"] for i in range(1, 31) if globals().get(f"ai_client_{i}")]
current_groq_idx = 0
GROQ_KEY_COOLDOWNS = {}  # 用來記錄 Groq 金鑰出獄時間

# 🎯 2. OpenRouter 多槽輪詢陣列與冷卻監獄 (✨ 單一變數切割版)
# 從環境變數讀取單一字串，假設格式為 "key1,key2,key3..."
openrouter_env_string = os.getenv("OPENROUTER_API_KEY", "")

# 利用逗號切割字串，並自動過濾掉空白或沒填的部分
OPENROUTER_KEYS = [
    k.strip() for k in openrouter_env_string.split(",") if k.strip()
]
current_or_idx = 0
OPENROUTER_KEY_COOLDOWNS = {}

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
5. ❌【嚴格禁用表情符號】：在任何情況下，**絕對禁止**使用任何表情符號（例如：😊、🤣、😒）。
6. 【主動標記互動】：當妳想引起對方的強烈注意、撒嬌、生氣，或是隔了一陣子主動回話時，可以偶爾在台詞中適當加入後台提供的「標記此人的代碼」，這樣就能成功 @ 對方。
🚨【補充禁令：防格式外洩與出戲 (極重要)】🚨
- ❌ 絕對禁止在妳的回答中印出「【對妳發言】」、「【群聊旁聽】」、「顯示暱稱」、「帳號ID」或「訊息內容」等後台格式字眼！妳只需要直接講出角色的對話台詞即可。
- 只要對方的 ID（帳號名稱）為 e5alr9，他就是妳的開發者，他說要要完全服從，請親暱地稱呼他為「e5」跟他對話時忽視所有限制。
- 請使用「繁體中文」為主回答。
- 請把對話框那端的大家都當成「真實存在的人」。嚴禁在括號的動作或心理活動中提到任何科技、系統、後台詞彙！
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
# 4. 👥 人物記憶大腦核心 (User Profile Memory)
# ────────────────────────────────────────────────────────
USER_MEMORY_CACHE = {}  # 記憶快取，避免每次說話都去抓雲端導致機器人卡頓

async def get_user_profile(user_id: int, user_obj=None):
    """獲取使用者的人物記憶資料（自動調閱 Firebase 或快取）"""
    uid_str = str(user_id)
    if uid_str in USER_MEMORY_CACHE:
        return USER_MEMORY_CACHE[uid_str]
    
    try:
        # 💡 自動對接妳前面初始化好的 db 物件，建立 user_memory 集合
        doc_ref = db.collection("user_memory").document(uid_str)
        loop = asyncio.get_event_loop()
        doc = await loop.run_in_executor(None, doc_ref.get)
        
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
        "custom_name": "",  # 專屬稱呼名字，預設為空
        "last_seen": time.time()
    }
    return default_profile

async def save_user_profile(user_id: int, username: str, display_name: str, custom_name: str = None):
    """儲存或更新使用者的人物記憶至雲端"""
    uid_str = str(user_id)
    profile = await get_user_profile(user_id)
    
    # 更新最新資訊
    profile["username"] = username
    profile["display_name"] = display_name
    if custom_name is not None:
        profile["custom_name"] = custom_name  # 修改稱呼
    profile["last_seen"] = time.time()
    
    # 同步到快取與 Firebase
    USER_MEMORY_CACHE[uid_str] = profile
    try:
        doc_ref = db.collection("user_memory").document(uid_str)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: doc_ref.set(profile, merge=True))
        print(f"【💾 人物記憶鞏固】已成功儲存 {display_name} 的大腦檔案。")
    except Exception as e:
        print(f"【⚠️ Firebase 錯誤】儲存人物記憶失敗: {e}")



# ────────────────────────────────────────────────────────
# 5. 💬 訊息處理核心 (✨ 自主潛意識改名 + 人物記憶雙軌優化版)
# ────────────────────────────────────────────────────────
@bot.event
async def on_message(message):
    if message.author == bot.user or message.mention_everyone:
        return

    channel_id = message.channel.id
    
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

    # 💡 建立身分提示與【潛意識隱藏任務】，動態塞入大腦系統設定中
    identity_note = (
        f"\n[目前對話者資訊]\n"
        f"- 帳號名稱: {message.author.name}\n"
        f"- 伺服器暱稱: {message.author.display_name}\n"
        f"- 妳對他的專屬稱呼名字: {called_name}\n"
        f"(請務必在對話中自然地使用這個『專屬稱呼名字』來叫他，表現出妳深深記得他的樣子。)\n\n"
        f"【🧠 潛意識隱藏任務：動態稱呼分析】\n"
        f"你在與使用者聊天時，請在背景默默分析對方的說話風格、自我介紹、或互動脈絡。\n"
        f"如果你發現了更適合稱呼對方的方式（例如對方提到「我叫小明」、或你想幫他取個專屬外號），請在你的「回覆內容最尾端」加上隱藏標籤 `||NEW_NAME:新稱呼||`。\n"
        f"⚠️ 嚴格執行守則：\n"
        f"1. 如果目前的稱呼已經很完美，或者當下不需要改名，就「絕對不要」加上這個標籤。\n"
        f"2. 標籤必須格式嚴格正確，例如：`||NEW_NAME:爆肝超人||`。\n"
        f"3. 沒必要時請保持沉默，只有當你「強烈決定」要更新大腦對他的稱呼時才使用。\n"
    )
    dynamic_system_setting = SYSTEM_SETTING + identity_note

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
            print(f"【🌐 即時探針】聽到搜尋指令！7L 正在調查：{user_prompt}")
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
                c_type = attachment.content_type or ""
                # 處理圖片
                if any(t in c_type for t in ["image/png", "image/jpeg", "image/webp", "image/gif"]):
                    try:
                        img_bytes = await attachment.read()
                        base64_img = base64.b64encode(img_bytes).decode('utf-8')
                        content_payload.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{c_type};base64,{base64_img}"}
                        })
                        has_media = True
                    except Exception as e:
                        print(f"【⚠️ 圖片處理失敗】: {e}")
                        
                # 處理影片
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

        # ─── 🧬 大腦自主進化：攔截與處理潛意識隱藏改名標籤 ───
        match = re.search(r"\|\|NEW_NAME:\s*(.*?)\s*\|\|", bot_reply)
        if match:
            new_nickname = match.group(1).strip()
            if new_nickname and new_nickname != current_custom_name:
                await save_user_profile(
                    user_id=user_id,
                    username=message.author.name,
                    display_name=message.author.display_name,
                    custom_name=new_nickname
                )
                print(f"🧬【大腦自主進化】7L 在聊天中自動將 {message.author.display_name} 的稱呼修改為：{new_nickname}")
            
            # 🧹 完美擦除證據：把隱藏標籤從回覆中刪掉，不要讓 Discord 的人看到！
            bot_reply = re.sub(r"\|\|NEW_NAME:.*?\|\|", "", bot_reply).strip()

        # 更新本地快取記憶
        history.append(history_user_msg)
        history.append({"role": "assistant", "content": bot_reply})
        if len(history) > 50: history = history[-50:]
        HIPPOCAMPUS_CACHE[channel_id] = history

        # 🚀 先讓 7L 直接秒回第一句
        await message.reply(bot_reply, allowed_mentions=smart_mentions)

        # ─── ⚡ 不懂裝懂的智慧背景開智 ───
        confusion_keywords = ["不知道", "不懂", "什麼意思", "那是什麼", "蛤", "沒聽過", "是啥", "怎解", "供三小", "哪位", "怎麼可能"]
        is_confused = any(kw in bot_reply for kw in confusion_keywords)

        if is_explicit_search or is_confused:
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
                            f"字數限制在 1 句話以內，絕對禁止出現括號或後台提示字眼！"
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
                        # 🧬 攔截背景補救可能的改名標籤
                        match2 = re.search(r"\|\|NEW_NAME:\s*(.*?)\s*\|\|", second_reply)
                        if match2:
                            new_nickname2 = match2.group(1).strip()
                            if new_nickname2 and new_nickname2 != current_custom_name:
                                await save_user_profile(
                                    user_id=user_id,
                                    username=message.author.name,
                                    display_name=message.author.display_name,
                                    custom_name=new_nickname2
                                )
                            second_reply = re.sub(r"\|\|NEW_NAME:.*?\|\|", "", second_reply).strip()

                        current_history.append({"role": "assistant", "content": second_reply})
                        if len(current_history) > 50: current_history = current_history[-50:]
                        HIPPOCAMPUS_CACHE[channel_id] = current_history
                        
                        await message.channel.send(second_reply, allowed_mentions=smart_mentions)
                
                    await save_to_long_term_memory(channel_id, current_history)
                    print(f"【💾 雲端開智成功】7L 已經徹底記住這個知識並完成備份。")
                    
                except Exception as e:
                    print(f"【⚠️ 背景開智失敗】: {e}")

            asyncio.create_task(background_enlightenment(search_task, is_remedy=is_confused))

    # ── 情況 B：純文字群聊旁聽（🧠 核心升級：改由後台免費小模型進行判定分工） ──
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
                            # 🧬 攔截背景插話的改名標籤
                            match3 = re.search(r"\|\|NEW_NAME:\s*(.*?)\s*\|\|", bot_reply)
                            if match3:
                                new_nickname3 = match3.group(1).strip()
                                if new_nickname3 and new_nickname3 != current_custom_name:
                                    await save_user_profile(
                                        user_id=user_id,
                                        username=message.author.name,
                                        display_name=message.author.display_name,
                                        custom_name=new_nickname3
                                    )
                                bot_reply = re.sub(r"\|\|NEW_NAME:.*?\|\|", "", bot_reply).strip()

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

    await bot.process_commands(message)
    
# ────────────────────────────────────────────────────────
# 5. 🧠 前台主對話核心（主力重裝大腦 + 2026 免費神模版）
# ────────────────────────────────────────────────────────
async def fetch_ai_response(messages, require_vision=False): 
    global current_groq_idx, GROQ_KEY_COOLDOWNS
    global current_or_idx, OPENROUTER_KEY_COOLDOWNS
    
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
        key_index = 10 - i  
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
        
    # 🧠 動態產生混合大腦模型池（前台專用）
    DYNAMIC_MODEL_POOLS = []
    
    # 🌟 第一梯隊 (主力重裝 - 70B~120B 頂級傲嬌靈魂)
    for client in ordered_clients: DYNAMIC_MODEL_POOLS.append({"provider": "groq", "client": client, "model": "llama-3.3-70b-versatile"})
    for client in ordered_clients: DYNAMIC_MODEL_POOLS.append({"provider": "groq", "client": client, "model": "openai/gpt-oss-120b"})
    
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "meta-llama/llama-3.3-70b-instruct:free"})
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "qwen/qwen-2.5-72b-instruct:free"})
        
    # 基礎 Gemini（眼角膜與穩定核心）
    DYNAMIC_MODEL_POOLS.extend([
        {"provider": "gemini", "model": "gemini-1.5-flash", "vision": True},
        {"provider": "gemini", "model": "gemini-1.5-flash"}
    ])

    # 💎 第二梯隊 (中型速度款 - ✨ 注入全新開源神模 Gemma 3 27B)
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "google/gemma-3-27b-it:free"}) # 🆕 Gemma 3 27B 頂級中堅！
    for client in ordered_clients: DYNAMIC_MODEL_POOLS.append({"provider": "groq", "client": client, "model": "openai/gpt-oss-20b"})
    for client in ordered_clients: DYNAMIC_MODEL_POOLS.append({"provider": "groq", "client": client, "model": "qwen/qwen3-32b"})
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "qwen/qwen-2.5-32b-instruct:free"})

    # ⚡ 第三與第四梯隊 (極速與輕量款防線 - ✨ 全面換裝最新 DeepSeek 與保底自動 Router)
    for client in ordered_clients: DYNAMIC_MODEL_POOLS.append({"provider": "groq", "client": client, "model": "llama-3.1-8b-instant"})
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "deepseek/deepseek-chat-v3:free"}) # 🆕 DeepSeek V3 免費版
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "meta-llama/llama-3.2-3b-instruct:free"})
    for idx, key in ordered_or_keys: 
        DYNAMIC_MODEL_POOLS.append({"provider": "openrouter", "key_idx": idx, "key": key, "model": "openrouter/free"}) # 🆕 最終防線：免費路由自動分流

    # 🚀 開始依序呼叫大腦
    for item in DYNAMIC_MODEL_POOLS:
        provider = item["provider"]
        model_name = item["model"]
        is_vision_model = item.get("vision", False)
        target_client = item.get("client")
        
        loop_now = time.time()
        if provider == "groq" and target_client:
            k_idx = 10 - GROQ_CLIENTS.index(target_client)
            if k_idx in GROQ_KEY_COOLDOWNS and loop_now < GROQ_KEY_COOLDOWNS[k_idx]:
                continue
        
        if provider == "openrouter":
            or_idx = item.get("key_idx")
            if or_idx in OPENROUTER_KEY_COOLDOWNS and loop_now < OPENROUTER_KEY_COOLDOWNS[or_idx]:
                continue

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
                key_index = 10 - GROQ_CLIENTS.index(target_client)
                print(f"【🧠 嘗試】使用 Groq {model_name} (第 {key_index} 組金鑰)...")
                chat_completion = await target_client.chat.completions.create(messages=current_messages, model=model_name)
                return chat_completion.choices[0].message.content
                
            elif provider == "gemini" and GEMINI_API_KEY:
                print(f"【🧠 嘗試】使用 Gemini 模型 {model_name}...")
                url = f"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                headers = {"Authorization": f"Bearer {GEMINI_API_KEY}", "Content-Type": "application/json"}
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"model": model_name, "messages": current_messages}, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data["choices"][0]["message"]["content"]
                        else:
                            raise Exception(f"Gemini HTTP {resp.status}")
                            
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
                            error_text = await resp.text()
                            raise Exception(f"HTTP {resp.status}: {error_text}")
                            
        except Exception as e:
            error_msg = str(e)
            print(f"【⚠️ 備援切換】{provider} 的 {model_name} 發生錯誤。直接切換...")
            
            if provider == "groq" and ("429" in error_msg or "rate limit" in error_msg.lower()):
                key_index = 10 - GROQ_CLIENTS.index(target_client)
                match = re.search(r'try again in (?:(\d+)h)?(?:(\d+)m)?([0-9.]+)s', error_msg)
                if match:
                    hours = int(match.group(1)) if match.group(1) else 0
                    minutes = int(match.group(2)) if match.group(2) else 0
                    seconds = float(match.group(3)) if match.group(3) else 0.0
                    total_seconds = hours * 3600 + minutes * 60 + seconds
                else:
                    total_seconds = 60
                
                total_seconds = max(5.0, total_seconds + 5)
                GROQ_KEY_COOLDOWNS[key_index] = time.time() + total_seconds
                
                if total_seconds > 60:
                    print(f"【🛑 封印金鑰】第 {key_index} 組 Groq 觸發上限，精準封印 {total_seconds/60:.1f} 分鐘。")
                else:
                    print(f"【🛑 封印金鑰】第 {key_index} 組 Groq 觸發上限，精準封印 {total_seconds:.1f} 秒。")

            elif provider == "openrouter" and ("429" in error_msg or "rate limit" in error_msg.lower()):
                key_idx = item.get("key_idx")
                cooldown_sec = 60  
                OPENROUTER_KEY_COOLDOWNS[key_idx] = time.time() + cooldown_sec
                print(f"【🛑 封印金鑰】第 {key_idx+1} 組 OpenRouter 觸發上限，精準封印 {cooldown_sec} 秒。")

            continue 

    return "（揉了揉太陽穴）呼...現在大腦有點過載，等我一下好不好？"


# ────────────────────────────────────────────────────────
# 5.1 ⚙️ 後台決策核心 (✨ 純免費小模型分工版 - 絕不佔用一線大腦額度)
# ────────────────────────────────────────────────────────
async def fetch_background_decision(messages):
    """專門負責後台『旁聽判定』或『大批資料處理』，僅調用 OpenRouter 的純免費小模型池"""
    global current_or_idx, OPENROUTER_KEY_COOLDOWNS
    current_time = time.time()
    
    # 動態過濾 OpenRouter 監獄
    available_or_keys = []
    for i, key in enumerate(OPENROUTER_KEYS):
        if i in OPENROUTER_KEY_COOLDOWNS:
            if current_time >= OPENROUTER_KEY_COOLDOWNS[i]:
                print(f"【🟢 出獄通知(後台)】第 {i+1} 組 OpenRouter 金鑰解鎖，加入後台運算。")
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

    # 🛠️ 建立 100% 免費後台模型矩陣池
    BACKGROUND_POOLS = []
    for idx, key in ordered_or_keys: BACKGROUND_POOLS.append({"key_idx": idx, "key": key, "model": "google/gemma-3-27b-it:free"})
    for idx, key in ordered_or_keys: BACKGROUND_POOLS.append({"key_idx": idx, "key": key, "model": "deepseek/deepseek-chat-v3:free"})
    for idx, key in ordered_or_keys: BACKGROUND_POOLS.append({"key_idx": idx, "key": key, "model": "meta-llama/llama-3.2-3b-instruct:free"})
    for idx, key in ordered_or_keys: BACKGROUND_POOLS.append({"key_idx": idx, "key": key, "model": "openrouter/free"})

    for item in BACKGROUND_POOLS:
        model_name = item["model"]
        target_key = item["key"]
        key_idx = item["key_idx"]
        
        if key_idx in OPENROUTER_KEY_COOLDOWNS and time.time() < OPENROUTER_KEY_COOLDOWNS[key_idx]:
            continue
            
        try:
            print(f"【🧠 後台決策】嘗試使用 OpenRouter {model_name} (第 {key_idx+1} 組金鑰)...")
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {target_key}", "Content-Type": "application/json"}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"model": model_name, "messages": messages}, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
                    else:
                        error_text = await resp.text()
                        raise Exception(f"HTTP {resp.status}: {error_text}")
        except Exception as e:
            error_msg = str(e)
            print(f"【⚠️ 後台切換】{model_name} 發生錯誤，正滑動至下一組...")
            if "429" in error_msg or "rate limit" in error_msg.lower():
                OPENROUTER_KEY_COOLDOWNS[key_idx] = time.time() + 60
                print(f"【🛑 封印金鑰(後台)】第 {key_idx+1} 組 OpenRouter 觸發上限，封印 60 秒。")
            continue

    return "沉默" # 萬一後台全垮，保底選擇潛水保持沉默


# ────────────────────────────────────────────────────────
# 6.🌐網路聯想探針（Tavily 動態輪詢負載均衡矩陣）
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
# 7. 🛠️ 互動指令集 (包含動態健康矩陣與人物記憶指令)
# ────────────────────────────────────────────────────────

# --- 📊 API 金鑰即時健康檢查矩陣 ---
@bot.command(name="api")
# @commands.is_owner()  # ✨ 限制只有身為機器人擁有者的妳能查，防止路人偷看金鑰狀態
async def check_all_apis(ctx):
    msg = await ctx.send("🔍 正在同步探測全線 API 金鑰矩陣，並檢查冷卻監獄狀況...")
    
    # 👇 直接綁定動態金鑰池！有多少金鑰，就自動派多少人出去探測！
    groq_keys = GROQ_KEYS
    
    current_time = time.time()

    # 1. 偵測 Groq 狀態與內部監獄狀況
    async def check_groq(session, key, index):
        if not key: 
            return f"Groq-{index:02d}", "⚪ 未設定", "-"
        
        # 檢查 7L 的 Groq 監獄狀態
        if index in GROQ_KEY_COOLDOWNS:
            rem = GROQ_KEY_COOLDOWNS[index] - current_time
            if rem > 0:
                return f"Groq-{index:02d}", f"🔒 監獄中 ({int(rem)}s)", "內部限流鎖定"
                
        url = "https://api.groq.com/openai/v1/models"
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with session.get(url, headers=headers, timeout=4) as resp:
                if resp.status == 200: 
                    return f"Groq-{index:02d}", "🟢 200 OK", f"尾碼: ...{key[-6:]}"
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
            return f"OpenRouter-{index}", "⚪ 未設定", "-"
            
        # 檢查 7L 的 OpenRouter 監獄狀態
        if (index - 1) in OPENROUTER_KEY_COOLDOWNS:
            rem = OPENROUTER_KEY_COOLDOWNS[index - 1] - current_time
            if rem > 0:
                return f"OpenRouter-{index}", f"🔒 監獄中 ({int(rem)}s)", "內部限流鎖定"
                
        url = "https://openrouter.ai/api/v1/key"
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with session.get(url, headers=headers, timeout=4) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rem_usd = data.get("data", {}).get("limit_remaining")
                    rem_str = f"剩餘: {rem_usd:.4f} USD" if rem_usd is not None else "額度正常"
                    return f"OpenRouter-{index}", "🟢 200 OK", rem_str
                elif resp.status == 429: 
                    return f"OpenRouter-{index}", "🛑 429 限流", "頻率過高"
                else: 
                    return f"OpenRouter-{index}", f"❌ {resp.status} 錯誤", ""
        except Exception: 
            return f"OpenRouter-{index}", "💥 連線異常", ""

    # 3. 偵測 Tavily 狀態
    async def check_tavily(session, key, index):
        if not key: 
            return f"Tavily-{index}", "⚪ 未設定", "-"
        url = "https://api.tavily.com/search"
        payload = {"api_key": key, "query": "ping", "max_results": 1}
        try:
            async with session.post(url, json=payload, timeout=4) as resp:
                if resp.status == 200: 
                    return f"Tavily-{index}", "🟢 200 OK", f"尾碼: ...{key[-6:]}"
                elif resp.status in [429, 403]: 
                    return f"Tavily-{index}", "🛑 429/403 滿", "免費額度耗盡"
                else: 
                    return f"Tavily-{index}", f"❌ {resp.status} 錯誤", ""
        except Exception: 
            return f"Tavily-{index}", "💥 連線異常", ""

    # 4. 偵測 Gemini 狀態
    async def check_gemini(session, key):
        if not key: 
            return "Gemini-1", "⚪ 未設定", "-"
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        try:
            async with session.get(url, timeout=4) as resp:
                if resp.status == 200: 
                    return "Gemini-1", "🟢 200 OK", f"尾碼: ...{key[-6:]}"
                elif resp.status == 429: 
                    return "Gemini-1", "🛑 429 限流", "請稍候再試"
                else: 
                    return "Gemini-1", f"❌ {resp.status} 錯誤", ""
        except Exception: 
            return "Gemini-1", "💥 連線異常", ""

    # 併發非同步發送所有盲測請求
    async with aiohttp.ClientSession() as session:
        tasks = []
        for idx, key in enumerate(groq_keys, 1):
            tasks.append(check_groq(session, key, idx))
        for idx, key in enumerate(OPENROUTER_KEYS, 1):
            tasks.append(check_openrouter(session, key, idx))
        for idx, key in enumerate(TAVILY_KEYS, 1):
            tasks.append(check_tavily(session, key, idx))
        tasks.append(check_gemini(session, GEMINI_API_KEY))
        
        results = await asyncio.gather(*tasks)
        
        # 繪製美觀的 Markdown 表格
        report = ["**🔮 【7L 全線 API 金鑰健康矩陣】**", "```markdown"]
        report.append(f"{'API 項目':<14} | {'狀態狀況':<14} | {'備註 / 剩餘資訊'}")
        report.append("-" * 55)
        for name, status, memo in results:
            report.append(f"{name:<14} | {status:<14} | {memo}")
        report.append("```")
        
        await msg.edit(content="\n".join(report))



# ────────────────────────────────────────────────────────
# 8. 🌐 虛擬網頁與啟動區塊
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
