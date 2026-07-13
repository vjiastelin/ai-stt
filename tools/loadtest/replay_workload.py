#!/usr/bin/env python3
"""Replay one day of 3cx call recordings against ai_service, time-compressed.

Reads the S3 object listing for a date prefix (each object's LastModified is
treated as that call's arrival time), maps the full 24h day onto a short window
(default 5 min) preserving the relative arrival distribution, and fires
POST /requestTranscription at each scaled arrival time.

Compression factor = 86400 / duration  (default 288x for a 5-minute replay).

    # 1. start the BPM mock (returns 200)      -> tools/loadtest/bpm_mock.py
    # 2. run ai_service with BPM_CALLBACK_URL pointing at the mock + external whisper
    # 3. replay:
    python3 tools/loadtest/replay_workload.py \
        --target http://localhost:8080 \
        --prefix 3cx/2026.07.06/ \
        --duration 300

    # inspect the schedule without sending anything:
    python3 tools/loadtest/replay_workload.py --prefix 3cx/2026.07.06/ --dry-run

Dependencies: httpx (already a project dep). Listing uses stdlib urllib.
"""
import argparse
import asyncio
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter

import httpx

NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def fetch_listing(endpoint: str, bucket: str, prefix: str) -> list[dict]:
    """Paginate the public S3 v2 listing and return [{key, last_modified, size}]."""
    base = f"{endpoint.rstrip('/')}/{bucket}/"
    items: list[dict] = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        url = base + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=60) as resp:
            root = ET.fromstring(resp.read())
        for c in root.findall(f"{NS}Contents"):
            items.append(
                {
                    "key": c.findtext(f"{NS}Key"),
                    "last_modified": c.findtext(f"{NS}LastModified"),
                    "size": int(c.findtext(f"{NS}Size") or "0"),
                }
            )
        if root.findtext(f"{NS}IsTruncated") != "true":
            break
        token = root.findtext(f"{NS}NextContinuationToken")
        if not token:
            break
    return items


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)


def day_start_from_prefix(prefix: str, fallback: dt.datetime) -> dt.datetime:
    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", prefix)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return dt.datetime(y, mo, d, tzinfo=dt.timezone.utc)
    return fallback.replace(hour=0, minute=0, second=0, microsecond=0)


def build_schedule(items: list[dict], prefix: str, duration: float, bucket: str):
    """Return sorted list of (fire_at_seconds, call_record_url, key, size)."""
    parsed = []
    for it in items:
        if not it["key"].lower().endswith(".mp3"):
            continue
        parsed.append((parse_ts(it["last_modified"]), it["key"], it["size"]))
    parsed.sort(key=lambda x: x[0])
    if not parsed:
        return [], None

    day_start = day_start_from_prefix(prefix, parsed[0][0])
    scale = duration / 86400.0
    schedule = []
    for ts, key, size in parsed:
        offset = (ts - day_start).total_seconds()
        fire_at = max(0.0, offset * scale)
        url = f"s3://{bucket}/{key}"
        schedule.append((fire_at, url, key, size))
    schedule.sort(key=lambda x: x[0])
    return schedule, day_start


def print_summary(schedule, day_start, duration):
    n = len(schedule)
    total_mb = sum(s[3] for s in schedule) / 1e6
    print(f"objects (.mp3): {n}")
    print(f"day start (UTC): {day_start.isoformat()}")
    print(f"compression: {86400 / duration:.0f}x  (24h -> {duration:.0f}s)")
    print(f"payload data referenced: {total_mb:.1f} MB")
    print(f"replay window: {schedule[0][0]:.1f}s .. {schedule[-1][0]:.1f}s")
    # per-scaled-second-bucket histogram (split window into ~20 bins)
    bins = 20
    binw = duration / bins
    hist = Counter(min(bins - 1, int(s[0] / binw)) for s in schedule)
    mx = max(hist.values()) if hist else 1
    print(f"\narrival distribution ({bins} bins of {binw:.1f}s):")
    for b in range(bins):
        c = hist.get(b, 0)
        lo = b * binw
        hi_utc = day_start + dt.timedelta(seconds=lo * (86400 / duration))
        bar = "#" * round(40 * c / mx)
        print(f"  t+{lo:5.1f}s  ~{hi_utc.strftime('%H:%M')}UTC  {c:4d}  {bar}")
    peak_rate = mx / binw
    print(f"\npeak arrival rate: ~{peak_rate:.1f} req/s")


