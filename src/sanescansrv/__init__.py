from sanescansrv.server import __author__, __title__, __version__
from sanescansrv.server import sane_run as run

print(f"{__title__} v{__version__}  Copyright (C) 2023  {__author__}\n")

if __name__ == "__main__":
    run()
