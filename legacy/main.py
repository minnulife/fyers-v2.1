import logging

from auth import get_fyers
from config import TRADING_MODE, validate_runtime_config
from engine import Engine


def main() -> None:
    validate_runtime_config()
    logging.info("Starting FYERS platform engine in %s mode", TRADING_MODE)
    fyers = get_fyers()
    Engine(fyers).run()


if __name__ == "__main__":
    main()
