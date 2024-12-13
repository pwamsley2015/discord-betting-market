import sqlite3
import sys

DB_PATH = "betting_market.db"  

def migrate_add_target_user():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                ALTER TABLE bet_offers 
                ADD COLUMN target_user_id TEXT;
            ''')
            conn.commit()
            print("Migration successful: Added target_user_id column to bet_offers table")
    except sqlite3.Error as e:
        print(f"Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    migrate_add_target_user()