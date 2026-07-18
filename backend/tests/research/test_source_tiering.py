"""Source tiering + independence clustering."""

from __future__ import annotations

from riskagent.research.models import SourceCard
from riskagent.research.tiering import (
    apply_tiers,
    classify_source,
    cluster_independence,
)
from riskagent.research.models import SourceTier


def _card(url: str, **kw) -> SourceCard:
    payload = {"url": url, "origin": "fetched_page"}
    payload.update(kw)
    return SourceCard(**payload)


class TestClassify:
    def test_regulator_domain_is_tier1(self):
        card = _card("https://www.hkexnews.hk/listedco/doc.htm")
        tier, reason = classify_source(card)
        assert tier is SourceTier.tier1
        assert "tier1" in reason

    def test_gov_hk_suffix_is_tier1(self):
        card = _card("https://www.ird.gov.hk/eng/notice.htm")
        tier, _ = classify_source(card)
        assert tier is SourceTier.tier1

    def test_financial_media_is_tier2(self):
        card = _card("https://www.caixin.com/2025/article.html")
        tier, reason = classify_source(card)
        assert tier is SourceTier.tier2
        assert "tier2" in reason

    def test_forum_is_unknown_treated_as_tier3(self):
        card = _card("https://example-forum.com/thread/1")
        tier, reason = classify_source(card)
        assert tier is SourceTier.unknown
        assert "tier3" in reason

    def test_company_ir_domain_promoted_to_tier1(self):
        card = _card("https://ir.somebiotech.com/announcements/2025.htm")
        tier, reason = classify_source(card, ir_domains=["ir.somebiotech.com"])
        assert tier is SourceTier.tier1
        assert "IR" in reason

    def test_final_url_wins_over_url(self):
        card = _card(
            "https://redirect.example.com/go",
            final_url="https://www.sec.gov/filing.htm",
        )
        tier, _ = classify_source(card)
        assert tier is SourceTier.tier1

    def test_apply_tiers_stamps_in_place(self):
        cards = [
            _card("https://www.hkexnews.hk/a.htm"),
            _card("https://example-forum.com/x"),
        ]
        apply_tiers(cards)
        assert cards[0].tier is SourceTier.tier1
        assert cards[1].tier is SourceTier.unknown
        assert cards[0].tier_reason and cards[1].tier_reason


class TestIndependence:
    def test_distinct_reports_are_distinct_clusters(self):
        cards = [
            _card(
                "https://a.com/1",
                title="甲公司境外诉讼进展",
                extract="甲公司在美国的子公司近日被提起集体诉讼，涉及金额巨大。",
            ),
            _card(
                "https://b.com/2",
                title="乙公司营收下滑分析",
                extract="乙公司最新财报显示营收连续三个季度下滑，毛利率承压明显。",
            ),
        ]
        n = cluster_independence(cards)
        assert n == 2
        assert cards[0].independence_cluster != cards[1].independence_cluster

    def test_same_title_reprint_is_one_cluster(self):
        shared_title = "甲公司境外子公司被提起集体诉讼涉案金额巨大"
        cards = [
            _card("https://a.com/1", title=shared_title, extract="原始报道正文内容 A"),
            _card("https://b.com/2", title=shared_title, extract="完全不同的正文 B"),
        ]
        n = cluster_independence(cards)
        assert n == 1
        assert cards[0].independence_cluster == cards[1].independence_cluster

    def test_reprint_marker_clusters_with_overlap(self):
        body = "甲公司境外子公司近日被提起集体诉讼涉及金额巨大市场高度关注后续进展"
        cards = [
            _card("https://origin.com/1", title="独家：甲公司诉讼", extract=body),
            _card(
                "https://portal.com/2",
                title="甲公司诉讼消息",
                extract="来源：origin.com " + body,
            ),
        ]
        n = cluster_independence(cards)
        assert n == 1

    def test_independent_count_is_clusters_not_urls(self):
        # Three URLs but only two independent voices: two are the same report.
        shared = "丙公司核心专利被宣告无效影响主营业务收入市场份额或大幅下降"
        cards = [
            _card("https://x.com/1", title="丙公司专利无效", extract=shared),
            _card("https://y.com/2", title="丙公司专利无效", extract="转自 x.com " + shared),
            _card(
                "https://z.com/3",
                title="行业分析：专利格局",
                extract="本篇从行业整体角度分析专利布局与竞争态势，与个案无直接关联。",
            ),
        ]
        n = cluster_independence(cards)
        assert n == 2

    def test_empty_list(self):
        assert cluster_independence([]) == 0
