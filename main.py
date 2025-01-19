import os
import sqlite3
from decimal import Decimal
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord.ui import Select, View
import asyncio
import datetime
import re
import pytz

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

class BettingDatabase:
    def __init__(self, db_path='betting_market.db'):
        self.db_path = db_path
        self.init_database()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_database(self):
        """Initialize the database with the new schema"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Markets table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS markets (
                    market_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT CHECK (status IN ('open', 'closed', 'resolved')) DEFAULT 'open',
                    winning_outcome TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Market outcomes table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS market_outcomes (
                    outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER,
                    outcome_name TEXT NOT NULL,
                    FOREIGN KEY (market_id) REFERENCES markets(market_id)
                )
            ''')
            
            # Bet offers table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bet_offers (
                    bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER,
                    bettor_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    offer_amount DECIMAL NOT NULL,
                    ask_amount DECIMAL NOT NULL,
                    status TEXT CHECK (status IN ('open', 'accepted', 'cancelled')) DEFAULT 'open',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    target_user_id TEXT,
                    FOREIGN KEY (market_id) REFERENCES markets(market_id)
                )
            ''')
            
            # Accepted bets table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS accepted_bets (
                    accepted_bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bet_id INTEGER,
                    acceptor_id TEXT NOT NULL,
                    accepted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT CHECK (status IN ('active', 'completed', 'void')) DEFAULT 'active',
                    FOREIGN KEY (bet_id) REFERENCES bet_offers(bet_id)
                )
            ''')

class OutcomeSelect(Select):
    def __init__(self, options):
        # Convert market options into discord select options
        select_options = [
            discord.SelectOption(label=opt, value=str(i)) 
            for i, opt in enumerate(options)
        ]
        super().__init__(
            placeholder="Choose your outcome",
            min_values=1,
            max_values=1,
            options=select_options
        )
    
    async def callback(self, interaction: discord.Interaction):
        # Store the selected value and stop the view
        self.view.selected_option = self.values[0]
        self.view.stop()

class BetView(View):
    def __init__(self, market_data, user):
        super().__init__(timeout=60)
        self.market_data = market_data
        self.user = user
        self.selected_option = None
        
        # Add the select menu
        self.add_item(OutcomeSelect(market_data['options']))
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only allow the user who reacted to use this menu
        return interaction.user.id == self.user.id            

class BettingBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        super().__init__(command_prefix='!', intents=intents)
        self.db = BettingDatabase()

    async def setup_hook(self):
        print(f'Setting up {self.user} (ID: {self.user.id})')
        
        # Initialize active markets and bets dictionaries
        self.active_markets = {}
        self.active_bets = {}
        
        # Load active markets
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get all open markets with their message IDs
            cursor.execute('''
                SELECT market_id, discord_message_id, title 
                FROM markets 
                WHERE status = 'open' 
                AND discord_message_id IS NOT NULL
            ''')
            open_markets = cursor.fetchall()
            
            for market_id, message_id, title in open_markets:
                # Get market options
                cursor.execute('''
                    SELECT outcome_name 
                    FROM market_outcomes 
                    WHERE market_id = ?
                ''', (market_id,))
                options = [row[0] for row in cursor.fetchall()]
                
                # Store in active_markets
                self.active_markets[int(message_id)] = {
                    'market_id': market_id,
                    'options': options,
                    'title': title
                }
                print(f"Loaded active market: {title}")
                
            # Get all open bet offers with their message IDs
            cursor.execute('''
                SELECT bet_id, discord_message_id 
                FROM bet_offers 
                WHERE status = 'open' 
                AND discord_message_id IS NOT NULL
            ''')
            open_bets = cursor.fetchall()
            
            for bet_id, message_id in open_bets:
                # Store in active_bets
                self.active_bets[int(message_id)] = bet_id
                print(f"Loaded active bet: {bet_id}")
                
        print(f"Loaded {len(self.active_markets)} active markets and {len(self.active_bets)} active bets")

bot = BettingBot()


@bot.event
async def on_ready():
    print(f'Dennis is logged in and ready!')
    print(f'Bot ID: {bot.user.id}')

@bot.command(name='createmarket')
async def create_market(ctx, *, market_details):
    """
    Create a new betting market
    Usage: !createmarket What will happen? Option1, Option2, Option3
    """
    # Split market details
    parts = market_details.split('?')
    if len(parts) != 2:
        await ctx.send("Invalid format. Use: !createmarket Question? Option1, Option2, Option3")
        return
    
    title = parts[0].strip() + '?'
    options = [opt.strip() for opt in parts[1].split(',')]
    
    if len(options) < 2:
        await ctx.send("Please provide at least two options.")
        return
    
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Create market
        cursor.execute('''
            INSERT INTO markets (title, description, creator_id) 
            VALUES (?, ?, ?)
        ''', (title, title, str(ctx.author.id)))
        
        market_id = cursor.lastrowid
        
        # Insert outcomes
        for option in options:
            cursor.execute('''
                INSERT INTO market_outcomes (market_id, outcome_name) 
                VALUES (?, ?)
            ''', (market_id, option))
        
        embed = discord.Embed(
            title=title,
            color=discord.Color.green()
        )
        # embed.add_field(name="Market ID", value=market_id, inline=False)
        embed.add_field(name="Options", value="\n".join(options), inline=False)
        # embed.add_field(name="Offer bet:", value="React with <:dennis:1328277972612026388> to offer a bet. (can be repeated)", inline=False)
        # embed.add_field(name="Set resolver:", value="🇷 (creator is default)", inline=False)
        # embed.add_field(name="Set timer:", value="⏲️", inline=False)
        embed.add_field(name="help: ", value="🆘", inline=False)
        embed.set_footer(text=f"Created by {ctx.author.name}")
        
        # Send embed and store the message object
        message = await ctx.send(embed=embed)

        await message.add_reaction("<:dennis:1328277972612026388>")
        await message.add_reaction("🇷")
        await message.add_reaction("⏲️")
        await message.add_reaction("🆘")
        
        # Create thread
        thread = await message.create_thread(
            name=f"Market {market_id}: {title[:50]}{'...' if len(title) > 50 else ''}"  # Truncate long titles
        )
        
        # Welcome message in thread
        await thread.send(
            "https://tenor.com/view/memeplex-sol-remilia-remilio-milady-gif-17952083022135309581"
        )
        
        # Update the database with the message ID and thread ID
        cursor.execute('''
            UPDATE markets 
            SET discord_message_id = ?, 
                thread_id = ?
            WHERE market_id = ?
        ''', (str(message.id), str(thread.id), market_id))
        
        conn.commit()
        
        # Store message ID and market details for reaction handling
        bot.active_markets[message.id] = {
            'market_id': market_id,
            'options': options,
            'title': title,
            'thread_id': thread.id
        }
@bot.event
async def on_raw_reaction_add(payload):
    # Ignore bot's own reactions
    if payload.user_id == bot.user.id:
        return
        
    # Get the message that was reacted to
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    user = await bot.fetch_user(payload.user_id)
    
    if message.id in bot.active_markets: 
        if str(payload.emoji) == "<:dennis:1328277972612026388>":
            await handle_bet_offer_reaction(message, user, bot.active_markets[message.id])
        elif str(payload.emoji) == "🇷":
            await handle_set_market_resolver(message, user)
        elif str(payload.emoji) == "⏲️":
            await handle_set_market_timer(message, user)
        elif str(payload.emoji) == "🆘":
            await handle_market_react_help(message)

   # Check if this is a bet acceptance or explanation
    elif message.id in bot.active_bets:
        bet_id = bot.active_bets[message.id]
        if str(payload.emoji) == "✅":
            await handle_bet_acceptance(message, user, bet_id)
        elif str(payload.emoji) == "❔":
            await handle_bet_explanation(message, user, bet_id)
        elif str(payload.emoji) == "❌":
            await handle_bet_cancellation(message, user, bet_id)
        elif str(payload.emoji) == "🆘":
            await handle_bet_react_help(message)

async def handle_market_react_help(message):
    help_text = (
        "<:dennis:1328277972612026388> Offer a bet\n" 
        "🇷 Set the resolver (creator by default) \n"
        "⏲️ Set a timer to close the market\n"
    )
    help_msg = await message.channel.send(help_text)
    await asyncio.sleep(30)
    await help_msg.delete()

async def update_market_stats(message, market_id):
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Get count and volume of open bets
        cursor.execute('''
            SELECT COUNT(*), SUM(offer_amount)
            FROM bet_offers 
            WHERE market_id = ? AND status = 'open'
        ''', (market_id,))
        open_count, open_volume = cursor.fetchone()
        
        # Get count and volume of accepted bets
        cursor.execute('''
            SELECT COUNT(*), SUM(bo.offer_amount)
            FROM bet_offers bo
            JOIN accepted_bets ab ON bo.bet_id = ab.bet_id
            WHERE bo.market_id = ? AND ab.status = 'active'
        ''', (market_id,))
        accepted_count, accepted_volume = cursor.fetchone()
        
        # Handle None values from SUM
        open_volume = open_volume or 0
        accepted_volume = accepted_volume or 0
        total_volume = open_volume + accepted_volume

        # Get current embed and update or add stats field
        embed = message.embeds[0]
        stats_text = (
            f"📊 **Market Activity**\n"
            f"Open Bets: {open_count}\n"
            f"Accepted Bets: {accepted_count}\n"
            f"Total Volume: ${total_volume:.0f}"
        )
        
        # Update or add the stats field
        stats_found = False
        for i, field in enumerate(embed.fields):
            if field.name == "Market Stats":
                embed.set_field_at(i, name="Market Stats", value=stats_text, inline=False)
                stats_found = True
                break
        
        if not stats_found:
            embed.add_field(name="Market Stats", value=stats_text, inline=False)
            
        await message.edit(embed=embed)

async def handle_bet_react_help(message):
   help_text = (
       "**Bet Reactions Guide:**\n"
       "✅ Accept this bet\n" 
       "❌ Cancel bet\n"
       "❔ See explanation\n"
       "📉 🗣️bad odds\n"
       "🤏 🗣️too small\n" 
       "<:monkaS:814271443327123466> 🗣️too big"
   )
   help_msg = await message.channel.send(help_text)
   
   # Delete help message after 20 seconds
   await asyncio.sleep(20)
   await help_msg.delete()

async def handle_set_market_timer(message, user):
    if message.id not in bot.active_markets:
        await message.channel.send("Error: This message is not an active market.")
        return

    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        market_data = bot.active_markets[message.id]
        market_id = market_data['market_id']
        
        cursor.execute('''
            SELECT creator_id, status
            FROM markets 
            WHERE market_id = ?
        ''', (market_id,))
        market = cursor.fetchone()
        
        if not market:
            await message.channel.send("Error: Market not found.")
            return
            
        creator_id, status = market
        
        if str(user.id) != str(creator_id):
            await message.channel.send("Only the market creator can set the timer.")
            return

        prompt_msg = await message.channel.send(
            "When should this market close?\n"
            "You can use:\n"
            "• Duration format: `24h`, `7d`, `3d12h30m`\n"
            "• Specific time: `2025-01-20 18:00`"
        )
        
        try:
            def check(m):
                return m.author.id == user.id and m.channel.id == message.channel.id
                
            response = await bot.wait_for('message', check=check, timeout=30.0)
            
            # Parse the time input
            time_str = response.content.lower().strip()
            deadline = None
            
            # Try parsing as duration
            duration_pattern = re.compile(r'^(\d+d)?(\d+h)?(\d+m)?$')
            if duration_match := duration_pattern.match(time_str):
                days = 0
                hours = 0
                minutes = 0
                if duration_match.group(1):
                    days = int(duration_match.group(1)[:-1])
                if duration_match.group(2):
                    hours = int(duration_match.group(2)[:-1])
                if duration_match.group(3):
                    minutes = int(duration_match.group(3)[:-1])
                    
                deadline = datetime.datetime.now() + datetime.timedelta(days=days, hours=hours, minutes=minutes)
            
            # Try parsing as specific time
            else:
                try:
                    deadline = datetime.datetime.strptime(time_str, '%Y-%m-%d %H:%M')
                except ValueError:
                    await message.channel.send("Invalid time format. Please use either duration (e.g., '24h', '7d', '3d12h30m') or specific time (e.g., '2025-01-20 18:00')")
                    return

            # Validate deadline is in the future
            if deadline <= datetime.datetime.now():
                await message.channel.send("The deadline must be in the future.")
                return

            # Update the database
            cursor.execute('''
                UPDATE markets
                SET close_time = ?
                WHERE market_id = ?
            ''', (deadline.isoformat(), market_id))
            conn.commit()

            # Delete user's response
            await response.delete()
            await prompt_msg.delete()
            
            # Update the market message with countdown
            await update_market_embed(message, market_id, deadline)
            
            # Schedule the countdown job
            bot.loop.create_task(handle_market_countdown(message, market_id, deadline))
            
            # Convert deadline to Pacific time for display
            pacific = pytz.timezone('America/Los_Angeles')
            deadline_pacific = deadline.astimezone(pacific)
            await message.channel.send(f"Market will close at {deadline_pacific.strftime('%Y-%m-%d %I:%M %p')} PT")
            
        except asyncio.TimeoutError:
            await prompt_msg.delete()
            timeout_msg = await message.channel.send("Timed out waiting for time input.")
            await asyncio.sleep(5)
            await timeout_msg.delete()

async def update_market_embed(message, market_id, deadline):
    # Get current embed and update it
    embed = message.embeds[0]
    time_remaining = deadline - datetime.datetime.now()
    days = time_remaining.days
    hours = time_remaining.seconds // 3600
    minutes = (time_remaining.seconds % 3600) // 60
    
    countdown = f"Closes in: {days}d {hours}h {minutes}m"
    
    # Update or add the countdown field
    countdown_found = False
    for field in embed.fields:
        if field.name == "Time Remaining":
            field.value = countdown
            countdown_found = True
            break
    
    if not countdown_found:
        embed.add_field(name="Time Remaining", value=countdown, inline=False)
    
    await message.edit(embed=embed)

async def handle_market_countdown(message, market_id, deadline):
    while True:
        now = datetime.datetime.now()
        if now >= deadline:
            # Close the market
            with bot.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE markets
                    SET status = 'closed'
                    WHERE market_id = ?
                ''', (market_id,))
                conn.commit()
            
            await message.channel.send(f"🔒 Market {market_id} is now closed for betting!")
            break
        
        # Send reminder at 1 hour remaining
        time_remaining = deadline - now
        if datetime.timedelta(hours=1) <= time_remaining <= datetime.timedelta(hours=1, minutes=1):
            await message.channel.send(f"⚠️ Market {market_id} closes in 1 hour!")
        
        # Update countdown every 5 minutes
        if time_remaining.seconds % 300 == 0:
            await update_market_embed(message, market_id, deadline)
        
        await asyncio.sleep(60)  # Check every minute

async def handle_set_market_resolver(message, user):
    if message.id not in bot.active_markets:
        await message.channel.send("Error: This message is not an active market.")
        return

    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        market_data = bot.active_markets[message.id]
        market_id = market_data['market_id']
        
        cursor.execute('''
            SELECT creator_id, status
            FROM markets 
            WHERE market_id = ?
        ''', (market_id,))
        market = cursor.fetchone()
        
        if not market:
            await message.channel.send("Error: Market not found.")
            return
            
        creator_id, status = market
        
        # Verify the user is the creator
        if str(user.id) != str(creator_id):
            await message.channel.send("Only the market creator can set the resolver.")
            return
        if status != 'open':
            await message.channel.send("Cannot modify a closed or resolved market.")
            return

        # Send message asking to mention the resolver
        prompt_msg = await message.channel.send("Please mention the user you want to set as resolver.")
        
        try:
            # Wait for the creator's response mentioning the resolver
            def check(m):
                return m.author.id == user.id and len(m.mentions) > 0 and m.channel.id == message.channel.id
                
            response = await bot.wait_for('message', check=check, timeout=30.0)
            resolver = response.mentions[0]

            await response.delete()
            
            # Update the database
            cursor.execute('''
                UPDATE markets
                SET resolver_id = ?
                WHERE market_id = ?
            ''', (str(resolver.id), market_id))
            conn.commit()
            
            await message.channel.send(f"{resolver.mention} has been set as the resolver for this market.")
            
        except asyncio.TimeoutError:
            try:
                await prompt_msg.delete()
            except:
                pass

async def handle_bet_cancellation(message, user, bet_id):
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Verify bet exists and user owns it
        cursor.execute('SELECT bettor_id FROM bet_offers WHERE bet_id = ?', (bet_id,))
        bet = cursor.fetchone()
        
        if not bet:
            await message.channel.send("Bet offer not found.", delete_after=10)
            return
            
        if str(user.id) != bet[0]:
            await message.channel.send("You can only cancel your own bet offers.", delete_after=10)
            return
        
        # Remove the bet offer
        cursor.execute('DELETE FROM bet_offers WHERE bet_id = ?', (bet_id,))
        conn.commit()
        
        # Remove from active bets
        bot.active_bets.pop(message.id, None)
    
        # Create cancelled embed
        cancelled_embed = discord.Embed(
            title="Bet Offer Cancelled",
            description=f"Bet offer #{bet_id} has been cancelled.",
            color=discord.Color.red()
        )
        
        # Edit the original message to show cancelled status
        await message.edit(embed=cancelled_embed, view=None)
        await update_market_stats(message, market_data['market_id'])

async def handle_bet_explanation(message, user, bet_id):
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Get bet details
        cursor.execute('''
            SELECT b.bettor_id, b.outcome, b.offer_amount, b.ask_amount, 
                   b.target_user_id, m.title, m.market_id
            FROM bet_offers b
            JOIN markets m ON b.market_id = m.market_id
            WHERE b.bet_id = ?
        ''', (bet_id,))
        
        bet = cursor.fetchone()
        if not bet:
            await message.channel.send("Bet not found.", delete_after=10)
            return
            
        bettor_id, outcome, offer, ask, target_id, title, market_id = bet
        
        # Get all possible outcomes for this market
        cursor.execute('''
            SELECT outcome_name 
            FROM market_outcomes 
            WHERE market_id = ?
        ''', (market_id,))
        outcomes = [row[0] for row in cursor.fetchall()]

    # Create explanation embed
    embed = discord.Embed(
        title=f"Bet #{bet_id} Explained",
        description=f"Market: {title}",
        color=discord.Color.blue()
    )
    
    # Get user names
    bettor = await bot.fetch_user(int(bettor_id))
    bettor_name = bettor.name if bettor else "Unknown"
    
    target_name = "anyone"
    if target_id:
        target = await bot.fetch_user(int(target_id))
        target_name = target.name if target else "Unknown"
    
    # Explain what happens for each outcome
    explanation = "If accepted:\n"
    for possible_outcome in outcomes:
        if possible_outcome == outcome:
            explanation += f"- If \"{possible_outcome}\": {bettor_name} wins ${ask}, acceptor loses ${ask}\n"
        else:
            explanation += f"- If \"{possible_outcome}\": {bettor_name} loses ${offer}, acceptor wins ${offer}\n"
    
    # Add equity explanation based on whether it's a bribe/gift
    if ask == 0:
        equity_explanation = "This is a free bet for the acceptor - they risk nothing to win money."
    elif offer == 0:
        equity_explanation = "This is a pure gift from the bettor - they give money with no chance of return."
    else:
        equity_needed = (ask / (ask + offer)) * 100
        equity_explanation = f"For this bet to be EV0, you need {equity_needed:.1f}% equity."

    explanation += f"\n{equity_explanation}"
    
    embed.add_field(
        name="Pot odds", 
        value=explanation,
        inline=False
    )
    
    # Add who can accept
    embed.add_field(
        name="Who can accept?",
        value=f"This bet can be accepted by {target_name}",
        inline=False
    )
    
    await message.channel.send(embed=embed)

async def handle_bet_offer_reaction(message, user, market_data):
    messages_to_delete = []
    
    # Try to find existing thread
    thread = None
    if 'thread_id' in market_data:
        try:
            thread = await message.guild.fetch_channel(market_data['thread_id'])
        except:
            pass
    
    if thread is None:
        # Get the threads in this channel
        threads = await message.channel.fetch_threads()
        # Find thread started from this message
        for t in threads:
            if t.starter_message and t.starter_message.id == message.id:
                thread = t
                # Update market_data with found thread
                market_data['thread_id'] = thread.id
                
                # Update database with found thread_id
                with bot.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE markets 
                        SET thread_id = ? 
                        WHERE market_id = ?
                    ''', (str(thread.id), market_data['market_id']))
                    conn.commit()
                break

    if thread is None:
        await message.channel.send("Error: Could not find thread for this market.", delete_after=10)
        return
   
   # Verify market is open first
   with bot.db.get_connection() as conn:
       cursor = conn.cursor()
       cursor.execute('SELECT status FROM markets WHERE market_id = ?', 
                     (market_data['market_id'],))
       market_status = cursor.fetchone()
       
       if not market_status or market_status[0] != 'open':
           await message.channel.send("This market is not open for betting.", delete_after=10)
           return

   bet_embed = discord.Embed(
       title="Create Bet",
       description=f"{user.mention} is creating a bet offer.",
       color=discord.Color.blue()
   )
   bet_embed.add_field(
       name="Step 1: Choose your option",
       value="Use the dropdown menu below to select your outcome",
       inline=False
   )
   
   view = BetView(market_data, user)
   prompt_msg = await message.channel.send(embed=bet_embed, view=view)
   messages_to_delete.append(prompt_msg)
   
   await view.wait()
   
   if view.selected_option is None:
       await message.channel.send("Bet creation timed out.", delete_after=10)
       for msg in messages_to_delete:
           try:
               await msg.delete()
           except:
               pass
       return
       
   selected_index = int(view.selected_option)
   selected_option = market_data['options'][selected_index]

   # Target user prompt - in main channel
   target_embed = discord.Embed(
       title="Create Bet",
       description=f"Selected: {selected_option}",
       color=discord.Color.blue()
   )
   target_embed.add_field(
       name="Step 2: Target User (Optional)",
       value="Mention a user to offer this bet to them specifically, or type 'skip' to offer to anyone",
       inline=False
   )
   await prompt_msg.edit(embed=target_embed, view=None)

   # Check for messages in main channel
   def check(m):
       return m.author == user and m.channel == message.channel

   try:
       target_msg = await bot.wait_for('message', check=check, timeout=60.0)
       messages_to_delete.append(target_msg)
       target_user = None
       if target_msg.content.lower() != 'skip' and len(target_msg.mentions) > 0:
           target_user = target_msg.mentions[0]
       
       # Amount prompt - in main channel
       amount_embed = discord.Embed(
           title="Create Bet",
           description=f"Selected: {selected_option}",
           color=discord.Color.blue()
       )
       amount_embed.add_field(
           name="Step 3: Risk Amount",
           value="How much would you like to risk? (in $)",
           inline=False
       )
       await prompt_msg.edit(embed=amount_embed)
       
       amount_msg = await bot.wait_for('message', check=check, timeout=60.0)
       messages_to_delete.append(amount_msg)
       
       try:
           offer_amount = float(amount_msg.content)
           
           winnings_embed = discord.Embed(
               title="Create Bet",
               description=f"Selected: {selected_option}\nRisk Amount: ${offer_amount}",
               color=discord.Color.blue()
           )
           winnings_embed.add_field(
               name="Step 4: Desired Winnings",
               value="How much would you like to win? (in $)",
               inline=False
           )
           await prompt_msg.edit(embed=winnings_embed)
           
           winnings_msg = await bot.wait_for('message', check=check, timeout=60.0)
           messages_to_delete.append(winnings_msg)
           
           try:
               ask_amount = float(winnings_msg.content)
               
               # Create bet in database
               with bot.db.get_connection() as conn:
                   cursor = conn.cursor()
                   # Create final bet message in thread
                   final_embed = discord.Embed(
                       title=f"{user} offering {selected_option} on: {market_data['title']}",
                       color=discord.Color.green()
                   )
                   final_embed.add_field(name="Risking", value=f"${offer_amount}", inline=True)
                   final_embed.add_field(name="To Win", value=f"${ask_amount}", inline=True)
                   final_embed.add_field(name="Bet ID", value="Pending...", inline=True)
                   final_embed.add_field(name="Market ID:", value=market_data['market_id'], inline=True)
                   final_embed.add_field(name="Help: 🆘", value="", inline=False)

                   # Send final embed to thread and get the message object
                   bet_msg = await thread.send(embed=final_embed)
                   
                   # Now insert into database with the new message ID
                   cursor.execute('''
                       INSERT INTO bet_offers 
                       (market_id, bettor_id, outcome, offer_amount, ask_amount, target_user_id, discord_message_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                   ''', (market_data['market_id'], str(user.id), selected_option, 
                        offer_amount, ask_amount, str(target_user.id) if target_user else None, 
                        str(bet_msg.id)))
                   bet_id = cursor.lastrowid
                   conn.commit()

                   # Update the embed with the bet ID
                   final_embed.set_field_at(2, name="Bet ID", value=bet_id, inline=True)
                   
                   # Add reactions to the bet message
                   await bet_msg.add_reaction("✅")
                   await bet_msg.add_reaction("❌")
                   await bet_msg.add_reaction("❔")
                   await bet_msg.add_reaction("📉")
                   await bet_msg.add_reaction("🤏")
                   await bet_msg.add_reaction("<:monkaS:814271443327123466>")
                   await bet_msg.add_reaction("🆘")

                   if target_user:
                       final_embed.add_field(name="Offered To", value=target_user.mention, inline=False)
                   await bet_msg.edit(embed=final_embed)

                   # Store in active bets for reaction handling
                   bot.active_bets = getattr(bot, 'active_bets', {})
                   bot.active_bets[bet_msg.id] = bet_id
               
                   # Update market stats
                   try:
                       await update_market_stats(message, market_data['market_id'])
                   finally:
                       # Clean up all intermediate messages
                       for msg in messages_to_delete:
                           try:
                               await msg.delete()
                           except:
                               pass
               
           except ValueError:
               await message.channel.send("Invalid winnings amount. Bet creation cancelled.", delete_after=10)
               
       except ValueError:
           await message.channel.send("Invalid risk amount. Bet creation cancelled.", delete_after=10)
           
   except asyncio.TimeoutError:
       await message.channel.send("Bet creation timed out.", delete_after=10)
   
   # Clean up all intermediate messages
   finally:
       for msg in messages_to_delete:
           try:
               await msg.delete()
           except:
               pass

async def handle_bet_acceptance(message, user, bet_id):
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Get bet offer details
        cursor.execute('''
            SELECT bo.market_id, bo.bettor_id, bo.status, bo.outcome, 
                   bo.offer_amount, bo.ask_amount, m.status as market_status,
                   bo.target_user_id, m.title, m.description
            FROM bet_offers bo
            JOIN markets m ON bo.market_id = m.market_id
            WHERE bo.bet_id = ?
        ''', (bet_id,))
        
        bet = cursor.fetchone()
        if not bet:
            await message.channel.send("Bet offer not found.", delete_after=10)
            return
        
        market_id, bettor_id, bet_status, outcome, offer_amount, ask_amount, market_status, target_user_id, title, description = bet
        
        # Validation checks
        if str(user.id) == bettor_id:
            await message.channel.send("You cannot accept your own bet offer.", delete_after=10)
            return
        
        if bet_status != 'open':
            await message.channel.send("This bet offer is no longer available.", delete_after=10)
            return
        
        if market_status != 'open':
            await message.channel.send("This market is no longer open for betting.", delete_after=10)
            return

        # Check if bet was targeted at a specific user
        if target_user_id and str(user.id) != target_user_id:
            await message.channel.send("This bet was offered to a specific user only.", delete_after=10)
            return
        
        # Update bet offer status and create accepted bet record
        cursor.execute('''
            UPDATE bet_offers 
            SET status = 'accepted' 
            WHERE bet_id = ?
        ''', (bet_id,))
        
        cursor.execute('''
            INSERT INTO accepted_bets (bet_id, acceptor_id) 
            VALUES (?, ?)
        ''', (bet_id, str(user.id)))
        
        conn.commit()
        
        # Get bettor's username for the embed
        bettor = await bot.fetch_user(int(bettor_id))
        bettor_name = bettor.name if bettor else "Unknown User"
        
        embed = discord.Embed(
            title="Bet Accepted!",
            description=f"**Market:** {title}\n\nBet ID: {bet_id}",
            color=discord.Color.green()
        )
        embed.add_field(name="Market ID", value=market_id, inline=False)
        embed.add_field(name="Outcome", value=outcome, inline=False)
        embed.add_field(name="Original Bettor", value=bettor_name, inline=True)
        embed.add_field(name="Acceptor", value=user.name, inline=True)
        embed.add_field(name=f"{bettor_name} Risks", value=f"${offer_amount}", inline=True)
        embed.add_field(name=f"{user.name} Risks", value=f"${ask_amount}", inline=True)
        
        await message.channel.send(embed=embed)
        await update_market_stats(message, market_data['market_id'])
        
        # Remove from active bets
        bot.active_bets.pop(message.id, None)

@bot.command(name='offerbet')
async def offer_bet(ctx, market_id: int, outcome: str, offer: float, ask: float, target_user: discord.Member = None):
    await ctx.send("Thank you for being an early adopter. Bet offers are now made by reacting to the betting market message.")

@bot.command(name='acceptbet')
async def accept_bet(ctx, bet_id: int):
    await ctx.send("Thank you for being an early adopter. Bet accepting is now done by reacting to the betting offer message.")

@bot.command(name='cancelbet')
async def cancel_bet(ctx, bet_id: int):
    """
    Cancel an open bet offer by removing it
    Usage: !cancelbet <bet_id>
    """
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Verify bet exists and user owns it
        cursor.execute('SELECT bettor_id FROM bet_offers WHERE bet_id = ?', (bet_id,))
        bet = cursor.fetchone()
        
        if not bet:
            await ctx.send("Bet offer not found.")
            return
            
        if str(ctx.author.id) != bet[0]:
            await ctx.send("You can only cancel your own bet offers.")
            return
        
        # Remove the bet offer
        cursor.execute('DELETE FROM bet_offers WHERE bet_id = ?', (bet_id,))
        conn.commit()
    
    embed = discord.Embed(
        title="Bet Offer Cancelled",
        description=f"Bet offer #{bet_id} has been removed.",
        color=discord.Color.red()
    )
    
    await update_market_stats(message, market_data['market_id'])
    await ctx.send(embed=embed)

@bot.command(name='listmarkets')
async def list_markets(ctx):
    """List all active betting markets"""
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT m.market_id, m.title, GROUP_CONCAT(mo.outcome_name, ', ') as outcomes
            FROM markets m
            JOIN market_outcomes mo ON m.market_id = mo.market_id
            WHERE m.status = 'open'
            GROUP BY m.market_id
        ''')
        markets = cursor.fetchall()
    
    if not markets:
        await ctx.send("No active betting markets at the moment.")
        return
    
    embed = discord.Embed(title="Active Betting Markets", color=discord.Color.purple())
    
    for market_id, title, outcomes in markets:
        embed.add_field(
            name=f"Market ID: {market_id}",
            value=f"{title}\nOptions: {outcomes}",
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name='listbets')
async def list_bets(ctx, market_id: int = None):
    """
    List all open bet offers, optionally filtered by market
    Usage: !listbets [market_id]
    """
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        if market_id:
            cursor.execute('''
                SELECT bo.bet_id, m.title, bo.outcome, bo.offer_amount, bo.ask_amount, 
                       bo.bettor_id, bo.target_user_id
                FROM bet_offers bo
                JOIN markets m ON bo.market_id = m.market_id
                WHERE bo.status = 'open' AND bo.market_id = ?
            ''', (market_id,))
        else:
            cursor.execute('''
                SELECT bo.bet_id, m.title, bo.outcome, bo.offer_amount, bo.ask_amount,
                       bo.bettor_id, bo.target_user_id
                FROM bet_offers bo
                JOIN markets m ON bo.market_id = m.market_id
                WHERE bo.status = 'open'
            ''')
        
        bets = cursor.fetchall()
    
    if not bets:
        await ctx.send("No open bet offers found.")
        return
    
    embed = discord.Embed(title="Open Bet Offers", color=discord.Color.gold())
    
    for bet_id, title, outcome, offer, ask, bettor_id, target_user_id in bets:
        # Get bettor's name
        bettor = await bot.fetch_user(int(bettor_id))
        bettor_name = bettor.name if bettor else "Unknown User"
        
        # Build bet description
        description = [
            f"Market: {title}",
            f"Outcome: {outcome}",
            f"Offered by: {bettor_name}",
            f"Risk: ${offer}",
            f"To Win: ${ask}"
        ]
        
        # Add target user info if present
        if target_user_id:
            target_user = await bot.fetch_user(int(target_user_id))
            if target_user:
                description.append(f"Offered to: {target_user.mention}")
        
        embed.add_field(
            name=f"Bet ID: {bet_id}",
            value="\n".join(description),
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name='resolvemarket')
async def resolve_market(ctx, market_id: int, *, winning_outcome: str):
    """
    Resolve a betting market with the winning outcome
    Usage: !resolvemarket <market_id> <winning_outcome>
    Only the market creator or designated resolver can resolve markets.
    """
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Check if market exists and user is authorized
        cursor.execute('''
            SELECT title, status, creator_id, resolver_id
            FROM markets
            WHERE market_id = ?
        ''', (market_id,))
        market = cursor.fetchone()
        
        if not market:
            await ctx.send("Market not found.")
            return
        
        title, status, creator_id, resolver_id = market

        # Verify the user is either the creator or resolver
        if creator_id is not None and resolver_id is not None:
            if str(ctx.author.id) != str(creator_id) and str(ctx.author.id) != str(resolver_id):
                await ctx.send("Only the market creator or designated resolver can resolve this market.")
                return
        
        if status == 'resolved':
            await ctx.send("This market has already been resolved.")
            return
        
        # Verify the outcome is valid for this market
        cursor.execute('''
            SELECT outcome_name 
            FROM market_outcomes 
            WHERE market_id = ? AND outcome_name = ?
        ''', (market_id, winning_outcome))
        
        if not cursor.fetchone():
            await ctx.send(f"'{winning_outcome}' is not a valid outcome for this market.")
            return
        
        # Update market status
        cursor.execute('''
            UPDATE markets 
            SET status = 'resolved', 
                winning_outcome = ?
            WHERE market_id = ?
        ''', (winning_outcome, market_id))
        
        # Get all accepted bets for this market
        cursor.execute('''
            SELECT 
                bo.bet_id,
                bo.bettor_id,
                ab.acceptor_id,
                bo.outcome,
                bo.offer_amount,
                bo.ask_amount
            FROM bet_offers bo
            JOIN accepted_bets ab ON bo.bet_id = ab.bet_id
            WHERE bo.market_id = ? AND ab.status = 'active'
        ''', (market_id,))
        
        active_bets = cursor.fetchall()
        
        # Update accepted bets to completed
        cursor.execute('''
            UPDATE accepted_bets
            SET status = 'completed'
            WHERE bet_id IN (
                SELECT bet_id 
                FROM bet_offers 
                WHERE market_id = ?
            )
        ''', (market_id,))
        
        # Cancel any open offers
        cursor.execute('''
            UPDATE bet_offers
            SET status = 'cancelled'
            WHERE market_id = ? AND status = 'open'
        ''', (market_id,))
        
        conn.commit()
    
    # Create resolution announcement embed
    embed = discord.Embed(
        title="Market Resolved! 🏁",
        description=f"**{title}**\nWinning Outcome: {winning_outcome}",
        color=discord.Color.green()
    )
    
    # Add bet resolution details
    if active_bets:
        results_text = ""
        for bet_id, bettor_id, acceptor_id, outcome, offer_amount, ask_amount in active_bets:
            bettor = await bot.fetch_user(int(bettor_id))
            acceptor = await bot.fetch_user(int(acceptor_id))
            bettor_name = bettor.name if bettor else "Unknown User"
            acceptor_name = acceptor.name if acceptor else "Unknown User"
            
            # Determine winner
            if outcome == winning_outcome:
                winner = bettor_name
                loser = acceptor_name
                win_amount = ask_amount
            else:
                winner = acceptor_name
                loser = bettor_name
                win_amount = offer_amount
            
            results_text += f"**Bet ID {bet_id}**\n"
            results_text += f"🏆 {winner} wins ${win_amount}\n"
            results_text += f"💸 {loser} loses their stake\n\n"
        
        embed.add_field(
            name="Bet Resolutions",
            value=results_text,
            inline=False
        )

        embed.add_field(name="🤝", value="React 🤝 to confirm when payment is settled.", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='mybets')
async def my_bets(ctx):
    """
    List all your active bet offers and accepted bets
    Usage: !mybets
    """
    user_id = str(ctx.author.id)
    
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Get user's open offers
        cursor.execute('''
            SELECT 
                bo.bet_id,
                m.title,
                bo.outcome,
                bo.offer_amount,
                bo.ask_amount,
                'offer' as bet_type
            FROM bet_offers bo
            JOIN markets m ON bo.market_id = m.market_id
            WHERE bo.bettor_id = ? AND bo.status = 'open'
        ''', (user_id,))
        
        open_offers = cursor.fetchall()
        
        # Get bets where user is the original bettor
        cursor.execute('''
            SELECT 
                bo.bet_id,
                m.title,
                bo.outcome,
                bo.offer_amount as your_risk,
                bo.ask_amount as your_win,
                ab.acceptor_id,
                'original_bettor' as bet_type
            FROM bet_offers bo
            JOIN markets m ON bo.market_id = m.market_id
            JOIN accepted_bets ab ON bo.bet_id = ab.bet_id
            WHERE bo.bettor_id = ? AND bo.status = 'accepted' AND ab.status = 'active'
        ''', (user_id,))
        
        bets_as_bettor = cursor.fetchall()
        
        # Get bets where user is the acceptor
        cursor.execute('''
            SELECT 
                bo.bet_id,
                m.title,
                bo.outcome,
                bo.ask_amount as your_risk,
                bo.offer_amount as your_win,
                bo.bettor_id,
                'acceptor' as bet_type
            FROM bet_offers bo
            JOIN markets m ON bo.market_id = m.market_id
            JOIN accepted_bets ab ON bo.bet_id = ab.bet_id
            WHERE ab.acceptor_id = ? AND bo.status = 'accepted' AND ab.status = 'active'
        ''', (user_id,))
        
        bets_as_acceptor = cursor.fetchall()
    
    embed = discord.Embed(
        title=f"Betting Activity for {ctx.author.name}",
        color=discord.Color.blue()
    )
    
    # Add open offers section
    if open_offers:
        offers_text = ""
        for bet_id, title, outcome, offer, ask, _ in open_offers:
            offers_text += f"**Bet ID {bet_id}**\n"
            offers_text += f"Market: {title}\n"
            offers_text += f"Outcome: {outcome}\n"
            offers_text += f"You Risk: ${offer} to Win: ${ask}\n\n"
        
        embed.add_field(
            name="📊 Your Open Offers",
            value=offers_text or "No open offers",
            inline=False
        )
    
    # Add active bets where user is original bettor
    if bets_as_bettor:
        bettor_text = ""
        for bet_id, title, outcome, risk, win, acceptor_id, _ in bets_as_bettor:
            acceptor = await bot.fetch_user(int(acceptor_id))
            acceptor_name = acceptor.name if acceptor else "Unknown User"
            
            bettor_text += f"**Bet ID {bet_id}**\n"
            bettor_text += f"Market: {title}\n"
            bettor_text += f"Outcome: {outcome}\n"
            bettor_text += f"You Risk: ${risk} to Win: ${win}\n"
            bettor_text += f"Against: {acceptor_name}\n\n"
        
        embed.add_field(
            name="🎲 Your Active Bets (As Bettor)",
            value=bettor_text or "No active bets as bettor",
            inline=False
        )
    
    # Add active bets where user is acceptor
    if bets_as_acceptor:
        acceptor_text = ""
        for bet_id, title, outcome, risk, win, bettor_id, _ in bets_as_acceptor:
            bettor = await bot.fetch_user(int(bettor_id))
            bettor_name = bettor.name if bettor else "Unknown User"
            
            acceptor_text += f"**Bet ID {bet_id}**\n"
            acceptor_text += f"Market: {title}\n"
            acceptor_text += f"Outcome: {outcome}\n"
            acceptor_text += f"You Risk: ${risk} to Win: ${win}\n"
            acceptor_text += f"Against: {bettor_name}\n\n"
        
        embed.add_field(
            name="🎲 Your Active Bets (As Acceptor)",
            value=acceptor_text or "No active bets as acceptor",
            inline=False
        )
    
    if not (open_offers or bets_as_bettor or bets_as_acceptor):
        embed.description = "You have no open offers or active bets."
    
    await ctx.send(embed=embed)

@bot.command(name='explainbet')
async def explain_bet(ctx, bet_id: int):
    """
    Explain what would happen if a bet is accepted
    Usage: !explainbet <bet_id>
    """
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Get bet details
        cursor.execute('''
            SELECT b.bettor_id, b.outcome, b.offer_amount, b.ask_amount, 
                   b.target_user_id, m.title, m.market_id
            FROM bet_offers b
            JOIN markets m ON b.market_id = m.market_id
            WHERE b.bet_id = ?
        ''', (bet_id,))
        
        bet = cursor.fetchone()
        if not bet:
            await ctx.send("Bet not found.")
            return
            
        bettor_id, outcome, offer, ask, target_id, title, market_id = bet
        
        # Get all possible outcomes for this market
        cursor.execute('''
            SELECT outcome_name 
            FROM market_outcomes 
            WHERE market_id = ?
        ''', (market_id,))
        outcomes = [row[0] for row in cursor.fetchall()]

    # Create explanation embed
    embed = discord.Embed(
        title=f"Bet #{bet_id} Explained",
        description=f"Market: {title}",
        color=discord.Color.blue()
    )
    
    # Get user names (using IDs stored in DB)
    bettor = await bot.fetch_user(int(bettor_id))
    bettor_name = bettor.name if bettor else "Unknown"
    
    target_name = "anyone"
    if target_id:
        target = await bot.fetch_user(int(target_id))
        target_name = target.name if target else "Unknown"
    
    # Explain what happens for each outcome
    explanation = "If accepted:\n"
    for possible_outcome in outcomes:
        if possible_outcome == outcome:
            explanation += f"- If \"{possible_outcome}\": {bettor_name} wins ${ask}, acceptor loses ${ask}\n"
        else:
            explanation += f"- If \"{possible_outcome}\": {bettor_name} loses ${offer}, acceptor wins ${offer}\n"
    
    embed.add_field(
        name="Mechanics", 
        value=explanation,
        inline=False
    )
    
    # Add who can accept
    embed.add_field(
        name="Who can accept?",
        value=f"This bet can be accepted by {target_name}",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command(name='noslop')
async def noslop(ctx):
    await ctx.send("hey")

@bot.command(name='dennishelp')
async def dennis_help(ctx):
   """
   Show all available commands for Dennis the betting bot
   Usage: !dennishelp
   """
   embed = discord.Embed(
       title="Dennis Betting Bot Commands 🎲",
       description="A betting market bot for financial memetic warfare",
       color=discord.Color.blue()
   )
   
   # Market Creation & Resolution
   embed.add_field(
       name="📊 Markets",
       value="""
