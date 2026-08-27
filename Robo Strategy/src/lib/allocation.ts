import { etfUniverse } from "@/data/etfUniverse";
import { stockUniverse } from "@/data/stockUniverse";
import type {
  QuestionnaireAnswers,
  PortfolioResult,
  PortfolioItem,
} from "@/store/questionnaireStore";

// Map risk + time horizon to base stock/bond/commodity/reit split
function getBaseAllocation(
  risk: string,
  horizon: string
): { stocks: number; bonds: number; commodities: number; reits: number } {
  const riskMultiplier =
    risk === "aggressive" ? 1.0 : risk === "moderate" ? 0.7 : 0.4;

  const horizonBonus =
    horizon === "15+ years"
      ? 10
      : horizon === "7-15 years"
        ? 5
        : horizon === "3-7 years"
          ? 0
          : -10;

  let stocks = Math.round(Math.min(95, Math.max(20, riskMultiplier * 80 + horizonBonus)));
  let bonds = Math.round(Math.min(60, Math.max(0, (1 - riskMultiplier) * 45 - horizonBonus / 2)));
  let commodities = risk === "aggressive" ? 5 : risk === "moderate" ? 5 : 0;
  let reits = Math.max(0, 100 - stocks - bonds - commodities);

  // Normalize to 100
  const total = stocks + bonds + commodities + reits;
  return {
    stocks: Math.round((stocks / total) * 100),
    bonds: Math.round((bonds / total) * 100),
    commodities: Math.round((commodities / total) * 100),
    reits: Math.round((reits / total) * 100),
  };
}

// Score a sector based on user preferences
function scoreSector(
  sector: string,
  industryPrefs: Record<string, number>
): number {
  return industryPrefs[sector] ?? 50;
}

// Score a region based on user preferences
function scoreRegion(
  region: string,
  geoPrefs: Record<string, number>
): number {
  return geoPrefs[region] ?? 30;
}

// Normalize weights so they sum to 100
function normalizeWeights(weights: Record<string, number>): Record<string, number> {
  const total = Object.values(weights).reduce((s, v) => s + v, 0);
  if (total === 0) return weights;
  const normalized: Record<string, number> = {};
  for (const [k, v] of Object.entries(weights)) {
    normalized[k] = (v / total) * 100;
  }
  return normalized;
}

// Select ETFs based on target allocation
function selectETFs(
  baseAlloc: { stocks: number; bonds: number; commodities: number; reits: number },
  sectorWeights: Record<string, number>,
  geoWeights: Record<string, number>,
  totalAmount: number,
  _seed: number
): PortfolioItem[] {
  const selected: PortfolioItem[] = [];
  const maxETFs = 8;

  // Categorize ETFs
  const stockETFs = etfUniverse.filter(
    (e) => !["Bond", "Commodity"].includes(e.category)
  );
  const bondETFs = etfUniverse.filter((e) => e.category === "Bond");
  const commodityETFs = etfUniverse.filter((e) => e.category === "Commodity");
  const reitETFs = etfUniverse.filter((e) => e.category === "REIT");

  // Score each ETF
  const scoreETF = (etf: (typeof etfUniverse)[0]) => {
    let score = 0;
    score += scoreSector(etf.sector, sectorWeights) * 0.6;
    score += scoreRegion(etf.region, geoWeights) * 0.4;
    // Prefer lower expense ratios
    score -= etf.expenseRatio * 10;
    return score;
  };

  const allocateToCategory = (
    etfs: (typeof etfUniverse)[0][],
    targetPercent: number,
    count: number
  ) => {
    if (targetPercent <= 0 || etfs.length === 0) return;
    const sorted = [...etfs].sort((a, b) => scoreETF(b) - scoreETF(a));
    const picks = sorted.slice(0, count);
    const perPick = targetPercent / picks.length;

    for (const etf of picks) {
      const amount = (perPick / 100) * totalAmount;
      const mockPrice = getMockPrice(etf.ticker);
      selected.push({
        ticker: etf.ticker,
        name: etf.name,
        category: etf.category,
        sector: etf.sector,
        region: etf.region,
        allocationPercent: Math.round(perPick * 100) / 100,
        amount: Math.round(amount * 100) / 100,
        shares: Math.floor(amount / mockPrice),
        price: mockPrice,
        expenseRatio: etf.expenseRatio,
        riskLevel: etf.riskLevel,
      });
    }
  };

  // Distribute picks across categories
  const stockCount = Math.min(5, maxETFs - 3);
  const bondCount = baseAlloc.bonds > 0 ? 2 : 0;
  const commCount = baseAlloc.commodities > 0 ? 1 : 0;
  const reitCount = baseAlloc.reits > 0 ? 1 : 0;

  allocateToCategory(stockETFs, baseAlloc.stocks, stockCount);
  allocateToCategory(bondETFs, baseAlloc.bonds, bondCount);
  allocateToCategory(commodityETFs, baseAlloc.commodities, commCount);
  allocateToCategory(reitETFs, baseAlloc.reits, reitCount);

  return selected;
}

