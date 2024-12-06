import os
import uuid
import sqlite3
from dotenv import load_dotenv
import discord
from discord.ext import commands

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Database setup
def init_database():
    conn = sqlite3.connect('betting_market.db')
    cursor = conn.cursor()
    
    # Drop existing tables to reset the schema
    cursor.execute('DROP TABLE IF EXISTS bet_placements')
    cursor.execute('DROP TABLE IF EXISTS bet_options')
    cursor.execute('DROP TABLE IF EXISTS bets')
    
    # Create bets table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bets (
        bet_id TEXT PRIMARY KEY,
        creator_id TEXT,
        description TEXT,
        status TEXT DEFAULT 'OPEN',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Create bet options table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bet_options (
        bet_id TEXT,
        option_text TEXT,
        FOREIGN KEY(bet_id) REFERENCES bets(bet_id)
    )
    ''')
    
    # Create bet placements table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bet_placements (
        placement_id TEXT PRIMARY KEY,
        bet_id TEXT,
        user_id TEXT,
        chosen_outcome TEXT,  
        amount REAL,
        FOREIGN KEY(bet_id) REFERENCES bets(bet_id)
    )
    ''')
    
    conn.commit()
    return conn, cursor

# Intents setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Database connection
conn, cursor = init_database()

@bot.event
async def on_ready():
    print(f'Dennis is logged in and ready!')
    print(f'Bot ID: {bot.user.id}')

@bot.command(name='createbet')
async def create_bet(ctx, *, bet_details):
    """
    Create a new betting market
    Usage: !createbet What will happen? Option1, Option2, Option3
    """
    # Split bet details
    parts = bet_details.split('?')
    if len(parts) != 2:
        await ctx.send("Invalid bet format. Use: !createbet Question? Option1, Option2, Option3")
        return
    
    description = parts[0].strip() + '?'
    options = [opt.strip() for opt in parts[1].split(',')]
    
    if len(options) < 2:
        await ctx.send("Please provide at least two betting options.")
        return
    
    # Generate unique bet ID
    bet_id = str(uuid.uuid4())
    
    # Insert bet into database
    cursor.execute('''
        INSERT INTO bets (bet_id, creator_id, description) 
        VALUES (?, ?, ?)
    ''', (bet_id, str(ctx.author.id), description))
    
    # Insert bet options
    for option in options:
        cursor.execute('''
            INSERT INTO bet_options (bet_id, option_text) 
            VALUES (?, ?)
        ''', (bet_id, option))
    
    # Commit changes
    conn.commit()
    
    # Create embed for bet details
    embed = discord.Embed(
        title="New Betting Market Created!",
        description=description,
        color=discord.Color.green()
    )
    embed.add_field(name="Bet ID", value=bet_id, inline=False)
    embed.add_field(name="Options", value="\n".join(options), inline=False)
    embed.set_footer(text=f"Created by {ctx.author.name}")
    
    await ctx.send(embed=embed)

@bot.command(name='placebet')
async def place_bet(ctx, bet_id: str, option: str, amount: float):
    """
    Place a bet in an existing market
    Usage: !placebet <bet_id> <option> <amount>
    """
    # Check if bet exists
    cursor.execute('SELECT * FROM bets WHERE bet_id = ?', (bet_id,))
    bet = cursor.fetchone()
    
    if not bet:
        await ctx.send(f"No bet found with ID {bet_id}")
        return
    
    # Validate option
    cursor.execute('SELECT * FROM bet_options WHERE bet_id = ? AND option_text = ?', (bet_id, option))
    if not cursor.fetchone():
        await ctx.send(f"Invalid option for this bet. Check the available options.")
        return
    
    # Insert bet placement with chosen_outcome
    placement_id = str(uuid.uuid4())
    cursor.execute('''
        INSERT INTO bet_placements 
        (placement_id, bet_id, user_id, chosen_outcome, amount) 
        VALUES (?, ?, ?, ?, ?)
    ''', (placement_id, bet_id, str(ctx.author.id), option, amount))
    
    conn.commit()
    
    # Confirmation embed
    embed = discord.Embed(
        title="Bet Placed!",
        color=discord.Color.blue()
    )
    embed.add_field(name="Bet ID", value=bet_id, inline=False)
    embed.add_field(name="Option", value=option, inline=False)
    embed.add_field(name="Amount", value=f"${amount}", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='listbets')
async def list_bets(ctx):
    """
    List all active betting markets
    """
    cursor.execute('SELECT bet_id, description FROM bets WHERE status = "OPEN"')
    bets = cursor.fetchall()
    
    if not bets:
        await ctx.send("No active betting markets at the moment.")
        return
    
    embed = discord.Embed(title="Active Betting Markets", color=discord.Color.purple())
    
    for bet_id, description in bets:
        # Fetch options for this bet
        cursor.execute('SELECT option_text FROM bet_options WHERE bet_id = ?', (bet_id,))
        options = [opt[0] for opt in cursor.fetchall()]
        
        embed.add_field(
            name=f"Bet ID: {bet_id}",
            value=f"{description}\nOptions: {', '.join(options)}",
            inline=False
        )
    
    await ctx.send(embed=embed)

# Run the bot
bot.run(TOKEN)