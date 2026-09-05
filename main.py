import asyncio
import datetime
import json
import os
import zoneinfo
import discord

from discord.ext import commands

# --- Version & Bot Metadata ---
BOT_VERSION = "1.1.2"
BOT_RELEASE_DATE = "01/09/2026"

# --- Config & Initialization ---
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

ANNOUNCEMENT_CHANNEL_ID = 1223678863754657853
REMINDER_CHANNEL_ID = 1300484583170511052
LOG_CHANNEL_ID = 1535668588361416704

TRAINING_DEPT_ROLE_ID = 1240213202734678027
DEPT_MANAGER_ROLE_ID = 1430206480669216868
TRAINEE_ROLE_PING = "<@&1531657175359820007>"

YOUR_USER_ID = 946346020902674472
ALLOWED_GUILD_ID = 1404026674483564574

# Reaction Role IDs
TRAINING_PING_MESSAGE_ID = 1532047216846311424
TRAINING_PING_EMOJI_ID = 1278726581627650196
TRAINING_PING_ROLE_ID = 1531657175359820007

SHIFT_PING_MESSAGE_ID = 1532048683695079516
SHIFT_PING_EMOJI_ID = 1224031772514455675
SHIFT_PING_ROLE_ID = 1531888689334976623

HKT = zoneinfo.ZoneInfo("Asia/Hong_Kong")
STATE_FILE = "trainings_state.json"

trainings = {}
user_active_dms = {}
play_targets = {}
play_cooldowns = {}


# --- Persistence Helpers ---
def save_trainings_state():
  state_data = {}
  for host_id, data in trainings.items():
    state_data[str(host_id)] = {
        "training_dt": data["training_dt"].isoformat(),
        "host_mention": data["host_mention"],
        "quota": data["quota"],
        "link_posted": data.get("link_posted", False),
        "is_locked": data.get("is_locked", False),
        "ping_message_id": (
            data["ping_message"].id if data.get("ping_message") else None
        ),
        "announcement_message_id": (
            data["announcement_message"].id
            if data.get("announcement_message")
            else None
        ),
        "link_message_id": (
            data["link_message"].id if data.get("link_message") else None
        ),
        "posted_message_ids": [
            msg.id for msg in data.get("posted_messages", []) if msg
        ],
    }

  try:
    with open(STATE_FILE, "w") as f:
      json.dump(state_data, f, indent=4)
  except Exception as e:
    print(f"Error saving training state: {e}")


async def load_trainings_state():
  if not os.path.exists(STATE_FILE):
    return

  try:
    with open(STATE_FILE, "r") as f:
      state_data = json.load(f)

    announcement_channel = bot.get_channel(
        ANNOUNCEMENT_CHANNEL_ID
    ) or await bot.fetch_channel(ANNOUNCEMENT_CHANNEL_ID)
    if not announcement_channel:
      return

    for host_id_str, data in state_data.items():
      host_id = int(host_id_str)
      training_dt = datetime.datetime.fromisoformat(data["training_dt"])

      ping_msg = None
      if data.get("ping_message_id"):
        try:
          ping_msg = await announcement_channel.fetch_message(
              data["ping_message_id"]
          )
        except Exception:
          pass

      ann_msg = None
      if data.get("announcement_message_id"):
        try:
          ann_msg = await announcement_channel.fetch_message(
              data["announcement_message_id"]
          )
        except Exception:
          pass

      link_msg = None
      if data.get("link_message_id"):
        try:
          link_msg = await announcement_channel.fetch_message(
              data["link_message_id"]
          )
        except Exception:
          pass

      posted_msgs = []
      for mid in data.get("posted_message_ids", []):
        try:
          m = await announcement_channel.fetch_message(mid)
          posted_msgs.append(m)
        except Exception:
          pass

      trainings[host_id] = {
          "ping_message": ping_msg,
          "announcement_message": ann_msg,
          "posted_messages": posted_msgs,
          "training_dt": training_dt,
          "host_mention": data["host_mention"],
          "quota": data["quota"],
          "link_message": link_msg,
          "link_posted": data.get("link_posted", False),
          "is_locked": data.get("is_locked", False),
          "reminder_task": asyncio.create_task(
              send_reminder(data["host_mention"], training_dt)
          ),
          "auto_lock_task": asyncio.create_task(
              schedule_auto_lock(host_id, training_dt)
          ),
      }
    print("Successfully restored active training session state.")
  except Exception as e:
    print(f"Error loading training state: {e}")


