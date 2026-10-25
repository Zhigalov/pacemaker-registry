#!/usr/bin/env python3
"""Sequential, warm HTTP latency measurements; no request/response bodies saved."""
import argparse
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROUTES = ("/ready", "/", "/?event_id=1", "/?min_events=2", "/events", "/events/1", "/results/1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("output", type=Path)
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    report = {"base_url": args.base_url, "captured_at": datetime.now(timezone.utc).isoformat(),
              "samples": args.samples, "warmups_per_route": 1, "routes": {}}
    with httpx.Client(base_url=args.base_url.rstrip('/'), timeout=20) as client:
        for route in ROUTES:
            client.get(route).raise_for_status()
            samples, statuses, sizes = [], [], []
            for _ in range(args.samples):
                start = time.perf_counter()
                response = client.get(route)
                samples.append(round((time.perf_counter() - start) * 1000, 2))
                statuses.append(response.status_code)
                sizes.append(len(response.content))
                response.raise_for_status()
            median = round(statistics.median(samples), 2)
            p95 = sorted(samples)[math.ceil(len(samples) * .95) - 1]
            report['routes'][route] = {"median_ms": median, "p95_ms": p95,
                                      "samples_ms": samples, "statuses": statuses, "bytes": sizes}
            print(route, median, p95, sorted(set(statuses)), flush=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == "__main__":
    main()
