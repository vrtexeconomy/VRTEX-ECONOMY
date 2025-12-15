# main.py
from web_server import keep_alive

# start keep-alive server
keep_alive()

import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button, Select, Modal, TextInput
import os
import json
import datetime
import random
import asyncio
from typing import Optional, List, Dict

# -----------------------------
# Configuration / Environment
# -----------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
TEAM_IDS = [int(x) for x in os.getenv("TEAM_IDS", "").split(",") if x.strip().isdigit()]
TOPGG_LINK = os.getenv("TOPGG_LINK", "")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)  # text commands mostly handled manually
tree = bot.tree

# -----------------------------
# Files and storage helpers
# -----------------------------
FILES = {
    "users": "users.json",
    "servers": "servers.json",
    "businesses": "businesses.json",
    "items": "items.json",
    "jobs": "jobs.json",
    "market": "market.json",
    "quests": "quests.json",
    "economy": "economy.json"
}

for fname in FILES.values():
    if not os.path.exists(fname):
        with open(fname, "w", encoding="utf-8") as f:
            json.dump({}, f)

def load_json(file_key: str) -> dict:
    path = FILES[file_key]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            with open(path, "w", encoding="utf-8") as fw:
                json.dump({}, fw)
            return {}
    except Exception:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({}, f)
        return {}

def save_json(file_key: str, data: dict):
    path = FILES[file_key]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

# -----------------------------
# User helpers
# -----------------------------
async def get_user(user_id: int) -> dict:
    users = load_json("users")
    sid = str(user_id)
    if sid not in users:
        users[sid] = {
            "wallet": 0,
            "bank": 0,
            "daily_claimed": None,
            "work_claims": {},   # per-guild last work timestamp ISO
            "membership": False,
            "xp": 0,
            "level": 1,
            "job": None,
            "job_streak": 0,
            "items": {},
            "businesses": {}
        }
        save_json("users", users)
    return users[sid]

async def update_user(user_id: int, data: dict):
    users = load_json("users")
    sid = str(user_id)
    if sid not in users:
        users[sid] = {}
    users[sid].update(data)
    save_json("users", users)

async def is_plus(user_id: int) -> bool:
    u = await get_user(user_id)
    return u.get("membership", False)

# -----------------------------
# Server helpers (premium, prefix, disabled commands)
# -----------------------------
def get_server_entry(guild_id: int) -> dict:
    servers = load_json("servers")
    gk = str(guild_id)
    if gk not in servers:
        servers[gk] = {
            "premium": None,        # { "expires": iso, "owner_id": int }
            "prefix": None,         # text prefix string when premium active
            "disabled_commands": [],# list of command names disabled on this server
            "pending_keys": {}      # key -> purchaser_id mappings for activation
        }
        save_json("servers", servers)
    return servers[gk]

def save_server_entry(guild_id: int, data: dict):
    servers = load_json("servers")
    servers[str(guild_id)] = servers.get(str(guild_id), {})
    servers[str(guild_id)].update(data)
    save_json("servers", servers)

def server_has_premium(guild_id: int) -> bool:
    entry = get_server_entry(guild_id)
    prem = entry.get("premium")
    if not prem:
        return False
    try:
        exp = datetime.datetime.fromisoformat(prem.get("expires"))
        return exp > datetime.datetime.utcnow()
    except Exception:
        return False

def get_server_prefix(guild_id: int) -> Optional[str]:
    entry = get_server_entry(guild_id)
    if server_has_premium(guild_id):
        return entry.get("prefix")
    return None

# -----------------------------
# Economy helpers
# -----------------------------
def get_guild_economy(guild_id: int) -> dict:
    econ = load_json("economy")
    gid = str(guild_id)
    if gid not in econ:
        econ[gid] = {
            "currency_name": "Coins",
            "currency_symbol": "$",
            "starting_balance": 0,
            "tax_rate": 0
        }
        save_json("economy", econ)
    return econ[gid]

def set_guild_economy(guild_id: int, data: dict):
    econ = load_json("economy")
    econ[str(guild_id)] = econ.get(str(guild_id), {})
    econ[str(guild_id)].update(data)
    save_json("economy", econ)

# -----------------------------
# Utility
# -----------------------------
def utc_now():
    return datetime.datetime.utcnow()

def readable_time_delta(sec: int) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

# -----------------------------
# Leveling helper
# -----------------------------
async def add_xp(user_id: int, amount: int):
    user = await get_user(user_id)
    user['xp'] = user.get('xp', 0) + amount
    leveled = False
    if user['xp'] >= user.get('level', 1) * 100:
        user['xp'] -= user.get('level', 1) * 100
        user['level'] = user.get('level', 1) + 1
        leveled = True
    await update_user(user_id, user)
    return leveled

# -----------------------------
# Premium helpers (key generation / purchase simulation)
# -----------------------------
def generate_premium_key() -> str:
    return "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10))

async def deliver_premium_key_dm(user: discord.User, key: str, months: int = 1):
    try:
        await user.send(f"✅ Your VRTEX+ activation key (valid for {months} month(s)): **{key}**\nUse it in the server with `/premium activate {key}` to activate premium there.")
    except Exception:
        # ignore if can't DM
        pass

