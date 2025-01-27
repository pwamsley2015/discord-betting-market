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

from database import BettingDatabase
from market import Market
from views import BetView, OutcomeSelect

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

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
            
            # Get all open markets
            cursor.execute('''
                SELECT market_id, discord_message_id, title, thread_id, creator_id
                FROM markets 
                WHERE status = 'open' 
                AND discord_message_id IS NOT NULL
            ''')
            open_markets = cursor.fetchall()
            
            for market_id, message_id, title, thread_id, creator_id in open_markets:
                # Get market options
                cursor.execute('''
                    SELECT outcome_name 
                    FROM market_outcomes 
                    WHERE market_id = ?
                ''', (market_id,))
                options = [row[0] for row in cursor.fetchall()]
                
                # Create Market object and store in active_markets
                market = Market(market_id, title, options, creator_id, message_id, thread_id)
                market.db = self.db
                self.active_markets[int(message_id)] = market.to_dict()
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
    
    # Create new market
    market = await Market.create(bot.db, title, options, str(ctx.author.id))
    
    # Create message and thread
    message, thread = await market.create_message(ctx.channel, ctx.author.name)
    
    # Store in active_markets
    bot.active_markets[message.id] = market.to_dict()

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
        
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    user = await bot.fetch_user(payload.user_id)
    
    if message.id in bot.active_markets:
        market_data = bot.active_markets[message.id]
        market = Market.from_dict(market_data, bot.db)
        
        if str(payload.emoji) == "<:dennis:1328277972612026388>":
            await market.handle_bet_offer_reaction(message, user, bot)
        elif str(payload.emoji) == "🇷":
            await market.handle_set_resolver(message, user, bot)
        elif str(payload.emoji) == "⏲️":
            await market.handle_set_timer(message, user, bot)
        elif str(payload.emoji) == "🆘":
            await Market.handle_react_help(message)
            
    # bets
    elif message.id in bot.active_bets:
        bet_id = bot.active_bets[message.id]
        # Get market_id from bet_offers table
        with bot.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT market_id FROM bet_offers WHERE bet_id = ?', (bet_id,))
            result = cursor.fetchone()
            if result:
                market_id = result[0]
                # Look up market data from active_markets using market_id
                market_data = None
                for m in bot.active_markets.values():
                    if m['market_id'] == market_id:
                        market_data = m
                        break
                
                if market_data:
                    market = Market.from_dict(market_data, bot.db)
                    if str(payload.emoji) == "✅":
                        await market.handle_bet_acceptance(message, user, bet_id)
                    elif str(payload.emoji) == "❔":
                        await market.handle_bet_explanation(message, user, bet_id)
                    elif str(payload.emoji) == "❌":
                        await market.handle_bet_cancellation(message, user, bet_id)
                    elif str(payload.emoji) == "🆘":
                        await market.handle_bet_react_help(message)

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

    results = []
    for market_id, title, outcomes in markets:
        results.append(f"{title} [{market_id}]\n")

    # Split results into chunks of 5
    for i in range(0, len(results), 5):
        chunk = results[i:i+5]
        final_result = ''.join(chunk)
        await ctx.send(final_result)

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

@bot.command(name='rm')
async def remove_markets(ctx, *market_ids: str):
    """Remove one or more markets from the database"""
    
    # Check if user is bot owner or has admin permissions
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("Sorry, only administrators can remove markets.")
        return
        
    if not market_ids:
        await ctx.send("Please provide at least one market ID to remove.")
        return
    
    # Parse market IDs and validate they're numbers
    try:
        ids_to_remove = [int(market_id.strip()) for market_id in market_ids]
    except ValueError:
        await ctx.send("Invalid market ID format. Please provide numeric IDs.")
        return
    
    with bot.db.get_connection() as conn:
        cursor = conn.cursor()
        
        try:
            # Start transaction
            cursor.execute('BEGIN TRANSACTION')
            
            # Delete associated bet offers first (due to foreign key constraints)
            cursor.execute('''
                DELETE FROM bet_offers 
                WHERE market_id IN ({})
            '''.format(','.join('?' * len(ids_to_remove))), ids_to_remove)
            
            # Delete market outcomes
            cursor.execute('''
                DELETE FROM market_outcomes 
                WHERE market_id IN ({})
            '''.format(','.join('?' * len(ids_to_remove))), ids_to_remove)
            
            # Delete markets
            cursor.execute('''
                DELETE FROM markets 
                WHERE market_id IN ({})
            '''.format(','.join('?' * len(ids_to_remove))), ids_to_remove)
            
            deleted_count = cursor.rowcount
            
            # Commit transaction
            conn.commit()
            
            # Remove from active_markets if present
            for market_data in list(bot.active_markets.values()):
                if market_data['market_id'] in ids_to_remove:
                    message_id = market_data.get('message_id')
                    if message_id:
                        bot.active_markets.pop(int(message_id), None)
            
            await ctx.send(f"Successfully removed {deleted_count} markets.")
            
        except Exception as e:
            conn.rollback()
            await ctx.send(f"Error removing markets: {str(e)}")
            
# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)
