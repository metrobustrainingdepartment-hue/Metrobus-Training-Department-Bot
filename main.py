import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
import nest_asyncio

nest_asyncio.apply()

# File path for saving active sessions state across restarts
SESSIONS_FILE = "sessions.json"

# Guild
GUILD_ID = int(os.getenv("GUILD_ID", "1554533436033597550"))

# Channels
PUBLIC_ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("PUBLIC_ANNOUNCEMENT_CHANNEL_ID", "1555067926774677525"))
INSTRUCTOR_COMM_CHANNEL_ID = int(os.getenv("INSTRUCTOR_COMM_CHANNEL_ID", "1554825629030027274"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "1535668588361416704"))

# Management & Staff Roles
DEPARTMENT_HEAD_ROLE_ID = int(os.getenv("DEPARTMENT_HEAD_ROLE_ID", "1554534391466823781"))
TRAINING_DEPT_ROLE_ID = int(os.getenv("TRAINING_DEPT_ROLE_ID", "1554535058805891164"))

# Announcement Ping Roles
PCV_DRIVER_ROLE_ID = int(os.getenv("PCV_DRIVER_ROLE_ID", "1554535379992842310"))
TD_DRIVER_ROLE_ID = int(os.getenv("TD_DRIVER_ROLE_ID", "1554845505975226389"))

# Reaction / Self-Assignable Roles
SHIFT_PING_ROLE_ID = int(os.getenv("SHIFT_PING_ROLE_ID", "1556564459227713556"))
REACTION_ROLE_MESSAGE_ID = int(os.getenv("REACTION_ROLE_MESSAGE_ID", "1557671185313959987"))
REACTION_ROLE_EMOJI_ID = int(os.getenv("REACTION_ROLE_EMOJI_ID", "1555074907887501363"))

# Bot Status & Version Definitions
BOT_VERSION = "1.0"
BOT_STAGE = "Public Release"
LAST_UPDATE = "20261008 1649"

# Bot Setup with explicitly enabled reaction intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Active sessions dictionary keyed by host_id (int)
sessions = {}

def save_sessions_to_file():
    data = {}
    for host_id, session in sessions.items():
        data[str(host_id)] = {
            "active": session["active"],
            "session_type": session["session_type"],
            "date_str": session["date_str"],
            "time_str": session["time_str"],
            "quota": session["quota"],
            "unix_timestamp": session["unix_timestamp"],
            "host_id": session["host_id"],
            "announcement_msg_id": session["announcement_msg_id"],
            "ping_msg_id": session["ping_msg_id"],
            "reminder_sent": session["reminder_sent"],
            "link_ping_msg_id": session["link_ping_msg_id"],
            "link_msg_id": session["link_msg_id"],
            "thread_id": session["thread_id"],
            "server_locked": session["server_locked"],
            "lock_msg_id": session["lock_msg_id"],
            "track_date_key": session["track_date_key"],
            "messages_to_clear": session["messages_to_clear"]
        }
    try:
        with open(SESSIONS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Error saving session state: {e}")

def load_sessions_from_file():
    if not os.path.exists(SESSIONS_FILE):
        return
    try:
        with open(SESSIONS_FILE, "r") as f:
            data = json.load(f)
            for host_id_str, s in data.items():
                host_id = int(host_id_str)
                sessions[host_id] = {
                    "active": s.get("active", False),
                    "session_type": s.get("session_type"),
                    "date_str": s.get("date_str"),
                    "time_str": s.get("time_str"),
                    "quota": s.get("quota"),
                    "unix_timestamp": s.get("unix_timestamp"),
                    "host_id": host_id,
                    "announcement_msg_id": s.get("announcement_msg_id"),
                    "ping_msg_id": s.get("ping_msg_id"),
                    "reminder_sent": s.get("reminder_sent", False),
                    "reminder_task": None,
                    "auto_lock_task": None,
                    "link_ping_msg_id": s.get("link_ping_msg_id"),
                    "link_msg_id": s.get("link_msg_id"),
                    "thread_id": s.get("thread_id"),
                    "server_locked": s.get("server_locked", False),
                    "lock_msg_id": s.get("lock_msg_id"),
                    "track_date_key": s.get("track_date_key"),
                    "messages_to_clear": s.get("messages_to_clear", [])
                }
    except Exception as e:
        print(f"Error loading session state: {e}")

# Helper functions
def has_role_or_above(member: discord.Member, target_role_id: int) -> bool:
    if member.guild_permissions.administrator:
        return True
    target_role = member.guild.get_role(target_role_id)
    if not target_role:
        return False
    return any(role.position >= target_role.position for role in member.roles)

def is_training_or_head():
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member):
            await ctx.send("Insufficient rank. `703`")
            return False
        if has_role_or_above(ctx.author, DEPARTMENT_HEAD_ROLE_ID) or has_role_or_above(ctx.author, TRAINING_DEPT_ROLE_ID):
            return True
        await ctx.send("Insufficient rank. `703`")
        return False
    return commands.check(predicate)

