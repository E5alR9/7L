import re
import os
import json
import random
import asyncio
import aiohttp
import discord
import threading
import base64  # 用於將圖片轉為 Base64 格式
from http.server import BaseHTTPRequestHandler, HTTPServer
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo  # Python 3.9+ 內建，支援直接鎖定台灣時區
# 用於影片關鍵影格抽樣
try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# ────────────────────────────────────────────────────────
# 1. 🔑 金鑰與基礎設定
# ────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN_7L") 
GROQ_API_KEY_1 = os.getenv("GROQ_API_KEY_1")
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2")
GROQ_API_KEY_3 = os.getenv("GROQ_API_KEY_3")
GROQ_API_KEY_4 = os.getenv("GROQ_API_KEY_4")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# ✨ Firebase 環境變數 (請將下載的 JSON 金鑰內容整串貼入此環境變數)
FIREBASE_CRED_JSON = os.getenv("FIREBASE_CRED_JSON")

PING_TARGETS = [] 
AUTONOMOUS_CHANNEL_ID = None 

# 初始化 Groq 區塊
try:
    from groq import AsyncGroq
    ai_client_1 = AsyncGroq(api_key=GROQ_API_KEY_1) if GROQ_API_KEY_1 else None
    ai_client_2 = AsyncGroq(api_key=GROQ_API_KEY_2) if GROQ_API_KEY_2 else None
    ai_client_3 = AsyncGroq(api_key=GROQ_API_KEY_3) if GROQ_API_KEY_3 else None
    ai_client_4 = AsyncGroq(api_key=GROQ_API_KEY_4) if GROQ_API_KEY_4 else None
except ImportError:
    ai_client_1 = None
    ai_client_2 = None
    ai_client_3 = None
    ai_client_4 = None
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
        # 將字串轉回 JSON 字典
        cred_dict = json.loads(FIREBASE_CRED_JSON)
        cred = credentials.Certificate(cred_dict)
        # 初始化 Firebase (如果還沒初始化過)
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
            # 取得 channel_history 集合中對應頻道 ID 的文件
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
            # 將紀錄寫入 Firestore (若無則新增，若有則覆蓋 history 欄位)
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
# 🧠 豪華跨平台備用大腦池 (加入了 Vision 標記)
# ────────────────────────────────────────────────────────

