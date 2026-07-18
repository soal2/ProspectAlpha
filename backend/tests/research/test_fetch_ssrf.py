import asyncio
import hashlib

import httpx
import pytest

from riskagent.research.html_text import extract_text


def run(coro):
    return asyncio.run(coro)


def html_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, html=body)


PAGE_HTML = """<!doctype html>
<html>
<head>
  <title>  监管处罚 公告 </title>
  <meta property="article:published_time" content="2025-11-03T08:00:00+08:00">
  <script>alert("tracking");</script>
  <style>.x { color: red; }</style>
</head>
<body>
  <h1>某公司收到监管函</h1>
  <p>公司于2025年11月收到证监会问询函。</p>
  <script>console.log("more js");</script>
  <p>问询涉及境外子公司诉讼事项。</p>
</body>
</html>"""


class TestUrlValidation:
    @pytest.mark.parametrize(
        "url,reason",
        [
            ("ftp://public.test/file.txt", "unsupported_scheme"),
            ("javascript:alert(1)", "unsupported_scheme"),
            ("file:///etc/passwd", "unsupported_scheme"),
            ("https://public.test:8443/page", "unsupported_port"),
            ("http://public.test:2222/page", "unsupported_port"),
            ("https://user:pass@public.test/page", "userinfo_present"),
            ("https://admin@public.test/page", "userinfo_present"),
        ],
    )
    def test_scheme_port_userinfo_rejected_without_http(
        self, make_safe_fetcher, url, reason
    ):
        fetcher, handler = make_safe_fetcher()
        outcome = run(fetcher.fetch(url))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == reason
        assert handler.requests == []

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/status",
            "http://127.0.0.53/x",
            "http://10.0.0.5/internal",
            "http://172.16.3.4/internal",
            "http://192.168.1.1/router",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0/x",
            "http://[::1]/x",
            "http://[fc00::1]/x",
            "http://[fe80::1]/x",
            "http://[::ffff:10.0.0.5]/x",
        ],
    )
    def test_private_ip_literals_rejected_without_http(self, make_safe_fetcher, url):
        fetcher, handler = make_safe_fetcher()
        outcome = run(fetcher.fetch(url))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "private_address"
        assert handler.requests == []

    def test_dns_resolving_to_private_rejected(self, make_safe_fetcher):
        fetcher, handler = make_safe_fetcher(dns={"internal.test": ["10.0.0.8"]})
        outcome = run(fetcher.fetch("http://internal.test/secret"))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "private_address"
        assert handler.requests == []

    def test_dns_any_private_record_rejects(self, make_safe_fetcher):
        fetcher, handler = make_safe_fetcher(
            dns={"mixed.test": ["93.184.216.34", "192.168.0.9"]}
        )
        outcome = run(fetcher.fetch("http://mixed.test/page"))
        assert outcome.inaccessible_reason == "private_address"
        assert handler.requests == []

    def test_dns_failure_is_lead_card(self, make_safe_fetcher):
        fetcher, handler = make_safe_fetcher(dns={"ghost.test": None})
        outcome = run(fetcher.fetch("http://ghost.test/page"))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "dns_error"
        assert handler.requests == []


class TestRedirects:
    def test_redirect_to_metadata_endpoint_rejected(self, make_safe_fetcher):
        fetcher, handler = make_safe_fetcher(
            routes={
                "http://public.test/leak": httpx.Response(
                    302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
                ),
            }
        )
        outcome = run(fetcher.fetch("http://public.test/leak"))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "private_address"
        assert outcome.redirect_chain == ["http://public.test/leak"]
        assert all("169.254" not in str(r.url) for r in handler.requests)

    def test_redirect_to_private_hostname_rejected(self, make_safe_fetcher):
        fetcher, handler = make_safe_fetcher(
            dns={"intranet.test": ["10.1.1.1"]},
            routes={
                "http://public.test/hop": httpx.Response(
                    301, headers={"location": "http://intranet.test/db"}
                ),
            },
        )
        outcome = run(fetcher.fetch("http://public.test/hop"))
        assert outcome.inaccessible_reason == "private_address"
        assert all("intranet.test" not in str(r.url) for r in handler.requests)

    def test_redirect_count_capped(self, make_safe_fetcher):
        routes = {
            f"http://public.test/r{i}": httpx.Response(
                302, headers={"location": f"http://public.test/r{i + 1}"}
            )
            for i in range(5)
        }
        fetcher, _ = make_safe_fetcher(routes=routes, fetch_max_redirects=3)
        outcome = run(fetcher.fetch("http://public.test/r0"))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "too_many_redirects"

    def test_redirects_within_cap_succeed(self, make_safe_fetcher):
        fetcher, _ = make_safe_fetcher(
            routes={
                "http://public.test/a": httpx.Response(
                    302, headers={"location": "/b"}
                ),
                "http://public.test/b": html_response("<title>done</title>ok"),
            }
        )
        outcome = run(fetcher.fetch("http://public.test/a"))
        assert outcome.accessible is True
        assert outcome.final_url == "http://public.test/b"
        assert outcome.redirect_chain == ["http://public.test/a"]


