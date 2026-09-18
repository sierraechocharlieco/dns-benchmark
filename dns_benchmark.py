#!/usr/bin/env python3
"""
DNS resolver benchmark: measures speed and stability of public DNS providers.

Safety by design (do not change these defaults without good reason):
  - Queries run sequentially per resolver, never in parallel bursts.
  - A small, fixed delay (with jitter) separates every query.
  - A modest number of queries per resolver (a few dozen) is plenty to get
    statistically useful latency numbers without looking anything
    like load testing.
  - Query order is interleaved across resolvers/domains so no single
    resolver receives a tight burst.

Usage:
    uv run dns_benchmark.py
    uv run dns_benchmark.py --rounds 20 --delay 0.4 --csv results.csv
    uv run dns_benchmark.py --resolvers "Cloudflare=1.1.1.1,Quad9=9.9.9.9,Google=8.8.8.8"
"""

import argparse
import csv
import random
import statistics
import sys
import time

try:
    import dns.exception
    import dns.resolver
except ImportError:
    sys.exit("Missing dependency 'dnspython'. Run the script with:\n    uv run dns_benchmark.py")

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
    "rnz.nz",
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
    for _ in range(rounds):
        for domain in domains:
            for name in resolvers:
                jobs.append((name, domain))
    random.shuffle(jobs)  # avoid any accidental bursty pattern per resolver

    total = len(jobs)
    for i, (name, domain) in enumerate(jobs, 1):
        ip = resolvers[name]
        latency = query_once(ip, domain, timeout)
        results[name].append((domain, latency))
        line = (
            f"[{i}/{total}] {name:<12} {domain:<16} "
            f"{'timeout/fail' if latency is None else f'{latency:6.1f} ms'}"
        )
        print(f"\r{line:<60}", end="", flush=True)
        time.sleep(delay + random.uniform(0, delay * 0.5))
    print()
    return results


def summarise(results: dict) -> list:
    rows = []
    for name, samples in results.items():
        latencies = sorted(lat for _, lat in samples if lat is not None)
        ok = len(latencies)
        if latencies:
            median = statistics.median(latencies)
            p95 = latencies[round(0.95 * (ok - 1))]
        else:
            median = p95 = float("inf")
        rows.append(
            {
                "name": name,
                "success_rate": ok / len(samples) if samples else 0.0,
                "median_ms": median,
                "p95_ms": p95,
            }
        )
    return rows


def print_report(rows: list):
    print("\n=== Ranked by median latency (lower is better) ===")
    for r in sorted(rows, key=lambda r: r["median_ms"]):
        if r["success_rate"] == 0:
            print(f"  {r['name']:<12} all queries failed")
            continue
        success = f"success={r['success_rate'] * 100:5.1f}%"
        warning = "  ⚠ failures" if r["success_rate"] < 1 else ""
        print(
            f"  {r['name']:<12} median={r['median_ms']:6.1f} ms  "
            f"p95={r['p95_ms']:6.1f} ms  {success}{warning}"
        )

    fastest = min(rows, key=lambda r: r["median_ms"])
    if fastest["success_rate"] == 0:
        print("\nNo resolver answered any queries.")
        return
    print(f"\nFastest: {fastest['name']} ({fastest['median_ms']:.1f} ms median)")


def write_csv(path: str, results: dict):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["resolver", "domain", "latency_ms"])
        for name, samples in results.items():
            for domain, latency in samples:
                writer.writerow([name, domain, "" if latency is None else f"{latency:.2f}"])
    print(f"Raw samples written to {path}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--resolvers",
        default=None,
        help="Comma-separated Name=IP pairs, e.g. 'Cloudflare=1.1.1.1,Google=8.8.8.8'",
    )
    parser.add_argument(
        "--domains",
        default=None,
        help="Comma-separated domains to query (defaults to a small fixed list)",
    )
    parser.add_argument(
        "--rounds", type=int, default=10, help="Queries per domain per resolver (default: 10)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Base delay in seconds between each query, jitter added on top (default: 0.3)",
    )
    parser.add_argument(
        "--timeout", type=float, default=2.0, help="Per-query timeout in seconds (default: 2.0)"
    )
    parser.add_argument(
        "--csv", default=None, help="Optional path to write raw per-query samples as CSV"
    )
    args = parser.parse_args()

    resolvers = parse_resolvers(args.resolvers) if args.resolvers else DEFAULT_RESOLVERS
    domains = [d.strip() for d in args.domains.split(",")] if args.domains else DEFAULT_DOMAINS

    total_queries = len(resolvers) * len(domains) * args.rounds
    est_seconds = total_queries * (args.delay * 1.25)
    print(f"Resolvers: {resolvers}")
    print(f"Domains:   {domains}")
    print(
        f"Plan: {args.rounds} rounds x {len(domains)} domains x {len(resolvers)} resolvers "
        f"= {total_queries} total queries, spaced ~{args.delay}s apart "
        f"(est. ~{est_seconds:.0f}s runtime)\n"
    )

    results = run_benchmark(resolvers, domains, args.rounds, args.delay, args.timeout)
    rows = summarise(results)
    print_report(rows)

    if args.csv:
        write_csv(args.csv, results)


if __name__ == "__main__":
    main()
