import sqlite3

class BettingDatabase:
   def __init__(self, db_path='betting_market.db'):
       self.db_path = db_path
       # We can add ensure_tables_exist() back if needed
       
   def get_connection(self):
       """Get a connection to the SQLite database"""
       return sqlite3.connect(self.db_path)