// Select individual stocks
function selectStocks(
  sectorWeights: Record<string, number>,
  geoWeights: Record<string, number>,
  risk: string,
  totalAmount: number,
  _seed: number
): PortfolioItem[] {
  const selected: PortfolioItem[] = [];
  const targetCount = risk === "conservative" ? 12 : risk === "moderate" ? 15 : 18;

  // Normalize sector weights
  const normSectors = normalizeWeights(sectorWeights);

  // For each sector, pick stocks
  const sectors = Object.entries(normSectors)
    .filter(([, w]) => w > 3)
    .sort((a, b) => b[1] - a[1]);

  let remainingPicks = targetCount;

  for (const [sector, sectorWeight] of sectors) {
    if (remainingPicks <= 0) break;
    const picksForSector = Math.max(1, Math.round((sectorWeight / 100) * targetCount));
    const actualPicks = Math.min(picksForSector, remainingPicks);

    // Get stocks in this sector
    const sectorStocks = stockUniverse.filter((s) => s.sector === sector);

    // Score each stock
    const scored = sectorStocks.map((stock) => {
      let score = scoreRegion(stock.region, geoWeights);
      // Risk preference
      if (risk === "conservative" && stock.riskLevel === "low") score += 20;
      if (risk === "aggressive" && stock.riskLevel === "high") score += 15;
      if (stock.marketCapTier === "mega") score += 10;
      return { stock, score };
    });

    scored.sort((a, b) => b.score - a.score);
    const picks = scored.slice(0, actualPicks);

    // Equal weight within sector
    const perPickWeight = sectorWeight / actualPicks;

    for (const { stock } of picks) {
      const amount = (perPickWeight / 100) * totalAmount;
      const mockPrice = getMockPrice(stock.ticker);
      selected.push({
        ticker: stock.ticker,
        name: stock.name,
        sector: stock.sector,
        region: stock.region,
        allocationPercent: Math.round(perPickWeight * 100) / 100,
        amount: Math.round(amount * 100) / 100,
        shares: Math.floor(amount / mockPrice),
        price: mockPrice,
        riskLevel: stock.riskLevel,
      });
    }

    remainingPicks -= actualPicks;
  }

  // Enforce max 10% per position
  const maxPerPosition = totalAmount * 0.1;
  for (const item of selected) {
    if (item.amount > maxPerPosition) {
      item.amount = Math.round(maxPerPosition * 100) / 100;
      item.allocationPercent = Math.round((item.amount / totalAmount) * 10000) / 100;
      item.shares = Math.floor(item.amount / item.price);
    }
  }

  return selected;
}