# --- Permission Helpers ---
def user_has_role_or_higher_position(
    member: discord.Member, target_role_id: int
) -> bool:
  if member.id == YOUR_USER_ID or member.guild_permissions.administrator:
    return True

  target_role = member.guild.get_role(target_role_id)
  if not target_role:
    return False

  return any(role.position >= target_role.position for role in member.roles)


def has_role_or_above_position(required_role_id: int):

  async def predicate(ctx):
    if not isinstance(ctx.author, discord.Member):
      return False

    if user_has_role_or_higher_position(ctx.author, required_role_id):
      return True

    await ctx.send("You don't have the permission to run this command!")
    return False

  return commands.check(predicate)


def is_u47is_only():

  async def predicate(ctx):
    return ctx.author.id == YOUR_USER_ID

  return commands.check(predicate)


def is_dept_manager():

  def check(author):
    if not isinstance(author, discord.Member):
      return False
    return user_has_role_or_higher_position(author, DEPT_MANAGER_ROLE_ID)

  return check


# ==============================================================================
# 1. BACKGROUND FEATURES & REACTION ROLES
# ==============================================================================


async def send_reminder(host_mention, target_dt):
  now = datetime.datetime.now(HKT)
  reminder_dt = target_dt - datetime.timedelta(minutes=20)
  delay = (reminder_dt - now).total_seconds()

  reminder_channel = bot.get_channel(REMINDER_CHANNEL_ID)
  if delay > 0:
    await asyncio.sleep(delay)
    if reminder_channel:
      await reminder_channel.send(
          f"Reminder: {host_mention} Your MTB PCV Training starts in 20"
          " minutes! Please prepare to host on time."
      )
  else:
    if reminder_channel:
      await reminder_channel.send(
          f"Reminder: {host_mention} Your MTB PCV Training starts in less than"
          " 20 minutes! Please prepare to host on time."
      )


async def schedule_auto_lock(host_id, target_dt):
  now = datetime.datetime.now(HKT)
  delay = (target_dt - now).total_seconds()

  if delay > 0:
    await asyncio.sleep(delay)

  training = trainings.get(host_id)
  if training and training.get("link_message"):
    try:
      link_msg = training["link_message"]
      announcement_channel = link_msg.channel
      await link_msg.delete()
      if link_msg in training["posted_messages"]:
        training["posted_messages"].remove(link_msg)

      training["link_message"] = None
      training["is_locked"] = True
      save_trainings_state()

      embed = discord.Embed(
          title="Server Locked",
          description=(
              "Server locked due to starting time reached. Please join the"
              " next session if you failed to join this session."
          ),
          color=discord.Color.red(),
      )
      lock_msg = await announcement_channel.send(embed=embed)
      training["posted_messages"].append(lock_msg)
      save_trainings_state()
    except Exception as e:
      print(f"Error during auto lock: {e}")


@bot.event
async def on_ready():
  print(f"Logged in as {bot.user}!")
  await load_trainings_state()


@bot.event
async def on_message(message: discord.Message):
  if message.author.bot:
    return

  # Auto-Reply for Play Targets
  if (
      message.guild
      and message.guild.id == ALLOWED_GUILD_ID
      and message.author.id in play_targets
  ):
    data = play_targets[message.author.id]
    try:
      if data["type"] == "sticker":
        await message.reply(stickers=[data["content"]], mention_author=False)
      elif data["type"] == "image_url":
        embed = discord.Embed()
        embed.set_image(url=data["content"])
        await message.reply(embed=embed, mention_author=False)
      else:
        await message.reply(content=data["content"], mention_author=False)
    except Exception as e:
      print(f"Error in play auto-reply: {e}")

  # Incoming DM Listener
  if isinstance(message.channel, discord.DMChannel):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
      try:
        channel = await bot.fetch_channel(LOG_CHANNEL_ID)
      except Exception:
        print(f"Error: Log channel {LOG_CHANNEL_ID} not found.")
        return

    is_new_session = message.author.id not in user_active_dms

    if is_new_session:
      user_active_dms[message.author.id] = True
      welcome_msg = (
          f"`{message.author.id}` has sent a new message to the Metrobus"
          " Training Department Bot!"
      )
      await channel.send(welcome_msg)
      try:
        receipt_embed = discord.Embed(
            title="Message Received",
            description="Message received! Our staff will reply you shortly.",
            color=discord.Color.green(),
        )
        await message.author.send(embed=receipt_embed)
      except discord.Forbidden:
        pass

    embed = discord.Embed(
        title="New DM Received",
        description=(
            message.content if message.content else "*[Attachment/File Only]*"
        ),
        color=discord.Color.blue(),
        timestamp=message.created_at,
    )
    embed.set_author(
        name=f"{message.author.name}",
        icon_url=message.author.display_avatar.url,
    )
    embed.set_footer(
        text=f"User ID: {message.author.id} | Message ID: {message.id}"
    )

    files = []
    if message.attachments:
      first_att = message.attachments[0]
      if first_att.content_type and first_att.content_type.startswith("image/"):
        embed.set_image(url=first_att.url)

      for attachment in message.attachments:
        try:
          files.append(await attachment.to_file())
        except Exception as e:
          print(f"Error processing attachment: {e}")

    await channel.send(embed=embed, files=files)
    return

  await bot.process_commands(message)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
  if isinstance(before.channel, discord.DMChannel) and not before.author.bot:
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
      embed = discord.Embed(
          title="Message Edited in DM", color=discord.Color.gold()
      )
      embed.add_field(
          name="Before",
          value=before.content if before.content else "*None*",
          inline=False,
      )
      embed.add_field(
          name="After",
          value=after.content if after.content else "*None*",
          inline=False,
      )
      embed.set_footer(
          text=f"User ID: {before.author.id} | Message ID: {before.id}"
      )
      await channel.send(embed=embed)


