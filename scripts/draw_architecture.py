"""One-glance architecture of the AI Hedge Fund system as of 2026-10-03 (v2.23.0). Matplotlib, monochrome
with one accent, exported as PNG (for the reference document and the chat) and SVG."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = "docs/architecture/architecture_v2_23_0"

INK, MID, PALE, ACC, ACCF, BG = "#111111", "#555555", "#f2f2f2", "#1f4e79", "#e8eef5", "#ffffff"
fig, ax = plt.subplots(figsize=(22, 14.5), dpi=150)
ax.set_xlim(0, 220); ax.set_ylim(-3, 143); ax.axis("off"); fig.patch.set_facecolor(BG)


def box(x, y, w, h, title, lines=(), fill=PALE, edge=INK, lw=1.0, tsize=10.5, lsize=8.2):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.2", fc=fill, ec=edge, lw=lw))
    ax.text(x + 1.6, y + h - 2.0, title, fontsize=tsize, fontweight="bold", color=INK, va="top", ha="left")
    for i, ln in enumerate(lines):
        ax.text(x + 1.6, y + h - 5.3 - i * 2.9, ln, fontsize=lsize, color=INK, va="top", ha="left")


def band(x, y, w, h, label):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.5", fc="none", ec=MID, lw=0.8, ls=(0, (3, 2))))
    ax.text(x + 1.5, y + h - 0.9, label, fontsize=9.5, color=MID, va="top", ha="left", fontweight="bold")


def arrow(x1, y1, x2, y2, color=INK, lw=1.3, style="-|>"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=12, color=color, lw=lw))


ax.text(2, 142, "AI Hedge Fund — system architecture at one glance (v2.23.0, 3 October 2026)", fontsize=16, fontweight="bold", color=INK, va="top")
ax.text(2, 138, "One run of a ticker flows left to right through the pipeline; every surface reads the one stored run; the loops at the bottom feed the next run.",
        fontsize=10, color=MID, va="top")

# ── INPUTS ──
band(2, 116, 216, 18.5, "INPUTS")
yi, hi = 117.2, 13.5
box(4, yi, 36, hi, "Market & filings", ["FMP statements, estimates, prices (7 markets)", "EDGAR / HKEXnews filings; FX rates"])
box(42, yi, 40, hi, "Web research", ["DashScope qwen3.8-flash, native search", "Sections 2A–2G, length-budgeted"])
box(84, yi, 42, hi, "Owner-set configuration", ["145 profiles → legs and weights; 7 market multiples", "valuation_constants (PROPOSED until accepted)"])
box(128, yi, 44, hi, "Review-gated inputs", ["industry_inputs: PV-10, backlog, SOTP, NAV, PPA", "Gemini pre-fills, cited; accepted on Model Accuracy"])
box(174, yi, 42, hi, "Memory", ["Research archive and freshness (fast path)", "Assumption ledger R1–R3, prior-run recap"])

# ── PIPELINE ──
band(2, 56, 216, 57, "THE PIPELINE   (FastAPI web service → queue → worker on Railway)")
y2, h2 = 82, 27
box(4, y2, 25, h2, "1–2.9  Front block", ["Archive and freshness", "Macro regime", "Strategic router:", "  profile + raw financials", "Intelligence signals", "Filings resolver"])
box(31, y2, 30, h2, "3  Deep research", ["Industry Intelligence Brief", "  8–12 bullets, [n] footnotes", "2G GUIDANCE_BLOCK", "Extractors: guidance_estimates", "  (thinking), family_metrics,", "  bank / REIT / SaaS KPIs"])
box(63, y2, 32, h2, "4  Guidance → forecast", ["bear / base / bull FY+1, FY+2,", "  medium-term target", "Five principles: deconstruct,", "  archetype curve, 3 statements,", "  fade and terminal, invariants", "9-step trace published"], fill=ACCF, edge=ACC)
box(97, y2, 40, h2, "4.5  Valuation engine", ["DCF on the forecast's growth and", "  FCF-margin schedules (unlevered)", "Forward P/E and EV/EBITDA on the", "  estimates, consensus kept beside", "Banks: GGM / RI / P/TBV read the", "  earnings-and-capital model", "PPA project-finance DCF; SOTP; NAV"], fill=ACCF, edge=ACC)
box(139, y2, 36, h2, "Statements & checks", ["3-statement forecast FY+1E..FY+5E:", "  IS → schedules → CF → BS", "Bank / insurer earnings-and-capital", "  model (CET1 held at target)", "5-assertion reconciliation suite;", "  a failure withholds the output"])
box(177, y2, 39, h2, "5–9  Decision", ["Scenarios bear / base / bull, probabilities", "Sector card, power law, value trap", "Risk manager", "PM: 300-word institutional rationale,", "  18-word quantified headline", "Post-trade review"])
box(97, 59, 78, 17, "Blend & target", ["Legs at the profile's weights, no display-only figures  ·  IV = Σ w × leg", "Target = spot + 0.5 × (IV − spot) per scenario  ·  12m PT probability-weighted", "Every leg traced with its inputs, basis and gate evaluations"], fill=ACCF, edge=ACC)
box(4, 59, 91, 17, "Archive of the run", ["web_runs.full_result_json: the one stored run every surface reads", "ticker_signals / agent_signals; phase durations; progress log; citation registry"])
for x1, x2 in ((29, 31), (61, 63), (95, 97), (137, 139), (175, 177)):
    arrow(x1, y2 + 13.5, x2, y2 + 13.5)
arrow(117, y2, 117, 76)
arrow(196, y2, 196, 67.5); arrow(196, 67.5, 175.5, 67.5)
arrow(97, 67.5, 95.5, 67.5)
for x in (22, 62, 105, 150, 195):
    arrow(x, 116, x, 109.5, color=MID, lw=0.9)

# ── SURFACES ──
band(2, 27, 216, 26.5, "SURFACES   (all built from the stored run at read time; a saved estimate override is applied on read)")
ys, hs = 28.2, 21.5
box(4, ys, 54, hs, "Web report (desktop and mobile)", ["Rating pin; 18-word headline; valuation ladder", "Guidance → estimates card with the 9-step trace", "Estimate workbench: edit, recompute, save, revert", "Ask the agent: answers from the trace, proposes only", "SOTP card; progress header names the running phase"])
box(60, ys, 48, hs, "PDF (reportlab)", ["Valuation summary and guidance forecast block", "Financial Statements page FY2022 → FY2030E", "  in one table set, reconciliation suite beneath", "Rationale and headline; references on the last page"])
box(110, ys, 52, hs, "Excel workbook (openpyxl, live formulas)", ["Summary; IS / BS / CFS with FY+1E..FY+5E linked", "  to the Model tab (4 blocks + suite)", "WACC; DCF bridge with minorities and preferreds;", "  Multiples, Comps, SOTP, Guidance, Family, Target"])
box(164, ys, 52, hs, "Model Accuracy page", ["Accept / revoke review-gated inputs", "Carried estimate overrides, latest per ticker", "Dynamic multiples log; calibration promote / rollback", "Outcome scoring on the agent's estimates only"])
for x in (31, 84, 136, 190):
    arrow(x, 56, x, 50.5, color=MID, lw=0.9)

# ── CLOSED LOOPS ──
band(2, 1, 216, 24, "CLOSED LOOPS   (what the next run inherits)")
yl, hl = 2.2, 19.5
box(4, yl, 42, hl, "Estimate overrides", ["User disagrees → the same engine recomputes", "Saved per run; carried into the next run", "Agent proposes; only the user saves; revocable"])
box(48, yl, 40, hl, "Review gate", ["Inputs and SOTP memory accepted by the owner", "A changed entry needs a new acceptance", "Nothing un-accepted prices a leg"])
box(90, yl, 40, hl, "Calibration & constants", ["Owner-set constants; quarterly clock, inertia band", "Calibration proposes; only --accept records", "New constants ship PROPOSED with a derivation"])
box(132, yl, 40, hl, "Golden harness & pins", ["17 fixtures replayed offline on every change", "Pins restate each methodology move, with reason", "Snapshots hold values and flag text"])
box(174, yl, 42, hl, "Accuracy & memory", ["Outcomes score matured targets (agent only)", "Prior-run recap, freshness delta, steward", "Research reused under 14 days (fast path)"])
for x in (25, 68, 110, 152, 195):
    arrow(x, 27, x, 22.3, color=MID, lw=0.9, style="<|-")

ax.text(2, -0.6, "Infrastructure: Vercel (React / Vite) · Railway web (FastAPI) and worker (queue; 65-min dedupe lock; 30-min enqueue expiry) · Postgres (web_runs, ticker_signals, "
        "estimate_overrides, industry_input_reviews, valuation_outcomes) · Redis", fontsize=8, color=MID, va="top")
ax.text(2, -2.4, "Models: Anthropic (portfolio manager, extractors) · DashScope qwen3.8-flash (research) · DeepSeek (estimate agent, what-if) · Gemini (review-gate pre-fills)",
        fontsize=8, color=MID, va="top")

fig.savefig(OUT + ".png", dpi=150, bbox_inches="tight", facecolor=BG)
fig.savefig(OUT + ".svg", bbox_inches="tight", facecolor=BG)
print("saved", OUT + ".png")
