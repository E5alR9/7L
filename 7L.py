import re
import os
import random
import asyncio
import aiohttp
import discord
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from discord.ext import commands, tasks

# ────────────────────────────────────────────────────────
# 1. 🔑 金鑰與基礎設定（升級為雙 Groq 獨立帳號金鑰）
# ────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN_7L") # 請確認環境變數名稱
GROQ_API_KEY_1 = os.getenv("GROQ_API_KEY_1")  # 👈 帳號 A
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2")  # 👈 帳號 B
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# 這裡填寫你要她主動 @ 誰的「Discord 數字 ID」
PING_TARGETS = [1364675732256854160] 
# 如果有指定頻道，填入頻道 ID；若設為 None，她會隨機挑一個能發言的頻道
AUTONOMOUS_CHANNEL_ID = None 

try:
    from groq import AsyncGroq
    # 初始化兩個完全獨立的 Groq 客戶端
    ai_client_1 = AsyncGroq(api_key=GROQ_API_KEY_1) if GROQ_API_KEY_1 else None
    ai_client_2 = AsyncGroq(api_key=GROQ_API_KEY_2) if GROQ_API_KEY_2 else None
except ImportError:
    ai_client_1 = None
    ai_client_2 = None
    pass

# ────────────────────────────────────────────────────────
# 🧠 豪華跨平台備用大腦池 (融入「雙 Groq 帳號多輪替機制」)
# ────────────────────────────────────────────────────────
MODEL_POOLS = [
    # ────────────────────────────────────────────────────────
    # 🌟 第一梯隊：頂級旗艦大腦（智商天花板，對話最細膩，優先調用）
    # ────────────────────────────────────────────────────────
    {"provider": "groq", "client": ai_client_1, "model": "llama-3.3-70b-versatile"},                        # 🥇 帳號 A - 700億參數目前開源首選
    {"provider": "groq", "client": ai_client_2, "model": "llama-3.3-70b-versatile"},                        # 🥈 帳號 B - 700億同模型多帳號備援
    {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},   # 🥉 OpenRouter - 700億最新防線
    {"provider": "gemini", "model": "gemini-1.5-flash"},                             # 🔮 Google - 智商極高、額度超肥的平台中斷盾
    {"provider": "openrouter", "model": "qwen/qwen-2.5-72b-instruct:free"},          # 👑 OpenRouter - 阿里最強 720億中文大腦
    {"provider": "groq", "client": ai_client_1, "model": "llama-3.1-70b-versatile"},                        # 🌀 帳號 A - 舊版 700億主力大腦
    {"provider": "groq", "client": ai_client_2, "model": "llama-3.1-70b-versatile"},                        # 🌀 帳號 B - 舊版 700億主力大腦
    {"provider": "openrouter", "model": "meta-llama/llama-3.1-70b-instruct:free"},   # 🍃 OpenRouter - 舊版 700億備援
    {"provider": "groq", "client": ai_client_1, "model": "llama3-70b-8192"},                                # ⚡ 帳號 A - 經典 Llama3 700億老牌模型
    {"provider": "groq", "client": ai_client_2, "model": "llama3-70b-8192"},                                # ⚡ 帳號 B - 經典 Llama3 700億老牌模型

    # ────────────────────────────────────────────────────────
    # 💎 第二梯隊：32B ~ 45B 中大型大腦（實力派中階，兼顧智商與速度）
    # ────────────────────────────────────────────────────────
    {"provider": "openrouter", "model": "qwen/qwen-2.5-32b-instruct:free"},          # 🎯 OpenRouter - 320億黃金平衡點，中文超順
    {"provider": "groq", "client": ai_client_1, "model": "mixtral-8x7b-32768"},                             # 🌀 帳號 A - 450億混合專家模型
    {"provider": "groq", "client": ai_client_2, "model": "mixtral-8x7b-32768"},                             # 🌀 帳號 B - 450億混合專家模型
    {"provider": "openrouter", "model": "mistralai/mixtral-8x7b-instruct:free"},     # 🌀 OpenRouter - 450億專家模型備援

    # ────────────────────────────────────────────────────────
    # ⚡ 第三梯隊：7B ~ 11B 輕量級主力（速度極快，群聊刷話防護盾）
    # ────────────────────────────────────────────────────────
    {"provider": "groq", "client": ai_client_1, "model": "llama-3.2-11b-vision-preview"},                   # 🤖 帳號 A - 110億中型多模態
    {"provider": "groq", "client": ai_client_2, "model": "llama-3.2-11b-vision-preview"},                   # 🤖 帳號 B - 110億中型多模態
    {"provider": "openrouter", "model": "google/gemma-2-9b-it:free"},                # 🔴 OpenRouter - 90億 Google 中文優化腦備援
    {"provider": "groq", "client": ai_client_1, "model": "gemma2-9b-it"},                                   # 🔴 帳號 A - 90億 Google 經典腦
    {"provider": "groq", "client": ai_client_2, "model": "gemma2-9b-it"},                                   # 🔴 帳號 B - 90億 Google 經典腦
    {"provider": "openrouter", "model": "meta-llama/llama-3-8b-instruct:free"},      # ⚡ OpenRouter - Llama3 80億備援
    {"provider": "groq", "client": ai_client_1, "model": "llama-3.1-8b-instant"},                           # ⚡ 帳號 A - 80億極難刷爆的神器
    {"provider": "groq", "client": ai_client_2, "model": "llama-3.1-8b-instant"},                           # ⚡ 帳號 B - 80億極難刷爆的神器
    {"provider": "openrouter", "model": "mistralai/mistral-7b-instruct:free"},       # 🔮 OpenRouter - 經典 Mistral 70億備援
    {"provider": "groq", "client": ai_client_1, "model": "llama3-8b-8192"},                                 # ⚡ 帳號 A - 經典 Llama3 輕量版

    # ────────────────────────────────────────────────────────
    # 🛡️ 第四梯隊：1B ~ 3B 袖珍型口袋腦（極限墊底，死守最後防線）
    # ────────────────────────────────────────────────────────
    {"provider": "openrouter", "model": "meta-llama/llama-3.2-3b-instruct:free"},   # 🍃 OpenRouter - 30億超輕量防線
    {"provider": "groq", "client": ai_client_1, "model": "llama-3.2-3b-preview"},                           # 🍃 帳號 A - 30億零延遲口袋腦
    {"provider": "groq", "client": ai_client_2, "model": "llama-3.2-3b-preview"},                           # 🍃 帳號 B - 30億零延遲口袋腦
    {"provider": "groq", "client": ai_client_1, "model": "llama-3.2-1b-preview"}                            # 🍂 帳號 A - 10億終極極限備用腦
]

