"""
Load test: N simulated workers texting at once against a running server.
  IMPACT_DB=data/load.db uvicorn app.main:app --port 8765     (separate terminal)
  python -m eval.load_test http://127.0.0.1:8765 500 25
"""
import asyncio
import random
import statistics
import sys
import time

import httpx

NOTES = ["fed 20 kids at sherman park", "handed out 30 coats w repairers of the breach", "connected a mom to 211 for rent help",
         "basketball w 12 teens", "door knocked 40 houses in 53206", "ME", "HELP"]


async def main(url: str, total: int, concurrency: int) -> None:
    sem, lat, errors = asyncio.Semaphore(concurrency), [], 0
    async with httpx.AsyncClient(timeout=30) as c:
        async def one(i: int):
            nonlocal errors
            async with sem:
                t = time.perf_counter()
                try:
                    r = await c.post(url + "/sms", data={"From": f"+1414555{i % 50:04d}", "Body": random.choice(NOTES)})
                    errors += r.status_code != 200
                except httpx.HTTPError:
                    errors += 1
                lat.append((time.perf_counter() - t) * 1000)
        start = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(total)))
        wall = time.perf_counter() - start
    lat.sort()
    print(f"{total} texts, {concurrency} at once: {total / wall:.0f} texts/sec · errors {errors} · "
          f"median {statistics.median(lat):.0f} ms · p95 {lat[int(len(lat) * .95) - 1]:.0f} ms · max {lat[-1]:.0f} ms")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
