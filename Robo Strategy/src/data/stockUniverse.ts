export interface StockData {
  ticker: string;
  name: string;
  sector: string;
  region: string; // US, Europe, Asia Pacific, Emerging Markets
  marketCapTier: "mega" | "large" | "mid";
  riskLevel: "low" | "medium" | "high";
}

export const stockUniverse: StockData[] = [
  // --- Technology ---
  { ticker: "AAPL", name: "Apple Inc.", sector: "Technology", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "MSFT", name: "Microsoft Corp.", sector: "Technology", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "GOOGL", name: "Alphabet Inc.", sector: "Technology", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "NVDA", name: "NVIDIA Corp.", sector: "Technology", region: "US", marketCapTier: "mega", riskLevel: "high" },
  { ticker: "AMD", name: "Advanced Micro Devices", sector: "Technology", region: "US", marketCapTier: "large", riskLevel: "high" },
  { ticker: "INTC", name: "Intel Corp.", sector: "Technology", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "CRM", name: "Salesforce Inc.", sector: "Technology", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "ADBE", name: "Adobe Inc.", sector: "Technology", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "ORCL", name: "Oracle Corp.", sector: "Technology", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "AVGO", name: "Broadcom Inc.", sector: "Technology", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "TSM", name: "Taiwan Semiconductor", sector: "Technology", region: "Asia Pacific", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "ASML", name: "ASML Holding", sector: "Technology", region: "Europe", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "SAP", name: "SAP SE", sector: "Technology", region: "Europe", marketCapTier: "large", riskLevel: "medium" },

  // --- Healthcare ---
  { ticker: "UNH", name: "UnitedHealth Group", sector: "Healthcare", region: "US", marketCapTier: "mega", riskLevel: "low" },
  { ticker: "JNJ", name: "Johnson & Johnson", sector: "Healthcare", region: "US", marketCapTier: "mega", riskLevel: "low" },
  { ticker: "LLY", name: "Eli Lilly & Co.", sector: "Healthcare", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "PFE", name: "Pfizer Inc.", sector: "Healthcare", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "ABBV", name: "AbbVie Inc.", sector: "Healthcare", region: "US", marketCapTier: "mega", riskLevel: "low" },
  { ticker: "MRK", name: "Merck & Co.", sector: "Healthcare", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "TMO", name: "Thermo Fisher Scientific", sector: "Healthcare", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "NVO", name: "Novo Nordisk A/S", sector: "Healthcare", region: "Europe", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "AZN", name: "AstraZeneca PLC", sector: "Healthcare", region: "Europe", marketCapTier: "large", riskLevel: "medium" },

  // --- Financials ---
  { ticker: "JPM", name: "JPMorgan Chase", sector: "Financials", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "BAC", name: "Bank of America", sector: "Financials", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "BRK.B", name: "Berkshire Hathaway", sector: "Financials", region: "US", marketCapTier: "mega", riskLevel: "low" },
  { ticker: "V", name: "Visa Inc.", sector: "Financials", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "MA", name: "Mastercard Inc.", sector: "Financials", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "GS", name: "Goldman Sachs", sector: "Financials", region: "US", marketCapTier: "large", riskLevel: "high" },
  { ticker: "HSBC", name: "HSBC Holdings", sector: "Financials", region: "Europe", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "UBS", name: "UBS Group AG", sector: "Financials", region: "Europe", marketCapTier: "large", riskLevel: "medium" },

  // --- Consumer Discretionary ---
  { ticker: "AMZN", name: "Amazon.com Inc.", sector: "Consumer", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "TSLA", name: "Tesla Inc.", sector: "Consumer", region: "US", marketCapTier: "mega", riskLevel: "high" },
  { ticker: "HD", name: "Home Depot Inc.", sector: "Consumer", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "MCD", name: "McDonald's Corp.", sector: "Consumer", region: "US", marketCapTier: "mega", riskLevel: "low" },
  { ticker: "NKE", name: "Nike Inc.", sector: "Consumer", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "SBUX", name: "Starbucks Corp.", sector: "Consumer", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "LVMH.PA", name: "LVMH Moet Hennessy", sector: "Consumer", region: "Europe", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "TM", name: "Toyota Motor Corp.", sector: "Consumer", region: "Asia Pacific", marketCapTier: "mega", riskLevel: "low" },

  // --- Energy ---
  { ticker: "XOM", name: "Exxon Mobil Corp.", sector: "Energy", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "CVX", name: "Chevron Corp.", sector: "Energy", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "SHEL", name: "Shell PLC", sector: "Energy", region: "Europe", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "TTE", name: "TotalEnergies SE", sector: "Energy", region: "Europe", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "COP", name: "ConocoPhillips", sector: "Energy", region: "US", marketCapTier: "large", riskLevel: "high" },

  // --- Industrials ---
  { ticker: "CAT", name: "Caterpillar Inc.", sector: "Industrials", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "HON", name: "Honeywell International", sector: "Industrials", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "GE", name: "GE Aerospace", sector: "Industrials", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "BA", name: "Boeing Co.", sector: "Industrials", region: "US", marketCapTier: "large", riskLevel: "high" },
  { ticker: "UNP", name: "Union Pacific Corp.", sector: "Industrials", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "DE", name: "Deere & Co.", sector: "Industrials", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "SIE.DE", name: "Siemens AG", sector: "Industrials", region: "Europe", marketCapTier: "large", riskLevel: "medium" },

  // --- Real Estate ---
  { ticker: "PLD", name: "Prologis Inc.", sector: "Real Estate", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "AMT", name: "American Tower Corp.", sector: "Real Estate", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "CCI", name: "Crown Castle Inc.", sector: "Real Estate", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "EQIX", name: "Equinix Inc.", sector: "Real Estate", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "O", name: "Realty Income Corp.", sector: "Real Estate", region: "US", marketCapTier: "large", riskLevel: "low" },

  // --- Utilities ---
  { ticker: "NEE", name: "NextEra Energy", sector: "Utilities", region: "US", marketCapTier: "mega", riskLevel: "low" },
  { ticker: "DUK", name: "Duke Energy Corp.", sector: "Utilities", region: "US", marketCapTier: "large", riskLevel: "low" },
  { ticker: "SO", name: "Southern Company", sector: "Utilities", region: "US", marketCapTier: "large", riskLevel: "low" },
  { ticker: "D", name: "Dominion Energy", sector: "Utilities", region: "US", marketCapTier: "mid", riskLevel: "low" },

  // --- Materials ---
  { ticker: "LIN", name: "Linde PLC", sector: "Materials", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "APD", name: "Air Products & Chemicals", sector: "Materials", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "SHW", name: "Sherwin-Williams Co.", sector: "Materials", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "BHP", name: "BHP Group", sector: "Materials", region: "Emerging Markets", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "RIO", name: "Rio Tinto Group", sector: "Materials", region: "Europe", marketCapTier: "large", riskLevel: "medium" },

  // --- Communication ---
  { ticker: "META", name: "Meta Platforms Inc.", sector: "Communication", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "NFLX", name: "Netflix Inc.", sector: "Communication", region: "US", marketCapTier: "mega", riskLevel: "high" },
  { ticker: "DIS", name: "Walt Disney Co.", sector: "Communication", region: "US", marketCapTier: "mega", riskLevel: "medium" },
  { ticker: "CMCSA", name: "Comcast Corp.", sector: "Communication", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "T", name: "AT&T Inc.", sector: "Communication", region: "US", marketCapTier: "large", riskLevel: "low" },
  { ticker: "VZ", name: "Verizon Communications", sector: "Communication", region: "US", marketCapTier: "large", riskLevel: "low" },
  { ticker: "TMUS", name: "T-Mobile US Inc.", sector: "Communication", region: "US", marketCapTier: "mega", riskLevel: "medium" },

  // --- Emerging Markets ---
  { ticker: "PDD", name: "PDD Holdings Inc.", sector: "Technology", region: "Emerging Markets", marketCapTier: "large", riskLevel: "high" },
  { ticker: "BABA", name: "Alibaba Group", sector: "Technology", region: "Emerging Markets", marketCapTier: "mega", riskLevel: "high" },
  { ticker: "NUE", name: "Nucor Corp.", sector: "Materials", region: "US", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "ITUB", name: "Itau Unibanco Holding", sector: "Financials", region: "Emerging Markets", marketCapTier: "large", riskLevel: "high" },
  { ticker: "VALE", name: "Vale S.A.", sector: "Materials", region: "Emerging Markets", marketCapTier: "large", riskLevel: "high" },
  { ticker: "WIT", name: "Wipro Ltd.", sector: "Technology", region: "Emerging Markets", marketCapTier: "large", riskLevel: "medium" },
  { ticker: "INFY", name: "Infosys Ltd.", sector: "Technology", region: "Emerging Markets", marketCapTier: "large", riskLevel: "medium" },
];