**!createmarket** `<question>? <option1>, <option2>, ...`
Create a new betting market
Example: `!createmarket Will it rain tomorrow? Yes, No`

**!resolvemarket** `<market_id> <winning_outcome>`
Resolve a market (creator or designated resolver only)
Example: `!resolvemarket 1 Yes`
       """,
       inline=False
   )
   
   # Betting System
   embed.add_field(
       name="💰 How Betting Works",
       value="""
**Creating Bets**
1. React with <:dennis:1328277972612026388> on any market
2. Select which outcome you're betting on
3. Enter how much you want to risk and how much you want to win

**Managing Bets**
- ✅ Accept a bet
- ❌ Cancel your bet
- ❔ See bet explanation (including your pot odds!)
- 📉 Flag bet for bad odds
- 🤏 Flag bet as too small
- <:monkaS:814271443327123466> Flag bet as too big
       """,
       inline=False
   )
   
   # Market Features
   embed.add_field(
       name="⚙️ Market Features",
       value="""
**Resolver Settings**
- React 🇷 to set a different resolver for your market

**Timer Settings**
- React ⏲️ to set when the market closes
- Support for duration (24h, 7d) or specific time
       """,
       inline=False
   )

   # Tracking Bets
   embed.add_field(
       name="📈 Track Your Bets",
       value="""
**!listbets** `[market_id]`
List all open bet offers, optionally filtered by market
Example: `!listbets` or `!listbets 1`

**!mybets**
Show your open offers and active bets
       """,
       inline=False
   )
   
   # Usage Tips
   embed.add_field(
       name="💡 Tips",
       value="""
- When offering a bet, the 'offer' is what you risk and the 'ask' is what you want to win
- Betting amounts are in dollars ($)
- You can't accept your own bets
- Only market creators or designated resolvers can resolve markets
- Markets can have optional close times
       """,
       inline=False
   )

   embed.add_field(
       name="🔧",
       value="**!noslop**",
       inline=False
   )
   
   embed.set_footer(text="Dennis v2.0 | Boats carried by Claude")
   
   await ctx.send(embed=embed)
# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)