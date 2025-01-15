import os
import sqlite3
from decimal import Decimal
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord.ui import Select, View

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
                    'options': options
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
        embed.add_field(name="Market ID", value=market_id, inline=False)
        embed.add_field(name="Options", value="\n".join(options), inline=False)
        embed.add_field(name="Offer bet:", value="React with <:dennis:1328277972612026388> to offer a bet. (can be repeated)", inline=False)
        embed.set_footer(text=f"Created by {ctx.author.name}")
        
        # Send embed and store the message object
        message = await ctx.send(embed=embed)
        
        # Update the database with the message ID
        cursor.execute('''
            UPDATE markets 
            SET discord_message_id = ? 
            WHERE market_id = ?
        ''', (str(message.id), market_id))
        
        conn.commit()
        
        # Add the betting reaction
        await message.add_reaction("<:dennis:1328277972612026388>")
        
        # Store message ID and market details for reaction handling
        bot.active_markets[message.id] = {
            'market_id': market_id,
            'options': options
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
    
    # Check if this is a betting reaction on a market message
    if message.id in bot.active_markets and str(payload.emoji) == "<:dennis:1328277972612026388>":
        await handle_bet_offer_reaction(message, user, bot.active_markets[message.id])

   # Check if this is a bet acceptance or explanation
    elif message.id in bot.active_bets:
        bet_id = bot.active_bets[message.id]
        if str(payload.emoji) == "✅":
            await handle_bet_acceptance(message, user, bet_id)
        elif str(payload.emoji) == "❔":
            await handle_bet_explanation(message, user, bet_id)
        elif str(payload.emoji) == "❌":
            await handle_bet_cancellation(message, user, bet_id)

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
        equity_explanation = f"For this bet to be >=EV0, you need to believe you have {equity_needed:.1f}% equity."

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
    # List to store messages we'll want to clean up
    messages_to_delete = []
    
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
    
    # Create view with select menu
    view = BetView(market_data, user)
    
    # Send in the same channel as the original message
    prompt_msg = await message.channel.send(embed=bet_embed, view=view)
    
    # Wait for selection
    await view.wait()
    
    if view.selected_option is None:
        await message.channel.send("Bet creation timed out.", delete_after=10)
        await prompt_msg.delete()
        return
        
    selected_index = int(view.selected_option)
    selected_option = market_data['options'][selected_index]

    # Ask for target user
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

    # Wait for their response
    def check(m):
        return m.author == user and m.channel == message.channel

    try:
        # Get target user
        target_msg = await bot.wait_for('message', check=check, timeout=60.0)
        messages_to_delete.append(target_msg)  # Store for deletion
        target_user = None
        if target_msg.content.lower() != 'skip' and len(target_msg.mentions) > 0:
            target_user = target_msg.mentions[0]
        
        # Ask for bet amount
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
        
        # Get amount
        amount_msg = await bot.wait_for('message', check=check, timeout=60.0)
        messages_to_delete.append(amount_msg)  # Store for deletion
        try:
            offer_amount = float(amount_msg.content)
            
            # Ask for desired winnings
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
            
            # Get desired winnings
            winnings_msg = await bot.wait_for('message', check=check, timeout=60.0)
            messages_to_delete.append(winnings_msg)  # Store for deletion
            try:
                ask_amount = float(winnings_msg.content)
                
                # Create the bet offer in the database
                with bot.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO bet_offers 
                        (market_id, bettor_id, outcome, offer_amount, ask_amount, target_user_id, discord_message_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (market_data['market_id'], str(user.id), selected_option, 
                         offer_amount, ask_amount, str(target_user.id) if target_user else None, 
                         str(prompt_msg.id)))
                    bet_id = cursor.lastrowid
                    conn.commit()
                
                # Show final confirmation
                final_embed = discord.Embed(
                    title="Bet Offered!",
                    color=discord.Color.green()
                )

                final_embed.add_field(name="Bet ID", value=bet_id, inline=False)
                final_embed.add_field(name="Market ID", value=market_data['market_id'], inline=False)
                final_embed.add_field(name="Outcome", value=selected_option, inline=False)
                final_embed.add_field(name="You Risk", value=f"${offer_amount}", inline=True)
                final_embed.add_field(name="To Win", value=f"${ask_amount}", inline=True)
                final_embed.add_field(name="Offered By", value=user.mention, inline=False)
                final_embed.add_field(name="Reacts:", value="✅ to accept this bet. ❌ to cancel bet. ❔for explanation.", inline=False)

                await prompt_msg.add_reaction("✅")
                await prompt_msg.add_reaction("❔")
                await prompt_msg.add_reaction("❌")
                # Store in active bets for reaction handling
                bot.active_bets = getattr(bot, 'active_bets', {})
                bot.active_bets[prompt_msg.id] = bet_id

                if target_user:
                    final_embed.add_field(name="Offered To", value=target_user.mention, inline=False)
                await prompt_msg.edit(embed=final_embed)
                
                # Clean up all the intermediate messages
                for msg in messages_to_delete:
                    try:
                        await msg.delete()
                    except:
                        pass  # Ignore any messages that were already deleted
                
            except ValueError:
                await message.channel.send("Invalid winnings amount. Bet creation cancelled.", delete_after=10)
                await prompt_msg.delete()
                
        except ValueError:
            await message.channel.send("Invalid risk amount. Bet creation cancelled.", delete_after=10)
            await prompt_msg.delete()
            
    except asyncio.TimeoutError:
        await message.channel.send("Bet creation timed out.", delete_after=10)
        await prompt_msg.delete()
    
    # Clean up messages even if there was an error
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
    Only the market creator can resolve their markets.
    """
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Check if market exists and user is the creator
        cursor.execute('''
            SELECT title, status, creator_id
            FROM markets
            WHERE market_id = ?
        ''', (market_id,))
        market = cursor.fetchone()
        
        if not market:
            await ctx.send("Market not found.")
            return
        
        title, status, creator_id = market

        # Verify the user is the creator
        if creator_id is not None:
            if str(ctx.author.id) != str(creator_id):
                await ctx.send("Only the market creator can resolve this market.")
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
        description="Here's everything I can do!",
        color=discord.Color.blue()
    )
    # Market Management
    embed.add_field(
        name="📊 Create & Manage Markets",
        value="""
**!createmarket** `<title>? <option1>, <option2>, ...`
Create a new betting market
Example: `!createmarket Will it rain tomorrow? Yes, No`
**!resolvemarket** `<market_id> <winning_outcome>`
Resolve a market (creator only)
Example: `!resolvemarket 1 Yes`
**!listmarkets**
Show all active betting markets
        """,
        inline=False
    )
    # Betting Commands
    embed.add_field(
        name="💰 Place & Accept Bets",
        value="""
**!offerbet** `<market_id> <outcome> <offer> <ask>`
Create a bet offer
- offer: amount you risk
- ask: amount you want to win
Example: `!offerbet 1 Yes 10 20`
**!acceptbet** `<bet_id>`
Accept an open bet offer
Example: `!acceptbet 1`
**!cancelbet** `<bet_id>`
Cancel your open bet offer
Example: `!cancelbet 1`
**!listbets** `[market_id]`
List all open bet offers, optionally filtered by market
Example: `!listbets` or `!listbets 1`
        """,
        inline=False
    )
    # Personal Tracking
    embed.add_field(
        name="👤 Track Your Bets",
        value="""
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
- Only market creators can resolve their markets
- Keep track of your bet IDs and market IDs!
        """,
        inline=False
    )
    
    embed.add_field(
        name="🔧",
        value="**!noslop**",
        inline=False
    )
    embed.set_footer(text="Dennis v1.0 | Boats carried by Claude")
    
    await ctx.send(embed=embed)

# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)