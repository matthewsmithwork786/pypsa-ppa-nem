"""Private entry point for sizing in an independent Python interpreter."""
from __future__ import annotations

import dataclasses
import json
import pickle
import sys
import traceback
from pathlib import Path


def main() -> None:
    request, response = map(Path, sys.argv[1:])
    try:
        from ppa.scenario import Scenario
        from ppa.sizing import optimise_capacities

        # This file is created by run_sizing_subprocess, never supplied by users.
        with request.open("rb") as fh:
            ts, fields = pickle.load(fh)
        sized = optimise_capacities(ts, Scenario(**fields), solver_options={"threads": 1})
        payload = {"kind": "ok", "sized": dataclasses.asdict(sized)}
    except BaseException:
        payload = {"kind": "err", "error": traceback.format_exc()}
    response.write_text(json.dumps(payload))


if __name__ == "__main__":
    main()