def is_department_head():
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member):
            await ctx.send("Insufficient rank. `703`")
            return False
        if has_role_or_above(ctx.author, DEPARTMENT_HEAD_ROLE_ID):
            return True
        await ctx.send("Insufficient rank. `703`")
        return False
    return commands.check(predicate)

def create_announcement_embed(session_type: str, unix_timestamp: int, host_mention: str, quota: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"NEXTransit {session_type.upper()} Training - <t:{unix_timestamp}:F>",
        color=discord.Color.blue()
    )
    embed.add_field(name="Host", value=host_mention, inline=True)
    embed.add_field(name="Trainee Quota", value=str(quota), inline=True)
    reminders = (
        "1. Please join the NEXTransit Roblox community\n"
        "2. Link will be released 10 minutes before the starting time\n"
        "3. The training may start earlier if training quota is reached"
    )
    embed.add_field(name="Reminders before joining:", value=reminders, inline=False)
    return embed

async def schedule_training_reminder(host_id: int, host_mention: str, session_type: str, start_timestamp: int):
    now_ts = datetime.now(timezone.utc).timestamp()
    reminder_ts = start_timestamp - 1200  # 20 minutes before start time
    
    internal_channel = bot.get_channel(INSTRUCTOR_COMM_CHANNEL_ID)
    if not internal_channel:
        try:
            internal_channel = await bot.fetch_channel(INSTRUCTOR_COMM_CHANNEL_ID)
        except Exception:
            return

    if now_ts >= reminder_ts:
        msg = f"Training reminder: {host_mention} Your {session_type} training starts in less than 20 minutes! Please prepare to host punctually."
        reminder_msg = await internal_channel.send(msg)
        if host_id in sessions:
            sessions[host_id]["reminder_sent"] = True
            if reminder_msg:
                sessions[host_id]["messages_to_clear"].append(reminder_msg.id)
            save_sessions_to_file()
    else:
        wait_seconds = reminder_ts - now_ts
        try:
            await asyncio.sleep(wait_seconds)
            msg = f"Training reminder: {host_mention} Your {session_type} training starts in 20 minutes! Please prepare to host punctually."
            reminder_msg = await internal_channel.send(msg)
            if host_id in sessions:
                sessions[host_id]["reminder_sent"] = True
                if reminder_msg:
                    sessions[host_id]["messages_to_clear"].append(reminder_msg.id)
                save_sessions_to_file()
        except asyncio.CancelledError:
            pass