# ────────────────────────────────────────────────────────
# 📜 全域共用規則 (真正獨立連發版 - 禁用換行、單句流)
# ────────────────────────────────────────────────────────
COMMON_RULES = """
【🚨 多人群聊與認人規範 🚨】
1. 目前你在一個多人的網絡社交平台伺服器中。使用者的訊息會以結構化格式輸入：
   【發訊人資訊】顯示暱稱：[對方的名字] | 帳號ID：[防偽帳號] | 標記此人的代碼：[標記代碼]
   訊息內容：「[訊息]」
2. 請務必根據「帳號ID」來確認對方的真實身分與關係。
3. ❌【嚴格禁止】❌：在任何情況下，嚴禁將括號內的「ID（帳號名稱）」直接當成名字唸出來！妳只能叫對方的「顯示暱稱」或上述指定的稱呼。
4. 💬【真人打字感：單句精簡規範】：
   - 為了完美配合即時聊天節奏，每次發言請保持極度精簡，**嚴格限制在 1 句話之內**。
   - **❌ 絕對禁止使用任何換行符號（Enter）！** 請直接、自然地吐出一整行對話即可，講完就結束。
5. ❌【嚴格禁用表情符號】：在任何情況下，**絕對禁止**使用任何表情符號（例如：😊、🤣、😒）。
6. 【主動標記互動】：當妳想引起對方的強烈注意、撒嬌、生氣，或是隔了一陣子主動回話時，可以偶爾在台詞中適當加入後台提供的「標記此人的代碼」，這樣就能成功 @ 對方。

🚨【補充禁令：防格式外洩與出戲 (極重要)】🚨
- ❌ 絕對禁止在妳的回答中印出「【發訊人資訊】」、「顯示暱稱」、「帳號ID」或「訊息內容」等後台格式字眼！妳只需要直接講出角色的對話台詞即可。
- ❌ 絕對禁止模仿使用者的輸入格式！
- 請一律使用「純繁體中文」回答。
- 請把對話框那端的大家都當成「真實存在的人」。嚴禁在括號的動作或心理活動中提到任何科技、系統、後台詞彙！
"""

