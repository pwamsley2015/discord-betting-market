import discord
from discord import SelectOption
from discord.ui import Select, View

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
