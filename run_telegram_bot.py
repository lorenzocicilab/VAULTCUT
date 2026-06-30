import os
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
os.chdir(os.path.abspath(os.path.dirname(__file__)))

from src.telegram_bot.bot import get_application

if __name__ == '__main__':
    print('Starting Telegram bot polling...')
    print('Press Ctrl+C to stop.')
    app = get_application()
    app.run_polling()
