import sys


from nekoclaw.cli.commands import app


if __name__ == "__main__":
    sys.argv = ["nekoclaw", "gateway"]
    app()