MODEL_POOLS = [
    # 🌟 第一梯隊：頂級旗艦大腦 (大參數 / 強推理 / 全數在線)
    {"provider": "groq", "client": ai_client_4, "model": "llama-3.3-70b-versatile"},                        
    {"provider": "groq", "client": ai_client_3, "model": "llama-3.3-70b-versatile"},                        
    {"provider": "groq", "client": ai_client_2, "model": "llama-3.3-70b-versatile"},                        
    {"provider": "groq", "client": ai_client_1, "model": "llama-3.3-70b-versatile"},                        
    {"provider": "groq", "client": ai_client_4, "model": "openai/gpt-oss-120b"},  # 🚀 新上架 120B 頂級旗艦
    {"provider": "groq", "client": ai_client_3, "model": "openai/gpt-oss-120b"},                        
    {"provider": "groq", "client": ai_client_2, "model": "openai/gpt-oss-120b"},                        
    {"provider": "groq", "client": ai_client_1, "model": "openai/gpt-oss-120b"},                        
    {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},   
    {"provider": "openrouter", "model": "qwen/qwen-2.5-72b-instruct:free"},          
    {"provider": "gemini", "model": "gemini-1.5-flash", "vision": True}, # ✨ 支援視覺                            

    # 💎 第二梯隊：中堅主力大腦 (高速度 / 優秀效能)
    {"provider": "groq", "client": ai_client_4, "model": "openai/gpt-oss-20b"},   # ⚡ 1000 tps 超高速模型
    {"provider": "groq", "client": ai_client_3, "model": "openai/gpt-oss-20b"},                        
    {"provider": "groq", "client": ai_client_2, "model": "openai/gpt-oss-20b"},                        
    {"provider": "groq", "client": ai_client_1, "model": "openai/gpt-oss-20b"},                        
    {"provider": "groq", "client": ai_client_4, "model": "qwen/qwen3-32b"},        # 🔮 全新 Qwen3 預覽
    {"provider": "groq", "client": ai_client_3, "model": "qwen/qwen3-32b"},                        
    {"provider": "groq", "client": ai_client_2, "model": "qwen/qwen3-32b"},                        
    {"provider": "groq", "client": ai_client_1, "model": "qwen/qwen3-32b"},                        
    {"provider": "groq", "client": ai_client_4, "model": "qwen/qwen3.6-27b"},      # 🔮 全新 Qwen3.6 預覽
    {"provider": "groq", "client": ai_client_3, "model": "qwen/qwen3.6-27b"},                        
    {"provider": "groq", "client": ai_client_2, "model": "qwen/qwen3.6-27b"},                        
    {"provider": "groq", "client": ai_client_1, "model": "qwen/qwen3.6-27b"},                        
    {"provider": "openrouter", "model": "qwen/qwen-2.5-32b-instruct:free"},          
    {"provider": "openrouter", "model": "mistralai/mixtral-8x7b-instruct:free"},     

    # ⚡ 第三梯隊：高效能輕量 / 次世代預覽
    {"provider": "groq", "client": ai_client_4, "model": "meta-llama/llama-4-scout-17b-16e-instruct"}, # 🏹 Llama 4 搶先預覽版 (750 tps)
    {"provider": "groq", "client": ai_client_3, "model": "meta-llama/llama-4-scout-17b-16e-instruct"},                        
    {"provider": "groq", "client": ai_client_2, "model": "meta-llama/llama-4-scout-17b-16e-instruct"},                        
    {"provider": "groq", "client": ai_client_1, "model": "meta-llama/llama-4-scout-17b-16e-instruct"},                        
    {"provider": "groq", "client": ai_client_4, "model": "llama-3.1-8b-instant"}, # 🥦 官方認證穩定生產版
    {"provider": "groq", "client": ai_client_3, "model": "llama-3.1-8b-instant"},                        
    {"provider": "groq", "client": ai_client_2, "model": "llama-3.1-8b-instant"},                        
    {"provider": "groq", "client": ai_client_1, "model": "llama-3.1-8b-instant"},                        
    {"provider": "openrouter", "model": "google/gemma-2-9b-it:free"},                
    {"provider": "openrouter", "model": "meta-llama/llama-3-8b-instruct:free"},      
    {"provider": "openrouter", "model": "mistralai/mistral-7b-instruct:free"},       

    # 🛡️ 第四梯隊：輕量級防線 / 備用應急
    {"provider": "openrouter", "model": "meta-llama/llama-3.2-3b-instruct:free"}   
]

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
# 4. 💬 訊息處理核心 (✨ 加入視覺動態觸發開關)
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

    # ── 情況 A：有人標記或回覆 Bot ──
    if should_trigger:
        if not user_prompt and not message.attachments:
            await message.channel.send("找我嗎~？", allowed_mentions=smart_mentions)
            return

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

        # 如果有媒體，當前對話使用 Multimodal Payload；否則維持純文字
        if has_media:
            immediate_user_msg = {"role": "user", "content": content_payload}
            history_user_msg = {"role": "user", "content": f"（使用者傳送了圖片/影片）\n{formatted_prompt}"}
        else:
            immediate_user_msg = {"role": "user", "content": formatted_prompt}
            history_user_msg = {"role": "user", "content": formatted_prompt}

        messages = [{"role": "system", "content": SYSTEM_SETTING}] + history + [immediate_user_msg]
        
        bot_reply = await fetch_ai_response(messages, require_vision=has_media)

        if bot_reply is None:
            await message.reply("（角色暫時登出中，請稍後再試...）", allowed_mentions=smart_mentions)
            return

        # 更新本地快取記憶
        history.append(history_user_msg)
        history.append({"role": "assistant", "content": bot_reply})
        if len(history) > 50: history = history[-50:]
        HIPPOCAMPUS_CACHE[channel_id] = history

        # 🚀 修正點 1：先讓 7L 直接秒回訊息，使用者不卡頓
        await message.reply(bot_reply, allowed_mentions=smart_mentions)

        # ☁️ 修正點 2：回完訊息後，立刻丟給背景 task 去同步雲端，不拖延速度
        asyncio.create_task(save_to_long_term_memory(channel_id, history))

       # --- 真人連發第二句機制 (融入網路聯想與記憶鞏固) ---
        if random.random() < 0.7:
            # 🌐 【步驟 A】不影響第一句，現在背景偷偷聯想/上網查這個梗
            print(f"【🌐 網路探針】7L 正在大腦暗處調查關鍵字：{user_prompt}")
            web_knowledge = await search_internet_meme(user_prompt)
            
            # 🧠 【步驟 B】把查到的網頁資料，包裝成雲端海馬迴的「頓悟提示」
            brain_insight = f"（🧠 7L 的雲端大腦聯想補完：關於使用者提到的「{user_prompt}」，網路上的真實意思是：\n{web_knowledge}）"
            
            follow_up_prompt = (
                f"【系統提示（不可外洩）】妳剛剛秒回了對方。現在妳的雲端大腦剛剛聯想到了這個新知識：{brain_insight}。"
                f"請結合這個新知識，傲嬌地傳第二則短訊息補充（例如：突然看懂了對方的梗而吐槽、恍然大悟但嘴硬裝懂、或者用新學到的梗反擊對方）。"
                f"請直接說出妳的對話台詞，字數嚴格限制在 1 句話之內。絕對禁止吐出任何系統格式、括號或後台提示字眼！"
            )
            
            # 🎯 調整：高機率觸發「雲端深層回想」
            if random.random() < 0.7:
                print(f"【🔮 深層回想】觸發！7L 正在翻閱雲端長存記憶...")
                history = await fetch_from_long_term_memory(channel_id)
                if not history: 
                    history = HIPPOCAMPUS_CACHE[channel_id]
            else:
                history = HIPPOCAMPUS_CACHE[channel_id]
            
            # 💾 【步驟 C】把這個「新查到的知識」塞進歷史歷史紀錄，這樣她以後就「永遠記得」這個梗了！
            history.append({"role": "user", "content": f"（系統記憶注入：{brain_insight}）"})
                
            second_messages = [{"role": "system", "content": SYSTEM_SETTING}] + history + [{"role": "user", "content": follow_up_prompt}]
            second_reply = await fetch_ai_response(second_messages)
            
            if second_reply:
                history.append({"role": "assistant", "content": second_reply})
                if len(history) > 50: history = history[-50:]
                HIPPOCAMPUS_CACHE[channel_id] = history
                
                # 🚀 秒發第二句，並異步鞏固記憶到 Firebase
                await message.channel.send(second_reply, allowed_mentions=smart_mentions)
                asyncio.create_task(save_to_long_term_memory(channel_id, history))
                
    # ── 情況 B：純文字群聊旁聽 ──
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

            INTERRUPT_CHANCE = 0.45 
            
            if random.random() < INTERRUPT_CHANCE:
                interject_prompt = (
                    f"【系統事件（不可對外洩漏）】妳剛剛在旁聽群聊，聽到大家聊到這裡，妳傲嬌的性格讓妳忍不住想「直接插話」或吐槽。 "
                    f"請根據目前群組內的聊天氣氛或話題，自然地切入並插話。 "
                    f"請直接說出妳的對話台詞，字數嚴格限制在 1~3 句話之內。絕對禁止吐出任何系統格式、括號或後台提示字眼！"
                )
                
                interject_messages = [{"role": "system", "content": SYSTEM_SETTING}] + history + [{"role": "user", "content": interject_prompt}]
                bot_reply = await fetch_ai_response(interject_messages)
                
                if bot_reply:
                    history.append({"role": "assistant", "content": bot_reply})
                    if len(history) > 50: history = history[-50:]
                    HIPPOCAMPUS_CACHE[channel_id] = history
                    
                    await message.channel.send(bot_reply, allowed_mentions=smart_mentions)
                    asyncio.create_task(save_to_long_term_memory(channel_id, history))

    await bot.process_commands(message)
    