async def schedule_auto_lock(host_id: int, start_timestamp: int):
    now_ts = datetime.now(timezone.utc).timestamp()
    wait_seconds = start_timestamp - now_ts
    if wait_seconds > 0:
        try:
            await asyncio.sleep(wait_seconds)
        except asyncio.CancelledError:
            return

    session = sessions.get(host_id)
    if session and session["active"] and not session["server_locked"] and session["link_msg_id"]:
        ann_channel = bot.get_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID) or await bot.fetch_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID)
        if ann_channel:
            for msg_id in [session["link_msg_id"], session["link_ping_msg_id"]]:
                if msg_id:
                    try:
                        msg_to_del = await ann_channel.fetch_message(msg_id)
                        await msg_to_del.delete()
                    except Exception:
                        pass
            
            embed = discord.Embed(
                title="Server Locked",
                description="Server locked due to starting time reached. Please join the next session if you failed to join this session.",
                color=discord.Color.red()
            )
            lock_msg = await ann_channel.send(embed=embed)
            session["server_locked"] = True
            session["lock_msg_id"] = lock_msg.id
            session["messages_to_clear"].append(lock_msg.id)
            save_sessions_to_file()

# --- Events ---

@bot.event
async def on_ready():
    await bot.change_presence(status=discord.Status.online, activity=discord.Game(name="NEXTransit Training"))
    load_sessions_from_file()

    for host_id, session in sessions.items():
        if session["active"] and session["unix_timestamp"]:
            host_mention = f"<@{host_id}>"
            
            if not session["reminder_sent"]:
                session["reminder_task"] = asyncio.create_task(
                    schedule_training_reminder(host_id, host_mention, session["session_type"], session["unix_timestamp"])
                )
            
            if not session["server_locked"]:
                session["auto_lock_task"] = asyncio.create_task(
                    schedule_auto_lock(host_id, session["unix_timestamp"])
                )

    print(f"Bot connected successfully as {bot.user} and status set to Online!")

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.message_id != REACTION_ROLE_MESSAGE_ID or payload.emoji.id != REACTION_ROLE_EMOJI_ID:
        return

    guild = bot.get_guild(payload.guild_id) or await bot.fetch_guild(payload.guild_id)
    if not guild:
        return

    role = guild.get_role(SHIFT_PING_ROLE_ID)
    if not role:
        return

    member = payload.member
    if not member:
        try:
            member = await guild.fetch_member(payload.user_id)
        except discord.NotFound:
            return

    if member and not member.bot:
        try:
            await member.add_roles(role)
            print(f"Added role {role.name} to {member.display_name}")
        except Exception as e:
            print(f"Failed to add role: {e}")

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.message_id != REACTION_ROLE_MESSAGE_ID or payload.emoji.id != REACTION_ROLE_EMOJI_ID:
        return

    guild = bot.get_guild(payload.guild_id) or await bot.fetch_guild(payload.guild_id)
    if not guild:
        return

    role = guild.get_role(SHIFT_PING_ROLE_ID)
    if not role:
        return

    try:
        member = await guild.fetch_member(payload.user_id)
    except discord.NotFound:
        return

    if member and not member.bot:
        try:
            await member.remove_roles(role)
            print(f"Removed role {role.name} from {member.display_name}")
        except Exception as e:
            print(f"Failed to remove role: {e}")

# --- Commands ---

@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    embed = discord.Embed(
        title="NEXTransit Bot Command Help",
        color=discord.Color.blue()
    )

    # Public Available Commands
    public_cmds = "`!version` - Displays current bot version and testing stage."
    embed.add_field(name="Public Commands", value=public_cmds, inline=False)

    is_tdp = False
    is_dh = False

    if isinstance(ctx.author, discord.Member):
        is_dh = has_role_or_above(ctx.author, DEPARTMENT_HEAD_ROLE_ID)
        is_tdp = is_dh or has_role_or_above(ctx.author, TRAINING_DEPT_ROLE_ID)

    # Training Department Pegrsonnel Commands
    if is_tdp:
        tdp_cmds = (
            "`!announce <PCV/SD> <YYYYMMDD> <HHMM> <Quota>` - Post a training session announcement.\n"
            "`!update <variable> <value>` - Update session details (`type`, `date`, `time`, `traineequota`).\n"
            "`!link <link>` - Post the game link and create a training communication thread.\n"
            "`!full` - Lock the training session manually due to full quota.\n"
            "`!end [emoji]` - End the session and archive the communication thread."
        )
        embed.add_field(name="Training Department Commands", value=tdp_cmds, inline=False)

    # Department Head Commands
    if is_dh:
        dh_cmds = (
            "`!clear [date/host]` - Delete all session messages and remove the communication thread.\n"
            "`!delete <message_id>` - Delete a target message.\n"
            "`!send <channel_id> <message>` - Send a message to a specific channel.\n"
            "`!error` - Display the bot error code reference list."
        )
        embed.add_field(name="Department Head Commands", value=dh_cmds, inline=False)

    await ctx.send(embed=embed)

