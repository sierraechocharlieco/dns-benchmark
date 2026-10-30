"""Tests for the pure (non-network) parts of dns_benchmark."""

import pytest

import dns_benchmark as db


class TestParseResolvers:
    def test_single_pair(self):
        assert db.parse_resolvers("Cloudflare=1.1.1.1") == {"Cloudflare": "1.1.1.1"}

    def test_multiple_pairs_with_whitespace_and_blank_entries(self):
        spec = " Cloudflare = 1.1.1.1 , ,Quad9=9.9.9.9 ,"
        assert db.parse_resolvers(spec) == {"Cloudflare": "1.1.1.1", "Quad9": "9.9.9.9"}

    def test_ipv6(self):
        assert db.parse_resolvers("Cloudflare=2606:4700:4700::1111") == {
            "Cloudflare": "2606:4700:4700::1111"
        }

    def test_missing_equals_exits(self):
        with pytest.raises(SystemExit, match="expected Name=IP"):
            db.parse_resolvers("Cloudflare")

    def test_invalid_ip_exits(self):
        with pytest.raises(SystemExit, match="not a valid IP address"):
            db.parse_resolvers("Foo=notanip")

    def test_missing_name_exits(self):
        with pytest.raises(SystemExit, match="missing resolver name"):
            db.parse_resolvers("=1.2.3.4")

    def test_empty_spec_exits(self):
        with pytest.raises(SystemExit, match="at least one Name=IP"):
            db.parse_resolvers(" , ,")


class TestParseDomains:
    def test_single_domain(self):
        assert db.parse_domains("example.com") == ["example.com"]

    def test_blank_entries_filtered(self):
        assert db.parse_domains("example.com, ,google.com,") == ["example.com", "google.com"]

    def test_empty_spec_exits(self):
        with pytest.raises(SystemExit, match="at least one domain"):
            db.parse_domains(" , ,")


class TestSummarise:
    def test_mixed_success_and_failure(self):
        results = {"A": [("x.com", 10.0), ("x.com", None), ("x.com", 30.0), ("x.com", 20.0)]}
        (row,) = db.summarise(results)
        assert row["name"] == "A"
        assert row["success_rate"] == pytest.approx(0.75)
        assert row["median_ms"] == pytest.approx(20.0)
        # nearest-rank p95 of 3 samples: ceil(0.95*3)-1 = index 2 -> 30.0
        assert row["p95_ms"] == pytest.approx(30.0)

    def test_p95_index_capped_at_last_sample(self):
        results = {"A": [("x.com", float(i)) for i in range(1, 21)]}  # 20 samples
        (row,) = db.summarise(results)
        assert row["p95_ms"] == pytest.approx(19.0)  # ceil(0.95*20)-1 = 18 -> 19.0

    def test_all_failed(self):
        results = {"A": [("x.com", None), ("x.com", None)]}
        (row,) = db.summarise(results)
        assert row["success_rate"] == 0.0
        assert row["median_ms"] == float("inf")
        assert row["p95_ms"] == float("inf")

    def test_multiple_resolvers(self):
        results = {
            "A": [("x.com", 10.0)],
            "B": [("x.com", 5.0), ("x.com", 15.0)],
        }
        rows = db.summarise(results)
        assert {r["name"] for r in rows} == {"A", "B"}


class TestPrintReport:
    def test_empty_rows_does_not_crash(self, capsys):
        db.print_report([])
        assert "No resolvers to benchmark" in capsys.readouterr().out

    def test_all_failed_resolver(self, capsys):
        rows = [
            {
                "name": "Dead",
                "success_rate": 0.0,
                "median_ms": float("inf"),
                "p95_ms": float("inf"),
            },
            {"name": "Alive", "success_rate": 1.0, "median_ms": 12.0, "p95_ms": 20.0},
        ]
        db.print_report(rows)
        out = capsys.readouterr().out
        assert "all queries failed" in out
        assert "Fastest: Alive (12.0 ms median)" in out

    def test_no_resolver_answered(self, capsys):
        rows = [
            {
                "name": "Dead",
                "success_rate": 0.0,
                "median_ms": float("inf"),
                "p95_ms": float("inf"),
            }
        ]
        db.print_report(rows)
        assert "No resolver answered any queries" in capsys.readouterr().out

    def test_ranked_by_median_and_failure_warning(self, capsys):
        rows = [
            {"name": "Slow", "success_rate": 1.0, "median_ms": 50.0, "p95_ms": 60.0},
            {"name": "Fast", "success_rate": 0.9, "median_ms": 10.0, "p95_ms": 15.0},
        ]
        db.print_report(rows)
        out = capsys.readouterr().out
        assert out.index("Fast") < out.index("Slow")
        assert "⚠ failures" in out


class TestQueryOnce:
    def test_dns_failure_returns_none(self, monkeypatch):
        import dns.exception
        import dns.resolver

        class FakeResolver:
            def __init__(self, configure):
                pass

            nameservers = None
            timeout = None
            lifetime = None

            def resolve(self, domain, rdtype):
                raise dns.exception.Timeout

        monkeypatch.setattr(dns.resolver, "Resolver", FakeResolver)
        assert db.query_once("192.0.2.1", "example.com", 1.0) is None

    def test_os_error_returns_none(self, monkeypatch):
        import dns.resolver

        class FakeResolver:
            def __init__(self, configure):
                pass

            nameservers = None
            timeout = None
            lifetime = None

            def resolve(self, domain, rdtype):
                raise OSError("network unreachable")

        monkeypatch.setattr(dns.resolver, "Resolver", FakeResolver)
        assert db.query_once("192.0.2.1", "example.com", 1.0) is None

    def test_success_returns_positive_latency(self, monkeypatch):
        import dns.resolver

        class FakeResolver:
            def __init__(self, configure):
                pass

            nameservers = None
            timeout = None
            lifetime = None

            def resolve(self, domain, rdtype):
                return None

        monkeypatch.setattr(dns.resolver, "Resolver", FakeResolver)
        latency = db.query_once("1.1.1.1", "example.com", 1.0)
        assert latency is not None and latency >= 0
