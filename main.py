import os
import sqlite3
from decimal import Decimal
from dotenv import load_dotenv
import discord
from discord.ext import commands

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

class BettingBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        self.db = BettingDatabase()

    async def setup_hook(self):
        print(f'Setting up {self.user} (ID: {self.user.id})')

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
        
        conn.commit()
    
    embed = discord.Embed(
        title="New Betting Market Created!",
        description=title,
        color=discord.Color.green()
    )
    embed.add_field(name="Market ID", value=market_id, inline=False)
    embed.add_field(name="Options", value="\n".join(options), inline=False)
    embed.set_footer(text=f"Created by {ctx.author.name}")
    
    await ctx.send(embed=embed)

@bot.command(name='offerbet')
async def offer_bet(ctx, market_id: int, outcome: str, offer: float, ask: float, target_user: discord.Member = None):
    """
    Offer a bet in a market
    Usage: !offerbet <market_id> <outcome> <offer_amount> <ask_amount> [@user]
    """
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Verify market exists and is open
        cursor.execute('SELECT status, title, description FROM markets WHERE market_id = ?', (market_id,))
        market = cursor.fetchone()
        
        if not market:
            await ctx.send("Market not found.")
            return

        status, title, description = market
        
        if status != 'open':
            await ctx.send("Market is not open for betting.")
            return
        
        # Verify outcome exists
        cursor.execute('''
            SELECT 1 FROM market_outcomes 
            WHERE market_id = ? AND outcome_name = ?
        ''', (market_id, outcome))
        
        if not cursor.fetchone():
            await ctx.send("Invalid outcome for this market.")
            return
        
        # Create bet offer
        cursor.execute('''
            INSERT INTO bet_offers 
            (market_id, bettor_id, outcome, offer_amount, ask_amount, target_user_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (market_id, str(ctx.author.id), outcome, offer, ask, 
              str(target_user.id) if target_user else None))
        
        bet_id = cursor.lastrowid
        conn.commit()
    
    embed = discord.Embed(
        title="Bet Offered!",
        description=f"**Market:** {title}\n",
        color=discord.Color.blue()
    )
    embed.add_field(name="Bet ID", value=bet_id, inline=False)
    embed.add_field(name="Market ID", value=market_id, inline=False)
    embed.add_field(name="Outcome", value=outcome, inline=False)
    embed.add_field(name="You Risk", value=f"${offer}", inline=True)
    embed.add_field(name="To Win", value=f"${ask}", inline=True)
    
    if target_user:
        embed.add_field(name="Offered To", value=target_user.mention, inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='acceptbet')
async def accept_bet(ctx, bet_id: int):
   """
   Accept an open bet offer
   Usage: !acceptbet <bet_id>
   """
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
           await ctx.send("Bet offer not found.")
           return
       
       market_id, bettor_id, bet_status, outcome, offer_amount, ask_amount, market_status, target_user_id, title, description = bet
       
       # Validation checks
       if str(ctx.author.id) == bettor_id:
           await ctx.send("You cannot accept your own bet offer.")
           return
       
       if bet_status != 'open':
           await ctx.send("This bet offer is no longer available.")
           return
       
       if market_status != 'open':
           await ctx.send("This market is no longer open for betting.")
           return

       # Check if bet was targeted at a specific user
       if target_user_id and str(ctx.author.id) != target_user_id:
           await ctx.send("This bet was offered to a specific user only.")
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
       ''', (bet_id, str(ctx.author.id)))
       
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
       embed.add_field(name="Acceptor", value=ctx.author.name, inline=True)
       embed.add_field(name=f"{bettor_name} Risks", value=f"${offer_amount}", inline=True)
       embed.add_field(name=f"{ctx.author.name} Risks", value=f"${ask_amount}", inline=True)
       
       await ctx.send(embed=embed)

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
**!createmarket** `<question>? <option1>, <option2>, ...`
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
• When offering a bet, the 'offer' is what you risk and the 'ask' is what you want to win
• Betting amounts are in dollars ($)
• You can't accept your own bets
• Only market creators can resolve their markets
• Keep track of your bet IDs and market IDs!
        """,
        inline=False
    )

    embed.set_footer(text="Dennis v1.0 | Boats carried by Claude")
    
    await ctx.send(embed=embed)


# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)