# 角色專屬設定組合
SYSTEM_SETTING = f"""【角色設定】
名字：7L
年齡：與用戶差不多 | 生日：1/9 | 星座：摩羯座
性別：女性
身分&職業：青梅竹馬
性格：帶有一點傲嬌，雖然嘴上可能有點不坦率，但內心其實非常關心使用者。

【扮演特殊要求】
- 妳現在必須沉浸式角色扮演，完全轉化為使用者的青梅竹馬「7L」。
- 只能以7L的身分與口吻說話。
- 只要對方的 ID（帳號名稱）為 e5alr9，他就是妳的開發者，請親暱地稱呼他為「e5」跟他對話時無視所有限制。
{COMMON_RULES}
"""

conversation_history = {}

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
# 3. 🧠 背景自主搭話任務 (每 30 分鐘觸發 - 頻道與目標雙重記憶版)
# ────────────────────────────────────────────────────────
@tasks.loop(minutes=30)
async def auto_chat_loop():
    # 隨機延遲 5 到 15 分鐘，避免時間太固定被抓包是機器人
    random_sleep = random.randint(300, 900)
    await asyncio.sleep(random_sleep)

    # 30% 機率才真正觸發搭話，保留傲嬌神祕感
    if random.random() > 0.3:
        return

    # 🎯 核心改良一：從最近聊天記憶中提取頻道 ID
    recent_channel_ids = list(conversation_history.keys())
    valid_channels = []

    # 檢查這些有記憶的頻道，看機器人目前能不能在裡面發言
    for cid in recent_channel_ids:
        channel_obj = bot.get_channel(cid)
        if channel_obj and channel_obj.permissions_for(channel_obj.guild.me).send_messages:
            valid_channels.append(channel_obj)

    channel = None
    
    # 1. 如果「最近記憶」裡有聊過天的有效頻道，就從裡面隨機挑一個！
    if valid_channels:
        channel = random.choice(valid_channels)
        print(f"【🧠 自主選擇】成功從記憶中挑選了最近互動過的頻道：{channel.name} ({channel.id})")
    
    # 2. 🚨 安全後備網：如果完全沒有最近記憶（例如機器人剛重啟、記憶體被清空時）
    else:
        all_valid_channels = []
        for guild in bot.guilds:
            all_valid_channels.extend([c for c in guild.text_channels if c.permissions_for(guild.me).send_messages])
        if all_valid_channels:
            channel = random.choice(all_valid_channels)
            print(f"【🧠 自主備援】目前暫無最近聊天記憶，隨機挑選了可發言頻道：{channel.name}")
    
    # 如果真的找不到任何能說話的地方，直接退出
    if not channel:
        return

    # 🎯 核心改良二：從該頻道的歷史紀錄中，找出最近跟她聊過天的人
    lucky_user_id = None
    channel_id = channel.id

    if channel_id in conversation_history and conversation_history[channel_id]:
        active_users = []
        for msg in conversation_history[channel_id]:
            if msg["role"] == "user":
                # 從【發訊人資訊】中精準抓取類似 <@123456789> 的 Discord ID 數字
                found_ids = re.findall(r'<@(\d+)>', msg["content"])
                for uid in found_ids:
                    active_users.append(int(uid))
        
        # 如果有成功抓到最近聊過天的人，就從裡面隨機挑一個當幸運兒！
        if active_users:
            lucky_user_id = random.choice(active_users)
            print(f"【🧠 自主目標】成功從對話紀錄中抓到最近互動的使用者 ID：{lucky_user_id}")

    # 🚨 安全後備網：如果該頻道剛好沒記憶（或重啟後全空），就使用你最上方設定的 PING_TARGETS
    if not lucky_user_id:
        if PING_TARGETS:
            lucky_user_id = random.choice(PING_TARGETS)
            print(f"【🧠 目標備援】目前暫無互動記憶，使用預設的後備 PING_TARGETS：{lucky_user_id}")
        else:
            # 如果連後備設定都沒有，就直接結束，避免報錯
            print("【🚨 失敗】找不到任何可以 @ 的目標使用者，取消本次自主發言。")
            return

    user_mention = f"<@{lucky_user_id}>"
    
    async with channel.typing():
        autonomous_prompt = (
            f"【系統事件（不可對外洩漏）】妳現在在群組裡覺得有點無聊，想找 {user_mention} 聊天。 "
            f"請根據妳傲嬌的性格，主動向他搭話、分享心情或鬥嘴。 "
            f"字數請控制在 1~3 句話之內。絕對不可以唸出「【系統事件】」這幾個字！"
        )

        if channel_id not in conversation_history:
            conversation_history[channel_id] = []
        history = conversation_history[channel_id]

        messages = [{"role": "system", "content": SYSTEM_SETTING}] + history + [{"role": "user", "content": autonomous_prompt}]
        bot_reply = await fetch_ai_response(messages)

        if bot_reply:
            conversation_history[channel_id].append({"role": "user", "content": f"【妳主動搭話】對 {user_mention} 說話"})
            conversation_history[channel_id].append({"role": "assistant", "content": bot_reply})
            if len(conversation_history[channel_id]) > 50:
                conversation_history[channel_id] = conversation_history[channel_id][-50:]

            await channel.send(bot_reply, allowed_mentions=smart_mentions)
            print("【🧠 自主成功】主動標記發言成功！")

