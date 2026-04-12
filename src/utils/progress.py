from datetime import datetime, timezone
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.style import Style
from rich.text import Text
from typing import Dict, Optional, Callable, List
import sys
import threading

# Thread-local storage so each pipeline thread can stamp its own run_id.
# progress.update_status() reads this and includes run_id in every handler call
# so that concurrent SSE handlers can filter to their own run's events.
_tl = threading.local()

console = Console()


class AgentProgress:
    """Manages progress tracking for multiple agents."""

    def set_run_id(self, run_id: str) -> None:
        """Tag the current thread with run_id so update_status can stamp events."""
        _tl.run_id = run_id

    def get_run_id(self) -> Optional[str]:
        """Return the run_id for the current thread (None in CLI / untagged threads)."""
        return getattr(_tl, "run_id", None)

    def __init__(self):
        self.agent_status: Dict[str, Dict[str, str]] = {}
        self.started = False
        self.update_handlers: List[Callable[[str, Optional[str], str], None]] = []
        # Live table is only used when running interactively (no SSE handlers registered)
        self._table: Optional[Table] = None
        self._live: Optional[Live] = None

    # ── Handler management ────────────────────────────────────────────────────

    def register_handler(self, handler: Callable[[str, Optional[str], str], None]):
        """Register a handler to be called when agent status updates."""
        self.update_handlers.append(handler)
        return handler  # Return handler to support use as decorator

    def unregister_handler(self, handler: Callable[[str, Optional[str], str], None]):
        """Unregister a previously registered handler."""
        if handler in self.update_handlers:
            self.update_handlers.remove(handler)

    # ── Interactive (CLI) lifecycle ───────────────────────────────────────────

    def start(self):
        """Start the Rich Live progress display (CLI only)."""
        if not self.started and not self.update_handlers:
            self._table = Table(show_header=False, box=None, padding=(0, 1))
            self._live = Live(self._table, console=console, refresh_per_second=4)
            self._live.start()
            self.started = True

    def stop(self):
        """Stop the Rich Live progress display."""
        if self.started and self._live:
            try:
                self._live.stop()
            except Exception:
                pass
            self.started = False
            self._live = None
            self._table = None

    # ── Core update ───────────────────────────────────────────────────────────

    def update_status(
        self,
        agent_name: str,
        ticker: Optional[str] = None,
        status: str = "",
        analysis: Optional[str] = None,
        partial_data: Optional[dict] = None,
    ):
        """Update the status of an agent.

        partial_data: optional dict of pipeline output to stream to the frontend
                      immediately (e.g. {"macro_regime": {...}}).  Handlers
                      receive it as the 6th positional argument.
        """
        if agent_name not in self.agent_status:
            self.agent_status[agent_name] = {"status": "", "ticker": None}

        if ticker:
            self.agent_status[agent_name]["ticker"] = ticker
        if status:
            self.agent_status[agent_name]["status"] = status
        if analysis:
            self.agent_status[agent_name]["analysis"] = analysis

        timestamp = datetime.now(timezone.utc).isoformat()
        self.agent_status[agent_name]["timestamp"] = timestamp

        # Stamp event with the calling thread's run_id (set via set_run_id()).
        # Handlers registered for a different run will receive this run_id and
        # can drop the event — preventing partial_data cross-contamination when
        # multiple pipeline runs are active concurrently.
        run_id = getattr(_tl, "run_id", None)

        # Notify all registered SSE handlers.
        # run_id is passed as 7th positional arg; legacy handlers that only
        # accept 5 args will receive it via *args / ignore it.  Handlers that
        # want run_id filtering should accept it as event_run_id=None.
        for handler in self.update_handlers:
            try:
                handler(agent_name, ticker, status, analysis, timestamp, partial_data, run_id)
            except TypeError:
                # Legacy handler with fewer positional parameters — call without run_id
                try:
                    handler(agent_name, ticker, status, analysis, timestamp, partial_data)
                except TypeError:
                    handler(agent_name, ticker, status, analysis, timestamp)

        # Only refresh the Rich live table when running interactively (no SSE handlers).
        # In server mode the handlers carry all updates to the frontend; printing
        # the entire accumulated table on every update would flood stdout with
        # duplicate "Phase X complete" lines for each investor agent that fires.
        if self.started and self._live and not self.update_handlers:
            self._refresh_display()
        elif not self.update_handlers:
            # CLI mode without Live started — plain one-line print
            self._plain_print(agent_name, ticker, status)

    # ── Display helpers ───────────────────────────────────────────────────────

    def get_all_status(self):
        """Get the current status of all agents as a dictionary."""
        return {
            agent_name: {
                "ticker": info["ticker"],
                "status": info["status"],
                "display_name": self._get_display_name(agent_name),
            }
            for agent_name, info in self.agent_status.items()
        }

    def _get_display_name(self, agent_name: str) -> str:
        """Convert agent_name to a display-friendly format."""
        return agent_name.replace("_agent", "").replace("_", " ").title()

    def _plain_print(self, agent_name: str, ticker: Optional[str], status: str):
        """Single-line print for non-interactive fallback."""
        agent_display = self._get_display_name(agent_name)
        tick_part = f"[{ticker}] " if ticker else ""
        print(f" ... {agent_display:<20} {tick_part}{status}", flush=True)

    def _refresh_display(self):
        """Rebuild the Rich Live table in-place (interactive/CLI mode only)."""
        if not self._table or not self._live:
            return

        # Replace the table object entirely to avoid stale row accumulation.
        # Rich Live holds a reference to the renderable; swapping it via
        # live.update() ensures the display always shows exactly one row per agent.
        new_table = Table(show_header=False, box=None, padding=(0, 1))
        new_table.add_column(width=100)

        def sort_key(item):
            name = item[0]
            if "risk_management" in name:
                return (2, name)
            if "portfolio_management" in name:
                return (3, name)
            return (1, name)

        for agent_name, info in sorted(self.agent_status.items(), key=sort_key):
            status = info["status"]
            ticker = info.get("ticker")

            if status.lower() == "done":
                style = Style(color="green", bold=True)
                symbol = "[+]"
            elif status.lower() == "error":
                style = Style(color="red", bold=True)
                symbol = "[!]"
            else:
                style = Style(color="yellow")
                symbol = "..."

            agent_display = self._get_display_name(agent_name)
            row_text = Text()
            row_text.append(f"{symbol} ", style=style)
            row_text.append(f"{agent_display:<20}", style=Style(bold=True))
            if ticker:
                row_text.append(f"[{ticker}] ", style=Style(color="cyan"))
            row_text.append(status, style=style)
            new_table.add_row(row_text)

        self._table = new_table
        self._live.update(new_table)


# Create a global instance
progress = AgentProgress()
