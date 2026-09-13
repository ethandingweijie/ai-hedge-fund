"""The frontend news-stream wiring, asserted at the source level.

There is no JS test runner in this repo, so these read the files. They are
narrow on purpose: each pins a decision that is easy to undo by accident and
whose failure is silent in the browser.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / "app" / "frontend" / "src" / "hooks" / "useNewsStream.ts"
PANEL = ROOT / "app" / "frontend" / "src" / "components" / "report" / "NewsPanel.tsx"
PAGE = ROOT / "app" / "frontend" / "src" / "pages" / "NewsPage.tsx"
MENU = ROOT / "app" / "frontend" / "src" / "pages" / "MenuPage.tsx"


class TestHook:
    def test_it_does_not_use_eventsource(self):
        """EventSource cannot send an Authorization header (auth-fetch.ts) and
        every route here is behind a bearer token."""
        src = HOOK.read_text(encoding="utf-8")
        # Asserted on CONSTRUCTION, not on the word: the docstring explains why
        # EventSource is unusable here and would match a naive text search.
        assert "new EventSource" not in src
        assert "getReader()" in src

    def test_it_reconnects_when_the_tab_becomes_visible(self):
        """iOS Safari kills a stream on screen lock; active-run-context
        documents the same workaround for pipeline runs."""
        src = HOOK.read_text(encoding="utf-8")
        assert "visibilitychange" in src
        assert "removeEventListener" in src, "a listener left behind leaks"

    def test_a_reconnect_triggers_a_resync(self):
        """Items published while the connection was down were never delivered
        and the endpoint does not replay, so the caller must re-read."""
        src = HOOK.read_text(encoding="utf-8")
        assert "onResync" in src

    def test_an_empty_ticker_list_subscribes_to_nothing(self):
        """Empty must mean 'skip', not 'everything' — the backend would fall
        back to the whole watchlist and the page would stream tickers it is
        not showing."""
        src = HOOK.read_text(encoding="utf-8")
        assert "!key) return" in src.replace(" ", "") or "!key)return" in src.replace(" ", "")

    def test_callbacks_are_held_in_refs(self):
        """Otherwise a new callback identity on each parent render tears the
        stream down and rebuilds it."""
        src = HOOK.read_text(encoding="utf-8")
        assert "onItemRef" in src


class TestConsumers:
    def test_both_consumers_subscribe(self):
        for path in (PANEL, PAGE):
            assert "useNewsStream" in path.read_text(encoding="utf-8"), path.name

    def test_both_deduplicate_pushed_items(self):
        """The snapshot and the stream can carry the same item when a poll
        lands between them; the store id is the same either way."""
        for path in (PANEL, PAGE):
            src = path.read_text(encoding="utf-8")
            assert "prev.some" in src, path.name

    def test_the_panel_resyncs_quietly(self):
        """A reconnect must not flash the panel back to a Loading state."""
        assert "load(true)" in PANEL.read_text(encoding="utf-8")


class TestMobileNav:
    def test_watchlist_and_news_are_in_the_mobile_menu(self):
        """The desktop sidebar renders NAV_ITEMS; MenuPage and FloatingNavBar
        each carry their own list. Watchlist was reachable on mobile only by
        typing the URL."""
        src = MENU.read_text(encoding="utf-8")
        assert "byPath('/watchlist')" in src
        assert "byPath('/news')" in src

    def test_they_are_looked_up_by_path_not_redefined(self):
        """byPath keeps them tied to the same nav definitions the sidebar
        uses, rather than becoming a third list to keep in sync."""
        src = MENU.read_text(encoding="utf-8")
        assert 'label="Watchlist"' in src and "byPath('/watchlist')" in src