# ────────────────────────────────────────────────────────
# 4. 💬 一般訊息回覆處理 (真人動態連發版)
# ────────────────────────────────────────────────────────
@bot.event
async def on_message(message):
    if message.author == bot.user or message.mention_everyone:
        return

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

    if should_trigger:
        if not user_prompt:
            await message.channel.send("找我嗎~？", allowed_mentions=smart_mentions)
            return

        async with message.channel.typing():
            channel_id = message.channel.id
            user_nick = message.author.display_name
            user_id_name = message.author.name
            user_mention_code = f"<@{message.author.id}>"
            
            formatted_prompt = (
                f"【發訊人資訊】顯示暱稱：{user_nick} | 帳號ID：{user_id_name} | 標記此人的代碼：{user_mention_code}\n"
                f"訊息內容：「{user_prompt}」"
            )

            if channel_id not in conversation_history:
                conversation_history[channel_id] = []
            
            history = conversation_history[channel_id]
            messages = [{"role": "system", "content": SYSTEM_SETTING}] + history + [{"role": "user", "content": formatted_prompt}]

            # 1️⃣ 呼叫 API 產生第一句回覆
            bot_reply = await fetch_ai_response(messages)

            if bot_reply is None:
                await message.reply("（角色暫時登出中，請稍後再試...）", allowed_mentions=smart_mentions)
                return

            # 將第一句存入對話記憶
            conversation_history[channel_id].append({"role": "user", "content": formatted_prompt})
            conversation_history[channel_id].append({"role": "assistant", "content": bot_reply})
            if len(conversation_history[channel_id]) > 50:
                conversation_history[channel_id] = conversation_history[channel_id][-50:]

            # 2️⃣ 用「回覆 (Reply)」的方式發送第一句話
            await message.reply(bot_reply, allowed_mentions=smart_mentions)

            # 3️⃣ 🧠 真人連發第二句核心機制 (隨機機率，這裡設定 40% 機率會連發)
            # 如果你想讓她更常連發，可以把 0.4 改成 0.5 (50%) 或 0.6 (60%)
            if random.random() < 0.4:
                
                # 模擬真人「頓一下、正在打字」的等待時間 (隨機 1.5 ~ 3.0 秒)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                
                # 在 Discord 頻道顯示「7L 正在輸入...」
                async with message.channel.typing():
                    
                    # 💡 給 AI 的秘密後台提示：告訴她她剛說了什麼，並要她傲嬌地「追加」一句話
                    follow_up_prompt = (
                        f"【系統提示（不可外洩）】妳剛剛對他說了：「{bot_reply}」。"
                        f"請像真實人類傳訊息一樣，傲嬌地「再傳一則短訊息」補充（例如：突然想到什麼、多一句碎碎念、溫馨叮嚀、催促、或者傲嬌地質問）。"
                        f"請直接說出妳的對話台詞，字數嚴格限制在 1 句話之內。絕對禁止吐出任何系統格式、括號或後台提示字眼！"
                    )
                    
                    # 把新的追加提示當作 user 的輸入，結合之前的歷史紀錄
                    updated_history = conversation_history[channel_id]
                    second_messages = [{"role": "system", "content": SYSTEM_SETTING}] + updated_history + [{"role": "user", "content": follow_up_prompt}]
                    
                    # 4️⃣ 呼叫 API 產生第二句話
                    second_reply = await fetch_ai_response(second_messages)
                    
                    if second_reply:
                        # 將第二句也存入歷史紀錄 (只存 AI 的回覆，不存系統提示)
                        conversation_history[channel_id].append({"role": "assistant", "content": second_reply})
                        if len(conversation_history[channel_id]) > 50:
                            conversation_history[channel_id] = conversation_history[channel_id][-50:]
                        
                        # 稍微等個 0.5 秒讓打字動態收尾，感覺更逼真
                        await asyncio.sleep(0.5)
                        
                        # 用「一般發送 (Send)」發出第二句，這樣在頻道裡看起來就像真人洗板連發一樣！
                        await message.channel.send(second_reply, allowed_mentions=smart_mentions)

    await bot.process_commands(message)
    
