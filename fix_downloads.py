import sqlite3
conn = sqlite3.connect('data/vaultcut.db')
cursor = conn.cursor()

# Get existing columns
cursor.execute("PRAGMA table_info(downloaded_videos)")
existing = [row[1] for row in cursor.fetchall()]
print("Existing columns:", existing)

columns_to_add = [
    ('source_type', 'TEXT DEFAULT "youtube"'),
    ('deleted', 'INTEGER DEFAULT 0'),
    ('viral_score', 'REAL DEFAULT 0'),
    ('transcript_path', 'TEXT'),
    ('analysis_path', 'TEXT'),
    ('discovery_keyword', 'TEXT'),
    ('trend_match_score', 'REAL DEFAULT 0'),
    ('error_message', 'TEXT'),
]

for col_name, col_def in columns_to_add:
    if col_name not in existing:
        try:
            cursor.execute(f'ALTER TABLE downloaded_videos ADD COLUMN {col_name} {col_def}')
            print(f'? Added column: {col_name}')
        except Exception as e:
            print(f'? Error: {e}')
    else:
        print(f'  Exists (skip): {col_name}')

conn.commit()
conn.close()
print('\nDone. Run discovery engine again.')
