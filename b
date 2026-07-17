# ────────────────────────────────────────────────────────
# 4. 💬 一般訊息回覆處理
# ────────────────────────────────────────────────────────"""
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
            
            # 這裡完美對應了 COMMON_RULES 裡面的結構化要求
            formatted_prompt = (
                f"【發訊人資訊】顯示暱稱：{user_nick} | 帳號ID：{user_id_name} | 標記此人的代碼：{user_mention_code}\n"
                f"訊息內容：「{user_prompt}」"
            )

            if channel_id not in conversation_history:
                conversation_history[channel_id] = []
            
            history = conversation_history[channel_id]
            messages = [{"role": "system", "content": SYSTEM_SETTING}] + history + [{"role": "user", "content": formatted_prompt}]

            bot_reply = await fetch_ai_response(messages)

            if bot_reply is None:
                await message.reply("（角色暫時登出中，請稍後再試...）", allowed_mentions=smart_mentions)
                return

            conversation_history[channel_id].append({"role": "user", "content": formatted_prompt})
            conversation_history[channel_id].append({"role": "assistant", "content": bot_reply})
            if len(conversation_history[channel_id]) > 50:
                conversation_history[channel_id] = conversation_history[channel_id][-50:]

            await message.reply(bot_reply, allowed_mentions=smart_mentions)

    await bot.process_commands(message)