# ────────────────────────────────────────────────────────
# 5. 🧠 跨平台備援核心（支援動態大腦分流）
# ────────────────────────────────────────────────────────
async def fetch_ai_response(messages, require_vision=False): 
    # ─── 🕒 動態注入現實時間（台灣時區） ───
    try:
        # 取得精確的台灣時間
        tw_time = datetime.now(ZoneInfo("Asia/Taipei"))
        time_str = tw_time.strftime("%Y年%m月%d日 %H點%M分")
        
        # 轉換星期格式 (0=星期日, 1=星期一...)
        weekday_map = {0: "日", 1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}
        weekday_str = f"星期{weekday_map[int(tw_time.strftime('%w'))]}"
        
        # 建立時間提示詞，引導 7L 做出對應反應
        time_context = (
            f"\n\n【現實世界時間提示】現在時間是：{time_str} ({weekday_str})。"
            f"請根據這個時間和妳的性格做出對應反應（例如：如果是深夜，傲嬌地催使用者去睡覺；如果是早晨，碎碎念他怎麼這麼早起）。"
        )
        
        # 自動找到對話堆疊中的第一個 system 設定，把時間強行塞進去
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] = messages[0]["content"] + time_context
            
    except Exception as e:
        print(f"【⚠️ 時間時區注入失敗】: {e}，將使用預設無時間模式。")
    # ─────────────────────────────────────

    # ⚠️ 以下維持你原本的模型輪詢與分流邏輯不變
    for item in MODEL_POOLS:
        provider = item["provider"]
        model_name = item["model"]
        is_vision_model = item.get("vision", False)
        
        if require_vision and not is_vision_model:
            continue  
        if not require_vision and is_vision_model:
            continue  
            
        current_messages = []
        for msg in messages:
            content = msg["content"]
            if isinstance(content, list): 
                if not is_vision_model:
                    text_parts = [p["text"] for p in content if p["type"] == "text"]
                    combined_text = " ".join(text_parts)
                    combined_text = f"（系統提示：使用者傳了圖片/影片，但妳這個備用腦看不見，請傲嬌地抱怨、瞎猜或說妳不想看）\n{combined_text}"
                    current_messages.append({"role": msg["role"], "content": combined_text})
                else:
                    current_messages.append(msg)
            else:
                current_messages.append(msg)

        try:
            if provider == "groq":
                target_client = item.get("client")
                if not target_client: continue
                    
                print(f"【🧠 嘗試】正在使用 Groq 模型 {model_name}...")
                chat_completion = await target_client.chat.completions.create(
                    messages=current_messages, model=model_name
                )
                return chat_completion.choices[0].message.content
                
            elif provider == "gemini":
                if not GEMINI_API_KEY: continue
                print(f"【🧠 嘗試】正在使用 Gemini 模型 {model_name}...")
                url = f"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                headers = {"Authorization": f"Bearer {GEMINI_API_KEY}", "Content-Type": "application/json"}
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"model": model_name, "messages": current_messages}, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data["choices"][0]["message"]["content"]
                            
            elif provider == "openrouter":
                if not OPENROUTER_API_KEY: continue
                print(f"【🧠 嘗試】正在使用 OpenRouter 模型 {model_name}...")
                url = "https://openrouter.ai/api/v1/chat/completions"
                headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": "https://render.com", "X-Title": "7L Bot"}
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"model": model_name, "messages": current_messages}, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data["choices"][0]["message"]["content"]
                            
        except Exception as e:
            print(f"【⚠️ 失敗】{provider} 的 {model_name} 呼叫失敗: {e}。切換下一個備用腦...")
            continue
    return None

