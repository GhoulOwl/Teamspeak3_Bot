"""Entry point: python -m bot"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from bot.app import BotApplication
from bot.config import load_config


def main() -> None:
    # Load .env file from project root
    load_dotenv(Path(__file__).parent.parent / ".env")
    config = load_config()
    app = BotApplication(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
