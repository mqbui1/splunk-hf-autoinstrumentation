from .config import Config
from .daemon import run_daemon


def main():
    config = Config()
    run_daemon(config)


if __name__ == "__main__":
    main()
