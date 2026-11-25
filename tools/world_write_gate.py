import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pai.world_write_gate import *  # noqa: F401,F403
if __name__ == "__main__":
    from pai.world_write_gate import main
    raise SystemExit(main())
