import discord
import datetime
import asyncio

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
            await message.channel.send(f"{resolver.mention} has been set as the resolver for this market.")
            
            await response.delete()
            await prompt_msg.delete()  # Move this here!
            
        except asyncio.TimeoutError:
            await message.channel.send("Timed out waiting for resolver selection.")
            await prompt_msg.delete()  # And here for timeout case

    async def handle_set_timer(self, message, user, bot):
        """Handle ⏲️ reaction to set market timer"""
        if str(user.id) != str(self.creator_id):
            await message.channel.send("Only the market creator can set the timer.")
            return
        else:
            await message.channel.send("Timer feature is temporarily disasbled.")

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
