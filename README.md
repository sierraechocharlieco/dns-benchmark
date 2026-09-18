# dns-benchmark

Measures the speed and stability of public DNS resolvers from your network.

It sends a small number of `A` queries to each resolver, one at a time, and reports median,
mean, and p95 latency, jitter, and success rate. It then ranks the resolvers by speed and by
stability.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Install

```sh
git clone https://github.com/sierraechocharlieco/dns-benchmark.git
cd dns-benchmark
uv sync
```

## Usage

```sh
uv run dns_benchmark.py
```

By default this queries Cloudflare (`1.1.1.1`), Quad9 (`9.9.9.9`), Google (`8.8.8.8`) and
OpenDNS (`208.67.222.222`). It sends 10 rounds per domain (240 queries in total), about 0.3 s
apart, which takes about two minutes.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--resolvers` | the four above | Comma-separated `Name=IP` pairs |
| `--domains` | built-in list | Comma-separated domains to query |
| `--rounds` | `10` | Queries per domain per resolver |
| `--delay` | `0.3` | Base delay in seconds between queries, plus up to 50% random jitter |
| `--timeout` | `2.0` | Per-query timeout in seconds |
| `--csv` | none | Write raw per-query samples to this CSV path |

### Examples

Compare your router or ISP resolver against Cloudflare:

```sh
uv run dns_benchmark.py --resolvers "Router=192.168.1.1,Cloudflare=1.1.1.1"
```

Run a longer test and keep the raw samples:

```sh
uv run dns_benchmark.py --rounds 20 --delay 0.4 --csv results.csv
```

The CSV has one row per query with the columns `resolver`, `domain`, and `latency_ms`.
`latency_ms` is empty for queries that failed or timed out.

## Sample output

```text
=== Ranked by speed (median latency, lower is better) ===
  Google       median=   57.6 ms  mean=   57.6 ms  p95=   73.3 ms  success=100.0%
  OpenDNS      median=  135.6 ms  mean=  135.6 ms  p95=  217.4 ms  success=100.0%
  ...

=== Ranked by stability (jitter + failure penalty, lower is better) ===
  Google       stdev=   15.7 ms  success=100.0%  score=   15.7
  OpenDNS      stdev=   81.8 ms  success=100.0%  score=   81.8
  ...

Fastest:     Google (57.6 ms median)
Most stable: Google (jitter 15.7 ms, success 100.0%)
```

## How results are calculated

- **Speed** ranks resolvers by median latency of successful queries.
- **Stability score** is the population standard deviation of latency plus
  `(1 - success_rate) * 1000`. Each 1% of failed queries adds 10 points, so failures count
  for more than a few milliseconds of jitter. Lower is better.

The default domains are large, popular sites that resolvers will already have cached. The
results measure each resolver's response time from your network, not how fast it resolves
uncached names.

## Query rate

The tool is designed so that it never behaves like a load test:

- Queries run one at a time, never in parallel.
- Every query is followed by a delay with random jitter.
- Queries are shuffled across resolvers and domains, so no resolver gets a burst.
- The default run sends 60 queries to each resolver.

Keep `--delay` at a similar value if you change it, and don't point the tool at resolvers you
don't have permission to query.

## Development

```sh
uv run ruff check .
uv run ruff format .
uv run ty check
```

## License

[MIT](LICENSE) © 2026 Sierra Echo Charlie