@bot.event
async def on_message_delete(message: discord.Message):
  if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
      embed = discord.Embed(
          title="Message Deleted in DM",
          description=(
              message.content if message.content else "*[Attachment Deleted]*"
          ),
          color=discord.Color.red(),
      )
      embed.set_footer(
          text=f"User ID: {message.author.id} | Message ID: {message.id}"
      )

      if message.attachments:
        first_att = message.attachments[0]
        if (
            first_att.content_type
            and first_att.content_type.startswith("image/")
        ):
          embed.set_image(url=first_att.url)

      await channel.send(embed=embed)


@bot.event
async def on_raw_reaction_add(payload):
  guild = bot.get_guild(payload.guild_id)
  if not guild:
    return

  member = payload.member or guild.get_member(payload.user_id)
  if not member or member.bot:
    return

  role_to_add_id = None
  if (
      payload.message_id == TRAINING_PING_MESSAGE_ID
      and payload.emoji.id == TRAINING_PING_EMOJI_ID
  ):
    role_to_add_id = TRAINING_PING_ROLE_ID
  elif (
      payload.message_id == SHIFT_PING_MESSAGE_ID
      and payload.emoji.id == SHIFT_PING_EMOJI_ID
  ):
    role_to_add_id = SHIFT_PING_ROLE_ID

  if role_to_add_id:
    role = guild.get_role(role_to_add_id)
    if role:
      try:
        await member.add_roles(role)
      except Exception as e:
        print(f"Error adding role {role_to_add_id}: {e}")


@bot.event
async def on_raw_reaction_remove(payload):
  guild = bot.get_guild(payload.guild_id)
  if not guild:
    return

  member = guild.get_member(payload.user_id)
  if not member:
    try:
      member = await guild.fetch_member(payload.user_id)
    except Exception:
      return

  if not member or member.bot:
    return

  role_to_remove_id = None
  if (
      payload.message_id == TRAINING_PING_MESSAGE_ID
      and payload.emoji.id == TRAINING_PING_EMOJI_ID
  ):
    role_to_remove_id = TRAINING_PING_ROLE_ID
  elif (
      payload.message_id == SHIFT_PING_MESSAGE_ID
      and payload.emoji.id == SHIFT_PING_EMOJI_ID
  ):
    role_to_remove_id = SHIFT_PING_ROLE_ID

  if role_to_remove_id:
    role = guild.get_role(role_to_remove_id)
    if role:
      try:
        await member.remove_roles(role)
      except Exception as e:
        print(f"Error removing role {role_to_remove_id}: {e}")


# ==============================================================================
# 2. GENERAL PUBLIC COMMANDS
# ==============================================================================


@bot.command()
async def version(ctx):
  """Usage: !version"""
  await ctx.send(f"Version {BOT_VERSION}: Released {BOT_RELEASE_DATE}")


@bot.command()
async def training(ctx):
  """Usage: !training"""
  active_trainings = [
      data for data in trainings.values() if not data.get("is_locked")
  ]

  if active_trainings:
    next_training = min(active_trainings, key=lambda t: t["training_dt"])
    unix_ts = int(next_training["training_dt"].timestamp())
    timestamp_format = f"<t:{unix_ts}:f> (<t:{unix_ts}:R>)"
    await ctx.send(
        f"The next Metrobus Training for PCV is scheduled at {timestamp_format}"
    )
  else:
    await ctx.send(
        "Seems like there is no any training scheduled for now! You can go"
        " ahead and get yourself the Training Ping role to receive"
        " notification when one is being posted."
    )