@bot.command(name="error")
@is_department_head()
async def error_cmd(ctx: commands.Context):
    embed = discord.Embed(
        title="NEXTransit Bot Error Code Reference List",
        color=discord.Color.red()
    )
    error_codes = (
        "`701`: Incorrect command format or invalid argument.\n"
        "`702`: Failed to send message to target channel.\n"
        "`703`: Insufficient rank/permission to execute command.\n"
        "`704`: Command cooldown active or execution error.\n"
        "`705`: Action unavailable (no active session or requirements not met).\n"
        "`706`: No active session found to clear."
    )
    embed.add_field(name="Error Codes", value=error_codes, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="version")
async def version(ctx: commands.Context):
    await ctx.send(f"**Bot Version:** {BOT_VERSION}\n**Stage:** {BOT_STAGE}\n**Last updated on:** {LAST_UPDATE}")

@bot.command(name="delete")
@is_department_head()
async def delete_cmd(ctx: commands.Context, message_id: int):
    try:
        target_msg = await ctx.channel.fetch_message(message_id)
        await target_msg.delete()
    except Exception:
        pass

    try:
        await ctx.message.delete()
    except Exception:
        pass

@bot.command(name="send")
@is_department_head()
async def send_cmd(ctx: commands.Context, channel_id: int, *, message_content: str):
    target_channel = bot.get_channel(channel_id)
    if not target_channel:
        try:
            target_channel = await bot.fetch_channel(channel_id)
        except Exception:
            return

    if target_channel:
        await target_channel.send(content=message_content)

@bot.command(name="announce")
@commands.cooldown(1, 5, commands.BucketType.user)
@is_training_or_head()
async def announce(ctx: commands.Context, session_type: str, date_str: str, time_str: str, quota: int):
    host_id = ctx.author.id
    
    if host_id in sessions and sessions[host_id]["active"]:
        await ctx.send("You already have an active training session! Please end or clear it before announcing a new one.")
        return

    session_upper = session_type.upper()
    if session_upper not in ["PCV", "SD"]:
        await ctx.send("Please use the format !announce <PCV/SD> <YYYYMMDD> <HHMM> <TraineeQuota> `701`")
        return

    try:
        tz_gmt8 = timezone(timedelta(hours=8))
        dt_str = f"{date_str} {time_str}"
        dt = datetime.strptime(dt_str, "%Y%m%d %H%M").replace(tzinfo=tz_gmt8)
        unix_timestamp = int(dt.timestamp())
    except ValueError:
        await ctx.send("Please use the format !announce <PCV/SD> <YYYYMMDD> <HHMM> <TraineeQuota> `701`")
        return

    ping_role_content = f"<@&{TD_DRIVER_ROLE_ID}>" if session_upper == "PCV" else f"<@&{PCV_DRIVER_ROLE_ID}>"
    embed = create_announcement_embed(session_upper, unix_timestamp, ctx.author.mention, quota)

    try:
        channel = bot.get_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID) or await bot.fetch_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID)
        if channel:
            ping_msg = await channel.send(content=ping_role_content)
            ann_msg = await channel.send(embed=embed)
            await ctx.send("Announcement successfully posted!")

            sessions[host_id] = {
                "active": True,
                "session_type": session_upper,
                "date_str": date_str,
                "time_str": time_str,
                "quota": quota,
                "unix_timestamp": unix_timestamp,
                "host_id": host_id,
                "announcement_msg_id": ann_msg.id,
                "ping_msg_id": ping_msg.id,
                "reminder_sent": False,
                "reminder_task": None,
                "auto_lock_task": None,
                "link_ping_msg_id": None,
                "link_msg_id": None,
                "thread_id": None,
                "server_locked": False,
                "lock_msg_id": None,
                "track_date_key": date_str,
                "messages_to_clear": [ping_msg.id, ann_msg.id]
            }

            save_sessions_to_file()

            sessions[host_id]["reminder_task"] = asyncio.create_task(
                schedule_training_reminder(host_id, ctx.author.mention, session_upper, unix_timestamp)
            )
            sessions[host_id]["auto_lock_task"] = asyncio.create_task(
                schedule_auto_lock(host_id, unix_timestamp)
            )
        else:
            await ctx.send("Unsuccessful excursion. Please contact developer. `702`")
    except Exception:
        await ctx.send("Unsuccessful excursion. Please contact developer. `702`")