// Mock prices for display (will be replaced by FMP API in production)
function getMockPrice(ticker: string): number {
  const prices: Record<string, number> = {
    VTI: 265, VOO: 520, QQQ: 480, IWM: 210, SCHD: 82,
    XLK: 225, XLV: 145, XLF: 45, XLY: 195, XLE: 90,
    XLI: 130, XLRE: 42, XLU: 70, XLB: 88, XLC: 95,
    IBB: 130, ROBO: 68, VEA: 52, VWO: 42, EFA: 80,
    VPL: 72, EWJ: 68, INDA: 55, VXUS: 60, BND: 73,
    AGG: 98, TLT: 92, BNDX: 49, HYG: 77, GLD: 225,
    DBC: 23, VNQ: 88, VNQI: 42,
    AAPL: 220, MSFT: 440, GOOGL: 175, NVDA: 135, AMD: 165,
    INTC: 32, CRM: 280, ADBE: 520, ORCL: 175, AVGO: 180,
    TSM: 175, ASML: 750, SAP: 210, UNH: 530, JNJ: 158,
    LLY: 780, PFE: 28, ABBV: 180, MRK: 125, TMO: 580,
    NVO: 130, AZN: 72, JPM: 220, BAC: 42, "BRK.B": 450,
    V: 290, MA: 500, GS: 510, HSBC: 48, UBS: 32,
    AMZN: 195, TSLA: 260, HD: 380, MCD: 305, NKE: 80,
    SBUX: 95, "LVMH.PA": 720, TM: 185, XOM: 115, CVX: 165,
    SHEL: 68, TTE: 65, COP: 118, CAT: 370, HON: 215,
    GE: 175, BA: 180, UNP: 245, DE: 420, "SIE.DE": 180,
    PLD: 125, AMT: 210, CCI: 105, EQIX: 850, O: 58,
    NEE: 80, DUK: 105, SO: 85, D: 55, LIN: 460,
    APD: 290, SHW: 355, BHP: 62, RIO: 65, META: 510,
    NFLX: 680, DIS: 115, CMCSA: 42, T: 22, VZ: 42,
    TMUS: 180, PDD: 130, BABA: 85, NUE: 165, ITUB: 6,
    VALE: 12, WIT: 5, INFY: 18,
  };
  return prices[ticker] ?? 100;
}

// Calculate breakdowns from portfolio items
function calculateBreakdowns(items: PortfolioItem[]) {
  const sectorBreakdown: Record<string, number> = {};
  const geographyBreakdown: Record<string, number> = {};
  const riskBreakdown: Record<string, number> = {};

  for (const item of items) {
    sectorBreakdown[item.sector] =
      (sectorBreakdown[item.sector] ?? 0) + item.allocationPercent;
    geographyBreakdown[item.region] =
      (geographyBreakdown[item.region] ?? 0) + item.allocationPercent;
    riskBreakdown[item.riskLevel] =
      (riskBreakdown[item.riskLevel] ?? 0) + item.allocationPercent;
  }

  return { sectorBreakdown, geographyBreakdown, riskBreakdown };
}

export function generatePortfolio(
  answers: QuestionnaireAnswers
): PortfolioResult {
  const { riskTolerance, timeHorizon, industryPreferences, geographyPreferences, investmentAmount } = answers;

  // Step 1: Base allocation
  const baseAlloc = getBaseAllocation(riskTolerance, timeHorizon);

  // Step 2 & 3: Use user's sector/geo weights directly (already set by questionnaire or strategy)
  const sectorWeights = { ...industryPreferences };
  const geoWeights = { ...geographyPreferences };

  const seed = Date.now();

  // Step 4: Select securities
  const etfPortfolio = selectETFs(baseAlloc, sectorWeights, geoWeights, investmentAmount, seed);
  const stockPortfolio = selectStocks(sectorWeights, geoWeights, riskTolerance, investmentAmount, seed);

  // Step 5: Calculate breakdowns (from stock portfolio as primary)
  const { sectorBreakdown, geographyBreakdown, riskBreakdown } =
    calculateBreakdowns(stockPortfolio);

  return {
    etfPortfolio,
    stockPortfolio,
    sectorBreakdown,
    geographyBreakdown,
    riskBreakdown,
    totalInvestment: investmentAmount,
  };
}