@bot.command()
async def help(ctx):
  """Usage: !help"""
  is_dm_or_u47is = (
      isinstance(ctx.author, discord.Member)
      and user_has_role_or_higher_position(ctx.author, DEPT_MANAGER_ROLE_ID)
  )
  is_training_dept = (
      isinstance(ctx.author, discord.Member)
      and user_has_role_or_higher_position(ctx.author, TRAINING_DEPT_ROLE_ID)
  )
  is_in_allowed_guild = ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID

  embed = discord.Embed(
      title="Available Commands",
      description="Here are the commands you have permission to use:",
      color=discord.Color.blue(),
  )

  gen_cmds = (
      "`!version` - Display bot version and release date.\n"
      "`!training` - Display upcoming training schedule.\n"
      "`!help` - Display available commands."
  )
  embed.add_field(name="Public Commands", value=gen_cmds, inline=False)

  if is_in_allowed_guild:
    children_cmds = (
        "`!play <@user> <emoji/sticker/text/file>` - Auto-reply user's"
        " messages with target content.\n"
        "`!stopplay <@user>` - Stop auto-replying for a target user."
    )
    if ctx.author.id == YOUR_USER_ID:
      children_cmds += (
          "\n`!playlist` - View all active auto-reply target users."
      )

    embed.add_field(
        name="U47is Children Commands", value=children_cmds, inline=False
    )

  if is_training_dept or is_dm_or_u47is:
    training_cmds = (
        "`!announce <DD/MM/YYYY> <HH:MM> <quota>` - Announce a training"
        " session.\n"
        "`!update <field> <value>` - Update session details (time, host,"
        " traineequota).\n"
        "`!link <server_link>` - Post the training server link.\n"
        "`!full` - Set server status to FULL and remove link.\n"
        "`!end` - End session and post detailed result reaction message."
    )
    embed.add_field(
        name="Training Department Commands",
        value=training_cmds,
        inline=False,
    )

  if is_dm_or_u47is:
    manager_cmds = (
        "`!clear` - Clear active training session and feedback messages.\n"
        "`!dm <user_id> [message]` - Send a Direct Message to a user (supports"
        " attachments).\n"
        "`!dmclose <user_id>` - Close DM ticket and notify user.\n"
        "`!text <channel_id> <message>` - Post a message to a specific"
        " channel.\n"
        "`!delete <message_id> [channel_id]` - Delete a message by ID from"
        " any channel."
    )
    embed.add_field(
        name="Department Head / Management Commands",
        value=manager_cmds,
        inline=False,
    )

  await ctx.send(embed=embed)


# ==============================================================================
# 3. U47IS CHILDREN PLAY COMMANDS
# ==============================================================================


@bot.command()
async def play(ctx, target_user: discord.Member = None, *, content: str = ""):
  """Usage: !play <@user> <emoji/sticker/text/attachment>"""
  if not ctx.guild or ctx.guild.id != ALLOWED_GUILD_ID:
    return

  if not target_user:
    await ctx.send(
        "Error: Please mention a valid user. Usage: `!play <@user> <content>`"
    )
    return

  if ctx.author.id != YOUR_USER_ID:
    now = datetime.datetime.now()
    last_used = play_cooldowns.get(ctx.author.id)

    if last_used and (now - last_used).total_seconds() < 60:
      remaining = 60 - int((now - last_used).total_seconds())
      await ctx.send(
          f"Cooldown active! Please wait {remaining} seconds before using"
          " `!play` again."
      )
      return

    previous_targets = [
        uid
        for uid, data in play_targets.items()
        if data.get("setter_id") == ctx.author.id
    ]
    for uid in previous_targets:
      del play_targets[uid]

    play_cooldowns[ctx.author.id] = now

  asset_type = "text"
  asset_content = content

  if ctx.message.attachments:
    asset_type = "image_url"
    asset_content = ctx.message.attachments[0].url
  elif ctx.message.stickers:
    asset_type = "sticker"
    asset_content = ctx.message.stickers[0]
  elif not content:
    await ctx.send(
        "Error: Please provide text, an emoji, a sticker, or attach an image!"
    )
    return

  play_targets[target_user.id] = {
      "setter_id": ctx.author.id,
      "type": asset_type,
      "content": asset_content,
  }

  await ctx.send(f"Play mode enabled for {target_user.mention}!")


