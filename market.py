import discord
import datetime
import asyncio
import re
import pytz

class Market:
    def __init__(self, id, title, options, creator_id, message_id=None, 
                 thread_id=None, resolver_id=None, close_time=None, status='open'):
        self.id = id
        self.title = title
        self.options = options
        self.creator_id = creator_id
        self.message_id = message_id
        self.thread_id = thread_id
        self.resolver_id = resolver_id
        self.close_time = close_time
        self.status = status
        self.db = None  # We'll need to set this after initialization

    @classmethod
    async def create(cls, db, title, options, creator_id):
        """Create a new market in the database and return a Market object"""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO markets (title, description, creator_id) VALUES (?, ?, ?)',
                (title, title, str(creator_id))
            )
            market_id = cursor.lastrowid
            
            for option in options:
                cursor.execute(
                    'INSERT INTO market_outcomes (market_id, outcome_name) VALUES (?, ?)',
                    (market_id, option)
                )
            conn.commit()
            
        market = cls(market_id, title, options, creator_id)
        market.db = db
        return market

    async def create_message(self, channel, creator_name):
        """Create and send the market message, create thread, and add reactions"""
        embed = discord.Embed(title=self.title, color=discord.Color.green())
        embed.add_field(name="Options", value="\n".join(self.options), inline=False)
        embed.add_field(name="help: ", value="🆘", inline=False)
        embed.set_footer(text=f"Created by {creator_name}")
        
        message = await channel.send(embed=embed)
        self.message_id = message.id
        
        await message.add_reaction("<:dennis:1328277972612026388>")
        await message.add_reaction("🇷")
        await message.add_reaction("⏲️")
        await message.add_reaction("🆘")
        
        # Create thread
        thread = await channel.create_thread(
            name=f"Market {self.id}: {self.title[:50]}{'...' if len(self.title) > 50 else ''}",
            message=message,
            type=discord.ChannelType.public_thread
        )
        self.thread_id = thread.id
        
        # Welcome message in thread
        await thread.send("https://tenor.com/view/memeplex-sol-remilia-remilio-milady-gif-17952083022135309581")
        
        # Update database
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE markets SET discord_message_id = ?, thread_id = ? WHERE market_id = ?',
                (str(self.message_id), str(self.thread_id), self.id)
            )
            conn.commit()

        return message, thread

    async def handle_set_resolver(self, message, user, bot):
        """Handle 🇷 reaction to set market resolver"""
        if str(user.id) != str(self.creator_id):
            await message.channel.send("Only the market creator can set the resolver.")
            return

        # Get the thread from the stored thread_id
        thread = message.guild.get_thread(int(self.thread_id)) if self.thread_id else None
        if not thread:
            await message.channel.send("Error: Could not find the market thread.")
            return

        prompt_msg = await message.channel.send("Please mention the user you want to set as resolver.")
        
        try:
            def check(m):
                return m.author.id == user.id and len(m.mentions) > 0 and m.channel.id == message.channel.id
                
            response = await bot.wait_for('message', check=check, timeout=30.0)
            resolver = response.mentions[0]
            
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE markets
                    SET resolver_id = ?
                    WHERE market_id = ?
                ''', (str(resolver.id), self.id))
                conn.commit()
            
            self.resolver_id = resolver.id
            # Send confirmation to the thread instead of the main channel
            await thread.send(f"{resolver.mention} has been set as the resolver for this market.")
            
            await response.delete()
            await prompt_msg.delete()
            
        except asyncio.TimeoutError:
            await message.channel.send("Timed out waiting for resolver selection.")
            await prompt_msg.delete()

    async def handle_set_timer(self, message, user, bot):
        """Handle ⏲️ reaction to set market timer"""
        if str(user.id) != str(self.creator_id):
            await message.channel.send("Only the market creator can set the timer.")
            return

        # Get the thread
        thread = message.guild.get_thread(int(self.thread_id)) if self.thread_id else None
        if not thread:
            await message.channel.send("Error: Could not find the market thread.")
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
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE markets
                    SET close_time = ?
                    WHERE market_id = ?
                ''', (deadline.isoformat(), self.id))
                conn.commit()

            # Delete user's response and prompt
            await response.delete()
            await prompt_msg.delete()
            
            # Schedule the countdown job
            bot.loop.create_task(self.handle_market_countdown(thread, deadline, bot))
            
            # Convert deadline to Pacific time for display
            pacific = pytz.timezone('America/Los_Angeles')
            deadline_pacific = deadline.astimezone(pacific)
            await thread.send(f"⏲️ Market will close at {deadline_pacific.strftime('%Y-%m-%d %I:%M %p')} PT")
                
        except asyncio.TimeoutError:
            await prompt_msg.delete()
            timeout_msg = await message.channel.send("Timed out waiting for time input.")
            await asyncio.sleep(5)
            await timeout_msg.delete()

    async def handle_market_countdown(self, thread, deadline, bot):
        """Handle countdown and notifications for market closing"""
        while True:
            now = datetime.datetime.now()
            if now >= deadline:
                # Close the market
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE markets
                        SET status = 'closed'
                        WHERE market_id = ?
                    ''', (self.id,))
                    conn.commit()
                
                await thread.send(f"🔒 This market is now closed for betting!")
                break
            
            # Send reminder at 1 hour remaining
            time_remaining = deadline - now
            if datetime.timedelta(hours=1) <= time_remaining <= datetime.timedelta(hours=1, minutes=1):
                await thread.send(f"⚠️ This market closes in 1 hour!")
            
            await asyncio.sleep(60)  # Check every minute

    @staticmethod
    async def handle_react_help(message):
        """Handle 🆘 reaction on market"""
        help_text = (
            "<:dennis:1328277972612026388> Offer a bet\n" 
            "🇷 Set the resolver (creator by default)\n"
            "⏲️ Set a timer to close the market\n"
        )
        help_msg = await message.channel.send(help_text)
        await asyncio.sleep(30)
        await help_msg.delete()

    async def update_stats(self):
        """Update market stats in the embed"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get count and volume of open bets
            cursor.execute('''
                SELECT COUNT(*), SUM(offer_amount)
                FROM bet_offers 
                WHERE market_id = ? AND status = 'open'
            ''', (self.id,))
            open_count, open_volume = cursor.fetchone()
            
            # Get count and volume of accepted bets
            cursor.execute('''
                SELECT COUNT(*), SUM(bo.offer_amount)
                FROM bet_offers bo
                JOIN accepted_bets ab ON bo.bet_id = ab.bet_id
                WHERE bo.market_id = ? AND ab.status = 'active'
            ''', (self.id,))
            accepted_count, accepted_volume = cursor.fetchone()
            
            # Handle None values from SUM
            open_volume = open_volume or 0
            accepted_volume = accepted_volume or 0
            total_volume = open_volume + accepted_volume

    def to_dict(self):
        """Convert to dict for bot.active_markets"""
        return {
            'market_id': self.id,
            'options': self.options,
            'title': self.title,
            'thread_id': self.thread_id,
            'creator_id': self.creator_id
        }

    @classmethod
    def from_dict(cls, data, db):
        """Create Market instance from bot.active_markets data"""
        market = cls(
            id=data['market_id'],
            title=data['title'],
            options=data['options'],
            creator_id=data['creator_id'],
            thread_id=data.get('thread_id')
        )
        market.db = db
        return market