@bot.command(name="update")
@commands.cooldown(1, 5, commands.BucketType.user)
@is_training_or_head()
async def update(ctx: commands.Context, var_type: str, *, new_value: str):
    host_id = ctx.author.id
    session = sessions.get(host_id)

    if not session or not session["active"]:
        await ctx.send("Wrong format! Please use !update <variable> <value> 701")
        return

    var_lower = var_type.lower()
    if var_lower not in ["type", "date", "time", "traineequota"]:
        await ctx.send("Wrong format! Please use !update <variable> <value> 701")
        return

    orig_timestamp = session["unix_timestamp"]
    s_type = session["session_type"]
    d_str = session["date_str"]
    t_str = session["time_str"]
    q_val = session["quota"]

    if var_lower == "type":
        val_upper = new_value.upper()
        if val_upper not in ["PCV", "SD"]:
            await ctx.send("Wrong format! Please use !update <variable> <value> 701")
            return
        s_type = val_upper
    elif var_lower == "date":
        d_str = new_value.strip()
    elif var_lower == "time":
        t_str = new_value.strip()
    elif var_lower == "traineequota":
        try:
            q_val = int(new_value.strip())
        except ValueError:
            await ctx.send("Wrong format! Please use !update <variable> <value> 701")
            return

    try:
        tz_gmt8 = timezone(timedelta(hours=8))
        dt_full = f"{d_str} {t_str}"
        dt = datetime.strptime(dt_full, "%Y%m%d %H%M").replace(tzinfo=tz_gmt8)
        new_unix_ts = int(dt.timestamp())
    except ValueError:
        await ctx.send("Wrong format! Please use !update <variable> <value> 701")
        return

    session["session_type"] = s_type
    session["date_str"] = d_str
    session["time_str"] = t_str
    session["quota"] = q_val
    session["unix_timestamp"] = new_unix_ts
    session["track_date_key"] = d_str

    channel = bot.get_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID) or await bot.fetch_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID)
    if channel and session["announcement_msg_id"]:
        try:
            old_msg = await channel.fetch_message(session["announcement_msg_id"])
            await old_msg.delete()
        except Exception:
            pass

    embed = create_announcement_embed(s_type, new_unix_ts, ctx.author.mention, q_val)
    content_text = f"Details of the <t:{orig_timestamp}:F> training session has been updated! Please check the updated details as the following:"
    
    new_ann_msg = await channel.send(content=content_text, embed=embed)
    session["announcement_msg_id"] = new_ann_msg.id
    session["messages_to_clear"].append(new_ann_msg.id)

    save_sessions_to_file()

    if session["reminder_task"]:
        session["reminder_task"].cancel()
    if session["auto_lock_task"]:
        session["auto_lock_task"].cancel()

    session["reminder_task"] = asyncio.create_task(
        schedule_training_reminder(host_id, ctx.author.mention, s_type, new_unix_ts)
    )
    session["auto_lock_task"] = asyncio.create_task(
        schedule_auto_lock(host_id, new_unix_ts)
    )

    await ctx.send("Announcement successfully updated!")