@bot.command()
async def stopplay(ctx, target_user: discord.Member = None):
  """Usage: !stopplay <@user>"""
  if not ctx.guild or ctx.guild.id != ALLOWED_GUILD_ID:
    return

  if not target_user:
    await ctx.send(
        "Error: Please mention a valid user. Usage: `!stopplay <@user>`"
    )
    return

  if target_user.id in play_targets:
    data = play_targets[target_user.id]
    if (
        ctx.author.id != YOUR_USER_ID
        and data.get("setter_id") != ctx.author.id
    ):
      await ctx.send("You can only stop play mode for targets set by yourself!")
      return

    del play_targets[target_user.id]
    await ctx.send(f"Play mode stopped for {target_user.mention}.")
  else:
    await ctx.send(f"{target_user.mention} is currently not in play mode.")


@bot.command()
@is_u47is_only()
async def playlist(ctx):
  """Usage: !playlist (Owner Only)"""
  if not ctx.guild or ctx.guild.id != ALLOWED_GUILD_ID:
    return

  if not play_targets:
    await ctx.send("There are currently no active play targets.")
    return

  embed = discord.Embed(
      title="Active Play Targets List", color=discord.Color.purple()
  )

  for target_id, data in play_targets.items():
    setter_mention = f"<@{data['setter_id']}>"
    target_mention = f"<@{target_id}>"
    content_display = (
        data["content"].name
        if data["type"] == "sticker"
        else str(data["content"])
    )

    embed.add_field(
        name=f"Target: {target_id}",
        value=(
            f"**Target User:** {target_mention}\n"
            f"**Set By:** {setter_mention}\n"
            f"**Type:** {data['type']}\n"
            f"**Content:** {content_display}"
        ),
        inline=False,
    )

  await ctx.send(embed=embed)


# ==============================================================================
# 4. TRAINING DEPARTMENT COMMANDS
# ==============================================================================


@bot.command()
@has_role_or_above_position(TRAINING_DEPT_ROLE_ID)
async def announce(ctx, date_str: str, time_str: str, quota: str):
  """Usage: !announce DD/MM/YYYY HH:MM Quota."""
  try:
    naive_dt = datetime.datetime.strptime(
        f"{date_str} {time_str}", "%d/%m/%Y %H:%M"
    )
    hkt_dt = naive_dt.replace(tzinfo=HKT)
  except ValueError:
    await ctx.send(
        "Invalid Date/Time format! Please use DD/MM/YYYY and HH:MM (e.g.,"
        " 28/07/2026 20:00)."
    )
    return

  host_id = ctx.author.id
  host_mention = ctx.author.mention

  if host_id in trainings:
    if trainings[host_id].get("reminder_task"):
      trainings[host_id]["reminder_task"].cancel()
    if trainings[host_id].get("auto_lock_task"):
      trainings[host_id]["auto_lock_task"].cancel()

  unix_timestamp = int(hkt_dt.timestamp())
  timestamp_format = f"<t:{unix_timestamp}:f> (<t:{unix_timestamp}:R>)"

  embed = discord.Embed(
      title=f"MTB PCV Training - {timestamp_format}", color=discord.Color.blue()
  )
  embed.add_field(name="Host", value=host_mention, inline=False)
  embed.add_field(name="Trainee Quota", value=quota, inline=False)

  target_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
  if target_channel:
    ping_msg = await target_channel.send(content=TRAINEE_ROLE_PING)
    ann_msg = await target_channel.send(embed=embed)
    await ctx.send("Announcement successfully posted!")

    trainings[host_id] = {
        "ping_message": ping_msg,
        "announcement_message": ann_msg,
        "posted_messages": [ping_msg, ann_msg],
        "training_dt": hkt_dt,
        "host_mention": host_mention,
        "quota": quota,
        "link_message": None,
        "link_posted": False,
        "is_locked": False,
        "reminder_task": asyncio.create_task(
            send_reminder(host_mention, hkt_dt)
        ),
        "auto_lock_task": asyncio.create_task(
            schedule_auto_lock(host_id, hkt_dt)
        ),
    }
    save_trainings_state()
  else:
    await ctx.send(
        "Could not find <#1223678863754657853>. Please check permissions!"
    )