async def replay(schedule, target, run_id, concurrency, timeout):
    endpoint = target.rstrip("/") + "/requestTranscription"
    limits = httpx.Limits(max_connections=concurrency)
    sem = asyncio.Semaphore(concurrency)
    results = Counter()
    sent = 0

    async with httpx.AsyncClient(timeout=timeout, limits=limits, verify=False) as client:

        async def fire(url, key):
            nonlocal sent
            call_record_id = f"{run_id}-{key.rsplit('/', 1)[-1][:-4]}"
            payload = {"CallRecordId": call_record_id, "CallRecordUrl": url}
            async with sem:
                try:
                    r = await client.post(endpoint, json=payload)
                    results[r.status_code] += 1
                    if r.status_code >= 400:
                        print(f"  ! {r.status_code} {call_record_id}: {r.text[:120]}", flush=True)
                except httpx.HTTPError as exc:
                    results["error"] += 1
                    print(f"  ! send error {call_record_id}: {exc}", flush=True)
            sent += 1

        start = asyncio.get_event_loop().time()
        tasks = []
        for i, (fire_at, url, key, _size) in enumerate(schedule):
            now = asyncio.get_event_loop().time() - start
            delay = fire_at - now
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(fire(url, key)))
            if (i + 1) % 50 == 0:
                elapsed = asyncio.get_event_loop().time() - start
                print(f"  t+{elapsed:5.1f}s  scheduled {i + 1}/{len(schedule)}", flush=True)
        await asyncio.gather(*tasks)

    print(f"\ndone: {sent} requests sent")
    for code, cnt in sorted(results.items(), key=lambda x: str(x[0])):
        print(f"  {code}: {cnt}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="http://localhost:8080", help="ai_service base URL")
    ap.add_argument("--endpoint", default="https://s3.yandexcloud.net", help="S3 endpoint for listing")
    ap.add_argument("--bucket", default="3cx-recordings")
    ap.add_argument("--prefix", default="3cx/2026.07.06/", help="date prefix to replay")
    ap.add_argument("--duration", type=float, default=300.0, help="replay window seconds (default 300 = 5 min)")
    ap.add_argument("--concurrency", type=int, default=32, help="max concurrent in-flight POSTs")
    ap.add_argument("--timeout", type=float, default=30.0, help="per-request HTTP timeout")
    ap.add_argument("--run-id", default=None, help="CallRecordId prefix (default: prefix-based tag)")
    ap.add_argument("--from-json", default=None, help="load listing from a JSON file instead of S3")
    ap.add_argument("--dry-run", action="store_true", help="print schedule, send nothing")
    args = ap.parse_args()

    if args.from_json:
        items = json.load(open(args.from_json))
    else:
        print(f"fetching listing {args.bucket}/{args.prefix} ...", flush=True)
        items = fetch_listing(args.endpoint, args.bucket, args.prefix)

    schedule, day_start = build_schedule(items, args.prefix, args.duration, args.bucket)
    if not schedule:
        print("no .mp3 objects found", file=sys.stderr)
        sys.exit(1)

    print_summary(schedule, day_start, args.duration)

    if args.dry_run:
        print("\n[dry-run] no requests sent")
        return

    run_id = args.run_id or ("run-" + re.sub(r"\W+", "", args.prefix)[-12:])
    print(f"\nreplaying against {args.target}  run_id={run_id}\n")
    asyncio.run(replay(schedule, args.target, run_id, args.concurrency, args.timeout))


if __name__ == "__main__":
    main()
