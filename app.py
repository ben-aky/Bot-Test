# app.py
import os
import re
import logging
from datetime import datetime
import math

import discord
from discord import app_commands
from discord.ext import commands

# -------------------------
# Logging / Config
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("retention-bot")

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    logger.error("DISCORD_TOKEN environment variable not set. Set it and restart the bot.")
    raise SystemExit(1)

# -------------------------
# Intents & Bot
# -------------------------
intents = discord.Intents.default()
# If you ever need to read message content, enable message_content in code and Dev Portal.
# intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------
# Helpers
# -------------------------
def parse_salary_to_int(s: str) -> int | None:
    """Parse salary like "$12,000,000" or "12000000" to integer dollars."""
    if not s:
        return None
    # Remove currency symbols, spaces, commas
    cleaned = re.sub(r"[^\d\-]", "", s)
    if cleaned == "" or cleaned == "-" or cleaned == "--":
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None

def format_currency(n: int) -> str:
    """Format integer dollars to comma-separated string like 12,000,000"""
    return f"{n:,}"

def extract_player_name(stats_text: str) -> str:
    """Heuristic extraction of player name from a pasted stats block."""
    if not stats_text:
        return "Unknown Player"
    lines = [ln.strip() for ln in stats_text.splitlines() if ln.strip()]
    if not lines:
        return "Unknown Player"
    # Look for "Name: ..." or "Player: ..."
    for ln in lines[:8]:
        m = re.search(r"^(?:Name|Player)[:\s\-]+(.{2,60})$", ln, flags=re.I)
        if m:
            return m.group(1).strip()
    # Fallback: take first line up to separators
    first = lines[0]
    if " - " in first:
        return first.split(" - ", 1)[0].strip()
    if "," in first and len(first.split(",")[0]) < 40:
        return first.split(",", 1)[0].strip()
    return first[:60].strip()