class TestLimitsAndContentTypes:
    def test_oversize_body_tripped(self, make_safe_fetcher):
        fetcher, _ = make_safe_fetcher(
            routes={"http://public.test/big": html_response("x" * 500)},
            fetch_max_bytes=100,
        )
        outcome = run(fetcher.fetch("http://public.test/big"))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "oversize"

    def test_pdf_excluded(self, make_safe_fetcher):
        fetcher, _ = make_safe_fetcher(
            routes={
                "http://public.test/doc.pdf": httpx.Response(
                    200,
                    content=b"%PDF-1.7 fake",
                    headers={"content-type": "application/pdf"},
                )
            }
        )
        outcome = run(fetcher.fetch("http://public.test/doc.pdf"))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "unsupported_content_type"
        assert outcome.content_type == "application/pdf"

    def test_missing_content_type_rejected(self, make_safe_fetcher):
        fetcher, _ = make_safe_fetcher(
            routes={"http://public.test/blob": httpx.Response(200, content=b"data")}
        )
        outcome = run(fetcher.fetch("http://public.test/blob"))
        assert outcome.inaccessible_reason == "unsupported_content_type"

    def test_text_plain_allowed(self, make_safe_fetcher):
        fetcher, _ = make_safe_fetcher(
            routes={
                "http://public.test/note.txt": httpx.Response(
                    200, text="plain body 内容"
                )
            }
        )
        outcome = run(fetcher.fetch("http://public.test/note.txt"))
        assert outcome.accessible is True
        assert outcome.text == "plain body 内容"

    def test_timeout_is_lead_card(self, make_safe_fetcher):
        def raise_timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        fetcher, _ = make_safe_fetcher(
            routes={"http://public.test/slow": raise_timeout}
        )
        outcome = run(fetcher.fetch("http://public.test/slow"))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "timeout"


class TestRobotsAndAccessControls:
    def test_robots_disallow_keeps_lead(self, make_safe_fetcher):
        fetcher, handler = make_safe_fetcher(
            routes={
                "http://public.test/robots.txt": httpx.Response(
                    200, text="User-agent: *\nDisallow: /private/"
                ),
            }
        )
        outcome = run(fetcher.fetch("http://public.test/private/report"))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "robots_disallow"
        assert outcome.final_url == "http://public.test/private/report"
        assert [str(r.url) for r in handler.requests] == [
            "http://public.test/robots.txt"
        ]

    def test_robots_cached_per_host(self, make_safe_fetcher):
        fetcher, handler = make_safe_fetcher(
            routes={
                "http://public.test/robots.txt": httpx.Response(
                    200, text="User-agent: *\nAllow: /"
                ),
                "http://public.test/a": html_response("a"),
                "http://public.test/b": html_response("b"),
            }
        )
        run(fetcher.fetch("http://public.test/a"))
        run(fetcher.fetch("http://public.test/b"))
        robots_calls = [
            r for r in handler.requests if str(r.url).endswith("/robots.txt")
        ]
        assert len(robots_calls) == 1

    def test_robots_403_means_no_crawl(self, make_safe_fetcher):
        fetcher, _ = make_safe_fetcher(
            routes={"http://public.test/robots.txt": httpx.Response(403)}
        )
        outcome = run(fetcher.fetch("http://public.test/page"))
        assert outcome.inaccessible_reason == "robots_disallow"

    def test_paywall_403_is_lead_card_not_bypassed(self, make_safe_fetcher):
        fetcher, handler = make_safe_fetcher(
            routes={"http://public.test/premium": httpx.Response(403, text="pay up")}
        )
        outcome = run(fetcher.fetch("http://public.test/premium"))
        assert outcome.accessible is False
        assert outcome.inaccessible_reason == "http_403"
        assert outcome.status_code == 403
        # exactly one attempt at the page — no retry, no auth tricks
        page_calls = [r for r in handler.requests if r.url.path == "/premium"]
        assert len(page_calls) == 1

    def test_login_wall_401_is_lead_card(self, make_safe_fetcher):
        fetcher, _ = make_safe_fetcher(
            routes={"http://public.test/member": httpx.Response(401)}
        )
        outcome = run(fetcher.fetch("http://public.test/member"))
        assert outcome.inaccessible_reason == "http_401"


class TestExtraction:
    def test_title_text_date_and_hash(self, make_safe_fetcher):
        fetcher, _ = make_safe_fetcher(
            routes={"http://public.test/news": html_response(PAGE_HTML)}
        )
        outcome = run(fetcher.fetch("http://public.test/news"))
        assert outcome.accessible is True
        assert outcome.title == "监管处罚 公告"
        assert outcome.published_hint == "2025-11-03T08:00:00+08:00"
        assert "证监会问询函" in outcome.text
        assert "alert(" not in outcome.text
        assert "color: red" not in outcome.text
        assert outcome.content_hash == hashlib.sha256(
            PAGE_HTML.encode("utf-8")
        ).hexdigest()
        assert outcome.bytes_read == len(PAGE_HTML.encode("utf-8"))

    def test_extract_truncated_to_config_limit(self, make_safe_fetcher):
        body = "<p>" + "字" * 500 + "</p>"
        fetcher, _ = make_safe_fetcher(
            routes={"http://public.test/long": html_response(body)},
            extract_max_chars=64,
        )
        outcome = run(fetcher.fetch("http://public.test/long"))
        assert outcome.accessible is True
        assert len(outcome.text) == 64


class TestHtmlTextUnit:
    def test_meta_priority_and_blocks(self):
        doc = extract_text(
            "<head><meta name='date' content='2024-01-01'>"
            "<meta property='article:published_time' content='2025-02-02'>"
            "<title>T</title></head>"
            "<body><p>one</p><p>two</p></body>"
        )
        assert doc.title == "T"
        assert doc.published_hint == "2025-02-02"
        assert doc.text == "one\ntwo"

    def test_malformed_html_never_raises(self):
        doc = extract_text("<div><p>open<script>bad(</div>")
        assert isinstance(doc.text, str)