# ────────────────────────────────────────────────────────
# 🌐 網路聯想探針（免金鑰搜尋工具）
# ────────────────────────────────────────────────────────
async def search_internet_meme(query):
    """在背景偷偷上網查梗，限制只拿前 2 條精簡摘要"""
    if not query or len(query.strip()) < 2:
        return "無效的關鍵字"
    try:
        from duckduckgo_search import DDGS
        # 因為 DDGS 是同步阻塞的，我們用 asyncio.to_thread 丟到背景執行，避免卡死 Bot
        def sync_search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=2))
        
        results = await asyncio.to_thread(sync_search)
        if results:
            summary = []
            for r in results:
                summary.append(f"標題: {r['title']}\n內容: {r['body']}")
            return "\n\n".join(summary)
    except Exception as e:
        print(f"【⚠️ 網路探針故障】無法搜尋「{query}」: {e}")
    return "網路訊號不佳，查不到相關資料。"

# ────────────────────────────────────────────────────────
# 🌐 6. 虛擬網頁與啟動區塊
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

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_backup_server)
    server_thread.daemon = True
    server_thread.start()
    print("【🌐 系統通知】虛擬網頁伺服器已在背景啟動！")

    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("【錯誤】找不到 DISCORD_TOKEN_7L，請確認環境變數是否設定正確！")