@bot.command(name="link")
@is_training_or_head()
async def link_cmd(ctx: commands.Context, *, link_text: str):
    host_id = ctx.author.id
    session = sessions.get(host_id)

    if not session or not session["active"]:
        await ctx.send("Announcement not found! `705`")
        return
        
    if not session["reminder_sent"]:
        await ctx.send("Training reminder has not been posted yet! `705`")
        return

    ann_channel = bot.get_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID) or await bot.fetch_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID)
    if ann_channel:
        ping_role_id = TD_DRIVER_ROLE_ID if session["session_type"] == "PCV" else PCV_DRIVER_ROLE_ID
        ping_content = f"<@&{ping_role_id}>"

        ts_formatted = f"<t:{session['unix_timestamp']}:F>"
        s_type = session["session_type"]

        embed = discord.Embed(
            title="NEXTransit Training Centre Server Link",
            description=f"The {ts_formatted} training is now available for joining, please join the server through this link: {link_text}",
            color=discord.Color.blue()
        )

        ping_msg = await ann_channel.send(content=ping_content)
        link_msg = await ann_channel.send(embed=embed)

        tz_gmt8 = timezone(timedelta(hours=8))
        title_dt = datetime.fromtimestamp(session["unix_timestamp"], tz=tz_gmt8)
        title_time_str = title_dt.strftime("%Y/%m/%d %H:%M GMT+8")

        thread_name = f"NEXTransit {s_type} {title_time_str} Training Communication"
        thread = await ann_channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.public_thread
        )

        thread_first_msg = (
            f"**THIS IS A COMMUNICATION THREAD FOR INSTRUCTOR AND TRAINEES WHO ARE PARTICIPATING IN THE {ts_formatted} {s_type} TRAINING SESSION.**\n\n"
            f"Session Host: {ctx.author.mention}"
        )
        await thread.send(content=thread_first_msg)

        session["link_ping_msg_id"] = ping_msg.id
        session["link_msg_id"] = link_msg.id
        session["thread_id"] = thread.id
        session["messages_to_clear"].extend([ping_msg.id, link_msg.id])

        save_sessions_to_file()
        await ctx.send("Link and communication thread successfully posted!")

@bot.command(name="full")
@is_training_or_head()
async def full_cmd(ctx: commands.Context):
    host_id = ctx.author.id
    session = sessions.get(host_id)

    if not session or not session["active"] or session["link_msg_id"] is None:
        await ctx.send("Announcement not found! `705`")
        return

    ann_channel = bot.get_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID) or await bot.fetch_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID)
    if ann_channel:
        for msg_id in [session["link_msg_id"], session["link_ping_msg_id"]]:
            if msg_id:
                try:
                    link_msg = await ann_channel.fetch_message(msg_id)
                    await link_msg.delete()
                except Exception:
                    pass

        embed = discord.Embed(
            title="Server Locked",
            description="Server locked due to maximum number of trainee quota reached. Please join the next session if you failed to join this session.",
            color=discord.Color.red()
        )
        lock_msg = await ann_channel.send(embed=embed)
        session["server_locked"] = True
        session["lock_msg_id"] = lock_msg.id
        session["messages_to_clear"].append(lock_msg.id)

        save_sessions_to_file()
        await ctx.send("Server locked status updated!")