@bot.command()
@has_role_or_above_position(TRAINING_DEPT_ROLE_ID)
async def update(ctx, field: str, *, value: str):
  """Usage:

  !update time DD/MM/YYYY HH:MM !update host @User !update traineequota <quota>
  """
  host_id = ctx.author.id

  if host_id not in trainings:
    if is_dept_manager()(ctx.author) and len(trainings) > 0:
      host_id = list(trainings.keys())[-1]
    else:
      await ctx.send("You haven't announced the training yet!")
      return

  training = trainings[host_id]

  if training.get("is_locked"):
    await ctx.send(
        "The server is already locked! Updates are no longer allowed."
    )
    return

  field_clean = field.lower().replace("_", "").replace(" ", "")
  orig_unix = int(training["training_dt"].timestamp())
  orig_timestamp_str = f"<t:{orig_unix}:f>"

  if field_clean in ["time", "date", "datetime"]:
    try:
      parts = value.strip().split()
      if len(parts) != 2:
        raise ValueError
      date_part, time_part = parts[0], parts[1]
      naive_dt = datetime.datetime.strptime(
          f"{date_part} {time_part}", "%d/%m/%Y %H:%M"
      )
      training["training_dt"] = naive_dt.replace(tzinfo=HKT)

      if training.get("reminder_task"):
        training["reminder_task"].cancel()
      if training.get("auto_lock_task"):
        training["auto_lock_task"].cancel()

      training["reminder_task"] = asyncio.create_task(
          send_reminder(training["host_mention"], training["training_dt"])
      )
      training["auto_lock_task"] = asyncio.create_task(
          schedule_auto_lock(host_id, training["training_dt"])
      )
    except ValueError:
      await ctx.send(
          "Invalid time format for update! Please use: !update time DD/MM/YYYY"
          " HH:MM"
      )
      return

  elif field_clean in ["host"]:
    training["host_mention"] = value.strip()

  elif field_clean in ["traineequota", "quota"]:
    training["quota"] = value.strip()

  else:
    await ctx.send("Invalid field! Choose from: time, host, or traineequota.")
    return

  if training.get("announcement_message"):
    try:
      await training["announcement_message"].delete()
      if training["announcement_message"] in training["posted_messages"]:
        training["posted_messages"].remove(training["announcement_message"])
    except Exception:
      pass

  new_unix = int(training["training_dt"].timestamp())
  timestamp_format = f"<t:{new_unix}:f> (<t:{new_unix}:R>)"

  embed = discord.Embed(
      title=f"MTB PCV Training - {timestamp_format}", color=discord.Color.blue()
  )
  embed.add_field(name="Host", value=training["host_mention"], inline=False)
  embed.add_field(name="Trainee Quota", value=training["quota"], inline=False)

  announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
  if announcement_channel:
    content_msg = (
        f"The training of {orig_timestamp_str} has been updated! Please check"
        " the newest details as the following:"
    )
    new_ann_msg = await announcement_channel.send(
        content=content_msg, embed=embed
    )
    training["announcement_message"] = new_ann_msg
    training["posted_messages"].append(new_ann_msg)
    save_trainings_state()
    await ctx.send("Training announcement updated successfully!")
  else:
    await ctx.send("<#1223678863754657853> not found!")


@bot.command()
@has_role_or_above_position(TRAINING_DEPT_ROLE_ID)
async def link(ctx, server_link: str):
  """Usage: !link <server_link>."""
  target_host_id = ctx.author.id

  if target_host_id not in trainings:
    if is_dept_manager()(ctx.author) and len(trainings) > 0:
      target_host_id = list(trainings.keys())[-1]
    else:
      await ctx.send("You haven't announced the training yet!")
      return

  training = trainings[target_host_id]
  unix_ts = int(training["training_dt"].timestamp())
  time_display = f"<t:{unix_ts}:t>"

  embed = discord.Embed(
      title="MTB Training Centre Link",
      description=(
          f"The training scheduled for **{time_display}** is now starting,"
          " please join the MTB Training Centre through this link:\n\n"
          f"{server_link}\n\n"
          f"Server will be locked at {time_display}."
      ),
      color=discord.Color.green(),
  )

  announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
  if announcement_channel:
    link_msg = await announcement_channel.send(
        content=TRAINEE_ROLE_PING, embed=embed
    )
    training["link_message"] = link_msg
    training["link_posted"] = True
    training["posted_messages"].append(link_msg)
    save_trainings_state()

    await ctx.send("Link has been announced to <#1223678863754657853>!")
  else:
    await ctx.send("<#1223678863754657853> not found!")


