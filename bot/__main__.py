"""Entry point: python -m bot"""

import asyncio

from bot.app import BotApplication
from bot.config import load_config


def main() -> None:
    config = load_config()
    app = BotApplication(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
