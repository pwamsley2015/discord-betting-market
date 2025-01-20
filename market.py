import discord
import datetime
import asyncio
import re
import pytz
from views import BetView, OutcomeSelect

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

    async def handle_bet_offer_reaction(self, message, user, bot):
        """Handle the dennis emoji reaction to create a bet offer"""
        messages_to_delete = []
        
        # Get the thread
        thread = message.guild.get_thread(int(self.thread_id)) if self.thread_id else None
        if not thread:
            await message.channel.send("Error: Could not find market thread.", delete_after=10)
            return

        # Verify market is open
        if self.status != 'open':
            await message.channel.send("This market is not open for betting.", delete_after=10)
            return

        # Initialize bet creation flow in main channel
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
       
        view = BetView(self.to_dict(), user)  # Convert market to dict for compatibility
        prompt_msg = await message.channel.send(embed=bet_embed, view=view)
        messages_to_delete.append(prompt_msg)
       
        await view.wait()
       
        if view.selected_option is None:
            await message.channel.send("Bet creation timed out.", delete_after=10)
            await self._cleanup_messages(messages_to_delete)
            return
           
        selected_index = int(view.selected_option)
        selected_option = self.options[selected_index]  # Use self.options instead of dict access

        # Target user prompt - still in main channel
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

        try:
            target_msg = await self._get_user_response(message, user, bot)
            messages_to_delete.append(target_msg)
            target_user = None
            if target_msg.content.lower() != 'skip' and len(target_msg.mentions) > 0:
                target_user = target_msg.mentions[0]
           
            # Amount prompt
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
           
            amount_msg = await self._get_user_response(message, user, bot)
            messages_to_delete.append(amount_msg)
            offer_amount = float(amount_msg.content)
           
            # Winnings prompt
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
           
            winnings_msg = await self._get_user_response(message, user, bot)
            messages_to_delete.append(winnings_msg)
            ask_amount = float(winnings_msg.content)
           
            # Create bet in database and thread
            bet_id = await self._create_bet(
                user=user,
                selected_option=selected_option,
                offer_amount=offer_amount,
                ask_amount=ask_amount,
                target_user=target_user,
                thread=thread,
                bot=bot
            )

            if bet_id:
                # Add to active bets dict
                bot.active_bets = getattr(bot, 'active_bets', {})
                bot.active_bets[bet_id] = bet_id
                
                # Update market stats if needed
                await self._update_market_stats(message)

        except ValueError as e:
            await message.channel.send(f"Invalid input: {str(e)}. Bet creation cancelled.", delete_after=10)
        except asyncio.TimeoutError:
            await message.channel.send("Bet creation timed out.", delete_after=10)
        finally:
            await self._cleanup_messages(messages_to_delete)

    async def handle_bet_acceptance(self, message, user, bet_id):
        """Handle ✅ reaction to accept a bet"""
        print(f"Starting bet acceptance for bet_id {bet_id}")
        
        # Get bet info from database
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            print(f"Fetching bet info from database...")
            cursor.execute('''
                SELECT b.*, m.status as market_status, m.thread_id
                FROM bet_offers b
                JOIN markets m ON b.market_id = m.market_id
                WHERE b.bet_id = ?
            ''', (bet_id,))
            bet = cursor.fetchone()
            print(f"Fetched bet: {bet}")

            if not bet:
                print("Bet not found in database")
                await message.channel.send("Error: Bet not found.", delete_after=10)
                return

            # Convert to dict for easier access
            bet = dict(bet)
            print(f"Thread ID from bet: {bet.get('thread_id')}")
            
            # Get thread
            thread = message.guild.get_thread(int(bet['thread_id'])) if bet['thread_id'] else None
            print(f"Retrieved thread object: {thread}")
            if not thread:
                await message.channel.send("Error: Could not find market thread.", delete_after=10)
                return

            try:
                print(f"Validating bet acceptance...")
                print(f"Bet status: {bet['status']}")
                print(f"Market status: {bet['market_status']}")
                print(f"Bettor ID: {bet['bettor_id']}")
                print(f"Target user ID: {bet.get('target_user_id')}")
                print(f"User trying to accept: {user.id}")

                # Validate bet can be accepted
                if bet['status'] != 'open':
                    await thread.send(f"{user.mention} This bet is no longer open for acceptance.")
                    return

                if bet['market_status'] != 'open':
                    await thread.send(f"{user.mention} This market is closed.")
                    return

                if str(user.id) == bet['bettor_id']:
                    await thread.send(f"{user.mention} You cannot accept your own bet.")
                    return

                if bet['target_user_id'] and str(user.id) != bet['target_user_id']:
                    await thread.send(f"{user.mention} This bet was offered to a specific user.")
                    return

                print("All validations passed, proceeding with acceptance...")

                # Create accepted bet record and update bet status
                cursor.execute('''
                    INSERT INTO accepted_bets 
                    (bet_id, acceptor_id)
                    VALUES (?, ?)
                ''', (bet_id, str(user.id)))
                print("Inserted accepted_bets record")
                
                cursor.execute('''
                    UPDATE bet_offers
                    SET status = 'accepted'
                    WHERE bet_id = ?
                ''', (bet_id,))
                print("Updated bet_offers status")
                
                conn.commit()
                print("Committed database changes")

                print("Updating embed...")
                embed = message.embeds[0]
                embed.color = discord.Color.gold()
                embed.add_field(
                    name="Status", 
                    value=f"Accepted by {user.mention}",
                    inline=False
                )
                await message.edit(embed=embed)
                print("Updated embed")

                print("Clearing reactions...")
                for reaction in ["✅", "❌"]:
                    await message.clear_reaction(reaction)
                print("Cleared reactions")

                await thread.send(f"🤝 Bet {bet_id} has been accepted by {user.mention}!")
                print("Sent confirmation message")

            except Exception as e:
                print(f"Error during bet acceptance: {str(e)}")
                await thread.send(f"Error accepting bet: {str(e)}")
                conn.rollback()
                raise  # Re-raise to see full traceback in logs
    async def _get_user_response(self, message, user, bot, timeout=60.0):
        """Helper method to get a response from user in the main channel"""
        def check(m):
            return m.author == user and m.channel == message.channel
        return await bot.wait_for('message', check=check, timeout=timeout)

    async def _cleanup_messages(self, messages):
        """Helper method to clean up prompt messages"""
        for msg in messages:
            try:
                await msg.delete()
            except:
                pass

    async def _create_bet(self, user, selected_option, offer_amount, ask_amount, target_user, thread, bot):
        """Helper method to create bet in database and thread"""
        # Create final bet message in thread
        final_embed = discord.Embed(
            title=f"{user} offering {selected_option} on: {self.title}",
            color=discord.Color.green()
        )
        final_embed.add_field(name="Risking", value=f"${offer_amount}", inline=True)
        final_embed.add_field(name="To Win", value=f"${ask_amount}", inline=True)
        final_embed.add_field(name="Bet ID", value="Pending...", inline=True)
        final_embed.add_field(name="Market ID:", value=self.id, inline=True)
        final_embed.add_field(name="Help: 🆘", value="", inline=False)

        # Send final embed to thread
        bet_msg = await thread.send(embed=final_embed)
        
        # Insert into database
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO bet_offers 
                (market_id, bettor_id, outcome, offer_amount, ask_amount, target_user_id, discord_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (self.id, str(user.id), selected_option, 
                  offer_amount, ask_amount, str(target_user.id) if target_user else None, 
                  str(bet_msg.id)))
            bet_id = cursor.lastrowid
            conn.commit()

        # Update embed with bet ID and add reactions
        final_embed.set_field_at(2, name="Bet ID", value=bet_id, inline=True)
        if target_user:
            final_embed.add_field(name="Offered To", value=target_user.mention, inline=False)
        await bet_msg.edit(embed=final_embed)

        # Add reactions
        for reaction in ["✅", "❌", "❔", "📉", "🤏", "<:monkaS:814271443327123466>", "🆘"]:
            await bet_msg.add_reaction(reaction)

        return bet_id

    async def _update_market_stats(self, message):
        """Helper method to update market statistics"""
        # TODO: Implement market stats update logic
        pass

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