# -----------------------------
# Premium purchase (placeholder) & activation commands
# -----------------------------
@tree.command(name="premium", description="Premium purchase / activation commands")
@app_commands.describe(action="purchase | activate | info")
async def premium(interaction: discord.Interaction, action: str, key: Optional[str] = None):
    # This grouped command serves a few sub-actions: purchase, activate, info
    # but app_commands doesn't support dynamic subcommands easily in single function
    # We'll route by action string.
    action = (action or "").lower()
    if action == "purchase":
        # Only allow administrators or owner to initiate purchase flow
        if not interaction.user.guild_permissions.manage_guild and interaction.user.id != OWNER_ID:
            await interaction.response.send_message("You need Manage Server permission (or owner) to purchase premium for a server.", ephemeral=True)
            return
        # simulate payment: generate key and DM purchaser
        months = 1  # default; you can extend to choose monthly/yearly
        key = generate_premium_key()
        servers = load_json("servers")
        servers[str(interaction.guild.id)] = servers.get(str(interaction.guild.id), {})
        servers[str(interaction.guild.id)].setdefault("pending_keys", {})[key] = {
            "purchaser": interaction.user.id,
            "months": months,
            "created": utc_now().isoformat()
        }
        save_json("servers", servers)
        # DM the buyer
        await deliver_premium_key_dm(interaction.user, key, months=months)
        await interaction.response.send_message("✅ Payment processed (simulated). A one-time key has been sent to your DMs. Use `/premium activate <key>` in this server to activate.", ephemeral=True)
        return

    if action == "activate":
        # activation in server using key
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not key:
            await interaction.response.send_message("You must pass your activation key. Example: `/premium activate ABC123...`", ephemeral=True)
            return
        servers = load_json("servers")
        entry = servers.get(str(interaction.guild.id), {})
        pending = entry.get("pending_keys", {})
        kinfo = pending.get(key)
        if not kinfo:
            await interaction.response.send_message("❌ Invalid or already-used key.", ephemeral=True)
            return
        # mark premium: set expiry based on months purchased (simple monthly)
        months = kinfo.get("months", 1)
        expires = (utc_now() + datetime.timedelta(days=30*months)).isoformat()
        entry["premium"] = {"expires": expires, "owner_id": kinfo.get("purchaser")}
        # default prefix after activation
        entry["prefix"] = "ve"
        # remove the key from pending
        pending.pop(key, None)
        entry["pending_keys"] = pending
        servers[str(interaction.guild.id)] = entry
        save_json("servers", servers)
        await interaction.response.send_message(f"🎉 Server premium activated! Expires: {expires}. Default text prefix set to `ve`. Use `/settings` to customize.", ephemeral=True)
        return

    if action == "info":
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        entry = get_server_entry(interaction.guild.id)
        prem = entry.get("premium")
        if not prem:
            await interaction.response.send_message("This server doesn't have VRTEX+ activated.", ephemeral=True)
            return
        try:
            exp = datetime.datetime.fromisoformat(prem.get("expires"))
            delta = (exp - utc_now()).total_seconds()
            await interaction.response.send_message(f"Premium expires on {exp.date()} ({readable_time_delta(delta)} remaining).", ephemeral=True)
        except Exception:
            await interaction.response.send_message("Unable to read premium expiry.", ephemeral=True)
        return

    await interaction.response.send_message("Invalid premium action. Use `purchase`, `activate` or `info`.", ephemeral=True)

# -----------------------------
# Premium grant (owner-only) - immediately grant premium for testing / owner use
# -----------------------------
@tree.command(name="premium_grant", description="(Owner) grant premium to a server for testing / manual grant")
@app_commands.describe(guild_id="ID of guild to grant", months="months to grant")
async def premium_grant(interaction: discord.Interaction, guild_id: int, months: int = 1):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Only the bot owner can use this.", ephemeral=True)
        return
    servers = load_json("servers")
    entry = servers.get(str(guild_id), {})
    expires = (utc_now() + datetime.timedelta(days=30*months)).isoformat()
    entry["premium"] = {"expires": expires, "owner_id": interaction.user.id}
    entry["prefix"] = "ve"
    servers[str(guild_id)] = entry
    save_json("servers", servers)
    await interaction.response.send_message(f"Granted premium to server {guild_id} until {expires}.", ephemeral=True)

# -----------------------------
# Settings UI (slash) - when premium is active allow prefix change
# -----------------------------
class PrefixModal(Modal, title="Set Server Prefix"):
    prefix = TextInput(label="Prefix (e.g. ve, !, @, 21)", placeholder="ve", required=True, max_length=10)
    def __init__(self, guild: discord.Guild, user: discord.Member):
        super().__init__()
        self.guild = guild
        self.user = user

    async def on_submit(self, interaction: discord.Interaction):
        # only allow if premium active and user has manage_guild
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.id in TEAM_IDS or interaction.user.id == OWNER_ID):
            await interaction.response.send_message("You need Manage Server permission (or owner/team) to change prefix.", ephemeral=True)
            return
        if not server_has_premium(self.guild.id):
            await interaction.response.send_message("This server is not VRTEX+. Custom prefix only for VRTEX+ servers.", ephemeral=True)
            return
        p = self.prefix.value.strip()
        # basic validation
        if len(p) == 0:
            await interaction.response.send_message("Invalid prefix.", ephemeral=True)
            return
        entry = get_server_entry(self.guild.id)
        entry['prefix'] = p
        save_server_entry(self.guild.id, entry)
        await interaction.response.send_message(f"✅ Prefix set to `{p}` for this server. Text-prefix commands are now active alongside slash commands.", ephemeral=True)