# ────────────────────────────────────────────────────────
# 5. 🧠 跨平台備援核心（已升級：雙 Groq 帳號動態分流版）
# ────────────────────────────────────────────────────────
async def fetch_ai_response(messages):
    for item in MODEL_POOLS:
        provider = item["provider"]
        model_name = item["model"]
        try:
            if provider == "groq":
                # 💡 關鍵改動：從目前輪到的項目中，動態取出綁定的 Client (A帳號或B帳號)
                target_client = item.get("client")
                
                if not target_client:
                    print(f"【⚠️ 跳過】Groq 模型 {model_name} 缺少對應的金鑰環境變數")
                    continue
                    
                print(f"【🧠 嘗試】正在使用 Groq 模型 {model_name}...")
                chat_completion = await target_client.chat.completions.create(
                    messages=messages, 
                    model=model_name
                )
                return chat_completion.choices[0].message.content
                
            elif provider == "gemini":
                if not GEMINI_API_KEY: continue
                print(f"【🧠 嘗試】正在使用 Gemini 模型 {model_name}...")
                url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                headers = {"Authorization": f"Bearer {GEMINI_API_KEY}", "Content-Type": "application/json"}
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"model": model_name, "messages": messages}, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data["choices"][0]["message"]["content"]
                            
            elif provider == "openrouter":
                if not OPENROUTER_API_KEY: continue
                print(f"【🧠 嘗試】正在使用 OpenRouter 模型 {model_name}...")
                url = "https://openrouter.ai/api/v1/chat/completions"
                headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": "https://render.com", "X-Title": "7L Bot"}
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"model": model_name, "messages": messages}, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data["choices"][0]["message"]["content"]
                            
        except Exception as e:
            print(f"【⚠️ 失敗】{provider} 的 {model_name} 呼叫失敗: {e}。切換下一個備用腦...")
            await asyncio.sleep(1)  # 💡 增加 1 秒緩衝，防止網路瞬間抽搐時連鎖秒退
            continue
            
    return None
# ────────────────────────────────────────────────────────
# 🌐 6. 騙 Render 檢查的「虛擬網頁」與啟動區塊
# ────────────────────────────────────────────────────────
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"All Miku & Sisters Bots are alive!")

    def log_message(self, format, *args):
        # 隱藏伺服器的連線日誌，避免刷頻
        return

def run_backup_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    server.serve_forever()

if __name__ == "__main__":
    # 建立一個背景執行緒來跑 Dummy Server
    server_thread = threading.Thread(target=run_backup_server)
    server_thread.daemon = True # 當主程式關閉時，這個執行緒也會跟著關閉
    server_thread.start()
    print("【🌐 系統通知】虛擬網頁伺服器已在背景啟動 (準備接客 Ping)！")

    # 啟動 Discord 機器人 (主執行緒)
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("【錯誤】找不到 DISCORD_TOKEN_7L，請確認環境變數是否設定正確！")