@bot.command(name="end")
@is_training_or_head()
async def end_cmd(ctx: commands.Context, emoji: str = None):
    host_id = ctx.author.id
    session = sessions.get(host_id)

    if not session or not session["active"] or not session["server_locked"]:
        await ctx.send("The training cannot end since it haven't started. `705`")
        return

    ann_channel = bot.get_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID) or await bot.fetch_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID)
    if ann_channel:
        if session["lock_msg_id"]:
            try:
                lock_msg = await ann_channel.fetch_message(session["lock_msg_id"])
                await lock_msg.delete()
            except Exception:
                pass

        ts = session["unix_timestamp"]
        embed = discord.Embed(
            title=f"Detailed Result of the <t:{ts}:F> Session",
            color=discord.Color.blue()
        )
        
        if emoji:
            embed.description = f"If you need individual detailed result of this session, please react {emoji} to this message. Please make sure your Discord direct message permission from non-friends is opened before doing so."
        else:
            embed.description = "If you need individual detailed result of this session, please react to this message. Please make sure your Discord direct message permission from non-friends is opened before doing so."

        end_msg = await ann_channel.send(embed=embed)
        session["messages_to_clear"].append(end_msg.id)

        if session["thread_id"]:
            try:
                thread = bot.get_channel(session["thread_id"]) or await bot.fetch_channel(session["thread_id"])
                if thread:
                    await thread.send("THIS TRAINING HAS CONCLUDED. IF YOU HAVE ANYMORE ENQUIRY PLEASE OPEN A TICKET OR CONTACT THE INSTRUCTOR DIRECTLY.")
                    await thread.edit(archived=True, locked=True)
            except Exception as e:
                print(f"Error closing thread: {e}")

        save_sessions_to_file()
        await ctx.send("Training successfully ended!")

@bot.command(name="clear")
@is_department_head()
async def clear_cmd(ctx: commands.Context, target_arg: str = None):
    target_host_id = ctx.author.id

    if target_arg:
        clean_id = target_arg.replace("<@", "").replace(">", "").replace("!", "")
        if clean_id.isdigit():
            target_host_id = int(clean_id)

    session = sessions.get(target_host_id)

    if not session or not session["active"]:
        await ctx.send("There is no message to be cleared. `706`")
        return

    if session["reminder_task"]:
        session["reminder_task"].cancel()
    if session["auto_lock_task"]:
        session["auto_lock_task"].cancel()

    # Clear posted messages in the announcement channel
    ann_channel = bot.get_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID) or await bot.fetch_channel(PUBLIC_ANNOUNCEMENT_CHANNEL_ID)
    if ann_channel:
        for msg_id in session["messages_to_clear"]:
            try:
                msg = await ann_channel.fetch_message(msg_id)
                if msg.author.id == bot.user.id:
                    await msg.delete()
            except Exception:
                pass

    # Clear posted messages in internal channel
    internal_channel = bot.get_channel(INSTRUCTOR_COMM_CHANNEL_ID) or await bot.fetch_channel(INSTRUCTOR_COMM_CHANNEL_ID)
    if internal_channel:
        for msg_id in session["messages_to_clear"]:
            try:
                msg = await internal_channel.fetch_message(msg_id)
                if msg.author.id == bot.user.id:
                    await msg.delete()
            except Exception:
                pass

    # Delete communication thread
    if session["thread_id"]:
        try:
            thread = bot.get_channel(session["thread_id"]) or await bot.fetch_channel(session["thread_id"])
            if thread:
                await thread.delete()
        except Exception as e:
            print(f"Error deleting thread during clear: {e}")

    # Remove session from active sessions dictionary
    del sessions[target_host_id]
    save_sessions_to_file()

    await ctx.send("Training messages and communication thread successfully cleared!")

# --- Error Handlers ---

@announce.error
async def announce_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send("Unsuccessful excursion. Please contact developer. `704`")
    elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        await ctx.send("Please use the format !announce <PCV/SD> <YYYYMMDD> <HHMM> <TraineeQuota> `701`")
    elif isinstance(error, commands.CheckFailure):
        pass
    else:
        await ctx.send("Unsuccessful excursion. Please contact developer. `704`")

@update.error
async def update_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send("Wrong format! Please use !update <variable> <value> 704")
    elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        await ctx.send("Wrong format! Please use !update <variable> <value> 701")
    elif isinstance(error, commands.CheckFailure):
        pass
    else:
        await ctx.send("Wrong format! Please use !update <variable> <value> 701")

TOKEN = os.getenv("DISCORD_TOKEN")  

bot.run(TOKEN)
