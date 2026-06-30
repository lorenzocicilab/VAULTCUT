import sqlite3
conn = sqlite3.connect('data/vaultcut.db')
cursor = conn.cursor()

# Get all existing columns
cursor.execute("PRAGMA table_info(monitored_channels)")
existing = [row[1] for row in cursor.fetchall()]
print("Existing columns:", existing)

columns_to_add = [
    ('date_added', 'TEXT'),
    ('notes', 'TEXT'),
    ('tags', 'TEXT'),
    ('language', 'TEXT DEFAULT "en"'),
    ('region', 'TEXT DEFAULT "US"'),
    ('auto_discovered', 'INTEGER DEFAULT 0'),
    ('discovery_keyword', 'TEXT'),
    ('total_clips_made', 'INTEGER DEFAULT 0'),
    ('last_error', 'TEXT'),
    ('error_count', 'INTEGER DEFAULT 0'),
]

for col_name, col_def in columns_to_add:
    if col_name not in existing:
        try:
            cursor.execute(f'ALTER TABLE monitored_channels ADD COLUMN {col_name} {col_def}')
            print(f'? Added column: {col_name}')
        except Exception as e:
            print(f'? Error adding {col_name}: {e}')
    else:
        print(f'  Exists (skip): {col_name}')

conn.commit()
conn.close()
print('\nDone.')
