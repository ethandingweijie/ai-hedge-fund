"""The news SSE endpoint.

Exercised through the in-process bus fallback, so no Redis and no network.
The contract that matters: the stream opens immediately, pushes items that
arrive AFTER the client connected, and replays nothing — the page has already
loaded its snapshot from the store and would otherwise render the same
headlines twice.
"""
import pytest


@pytest.fixture(autouse=True)
def local_bus(monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCED", "false")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399")   # nothing there
    from app.backend.services import news_bus
    news_bus.reset_local_state()
    yield
    news_bus.reset_local_state()


def _client():
    from fastapi.testclient import TestClient
    import app.backend.main as m
    return TestClient(m.app)


class TestStream:
    """Live push is NOT asserted through TestClient.

    It buffers a streaming response: traced by hand, every frame — including
    an item published three seconds in — arrived together at the twenty-second
    mark. A test written against that measures the harness, not the endpoint,
    and would pass or fail on timing.

    The push path is covered where it can be observed honestly: the bus fan-out
    has direct tests in test_news_pipeline.py, and the route was verified in a
    real browser (news_open, then a news_item pushed onto an open connection).
    What remains here is the structure a regression would break.
    """


    def test_the_stream_opens_and_names_its_tickers(self):
        """The first frame identifies the subscription, so a client can tell a
        live connection from a proxy holding an empty response open."""
        with _client().stream(
                "GET", "/analysis/news/stream?tickers=0700.HK,D05.SI") as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers.get("content-type", "")
            first = next(iter(r.iter_lines()))
            assert first == "event: news_open"

    def test_it_subscribes_to_the_bus_rather_than_polling(self):
        import inspect
        from app.backend.routes import analysis
        src = inspect.getsource(analysis.stream_news)
        assert "news_bus.iter_items" in src
        # The page already rendered its snapshot from the store, so the
        # subscription must not replay the buffer — it would show the same
        # headlines twice. Asserted on the CALL, not on the prose: the
        # docstring explains this and would match a naive text search.
        assert "replay=True" not in src

    def test_no_tickers_and_an_empty_watchlist_is_a_400(self, monkeypatch):
        from app.backend.services import news_ingest
        monkeypatch.setattr(news_ingest, "watchlist_tickers",
                            lambda user_id=None: [])
        assert _client().get("/analysis/news/stream?tickers=").status_code == 400

    def test_the_keepalive_is_a_comment_frame(self):
        """Comment frames carry no event:/data: lines, so a client parser
        ignores them. They exist because edge proxies sever an idle SSE
        connection, and a news stream is idle by nature."""
        import inspect
        from app.backend.routes import analysis
        src = inspect.getsource(analysis.stream_news)
        assert '": keep-alive' in src
        assert "_NEWS_KEEPALIVE_S" in src

    def test_the_stream_has_a_deadline(self):
        """A phone that locks mid-stream leaves the server holding a reader
        nobody will read; the client reconnects when it wakes."""
        from app.backend.routes import analysis
        assert analysis._NEWS_STREAM_DEADLINE_S > 0
