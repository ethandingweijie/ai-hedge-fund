"""FMP's news filter is `symbols` (plural) and nothing else.

`/stable/news/stock` ignores an unrecognised filter and returns an unfiltered
US feed rather than erroring. The intelligence route passed `tickers=`, so
every ticker -- TSLA, 0700.HK, D05.SI alike -- received the same Apple
articles, and those articles then scored the news_sentiment signal and filled
the top_headlines the report renders.

Reproduced 2026-09-13:

    0700.HK  tickers -> ['AAPL','AAPL','AAPL']   symbols -> []
    TSLA     tickers -> ['AAPL','AAPL','AAPL']   symbols -> ['TSLA', ...]

src/tools/api.py:972 documents the trap for the singular `symbol`; this was
the same failure by a third spelling. No network -- the source is read as text.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "app" / "backend" / "routes" / "analysis.py"
API = ROOT / "src" / "tools" / "api.py"

#: Lines that mention the endpoint but cannot carry a filter kwarg: comments,
#: docstrings and URL construction.
_NOT_A_CALL = ("#", '"""', "Fetch latest news")


def _filter_bearing_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        if "/stable/news/stock" not in line and "news/stock" not in line:
            continue
        stripped = line.lstrip()
        if stripped.startswith(_NOT_A_CALL) or "https://" in line:
            continue
        if "=" not in line:
            continue
        out.append(line)
    return out


class TestNewsFilterParam:
    def test_no_call_site_uses_an_ignored_spelling(self):
        """`tickers=` and the singular `symbol=` are both silently ignored by
        FMP, which is why they are dangerous rather than merely wrong."""
        for path in (ANALYSIS, API):
            for line in _filter_bearing_lines(path.read_text(encoding="utf-8")):
                assert not re.search(r"\btickers\s*=", line), (path.name, line)
                assert not re.search(r"(?<!s)\bsymbol\s*=", line), (path.name, line)

    def test_the_intelligence_route_filters_by_symbols(self):
        text = ANALYSIS.read_text(encoding="utf-8")
        inline = [ln for ln in text.splitlines()
                  if "/stable/news/stock" in ln and "=sym" in ln
                  and not ln.lstrip().startswith("#")]
        assert inline, "the intelligence route no longer calls /stable/news/stock"
        for line in inline:
            assert re.search(r"\bsymbols=sym\b", line), line

    def test_the_shared_fetcher_builds_a_symbols_filter(self):
        """src/tools/api.py passes a params dict rather than kwargs, so the
        key is asserted where the dict is built."""
        text = API.read_text(encoding="utf-8")
        assert re.search(r'"symbols"\s*:', text), \
            "get_company_news no longer filters on `symbols`"

    def test_the_caution_comment_survives(self):
        """It is the only in-repo record of why the singular param is unsafe."""
        text = API.read_text(encoding="utf-8")
        assert "silently IGNORES" in text


class TestZeroCoverageIsDistinguishable:
    def test_the_route_flags_an_empty_feed(self):
        """FMP carries no rows for HKEX or SGX, so an Asian ticker now returns
        zero articles where it used to return Apple's. Zero articles score a
        composite of 0.0 and read as NEUTRAL -- a different wrong answer unless
        the payload says which one it is."""
        text = ANALYSIS.read_text(encoding="utf-8")
        assert '"no_coverage"' in text
