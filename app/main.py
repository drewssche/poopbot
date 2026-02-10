import asyncio
from app.core.config import load_settings
from app.core.logging import setup_logging
from app.bot.dispatcher import run_bot


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    main()
