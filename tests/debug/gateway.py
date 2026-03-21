import sys


from nanobot.cli.commands import app


if __name__ == "__main__":
    sys.argv = ["nanobot", "gateway"]
    app()
