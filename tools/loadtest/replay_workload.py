#!/usr/bin/env python3
"""Peak-hour load test for ai_service: inject the busiest hour, measure the drain.

Takes one day of 3cx call recordings (S3 LastModified = arrival time), picks the
busiest hour, and replays its calls with real relative timing compressed into a
short window (default 60 s) via POST /requestTranscription. Then it answers:

  1. how long does ai_service take to resolve the backlog (drain time,
     throughput, per-job latency percentiles), and
  2. does the backlog slow down further transcriptions — probe requests are
     sent every --probe-interval seconds during the drain and their end-to-end
     latency is compared to a baseline measured on an idle queue.

ai_service's worker is single-threaded FIFO, so probes queued behind the burst
WILL be delayed; this script quantifies by how much and confirms recovery.

    # 1. start the BPM mock (returns 200)      -> tools/loadtest/bpm_mock.py
    # 2. run ai_service with BPM_CALLBACK_URL pointing at the mock + external whisper
    # 3. quick run (20 calls), then the full peak hour:
    python3 tools/loadtest/replay_workload.py --target http://localhost:8080 \
        --prefix 3cx/2026.07.06/ --limit 20
    python3 tools/loadtest/replay_workload.py --target http://localhost:8080 \
        --prefix 3cx/2026.07.06/

    # inspect the peak-hour selection without sending anything:
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
TERMINAL = ("done", "failed")


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
    # same format the S3 listing and ai_service's db._now() both use
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)


def pick_peak_hour(items: list[dict], hour_override: int | None):
    """Return (hour_start_utc, calls) where calls = [(ts, key, size)] chronological."""
    parsed = []
    for it in items:
        if not it["key"].lower().endswith(".mp3"):
            continue
        parsed.append((parse_ts(it["last_modified"]), it["key"], it["size"]))
    if not parsed:
        return None, [], Counter()
    byhour = Counter(t.replace(minute=0, second=0, microsecond=0) for t, _, _ in parsed)
    if hour_override is not None:
        candidates = [h for h in byhour if h.hour == hour_override]
        if not candidates:
            return None, [], byhour
        hour_start = max(candidates, key=lambda h: byhour[h])
    else:
        hour_start = max(byhour, key=lambda h: byhour[h])
    calls = sorted(
        (c for c in parsed if c[0].replace(minute=0, second=0, microsecond=0) == hour_start),
        key=lambda c: c[0],
    )
    return hour_start, calls, byhour


def build_schedule(calls, hour_start, window: float, bucket: str):
    """[(fire_at_seconds, s3_url, key, size)] — hour offsets scaled into the window."""
    scale = window / 3600.0
    return [
        ((ts - hour_start).total_seconds() * scale, f"s3://{bucket}/{key}", key, size)
        for ts, key, size in calls
    ]


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


def server_latency(job: dict) -> float:
    """End-to-end seconds from server-side created_at -> updated_at (at terminal state)."""
    return (parse_ts(job["updated_at"]) - parse_ts(job["created_at"])).total_seconds()


def print_dry_run(byhour, hour_start, schedule, window):
    mx = max(byhour.values())
    print("\nper-hour arrivals (UTC):")
    for h in sorted(byhour):
        c = byhour[h]
        mark = "  <-- peak" if h == hour_start else ""
        print(f"  {h.strftime('%Y-%m-%d %H:00')}  {c:4d}  {'#' * round(40 * c / mx)}{mark}")
    total_mb = sum(s[3] for s in schedule) / 1e6
    print(f"\npeak hour: {hour_start.strftime('%H:00')} UTC — {len(schedule)} calls, {total_mb:.1f} MB")
    print(f"compressed into {window:.0f}s: fire window {schedule[0][0]:.1f}s .. {schedule[-1][0]:.1f}s")
    rate = Counter(int(s[0] / max(window / 12, 1e-9)) for s in schedule)
    peak_rate = max(rate.values()) / max(window / 12, 1e-9)
    print(f"peak arrival rate: ~{peak_rate:.1f} req/s")


class Runner:
    """Drives one load test against ai_service and collects measurements."""

    def __init__(self, client: httpx.AsyncClient, target: str, run_id: str, args):
        self.client = client
        self.base = target.rstrip("/")
        self.run_id = run_id
        self.args = args
        self.jobs: dict[str, dict] = {}  # id -> {kind, sent_at, job(final)|None, send_error}
        self.probe_meta: dict[str, dict] = {}  # id -> {offset, depth}
        self.loop = asyncio.get_event_loop()
        self.poll_sem = asyncio.Semaphore(args.concurrency)

    # -- HTTP helpers ---------------------------------------------------

    def register(self, call_record_id: str, kind: str) -> dict:
        """Create the tracking record synchronously, BEFORE the async POST runs,
        so the drain loop never sees an empty in-flight set mid-injection."""
        rec = {"kind": kind, "sent_at": self.loop.time(), "job": None, "send_error": None}
        self.jobs[call_record_id] = rec
        return rec

    async def enqueue(self, call_record_id: str, url: str, kind: str) -> bool:
        payload = {"CallRecordId": call_record_id, "CallRecordUrl": url}
        rec = self.jobs.get(call_record_id) or self.register(call_record_id, kind)
        try:
            r = await self.client.post(f"{self.base}/requestTranscription", json=payload)
            if r.status_code >= 400:
                rec["send_error"] = f"{r.status_code} {r.text[:120]}"
                print(f"  ! send {call_record_id}: {rec['send_error']}", flush=True)
                return False
        except httpx.HTTPError as exc:
            rec["send_error"] = str(exc)
            print(f"  ! send {call_record_id}: {exc}", flush=True)
            return False
        return True

    async def poll_one(self, call_record_id: str):
        async with self.poll_sem:
            try:
                r = await self.client.get(f"{self.base}/jobs/{call_record_id}")
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        job = r.json()
        if job["status"] in TERMINAL:
            self.jobs[call_record_id]["job"] = job

    def unresolved(self, kind: str | None = None) -> list[str]:
        return [
            cid
            for cid, rec in self.jobs.items()
            if rec["job"] is None
            and rec["send_error"] is None
            and (kind is None or rec["kind"] == kind)
        ]

    async def poll_unresolved(self):
        pending = self.unresolved()
        if pending:
            await asyncio.gather(*(self.poll_one(cid) for cid in pending))

    async def wait_resolved(self, call_record_id: str, deadline: float) -> dict | None:
        while self.loop.time() < deadline:
            await self.poll_one(call_record_id)
            job = self.jobs[call_record_id]["job"]
            if job is not None:
                return job
            await asyncio.sleep(self.args.poll_interval)
        return None

    # -- phases ----------------------------------------------------------

    async def preflight(self) -> bool:
        try:
            r = await self.client.get(f"{self.base}/healthz")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"target {self.base} not healthy: {exc}", file=sys.stderr)
            return False
        busy = 0
        for status in ("queued", "processing", "delivering"):
            r = await self.client.get(f"{self.base}/jobs", params={"status": status, "limit": 1})
            if r.status_code == 200:
                busy += r.json()["count"]
        if busy:
            print(f"WARNING: queue is not idle ({busy}+ active jobs) — measurements will be skewed")
            if not self.args.force:
                print("aborting (use --force to run anyway)", file=sys.stderr)
                return False
        return True

    async def baseline(self, probe_url: str) -> list[float]:
        latencies = []
        for n in range(self.args.baseline_probes):
            cid = f"{self.run_id}-probe-base{n}"
            print(f"baseline probe {n + 1}/{self.args.baseline_probes} ...", flush=True)
            if not await self.enqueue(cid, probe_url, "baseline"):
                continue
            job = await self.wait_resolved(cid, self.loop.time() + self.args.max_wait)
            if job is None:
                print(f"  ! baseline probe {cid} did not resolve in time", file=sys.stderr)
                continue
            lat = server_latency(job)
            latencies.append(lat)
            print(f"  baseline probe done in {lat:.1f}s (status={job['status']})", flush=True)
        return latencies

    async def inject(self, schedule):
        start = self.loop.time()
        for i, (fire_at, url, key, _size) in enumerate(schedule):
            delay = fire_at - (self.loop.time() - start)
            if delay > 0:
                await asyncio.sleep(delay)
            cid = f"{self.run_id}-{key.rsplit('/', 1)[-1][:-4]}"
            self.register(cid, "burst")
            asyncio.ensure_future(self.enqueue(cid, url, "burst"))
            if (i + 1) % 20 == 0:
                print(f"  t+{self.loop.time() - start:5.1f}s  injected {i + 1}/{len(schedule)}", flush=True)
        print(f"  injection finished: {len(schedule)} requests in {self.loop.time() - start:.1f}s", flush=True)

    async def drain_and_probe(self, probe_url: str, burst_start: float, inject_task: asyncio.Task):
        probe_n = 0
        next_probe = burst_start + self.args.probe_interval
        deadline = burst_start + self.args.max_wait
        last_status = 0.0
        while self.loop.time() < deadline:
            await self.poll_unresolved()
            burst_left = len(self.unresolved("burst"))
            if inject_task.done() and burst_left == 0 and not self.unresolved("probe"):
                return True
            now = self.loop.time()
            if (burst_left or not inject_task.done()) and now >= next_probe:
                probe_n += 1
                cid = f"{self.run_id}-probe-{probe_n}"
                self.probe_meta[cid] = {"offset": now - burst_start, "depth": burst_left}
                self.register(cid, "probe")
                asyncio.ensure_future(self.enqueue(cid, probe_url, "probe"))
                next_probe += self.args.probe_interval
            if now - last_status >= 15:
                print(f"  t+{now - burst_start:6.1f}s  burst unresolved: {burst_left}, probes sent: {probe_n}", flush=True)
                last_status = now
            await asyncio.sleep(self.args.poll_interval)
        return False

    async def recovery_probe(self, probe_url: str) -> dict | None:
        cid = f"{self.run_id}-probe-recovery"
        print("recovery probe (idle queue after drain) ...", flush=True)
        if not await self.enqueue(cid, probe_url, "recovery"):
            return None
        return await self.wait_resolved(cid, self.loop.time() + self.args.max_wait)


def report(runner: Runner, baseline_lat: list[float], drained: bool, window: float):
    base_mean = sum(baseline_lat) / len(baseline_lat) if baseline_lat else None
    burst = {c: r for c, r in runner.jobs.items() if r["kind"] == "burst"}
    resolved = {c: r for c, r in burst.items() if r["job"] is not None}
    failed = {c: r for c, r in resolved.items() if r["job"]["status"] == "failed"}
    send_errors = {c: r for c, r in burst.items() if r["send_error"]}

    print("\n" + "=" * 64)
    print("RESULTS")
    print("=" * 64)
    print(f"burst: {len(burst)} calls injected over {window:.0f}s window")
    if base_mean is not None:
        print(f"baseline latency (idle queue): {base_mean:.1f}s  ({', '.join(f'{v:.1f}' for v in baseline_lat)})")

    if resolved:
        created = [parse_ts(r["job"]["created_at"]) for r in resolved.values()]
        updated = [parse_ts(r["job"]["updated_at"]) for r in resolved.values()]
        drain = (max(updated) - min(created)).total_seconds()
        lats = [server_latency(r["job"]) for r in resolved.values()]
        print(f"\ndrain time (first enqueue -> last resolved): {drain:.0f}s ({drain / 60:.1f} min)")
        print(f"throughput: {len(resolved) / drain * 60:.1f} jobs/min" if drain else "")
        print(f"per-job end-to-end latency: p50={percentile(lats, 50):.0f}s  p90={percentile(lats, 90):.0f}s  max={max(lats):.0f}s")
    unresolved = runner.unresolved("burst")
    if not drained or unresolved:
        print(f"\n! NOT fully drained within --max-wait: {len(unresolved)} burst jobs still unresolved")
    if failed:
        print(f"\nfailed jobs ({len(failed)}):")
        for cid, r in list(failed.items())[:10]:
            print(f"  {cid}: {(r['job'].get('error') or '?')[:100]}")
    if send_errors:
        print(f"\nsend errors ({len(send_errors)}):")
        for cid, r in list(send_errors.items())[:10]:
            print(f"  {cid}: {r['send_error']}")

    probes = {c: r for c, r in runner.jobs.items() if r["kind"] == "probe"}
    max_ratio = None
    if probes:
        print("\nprobes during drain (new transcriptions arriving behind the backlog):")
        print("  sent at   queue depth   latency     vs baseline")
        for cid in sorted(probes, key=lambda c: runner.probe_meta[c]["offset"]):
            meta, rec = runner.probe_meta[cid], probes[cid]
            if rec["job"] is None:
                lat_s, ratio_s = "unresolved", "-"
            else:
                lat = server_latency(rec["job"])
                lat_s = f"{lat:7.1f}s"
                if base_mean:
                    ratio = lat / base_mean
                    ratio_s = f"{ratio:5.1f}x"
                    max_ratio = max(max_ratio or 0, ratio)
                else:
                    ratio_s = "-"
            print(f"  t+{meta['offset']:5.0f}s   {meta['depth']:6d}       {lat_s}   {ratio_s}")

    recovery = next((r for r in runner.jobs.values() if r["kind"] == "recovery"), None)
    rec_ratio = None
    if recovery and recovery["job"] is not None and base_mean:
        rec_lat = server_latency(recovery["job"])
        rec_ratio = rec_lat / base_mean
        print(f"\nrecovery probe (after drain): {rec_lat:.1f}s = {rec_ratio:.1f}x baseline")

    # verdict
    print("\nverdict:")
    if probes and base_mean and max_ratio:
        print(f"  during drain new requests were delayed up to {max_ratio:.1f}x baseline")
    if rec_ratio is not None:
        ok = "yes" if rec_ratio < 2.0 else "NO"
        print(f"  service recovered to normal latency after drain: {ok} ({rec_ratio:.1f}x)")


async def run(args, schedule, probe_url, window):
    async with httpx.AsyncClient(
        timeout=args.timeout, limits=httpx.Limits(max_connections=args.concurrency), verify=False
    ) as client:
        runner = Runner(client, args.target, args.run_id, args)
        if not await runner.preflight():
            sys.exit(1)

        baseline_lat = await runner.baseline(probe_url)
        if not baseline_lat:
            print("no baseline latency measured — ratios will be omitted", file=sys.stderr)

        print(f"\ninjecting peak hour: {len(schedule)} calls over {window:.0f}s ...", flush=True)
        burst_start = runner.loop.time()
        inject_task = asyncio.create_task(runner.inject(schedule))
        drained = await runner.drain_and_probe(probe_url, burst_start, inject_task)
        await inject_task
        if drained:
            await runner.recovery_probe(probe_url)

        report(runner, baseline_lat, drained, window)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="http://localhost:8080", help="ai_service base URL")
    ap.add_argument("--endpoint", default="https://s3.yandexcloud.net", help="S3 endpoint for listing")
    ap.add_argument("--bucket", default="3cx-recordings")
    ap.add_argument("--prefix", default="3cx/2026.07.06/", help="date prefix to analyze")
    ap.add_argument("--hour", type=int, default=None, help="UTC hour to replay (default: busiest)")
    ap.add_argument("--limit", type=int, default=None, help="cap burst size (full peak hour ~100 real transcriptions)")
    ap.add_argument("--window", type=float, default=60.0, help="seconds to compress the peak hour into (default 60)")
    ap.add_argument("--probe-interval", type=float, default=30.0, help="seconds between probes during drain")
    ap.add_argument("--baseline-probes", type=int, default=2, help="probes to establish idle-queue latency")
    ap.add_argument("--poll-interval", type=float, default=3.0, help="seconds between job-status polls")
    ap.add_argument("--max-wait", type=float, default=3600.0, help="give up waiting for drain after this many seconds")
    ap.add_argument("--concurrency", type=int, default=32, help="max concurrent HTTP requests")
    ap.add_argument("--timeout", type=float, default=30.0, help="per-request HTTP timeout")
    ap.add_argument("--run-id", default=None, help="CallRecordId prefix (default: peak-<prefix>)")
    ap.add_argument("--from-json", default=None, help="load listing from a JSON file instead of S3")
    ap.add_argument("--force", action="store_true", help="run even if the queue is not idle")
    ap.add_argument("--dry-run", action="store_true", help="print peak-hour selection, send nothing")
    args = ap.parse_args()

    if args.from_json:
        items = json.load(open(args.from_json))
    else:
        print(f"fetching listing {args.bucket}/{args.prefix} ...", flush=True)
        items = fetch_listing(args.endpoint, args.bucket, args.prefix)

    hour_start, calls, byhour = pick_peak_hour(items, args.hour)
    if not calls:
        print("no .mp3 objects found for the requested hour/prefix", file=sys.stderr)
        sys.exit(1)
    if args.limit:
        calls = calls[: args.limit]
    schedule = build_schedule(calls, hour_start, args.window, args.bucket)

    print_dry_run(byhour, hour_start, schedule, args.window)
    if args.dry_run:
        print("\n[dry-run] no requests sent")
        return

    # median-size file of the burst doubles as the probe payload (unique ids per probe)
    probe_url = sorted(schedule, key=lambda s: s[3])[len(schedule) // 2][1]
    print(f"probe file: {probe_url}")

    if args.run_id is None:
        args.run_id = "peak-" + re.sub(r"\W+", "", args.prefix)[-12:]
    print(f"target={args.target}  run_id={args.run_id}")
    asyncio.run(run(args, schedule, probe_url, args.window))


if __name__ == "__main__":
    main()