class SettingsView(View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

    @discord.ui.button(label="Economy", style=discord.ButtonStyle.primary)
    async def econ_btn(self, interaction: discord.Interaction, button: Button):
        econ = get_guild_economy(self.guild.id)
        embed = discord.Embed(title="Economy Settings", description=f"Currency: **{econ.get('currency_name')}** `{econ.get('currency_symbol','')}`\nStarting balance: **{econ.get('starting_balance',0)}**\nTax: **{econ.get('tax_rate',0)}%**", color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Commands toggle", style=discord.ButtonStyle.secondary)
    async def toggle_btn(self, interaction: discord.Interaction, button: Button):
        servers = load_json("servers")
        entry = servers.get(str(self.guild.id), {})
        disabled = entry.get("disabled_commands", [])
        # present a simple message listing disabled commands and how to toggle them via command (for brevity)
        await interaction.response.send_message(f"Disabled commands on this server: {disabled or 'None'}. Use `/settings toggle <command>` to toggle.", ephemeral=True)

    @discord.ui.button(label="Prefix / Premium Info", style=discord.ButtonStyle.gray)
    async def prefix_btn(self, interaction: discord.Interaction, button: Button):
        # show premium status & allow prefix modal if premium
        entry = get_server_entry(self.guild.id)
        prem = entry.get("premium")
        if not server_has_premium(self.guild.id):
            await interaction.response.send_message("This server is not premium. Admins can purchase premium using `/premium purchase`.", ephemeral=True)
            return
        # show remaining time button and prefix set option
        p = entry.get("prefix") or "(default ve)"
        embed = discord.Embed(title="VRTEX+ Info", description=f"Prefix: `{p}`", color=discord.Color.blurple())
        # add a dynamic button below to show expiry
        view = View()
        async def time_cb(inter: discord.Interaction):
            try:
                exp = datetime.datetime.fromisoformat(prem.get("expires"))
                delta = (exp - utc_now()).total_seconds()
                await inter.response.send_message(f"Premium expires on **{exp.date()}** ({readable_time_delta(delta)} remaining).", ephemeral=True)
            except Exception:
                await inter.response.send_message("Could not read expiry.", ephemeral=True)
        btn_time = Button(label="Show time left", style=discord.ButtonStyle.primary)
        btn_time.callback = time_cb
        view.add_item(btn_time)
        async def setpref_cb(inter: discord.Interaction):
            await inter.response.send_modal(PrefixModal(self.guild, inter.user))
        btn_set = Button(label="Change Prefix", style=discord.ButtonStyle.secondary)
        btn_set.callback = setpref_cb
        view.add_item(btn_set)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@tree.command(name="settings", description="Open VRTEX settings (Manage Server required to change)")
async def settings(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return
    if not (interaction.user.guild_permissions.manage_guild or interaction.user.id in TEAM_IDS or interaction.user.id == OWNER_ID):
        await interaction.response.send_message("You need Manage Server permission (or owner/team) to access settings.", ephemeral=True)
        return
    embed = discord.Embed(title="⚙️ VRTEX Settings", description="Use the buttons to configure economy & premium options.", color=discord.Color.orange())
    guild_entry = get_server_entry(interaction.guild.id)
    econ = get_guild_economy(interaction.guild.id)
    prem = guild_entry.get("premium")
    prefix = guild_entry.get("prefix") or "Not set"
    embed.add_field(name="Current", value=f"Currency: **{econ.get('currency_name')} {econ.get('currency_symbol','')}**\nStarting balance: **{econ.get('starting_balance',0)}**\nTax: **{econ.get('tax_rate',0)}%**\nPremium: **{'Active' if server_has_premium(interaction.guild.id) else 'Not active'}**\nPrefix: **{prefix}**", inline=False)
    view = SettingsView(interaction.guild)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# toggle subcommand for settings to disable/enable commands
@tree.command(name="settings_toggle", description="Toggle a command on this server")
@app_commands.describe(command_name="command name to toggle")
async def settings_toggle(interaction: discord.Interaction, command_name: str):
    if not interaction.guild:
        await interaction.response.send_message("Use in a server.", ephemeral=True); return
    if not (interaction.user.guild_permissions.manage_guild or interaction.user.id in TEAM_IDS or interaction.user.id == OWNER_ID):
        await interaction.response.send_message("You need Manage Server permission.", ephemeral=True); return
    servers = load_json("servers")
    entry = servers.get(str(interaction.guild.id), {})
    disabled = entry.get("disabled_commands", [])
    if command_name in disabled:
        disabled.remove(command_name)
        msg = f"Enabled {command_name}"
    else:
        disabled.append(command_name)
        msg = f"Disabled {command_name}"
    entry["disabled_commands"] = disabled
    servers[str(interaction.guild.id)] = entry
    save_json("servers", servers)
    await interaction.response.send_message(f"✅ {msg}", ephemeral=True)

# -----------------------------
# Help command (slash) - custom embed listing commands & categories
# -----------------------------
@tree.command(name="help", description="Show VRTEX Economy help & commands")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="💠 VRTEX Economy — Help", description="Slash commands are available below. If you have VRTEX+ you may also use a custom text prefix.", color=discord.Color.from_rgb(88,101,242))
    embed.add_field(name="Quick", value="/balance  /work  /profile  /settings  /premium activate", inline=False)
    embed.add_field(name="Economy (examples)", value="`/balance` — check balances\n`/deposit <amt>` — deposit to bank\n`/withdraw <amt>` — withdraw\n`/transfer <user> <amt>` — send money", inline=False)
    embed.add_field(name="Games & Jobs", value="`/work` `/applyjob` `/jobs` `/promote`", inline=False)
    embed.add_field(name="Business & Market", value="`/business buy` `/business list` `/market list`", inline=False)
    embed.add_field(name="Adventure & Quests", value="`/adventure` `/quests` `/achievements`", inline=False)
    embed.add_field(name="Premium perks", value="+25% work income, x2 daily, -20% cooldown, custom prefix, exclusive items", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=False)

# -----------------------------
# Core economy slash commands (and underlying helpers used by both slash & text)
# -----------------------------
def make_embed(title: str, description: str = None, color=None):
    c = color or discord.Color.from_rgb(34, 37, 46)
    e = discord.Embed(title=title, description=description or "", color=c)
    return e

async def send_balance_embed_ctx(ctx_or_inter, member: discord.Member):
    if isinstance(ctx_or_inter, discord.Interaction):
        guild = ctx_or_inter.guild
        author = member
        # use response
        econ = get_guild_economy(guild.id) if guild else {"currency_symbol":"$"}
        name = econ.get("currency_name","Coins")
        sym = econ.get("currency_symbol","")
        user = await get_user(member.id)
        wallet = user.get("wallet",0); bank = user.get("bank",0)
        embed = make_embed(f"{member.display_name}'s Balance", None, None)
        embed.add_field(name=f"{name} (Wallet)", value=f"{wallet} {sym}", inline=True)
        embed.add_field(name=f"{name} (Bank)", value=f"{bank} {sym}", inline=True)
        embed.add_field(name="Membership", value="VRTEX+" if user.get("membership") else "Normal", inline=False)
        await ctx_or_inter.response.send_message(embed=embed)
    else:
        # ctx_or_inter is message
        msg = ctx_or_inter
        guild = msg.guild
        member = member or msg.author
        econ = get_guild_economy(guild.id) if guild else {"currency_symbol":"$"}
        name = econ.get("currency_name","Coins")
        sym = econ.get("currency_symbol","")
        user = await get_user(member.id)
        wallet = user.get("wallet",0); bank = user.get("bank",0)
        embed = make_embed(f"{member.display_name}'s Balance", None, None)
        embed.add_field(name=f"{name} (Wallet)", value=f"{wallet} {sym}", inline=True)
        embed.add_field(name=f"{name} (Bank)", value=f"{bank} {sym}", inline=True)
        embed.add_field(name="Membership", value="VRTEX+" if user.get("membership") else "Normal", inline=False)
        await msg.channel.send(embed=embed)

@tree.command(name="balance", description="Check your wallet & bank")
@app_commands.describe(member="Member to check")
async def slash_balance(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    member = member or interaction.user
    await send_balance_embed_ctx(interaction, member)

# deposit
@tree.command(name="deposit", description="Deposit money into your bank")
@app_commands.describe(amount="Amount to deposit")
async def slash_deposit(interaction: discord.Interaction, amount: int):
    user = await get_user(interaction.user.id)
    if amount <= 0 or amount > user.get("wallet",0):
        await interaction.response.send_message("❌ Invalid deposit amount or insufficient wallet funds.", ephemeral=True)
        return
    user['wallet'] -= amount
    user['bank'] = user.get('bank',0) + amount
    await update_user(interaction.user.id, user)
    await interaction.response.send_message(f"✅ Deposited {amount}{get_guild_economy(interaction.guild.id).get('currency_symbol','')} into your bank.")

# withdraw
@tree.command(name="withdraw", description="Withdraw money from your bank")
@app_commands.describe(amount="Amount to withdraw")
async def slash_withdraw(interaction: discord.Interaction, amount: int):
    user = await get_user(interaction.user.id)
    if amount <= 0 or amount > user.get("bank",0):
        await interaction.response.send_message("❌ Invalid withdraw amount or insufficient bank funds.", ephemeral=True)
        return
    user['bank'] -= amount
    user['wallet'] = user.get('wallet',0) + amount
    await update_user(interaction.user.id, user)
    await interaction.response.send_message(f"✅ Withdrawn {amount}{get_guild_economy(interaction.guild.id).get('currency_symbol','')} to your wallet.")

# transfer
@tree.command(name="transfer", description="Send money to another user")
@app_commands.describe(member="Recipient", amount="Amount to send")
async def slash_transfer(interaction: discord.Interaction, member: discord.Member, amount: int):
    if member.id == interaction.user.id:
        await interaction.response.send_message("❌ You cannot transfer to yourself.", ephemeral=True); return
    sender = await get_user(interaction.user.id)
    receiver = await get_user(member.id)
    if amount <= 0 or amount > sender.get('wallet',0):
        await interaction.response.send_message("❌ Invalid transfer amount or insufficient balance.", ephemeral=True); return
    sender['wallet'] -= amount
    receiver['wallet'] = receiver.get('wallet',0) + amount
    await update_user(interaction.user.id, sender)
    await update_user(member.id, receiver)
    await interaction.response.send_message(f"✅ Transferred {amount}{get_guild_economy(interaction.guild.id).get('currency_symbol','')} to {member.mention}!")

# leaderboard
@tree.command(name="leaderboard", description="View the richest users")
async def slash_leaderboard(interaction: discord.Interaction):
    users = load_json("users")
    ranking = []
    for uid, data in users.items():
        total = data.get('wallet', 0) + data.get('bank', 0)
        ranking.append((uid, total))
    ranking.sort(key=lambda x: x[1], reverse=True)
    embed = make_embed("💰 Top Richest Users", None, None)
    guild = interaction.guild
    count = 0
    for uid, total in ranking:
        if count >= 10:
            break
        try:
            member = guild.get_member(int(uid)) if guild else None
            name = member.display_name if member else f"User {uid}"
        except Exception:
            name = f"User {uid}"
        embed.add_field(name=name, value=f"Total: {total}{get_guild_economy(guild.id).get('currency_symbol','')}", inline=False)
        count += 1
    await interaction.response.send_message(embed=embed)

# profile
@tree.command(name="profile", description="View your or another user's profile")
@app_commands.describe(member="Member to view")
async def slash_profile(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    member = member or interaction.user
    user = await get_user(member.id)
    econ = get_guild_economy(interaction.guild.id) if interaction.guild else {"currency_symbol":"$"}
    embed = make_embed(f"{member.display_name}'s Profile", None, None)
    embed.add_field(name="Balance", value=f"{user.get('wallet',0)+user.get('bank',0)}{econ.get('currency_symbol','')}", inline=False)
    embed.add_field(name="Level & XP", value=f"Level {user.get('level',1)} (XP: {user.get('xp',0)})", inline=False)
    embed.add_field(name="Job", value=user.get('job') or "Unemployed", inline=False)
    embed.add_field(name="Businesses", value=", ".join(user.get('businesses',{}).keys()) or "None", inline=False)
    await interaction.response.send_message(embed=embed)

# -----------------------------
# Work & Jobs
# -----------------------------
@tree.command(name="work", description="Work to earn coins (1-hour cooldown)")
async def slash_work(interaction: discord.Interaction):
    user = await get_user(interaction.user.id)
    if not interaction.guild:
        await interaction.response.send_message("Work can only be used in servers.", ephemeral=True); return
    guild_id = str(interaction.guild.id)
    last_claims = user.get("work_claims", {})
    now = utc_now()
    last_iso = last_claims.get(guild_id)
    # cooldown base 3600 sec
    cooldown = 3600
    if last_iso:
        try:
            last_dt = datetime.datetime.fromisoformat(last_iso)
            delta = (now - last_dt).total_seconds()
            if delta < cooldown:
                await interaction.response.send_message(f"❌ You can work again in **{readable_time_delta(cooldown - delta)}**", ephemeral=True)
                return
        except Exception:
            pass
    reward = 1000
    # premium perks: if user is VRTEX+, apply +25%
    if await is_plus(interaction.user.id):
        reward = int(reward * 1.25)
    # server premium perk: -20% cooldown: if server premium, reduce cooldown (not reward here)
    user['wallet'] = user.get('wallet', 0) + reward
    last_claims[guild_id] = now.isoformat()
    user['work_claims'] = last_claims
    await update_user(interaction.user.id, user)
    leveled = await add_xp(interaction.user.id, 20)
    msg = f"✅ You worked and earned **{reward}{get_guild_economy(interaction.guild.id).get('currency_symbol','')}**!"
    if leveled:
        msg += "\n🎉 You leveled up!"
    await interaction.response.send_message(msg)

# Job-related commands (simplified)
JOBS = {
    "cashier": {"pay": 500, "chance_promote": 0.2},
    "developer": {"pay": 1200, "chance_promote": 0.12},
    "miner": {"pay": 900, "chance_promote": 0.15},
}

@tree.command(name="jobs", description="List available jobs")
async def slash_jobs(interaction: discord.Interaction):
    embed = make_embed("💼 Jobs", None, None)
    for name, info in JOBS.items():
        embed.add_field(name=name.title(), value=f"Pay: {info['pay']} | Promote chance: {int(info['chance_promote']*100)}%", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="applyjob", description="Apply for a job")
@app_commands.describe(job_name="Job name")
async def slash_applyjob(interaction: discord.Interaction, job_name: str):
    job_name = job_name.lower().strip()
    if job_name not in JOBS:
        await interaction.response.send_message("❌ Job not found.", ephemeral=True); return
    user = await get_user(interaction.user.id)
    user['job'] = job_name
    user['job_streak'] = 0
    await update_user(interaction.user.id, user)
    await interaction.response.send_message(f"✅ You are now employed as **{job_name.title()}**.")

@tree.command(name="quitjob", description="Leave your current job")
async def slash_quitjob(interaction: discord.Interaction):
    user = await get_user(interaction.user.id)
    if not user.get("job"):
        await interaction.response.send_message("You don't have a job.", ephemeral=True); return
    user['job'] = None
    user['job_streak'] = 0
    await update_user(interaction.user.id, user)
    await interaction.response.send_message("You left your job.")

@tree.command(name="promote", description="Attempt an automatic promotion")
async def slash_promote(interaction: discord.Interaction):
    user = await get_user(interaction.user.id)
    job = user.get("job")
    if not job:
        await interaction.response.send_message("You have no job.", ephemeral=True); return
    info = JOBS.get(job, {})
    chance = info.get("chance_promote", 0.1)
    if random.random() < chance:
        # promotion effect: increase pay (we'll simulate by increasing stored 'job_rank' or similar)
        user.setdefault("job_rank", 1)
        user["job_rank"] += 1
        await update_user(interaction.user.id, user)
        await interaction.response.send_message(f"🎉 Congratulations — you were promoted! New rank: {user['job_rank']}")
    else:
        await interaction.response.send_message("No promotion this time. Keep working!")

# -----------------------------
# Business system (simplified)
# -----------------------------
DEFAULT_BUSINESSES = {
    "Bakery": {"cost": 5000, "profit": 500, "upkeep": 50, "tier": 1},
    "Mine": {"cost": 10000, "profit": 1200, "upkeep": 150, "tier": 2},
    "Shop": {"cost": 20000, "profit": 2500, "upkeep": 300, "tier": 3},
    # tier 3-5 may be unlocked by premium when implementing expansion
}

# ✅ Create the group properly
business_group = app_commands.Group(
    name="business",
    description="Business commands"
)

# -----------------------------
# List businesses
# -----------------------------
@business_group.command(name="list", description="Show available businesses")
async def business_list(interaction: discord.Interaction):
    embed = make_embed("🏠 Available Businesses", None, None)
    for name, info in DEFAULT_BUSINESSES.items():
        embed.add_field(name=name, value=f"Cost: {info['cost']} | Profit: {info['profit']}", inline=False)
    await interaction.response.send_message(embed=embed)

# -----------------------------
# Buy a business
# -----------------------------
@business_group.command(name="buy", description="Buy a business")
@app_commands.describe(name="Business name")
async def business_buy(interaction: discord.Interaction, name: str):
    name = name.title()
    if name not in DEFAULT_BUSINESSES:
        await interaction.response.send_message("❌ Business not found.", ephemeral=True)
        return
    user = await get_user(interaction.user.id)
    if name in user.get("businesses", {}):
        await interaction.response.send_message("❌ You already own this business.", ephemeral=True)
        return
    cost = DEFAULT_BUSINESSES[name]['cost']
    if user.get('wallet', 0) < cost:
        await interaction.response.send_message("❌ Not enough money.", ephemeral=True)
        return
    user['wallet'] -= cost
    user.setdefault('businesses', {})[name] = DEFAULT_BUSINESSES[name].copy()
    await update_user(interaction.user.id, user)
    await interaction.response.send_message(f"✅ You bought **{name}**!")

# -----------------------------
# Claim profits
# -----------------------------
@business_group.command(name="claim", description="Claim profits from your businesses")
async def business_claim(interaction: discord.Interaction):
    user = await get_user(interaction.user.id)
    total = 0
    for b, info in user.get('businesses', {}).items():
        total += info.get('profit', 0)
    user['wallet'] = user.get('wallet', 0) + total
    await update_user(interaction.user.id, user)
    await interaction.response.send_message(f"✅ Claimed {total}{get_guild_economy(interaction.guild.id).get('currency_symbol','')} from your businesses.")

# -----------------------------
# Business info
# -----------------------------
@business_group.command(name="info", description="Get info on a business")
@app_commands.describe(name="Business name")
async def business_info(interaction: discord.Interaction, name: str):
    name = name.title()
    info = DEFAULT_BUSINESSES.get(name)
    if not info:
        await interaction.response.send_message("❌ Business not found.", ephemeral=True)
        return
    embed = make_embed(f"{name} Info", None, None)
    embed.add_field(name="Cost", value=str(info['cost']), inline=True)
    embed.add_field(name="Profit", value=str(info['profit']), inline=True)
    embed.add_field(name="Tier", value=str(info['tier']), inline=True)
    await interaction.response.send_message(embed=embed)

# ✅ Register the group
tree.add_command(business_group)
# -----------------------------
# Marketplace & inventory (simplified)
# -----------------------------
@tree.command(name="inventory", description="Check your items")
async def slash_inventory(interaction: discord.Interaction):
    user = await get_user(interaction.user.id)
    items = user.get("items", {})
    if not items:
        await interaction.response.send_message("Your inventory is empty.", ephemeral=True); return
    txt = "\n".join(f"{k}: {v}" for k,v in items.items())
    await interaction.response.send_message(f"📦 Your items:\n{txt}")

@tree.command(name="use", description="Use an item")
@app_commands.describe(item="Item name")
async def slash_use(interaction: discord.Interaction, item: str):
    user = await get_user(interaction.user.id)
    items = user.get("items", {})
    if items.get(item,0) <= 0:
        await interaction.response.send_message("You don't have that item.", ephemeral=True); return
    # example item effect: if "xp_potion" then add xp
    items[item] -= 1
    user['items'] = items
    await update_user(interaction.user.id, user)
    await interaction.response.send_message(f"Used one {item}. (No special effect implemented for demo)")

@tree.command(name="sell", description="Sell an item")
@app_commands.describe(item="Item name", price="Price to sell for")
async def slash_sell(interaction: discord.Interaction, item: str, price: int):
    user = await get_user(interaction.user.id)
    items = user.get("items", {})
    if items.get(item,0) <= 0:
        await interaction.response.send_message("You don't have that item.", ephemeral=True); return
    items[item] -= 1
    user['wallet'] = user.get('wallet',0) + price
    user['items'] = items
    await update_user(interaction.user.id, user)
    await interaction.response.send_message(f"Sold {item} for {price}.")

# -----------------------------
# Adventure & quests (simplified)
# -----------------------------
@tree.command(name="adventure", description="Explore and find rewards")
async def slash_adventure(interaction: discord.Interaction):
    # simple random reward
    outcomes = [
        ("Found coins", 500),
        ("Found nothing", 0),
        ("Found item", "mysterious_gem"),
        ("Ambushed and lost coins", -200)
    ]
    pick = random.choice(outcomes)
    user = await get_user(interaction.user.id)
    if isinstance(pick[1], int):
        change = pick[1]
        if change >= 0:
            user['wallet'] = user.get('wallet',0) + change
            await update_user(interaction.user.id, user)
            await interaction.response.send_message(f"🧭 {pick[0]}: {change}{get_guild_economy(interaction.guild.id).get('currency_symbol','')}")
        else:
            user['wallet'] = max(0, user.get('wallet',0) + change)
            await update_user(interaction.user.id, user)
            await interaction.response.send_message(f"🧭 {pick[0]}: {change}{get_guild_economy(interaction.guild.id).get('currency_symbol','')}")
    else:
        item = pick[1]
        user.setdefault('items', {}).setdefault(item, 0)
        user['items'][item] += 1
        await update_user(interaction.user.id, user)
        await interaction.response.send_message(f"🧭 {pick[0]}: gained **{item}**!")

@tree.command(name="quests", description="Show current quests")
async def slash_quests(interaction: discord.Interaction):
    # simplified static quests
    embed = make_embed("🧭 Quests", "Active quests & rewards")
    embed.add_field(name="First Steps", value="Do /work 5 times — Reward: 1000", inline=False)
    embed.add_field(name="Treasure Hunter", value="Do /adventure 3 times — Reward: item", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="achievements", description="Show achievements")
async def slash_achievements(interaction: discord.Interaction):
    # placeholder achievements
    await interaction.response.send_message("🏆 Achievements: Beginner, Worker, Explorer (demo)")

# -----------------------------
# Local text-prefix command bridge for premium servers
# We manually parse messages that start with the server's configured prefix
# and dispatch them to the corresponding functions above.
# -----------------------------
TEXT_COMMAND_MAP = {
    # aliases map to slash command names or internal handlers
    "balance": "balance",
    "vebalance": "balance",
    "bal": "balance",
    "deposit": "deposit",
    "withdraw": "withdraw",
    "transfer": "transfer",
    "work": "work",
    "vework": "work",
    "profile": "profile",
    "veprofile": "profile",
    "leaderboard": "leaderboard",
    "veleaderboard": "leaderboard",
    "inventory": "inventory",
    "use": "use",
    "sell": "sell",
    "adventure": "adventure",
    "quests": "quests",
    "achievements": "achievements",
    "business": "business",   # needs parsing of subcommands
    "market": "market",       # not fully implemented
    "settings": "settings"
}

# Simplified argument splitter (preserves mention as first arg)
def split_args(content: str) -> List[str]:
    parts = content.strip().split()
    return parts

@bot.event
async def on_message(message: discord.Message):
    # ignore bots
    if message.author.bot:
        return
    # handle text prefix commands only in guilds
    if not message.guild:
        return
    prefix = get_server_prefix(message.guild.id)
    if not prefix:
        # no prefix enabled for this server (non-premium), do not treat text commands
        return
    # prefix may be multi-char; check start
    if not message.content.startswith(prefix):
        # also support prefix with mention of bot optionally? not necessary
        return
    # parse
    content = message.content[len(prefix):].strip()
    if not content:
        return
    parts = split_args(content)
    cmd = parts[0].lower()
    args = parts[1:]
    # map cmd
    mapped = TEXT_COMMAND_MAP.get(cmd)
    if not mapped:
        # maybe subcommands like vebusiness buy
        if cmd in ("vebusiness", "business"):
            # parse subcommand
            if len(args) == 0:
                await message.channel.send("Usage: business list | buy <name> | claim | info <name>")
                return
            sub = args[0].lower()
            if sub in ("list",):
                # call slash-like handler
                # call business_list
                await business_list._callback(await make_dummy_interaction_from_message(message))
                return
            if sub == "buy" and len(args) >= 2:
                name = " ".join(args[1:])
                # make a dummy interaction to call same handler
                inter = await make_dummy_interaction_from_message(message)
                await business_buy._callback(inter, name)
                return
        return
    # Now dispatch mapped command by calling corresponding handler via app_commands or direct functions
    try:
        inter = await make_dummy_interaction_from_message(message)
        # mapping to call
        if mapped == "balance":
            await slash_balance._callback(inter, member=message.author)
        elif mapped == "deposit":
            if not args:
                await message.channel.send("Provide amount.")
            else:
                try:
                    amt = int(args[0])
                    await slash_deposit._callback(inter, amount=amt)
                except Exception:
                    await message.channel.send("Invalid amount.")
        elif mapped == "withdraw":
            if not args:
                await message.channel.send("Provide amount.")
            else:
                try:
                    amt = int(args[0])
                    await slash_withdraw._callback(inter, amount=amt)
                except Exception:
                    await message.channel.send("Invalid amount.")
        elif mapped == "transfer":
            if len(args) < 2:
                await message.channel.send("Usage: <prefix>transfer @user amount")
            else:
                # try to resolve user mention or id
                target = None
                try:
                    if message.mentions:
                        target = message.mentions[0]
                        amt = int(args[-1])
                    else:
                        target = message.guild.get_member(int(args[0]))
                        amt = int(args[1])
                    await slash_transfer._callback(inter, member=target, amount=amt)
                except Exception:
                    await message.channel.send("Could not parse target or amount.")
        elif mapped == "work":
            await slash_work._callback(inter)
        elif mapped == "profile":
            # optional mention
            target = message.author
            if message.mentions:
                target = message.mentions[0]
            await slash_profile._callback(inter, member=target)
        elif mapped == "leaderboard":
            await slash_leaderboard._callback(inter)
        elif mapped == "inventory":
            await slash_inventory._callback(inter)
        elif mapped == "use":
            if not args:
                await message.channel.send("Provide item name.")
            else:
                item = " ".join(args)
                await slash_use._callback(inter, item=item)
        elif mapped == "sell":
            if len(args) < 2:
                await message.channel.send("Usage: <prefix>sell item price")
            else:
                item = " ".join(args[:-1])
                try:
                    price = int(args[-1])
                    await slash_sell._callback(inter, item=item, price=price)
                except Exception:
                    await message.channel.send("Invalid price.")
        elif mapped == "adventure":
            await slash_adventure._callback(inter)
        elif mapped == "quests":
            await slash_quests._callback(inter)
        elif mapped == "achievements":
            await slash_achievements._callback(inter)
        elif mapped == "settings":
            await settings._callback(inter)
        else:
            await message.channel.send("Command mapping not implemented yet.")
    except Exception as e:
        # debugging
        await message.channel.send(f"Error dispatching command: {e}")

# helper: build a fake Interaction-like object for calling slash callbacks from text
async def make_dummy_interaction_from_message(message: discord.Message):
    """
    Create a very small object that mimics enough of discord.Interaction for our callbacks.
    We supply `guild`, `user`, `channel`, and wrappers to send a response via message.channel.send.
    """
    class DummyResp:
        def __init__(self, msg: discord.Message):
            self.msg = msg
            self.sent = False
        async def send(self, *args, **kwargs):
            # emulate Interaction.response.send_message by replying in channel
            # ephemeral ignored
            content = kwargs.get("content")
            embed = kwargs.get("embed")
            view = kwargs.get("view")
            if embed is not None:
                await self.msg.channel.send(embed=embed)
            elif content is not None:
                await self.msg.channel.send(content)
            self.sent = True
    class DummyInteraction:
        def __init__(self, message):
            self.guild = message.guild
            self.user = message.author
            self.channel = message.channel
            self.message = message
            self.response = DummyResp(message)
    return DummyInteraction(message)

# -----------------------------
# Robust command-block safety: check disabled commands
# -----------------------------
@bot.check
async def global_command_block(ctx):
    # allow dms
    if ctx.guild is None:
        return True
    # allow help always
    if ctx.command and ctx.command.name in ("help",):
        return True
    servers = load_json("servers")
    server_entry = servers.get(str(ctx.guild.id), {})
    disabled = server_entry.get("disabled_commands", [])
    cmd_name = ctx.command.name if ctx.command else None
    if not cmd_name:
        return True
    if cmd_name in disabled:
        try:
            await ctx.send(f"⚠️ The command `{cmd_name}` is currently disabled on this server.")
        except Exception:
            pass
        print(f"[COMMAND BLOCKED] {ctx.guild.name}({ctx.guild.id}) blocked command: {cmd_name}")
        return False
    return True

# -----------------------------
# On ready
# -----------------------------
@bot.event
async def on_ready():
    await tree.sync()  # register slash commands globally (or restrict later)
    print(f"✅ Logged in as {bot.user} (id: {bot.user.id})")
    print("💾 JSON storage ready")
    # ensure economy file entries exist for guilds bot is in
    econ = load_json("economy")
    updated = False
    for g in bot.guilds:
        if str(g.id) not in econ:
            econ[str(g.id)] = {
                "currency_name": "Coins",
                "currency_symbol": "$",
                "starting_balance": 0,
                "tax_rate": 0
            }
            updated = True
    if updated:
        save_json("economy", econ)

# -----------------------------
# Run bot
# -----------------------------
if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN environment variable not set.")
    else:
        bot.run(TOKEN)