# -------------------------
# Modal (exactly 5 inputs; labels <=45 chars)
# -------------------------
class RetentionModal(discord.ui.Modal, title="Retention Form"):
    # Class attribute TextInputs create the modal fields (count must be <= 5)
    teams = discord.ui.TextInput(
        label="Teams (Trading -> Receiving)",  # length < 45
        placeholder="Example: Rangers -> Devils",
        required=True,
        max_length=100,
        style=discord.TextStyle.short,
    )

    player_stats = discord.ui.TextInput(
        label="Player Stats",
        placeholder="Paste stats block or a short stat line",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=1500,
    )

    player_salary = discord.ui.TextInput(
        label="Player Salary",
        placeholder="Example: $2,500,000 or 2500000",
        required=True,
        max_length=50,
        style=discord.TextStyle.short,
    )

    retention_amount = discord.ui.TextInput(
        label="Retention Amount",
        placeholder="If percent, give 25 for 25%. If numeric, give dollars.",
        required=True,
        max_length=40,
        style=discord.TextStyle.short,
    )

    years_retained = discord.ui.TextInput(
        label="Years Retained",
        placeholder="Integer years (e.g. 1, 2, 3)",
        required=True,
        max_length=4,
        style=discord.TextStyle.short,
    )

    def __init__(self, retention_type: str):
        super().__init__()  # required to initialize modal internals
        self.retention_type = retention_type  # "percent" or "number"

    async def on_submit(self, interaction: discord.Interaction):
        # Parse teams
        raw = self.teams.value.strip()
        if "->" in raw:
            t_from, t_to = [p.strip() for p in raw.split("->", 1)]
        elif "/" in raw:
            t_from, t_to = [p.strip() for p in raw.split("/", 1)]
        elif "," in raw:
            t_from, t_to = [p.strip() for p in raw.split(",", 1)]
        else:
            t_from = raw
            t_to = "Unknown"

        stats_text = self.player_stats.value.strip()
        player_name = extract_player_name(stats_text)

        # Salary
        salary_raw = self.player_salary.value.strip()
        salary_val = parse_salary_to_int(salary_raw)
        if salary_val is None:
            await interaction.response.send_message(
                f"Could not parse salary from `{salary_raw}`. Use digits and optional commas/dollar sign.",
                ephemeral=True
            )
            return

        # Years
        try:
            years = int(re.sub(r"[^\d\-]", "", self.years_retained.value.strip()) or "0")
            if years < 0:
                raise ValueError()
        except Exception:
            await interaction.response.send_message("Invalid years retained; provide a positive integer.", ephemeral=True)
            return

        # Retention amount parsing
        retention_raw = re.sub(r"[^\d\.-]", "", self.retention_amount.value.strip())
        if retention_raw == "":
            await interaction.response.send_message("Invalid retention amount.", ephemeral=True)
            return

        if self.retention_type == "percent":
            # Treat retention_raw as percent (e.g., "25" -> 25%)
            try:
                pct = float(retention_raw)
            except Exception:
                await interaction.response.send_message("Invalid percent value for retention.", ephemeral=True)
                return
            pct = max(0.0, min(100.0, pct))
            retained_amount = math.floor(salary_val * (pct / 100.0))
            remaining = salary_val - retained_amount
            retention_display = f"{pct:.2f}%"
        else:
            # Numeric retention (dollars)
            try:
                retained_amount = int(float(retention_raw))
            except Exception:
                await interaction.response.send_message("Invalid numeric retention value.", ephemeral=True)
                return
            retained_amount = max(0, retained_amount)
            remaining = max(0, salary_val - retained_amount)
            pct = (retained_amount / salary_val * 100.0) if salary_val > 0 else 0.0
            retention_display = f"${format_currency(retained_amount)} ({pct:.2f}%)"

        expire_year = datetime.now().year + years

        # Build embed
        embed = discord.Embed(
            title="Retention Result",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Trade", value=f"**{t_from} → {t_to}**", inline=False)
        embed.add_field(
            name="Player Retention",
            value=f"**{player_name}**, {retention_display}, {years} year(s) ({t_from} 0/{years if years>0 else 1})",
            inline=False
        )
        embed.add_field(
            name="Salary Applied",
            value=(
                f"Original Salary: **${format_currency(salary_val)}**\n"
                f"Retained by {t_from}: **${format_currency(retained_amount)}** → Remaining applied to {t_to}: **${format_currency(remaining)}**\n"
                f"(expires {expire_year})"
            ),
            inline=False
        )

        # Limit stats to avoid embed overflow
        stats_preview = stats_text if len(stats_text) <= 1000 else stats_text[:997] + "..."
        embed.add_field(name="Player Stats", value=f"```{stats_preview}```", inline=False)
        embed.set_footer(text=f"Retention type: {'Percent' if self.retention_type=='percent' else 'Numeric'}")

        await interaction.response.send_message(embed=embed)

# -------------------------
# View with Buttons (opens modal)
# -------------------------
class RetentionTypeView(discord.ui.View):
    def __init__(self, timeout: float = 180.0):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="Percent", style=discord.ButtonStyle.primary)
    async def percent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(RetentionModal("percent"))
        except discord.HTTPException as e:
            # Return clear error if modal can't open
            await interaction.response.send_message(f"Failed to open modal: {e}", ephemeral=True)

    @discord.ui.button(label="Numeric", style=discord.ButtonStyle.secondary)
    async def numeric_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(RetentionModal("number"))
        except discord.HTTPException as e:
            await interaction.response.send_message(f"Failed to open modal: {e}", ephemeral=True)

# -------------------------
# Slash command to show the buttons
# -------------------------
@bot.tree.command(name="retention", description="Open the retention form (choose Percent or Numeric)")
async def retention_command(interaction: discord.Interaction):
    view = RetentionTypeView()
    await interaction.response.send_message("Choose retention type:", view=view, ephemeral=True)

# -------------------------
# Events
# -------------------------
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        await bot.tree.sync()
        logger.info("Slash commands synced.")
    except Exception as e:
        logger.warning(f"Failed to sync slash commands: {e}")

# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    bot.run(TOKEN)