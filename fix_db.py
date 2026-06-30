import sqlite3
conn = sqlite3.connect('data/vaultcut.db')
cursor = conn.cursor()

# Add missing columns to monitored_channels
try:
    cursor.execute('ALTER TABLE monitored_channels ADD COLUMN source_type TEXT DEFAULT ''manual''')
    print('? Added column: source_type')
except:
    print('  Column source_type already exists')

try:
    cursor.execute('ALTER TABLE monitored_channels ADD COLUMN active INTEGER DEFAULT 1')
    print('? Added column: active')
except:
    print('  Column active already exists')

try:
    cursor.execute('ALTER TABLE monitored_channels ADD COLUMN category TEXT DEFAULT ''general''')
    print('? Added column: category')
except:
    print('  Column category already exists')

conn.commit()
conn.close()
print('\nDatabase updated. Try adding channels again.')
