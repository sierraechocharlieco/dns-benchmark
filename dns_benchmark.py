#!/usr/bin/env python3
"""
DNS resolver benchmark: measures speed and stability of public DNS providers.

Safety by design (do not change these defaults without good reason):
  - Queries run sequentially per resolver, never in parallel bursts.
  - A small, fixed delay (with jitter) separates every query.
  - A modest number of queries per resolver (a few dozen) is plenty to get
    statistically useful latency/jitter numbers without looking anything
    like load testing.
  - Query order is interleaved across resolvers/domains so no single
    resolver receives a tight burst.

Usage:
    pip install dnspython
    python3 dns_benchmark.py
    python3 dns_benchmark.py --rounds 20 --delay 0.4 --csv results.csv
    python3 dns_benchmark.py --resolvers "Cloudflare=1.1.1.1,Quad9=9.9.9.9,Google=8.8.8.8,OpenDNS=208.67.222.222"
"""

import argparse
import csv
import random
import statistics
import sys
import time

try:
    import dns.resolver
    import dns.exception
except ImportError:
    sys.exit(
        "Missing dependency 'dnspython'. Install it with:\n"
        "    pip install dnspython"
    )

DEFAULT_RESOLVERS = {
    "Cloudflare": "1.1.1.1",
    "Quad9": "9.9.9.9",
    "Google": "8.8.8.8",
    "OpenDNS": "208.67.222.222",
}

# A handful of large, always-cached, high-TTL domains. Querying these
# repeatedly costs resolvers essentially nothing since they're already hot
# in cache -- the point is to measure the resolver's response path, not to
# force fresh lookups.
DEFAULT_DOMAINS = [
    "google.com",
    "cloudflare.com",
    "wikipedia.org",
    "github.com",
    "stuff.co.nz", 
    "rnz.nz"
]


def parse_resolvers(spec: str) -> dict:
    resolvers = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            sys.exit(f"Bad --resolvers entry {entry!r}, expected Name=IP")
        name, ip = entry.split("=", 1)
        resolvers[name.strip()] = ip.strip()
    return resolvers


def query_once(server_ip: str, domain: str, timeout: float) -> float | None:
    """Return latency in milliseconds, or None on failure/timeout."""
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [server_ip]
    resolver.timeout = timeout
    resolver.lifetime = timeout
    start = time.perf_counter()
    try:
        resolver.resolve(domain, "A")
    except dns.exception.DNSException:
        return None
    return (time.perf_counter() - start) * 1000.0


def run_benchmark(resolvers, domains, rounds, delay, timeout):
    """Interleave queries across resolvers/domains, one at a time."""
    results = {name: [] for name in resolvers}  # name -> list of (domain, latency_ms | None)

    jobs = []
    for round_i in range(rounds):
        for domain in domains:
            for name in resolvers:
                jobs.append((name, domain))
    random.shuffle(jobs)  # avoid any accidental bursty pattern per resolver

    total = len(jobs)
    for i, (name, domain) in enumerate(jobs, 1):
        ip = resolvers[name]
        latency = query_once(ip, domain, timeout)
        results[name].append((domain, latency))
        line = (f"[{i}/{total}] {name:<12} {domain:<16} "
                f"{'timeout/fail' if latency is None else f'{latency:6.1f} ms'}")
        print(f"\r{line:<60}", end="", flush=True)
        time.sleep(delay + random.uniform(0, delay * 0.5))
    print()
    return results