@bot.command()
@has_role_or_above_position(TRAINING_DEPT_ROLE_ID)
async def full(ctx):
  """Usage: !full."""
  target_host_id = ctx.author.id

  if target_host_id not in trainings:
    if is_dept_manager()(ctx.author) and len(trainings) > 0:
      target_host_id = list(trainings.keys())[-1]
    else:
      await ctx.send("You haven't announced the training yet!")
      return

  training = trainings[target_host_id]

  if not training.get("link_posted"):
    await ctx.send("You haven't posted the link yet!")
    return

  if training.get("auto_lock_task"):
    training["auto_lock_task"].cancel()

  announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)

  if training.get("link_message"):
    try:
      await training["link_message"].delete()
      if training["link_message"] in training["posted_messages"]:
        training["posted_messages"].remove(training["link_message"])
      training["link_message"] = None
    except Exception:
      pass

  training["is_locked"] = True

  if announcement_channel:
    embed = discord.Embed(
        title="Server Locked (Full)",
        description=(
            "Server locked due to maximum amount of trainee reached. Please"
            " join the next session if you failed to join this session."
        ),
        color=discord.Color.red(),
    )
    full_msg = await announcement_channel.send(embed=embed)
    training["posted_messages"].append(full_msg)
    save_trainings_state()

    await ctx.send("Server status updated to FULL and removed link!")
  else:
    await ctx.send("<#1223678863754657853> not found!")


@bot.command()
@has_role_or_above_position(TRAINING_DEPT_ROLE_ID)
async def end(ctx):
  """Usage: !end."""
  target_host_id = ctx.author.id

  if target_host_id not in trainings:
    if is_dept_manager()(ctx.author) and len(trainings) > 0:
      target_host_id = list(trainings.keys())[-1]
    else:
      await ctx.send("You haven't announced the training yet!")
      return

  training = trainings[target_host_id]

  if not training.get("link_posted"):
    await ctx.send("You haven't posted the link yet!")
    return

  if not training.get("is_locked"):
    await ctx.send("The training has not ended")
    return

  for msg in training.get("posted_messages", []):
    try:
      await msg.delete()
    except Exception as e:
      print(f"Could not delete message: {e}")

  unix_ts = int(training["training_dt"].timestamp())
  training_time_str = f"<t:{unix_ts}:f>"

  embed = discord.Embed(
      title=f"Detailed feedback of the {training_time_str} session",
      description=(
          "If you need individual detailed result for this session, please"
          " react to this message."
      ),
      color=discord.Color.blue(),
  )

  announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
  if announcement_channel:
    await announcement_channel.send(embed=embed)

    await ctx.send(
        "Training status has updated to ENDED, detailed result reaction"
        " message posted successfully!"
    )
    del trainings[target_host_id]
    save_trainings_state()
  else:
    await ctx.send("<#1223678863754657853> not found!")


# ==============================================================================
# 5. DEPARTMENT HEAD & MANAGEMENT COMMANDS
# ==============================================================================


@bot.command()
@has_role_or_above_position(DEPT_MANAGER_ROLE_ID)
async def clear(ctx):
  """Usage: !clear."""
  for host_id, training in list(trainings.items()):
    if training.get("reminder_task"):
      training["reminder_task"].cancel()
    if training.get("auto_lock_task"):
      training["auto_lock_task"].cancel()

    for msg in training.get("posted_messages", []):
      try:
        await msg.delete()
      except Exception:
        pass

  trainings.clear()
  save_trainings_state()

  announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
  if announcement_channel:
    try:
      async for msg in announcement_channel.history(limit=50):
        if msg.id in [TRAINING_PING_MESSAGE_ID, SHIFT_PING_MESSAGE_ID]:
          continue

        if msg.embeds:
          for emb in msg.embeds:
            if (
                emb.title
                and "Detailed feedback of the" in emb.title
                and msg.author == bot.user
            ):
              try:
                await msg.delete()
              except Exception as e:
                print(f"Could not delete feedback message {msg.id}: {e}")
    except Exception as e:
      print(f"Error checking channel history: {e}")

  await ctx.send("Cleared all active training message(s).")


