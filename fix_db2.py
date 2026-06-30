import sqlite3
conn = sqlite3.connect('data/vaultcut.db')
cursor = conn.cursor()

columns_to_add = [
    ('added_by', 'TEXT DEFAULT "manual"'),
    ('last_video_id', 'TEXT'),
    ('last_video_date', 'TEXT'),
    ('check_frequency_hours', 'INTEGER DEFAULT 6'),
    ('min_views', 'INTEGER DEFAULT 0'),
    ('max_duration_minutes', 'INTEGER DEFAULT 60'),
]

for col_name, col_def in columns_to_add:
    try:
        cursor.execute(f'ALTER TABLE monitored_channels ADD COLUMN {col_name} {col_def}')
        print(f'? Added column: {col_name}')
    except:
        print(f'  Exists (skip): {col_name}')

conn.commit()
conn.close()
print('\nDone. Try adding channels again.')