def summarise(results: dict) -> list:
    rows = []
    for name, samples in results.items():
        latencies = [lat for _, lat in samples if lat is not None]
        n = len(samples)
        ok = len(latencies)
        success_rate = ok / n if n else 0.0

        if latencies:
            mean = statistics.mean(latencies)
            median = statistics.median(latencies)
            stdev = statistics.pstdev(latencies) if ok > 1 else 0.0
            p95 = sorted(latencies)[max(0, int(round(0.95 * (ok - 1))))]
            lat_min = min(latencies)
            lat_max = max(latencies)
        else:
            mean = median = stdev = p95 = lat_min = lat_max = float("inf")

        # Stability score: lower is better. Penalize jitter and failures.
        # Failures are penalized heavily since they matter more than a few
        # extra ms of jitter.
        stability_score = stdev + (1 - success_rate) * 1000

        rows.append({
            "name": name,
            "n": n,
            "success_rate": success_rate,
            "mean_ms": mean,
            "median_ms": median,
            "stdev_ms": stdev,
            "p95_ms": p95,
            "min_ms": lat_min,
            "max_ms": lat_max,
            "stability_score": stability_score,
        })
    return rows


def print_report(rows: list):
    print("\n=== Ranked by speed (median latency, lower is better) ===")
    for r in sorted(rows, key=lambda r: r["median_ms"]):
        print(f"  {r['name']:<12} median={r['median_ms']:7.1f} ms  "
              f"mean={r['mean_ms']:7.1f} ms  p95={r['p95_ms']:7.1f} ms  "
              f"success={r['success_rate']*100:5.1f}%")

    print("\n=== Ranked by stability (jitter + failure penalty, lower is better) ===")
    for r in sorted(rows, key=lambda r: r["stability_score"]):
        print(f"  {r['name']:<12} stdev={r['stdev_ms']:7.1f} ms  "
              f"success={r['success_rate']*100:5.1f}%  "
              f"score={r['stability_score']:7.1f}")

    fastest = min(rows, key=lambda r: r["median_ms"])
    steadiest = min(rows, key=lambda r: r["stability_score"])
    print(f"\nFastest:     {fastest['name']} ({fastest['median_ms']:.1f} ms median)")
    print(f"Most stable: {steadiest['name']} "
          f"(jitter {steadiest['stdev_ms']:.1f} ms, "
          f"success {steadiest['success_rate']*100:.1f}%)")


def write_csv(path: str, results: dict):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["resolver", "domain", "latency_ms"])
        for name, samples in results.items():
            for domain, latency in samples:
                writer.writerow([name, domain, "" if latency is None else f"{latency:.2f}"])
    print(f"Raw samples written to {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resolvers", default=None,
                         help="Comma-separated Name=IP pairs, e.g. 'Cloudflare=1.1.1.1,Google=8.8.8.8'")
    parser.add_argument("--domains", default=None,
                         help="Comma-separated domains to query (defaults to a small fixed list)")
    parser.add_argument("--rounds", type=int, default=10,
                         help="Queries per domain per resolver (default: 10)")
    parser.add_argument("--delay", type=float, default=0.3,
                         help="Base delay in seconds between each query, jitter added on top (default: 0.3)")
    parser.add_argument("--timeout", type=float, default=2.0,
                         help="Per-query timeout in seconds (default: 2.0)")
    parser.add_argument("--csv", default=None, help="Optional path to write raw per-query samples as CSV")
    args = parser.parse_args()

    resolvers = parse_resolvers(args.resolvers) if args.resolvers else DEFAULT_RESOLVERS
    domains = [d.strip() for d in args.domains.split(",")] if args.domains else DEFAULT_DOMAINS

    total_queries = len(resolvers) * len(domains) * args.rounds
    est_seconds = total_queries * (args.delay * 1.25)
    print(f"Resolvers: {resolvers}")
    print(f"Domains:   {domains}")
    print(f"Plan: {args.rounds} rounds x {len(domains)} domains x {len(resolvers)} resolvers "
          f"= {total_queries} total queries, spaced ~{args.delay}s apart "
          f"(est. ~{est_seconds:.0f}s runtime)\n")

    results = run_benchmark(resolvers, domains, args.rounds, args.delay, args.timeout)
    rows = summarise(results)
    print_report(rows)

    if args.csv:
        write_csv(args.csv, results)


if __name__ == "__main__":
    main()