@bot.command(name="dm")
@has_role_or_above_position(DEPT_MANAGER_ROLE_ID)
async def send_dm(ctx, target_user_id: int, *, message_text: str = ""):
  """Usage: !dm <user_id> [message] (Attachments supported)."""
  if not message_text and not ctx.message.attachments:
    await ctx.send("Error: Please provide text content or attach a file.")
    return

  try:
    target_user = bot.get_user(target_user_id) or await bot.fetch_user(
        target_user_id
    )
    if not target_user:
      await ctx.send("Error: User not found.")
      return

    send_files = [await att.to_file() for att in ctx.message.attachments]
    log_files = [await att.to_file() for att in ctx.message.attachments]

    sent_msg = await target_user.send(content=message_text, files=send_files)

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
      await channel.send(f"Successfully DMed `{target_user.id}`!")

      copy_embed = discord.Embed(
          title="Outgoing Message Copy",
          description=(
              message_text if message_text else "*[Attachment/File Only]*"
          ),
          color=discord.Color.green(),
      )

      copy_embed.set_footer(
          text=(
              f"Sender ID: {ctx.author.id} | Receiver ID: {target_user.id} |"
              f" Message ID: {sent_msg.id}"
          )
      )

      if ctx.message.attachments:
        first_att = ctx.message.attachments[0]
        if (
            first_att.content_type
            and first_att.content_type.startswith("image/")
        ):
          copy_embed.set_image(url=first_att.url)

      await channel.send(embed=copy_embed, files=log_files)

  except discord.Forbidden:
    await ctx.send(
        "Could not send DM. The user might have DMs disabled or blocked the"
        " bot."
    )
  except Exception as e:
    await ctx.send(f"Error sending message: {e}")


@bot.command(name="dmclose")
@has_role_or_above_position(DEPT_MANAGER_ROLE_ID)
async def dm_close(ctx, target_user_id: int):
  """Usage: !dmclose <user_id>"""
  try:
    target_user = bot.get_user(target_user_id) or await bot.fetch_user(
        target_user_id
    )
    if not target_user:
      await ctx.send("Error: Could not find user with that ID.")
      return

    closing_embed = discord.Embed(
        title="Enquiry Closed",
        description=(
            "Thank you for contacting Metrobus Training Department. You may"
            " contact us again if you have anymore enquiries."
        ),
        color=discord.Color.blue(),
    )
    try:
      await target_user.send(embed=closing_embed)
      dm_sent = True
    except discord.Forbidden:
      dm_sent = False

    if target_user_id in user_active_dms:
      del user_active_dms[target_user_id]

    status_text = f"DM ticket for `{target_user_id}` has been closed."
    if not dm_sent:
      status_text += " (Note: Could not send DM to user - DMs may be closed)."

    await ctx.send(status_text)

  except Exception as e:
    await ctx.send(f"Error executing !dmclose: {e}")


@bot.command()
@has_role_or_above_position(DEPT_MANAGER_ROLE_ID)
async def text(ctx, channel_id: int, *, message_text: str):
  """Usage: !text <channel_id> <message_content>."""
  target_channel = bot.get_channel(channel_id)
  if not target_channel:
    try:
      target_channel = await bot.fetch_channel(channel_id)
    except Exception:
      await ctx.send(
          "Error: Could not find or access the specified channel ID."
      )
      return

  try:
    await target_channel.send(message_text)
    await ctx.send(f"Message successfully sent to <#{channel_id}>!")
  except Exception as e:
    await ctx.send(f"Failed to send message: {e}")


@bot.command()
@has_role_or_above_position(DEPT_MANAGER_ROLE_ID)
async def delete(ctx, message_id: int, target_channel_id: int = None):
  """Usage: !delete <message_id> [channel_id]"""
  msg_to_delete = None

  if target_channel_id:
    channel = bot.get_channel(target_channel_id) or await bot.fetch_channel(
        target_channel_id
    )
    if channel:
      try:
        msg_to_delete = await channel.fetch_message(message_id)
      except discord.NotFound:
        await ctx.send("Error: Message not found in specified channel.")
        return
  else:
    try:
      msg_to_delete = await ctx.channel.fetch_message(message_id)
    except discord.NotFound:
      if ctx.guild:
        for channel in ctx.guild.text_channels:
          try:
            msg_to_delete = await channel.fetch_message(message_id)
            break
          except (discord.NotFound, discord.Forbidden):
            continue

  if not msg_to_delete:
    await ctx.send(
        "Error: Could not find that message in any accessible channel."
    )
    return

  try:
    await msg_to_delete.delete()
    if not isinstance(ctx.channel, discord.DMChannel):
      try:
        await ctx.message.delete()
      except Exception:
        pass
    await ctx.send(
        f"Successfully deleted message `{message_id}` from"
        f" {msg_to_delete.channel.mention}!",
        delete_after=5,
    )
  except discord.Forbidden:
    await ctx.send("Error: Bot lacks permission to delete that message.")
  except Exception as e:
    await ctx.send(f"Error deleting message: {e}")


TOKEN = os.getenv("DISCORD_TOKEN")  

bot.run(TOKEN)
