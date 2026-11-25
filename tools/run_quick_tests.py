"""Quick compatibility shell; the only tier list is gates.json."""
from tools.gates import main
raise SystemExit(main(["--tier", "quick"]))
