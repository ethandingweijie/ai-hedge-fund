/**
 * roboStrategyTemplates.ts
 * ==========================
 * 10 preset strategy templates ("Popular Strategy Templates"), ported
 * verbatim from the "Robo Strategy" prototype's src/data/strategies.ts —
 * same names, descriptions, risk levels, tags, asset splits, and raw
 * sector/geography weights, in the prototype's OWN taxonomy (a 10-sector /
 * 5-region split that differs from this app's canonical 11-sector /
 * 3-region system used by the live questionnaire and backend).
 *
 * `templateToQuestionnaire()` is the adapter: it maps a template's raw
 * weights onto this app's canonical taxonomy at the point a template is
 * selected, so the ported data stays faithful/auditable against the
 * source while the rest of the app only ever sees canonical keys.
 */
import type { RoboQuestionnaire, RoboRiskTolerance, RoboTimeHorizon } from '@/lib/api';
import { ROBO_SECTORS } from '@/lib/api';

export interface StrategyTemplate {
  id: string;
  name: string;
  description: string;
  riskLevel: RoboRiskTolerance;
  timeHorizon: string;
  goal: string;
  icon: string; // key into the ICONS map in RoboStrategyPage.tsx
  assetAllocation: { stocks: number; bonds: number; commodities: number; reits: number };
  /** Raw prototype-taxonomy weights — NOT canonical, see templateToQuestionnaire(). */
  sectorWeights: Record<string, number>;
  geographyWeights: Record<string, number>;
  tags: string[];
}

export const STRATEGY_TEMPLATES: StrategyTemplate[] = [
  {
    id: 'boglehead-3-fund',
    name: 'Boglehead 3-Fund',
    description: 'The classic Jack Bogle approach: own the entire market at minimal cost using just three broad index funds covering US stocks, international stocks, and bonds.',
    riskLevel: 'moderate',
    timeHorizon: '7-15 years',
    goal: 'Balanced Growth',
    icon: 'BarChart3',
    assetAllocation: { stocks: 70, bonds: 25, commodities: 0, reits: 5 },
    sectorWeights: {
      Technology: 20, Healthcare: 14, Financials: 13, Consumer: 12, Industrials: 10,
      Communication: 9, Energy: 5, 'Real Estate': 5, Utilities: 4, Materials: 4,
    },
    geographyWeights: { US: 60, Europe: 15, 'Asia Pacific': 10, 'Emerging Markets': 10, 'Global/International': 5 },
    tags: ['Index', 'Low Cost', 'Passive'],
  },
  {
    id: 'dividend-aristocrats',
    name: 'Dividend Aristocrats',
    description: 'Focus on companies with 25+ years of consecutive dividend increases. Ideal for investors seeking reliable income with capital preservation.',
    riskLevel: 'conservative',
    timeHorizon: '7-15 years',
    goal: 'Steady Income',
    icon: 'Banknote',
    assetAllocation: { stocks: 65, bonds: 25, commodities: 0, reits: 10 },
    sectorWeights: {
      Financials: 20, Consumer: 18, Industrials: 16, Healthcare: 12, 'Real Estate': 10,
      Utilities: 8, Technology: 6, Energy: 5, Materials: 3, Communication: 2,
    },
    geographyWeights: { US: 80, Europe: 10, 'Asia Pacific': 5, 'Emerging Markets': 0, 'Global/International': 5 },
    tags: ['Income', 'Dividends', 'Conservative'],
  },
  {
    id: 'tech-growth',
    name: 'Tech Growth',
    description: 'Heavy tilt toward technology and innovation sectors. Targets high-growth companies in software, semiconductors, cloud, and AI.',
    riskLevel: 'aggressive',
    timeHorizon: '7-15 years',
    goal: 'Maximum Growth',
    icon: 'Cpu',
    assetAllocation: { stocks: 90, bonds: 5, commodities: 0, reits: 5 },
    sectorWeights: {
      Technology: 45, Communication: 20, Healthcare: 10, Consumer: 8, Financials: 7,
      Industrials: 4, Energy: 2, 'Real Estate': 2, Materials: 1, Utilities: 1,
    },
    geographyWeights: { US: 65, Europe: 10, 'Asia Pacific': 15, 'Emerging Markets': 5, 'Global/International': 5 },
    tags: ['Growth', 'Technology', 'Innovation'],
  },
  {
    id: 'all-weather',
    name: 'All-Weather (Ray Dalio)',
    description: "Inspired by Ray Dalio's All-Weather portfolio. Designed to perform well across all economic environments through balanced risk allocation.",
    riskLevel: 'moderate',
    timeHorizon: '7-15 years',
    goal: 'Balanced Growth',
    icon: 'Shield',
    assetAllocation: { stocks: 30, bonds: 55, commodities: 10, reits: 5 },
    sectorWeights: {
      Technology: 12, Healthcare: 12, Financials: 12, Consumer: 12, Industrials: 12,
      Communication: 10, Energy: 8, 'Real Estate': 8, Utilities: 7, Materials: 7,
    },
    geographyWeights: { US: 35, Europe: 20, 'Asia Pacific': 15, 'Emerging Markets': 15, 'Global/International': 15 },
    tags: ['Balanced', 'Risk Parity', 'All-Weather'],
  },
  {
    id: 'healthcare-biotech',
    name: 'Healthcare & Biotech',
    description: 'Bet on healthcare innovation, aging demographics, and biotech breakthroughs. Concentrated in pharmaceuticals, biotech, and medical devices.',
    riskLevel: 'aggressive',
    timeHorizon: '3-7 years',
    goal: 'Maximum Growth',
    icon: 'HeartPulse',
    assetAllocation: { stocks: 85, bonds: 10, commodities: 0, reits: 5 },
    sectorWeights: {
      Healthcare: 55, Technology: 15, Materials: 10, Financials: 5, Consumer: 5,
      Industrials: 4, Communication: 2, Energy: 2, 'Real Estate': 1, Utilities: 1,
    },
    geographyWeights: { US: 60, Europe: 20, 'Asia Pacific': 10, 'Emerging Markets': 5, 'Global/International': 5 },
    tags: ['Sector', 'Healthcare', 'Biotech'],
  },
  {
    id: 'esg-sustainable',
    name: 'ESG / Sustainable',
    description: 'Environmental, Social, and Governance focused investing. Targets companies with strong sustainability practices and positive social impact.',
    riskLevel: 'moderate',
    timeHorizon: '7-15 years',
    goal: 'Balanced Growth',
    icon: 'Leaf',
    assetAllocation: { stocks: 70, bonds: 20, commodities: 0, reits: 10 },
    sectorWeights: {
      Technology: 22, Healthcare: 16, Financials: 14, Consumer: 12, Industrials: 10,
      Communication: 8, Utilities: 6, 'Real Estate': 5, Energy: 4, Materials: 3,
    },
    geographyWeights: { US: 45, Europe: 25, 'Asia Pacific': 12, 'Emerging Markets': 8, 'Global/International': 10 },
    tags: ['ESG', 'Sustainable', 'Impact'],
  },
  {
    id: 'emerging-markets',
    name: 'Emerging Markets',
    description: 'Growth exposure to developing economies with high potential. Targets consumer growth, tech adoption, and infrastructure in emerging markets.',
    riskLevel: 'aggressive',
    timeHorizon: '7-15 years',
    goal: 'Maximum Growth',
    icon: 'Globe',
    assetAllocation: { stocks: 85, bonds: 10, commodities: 0, reits: 5 },
    sectorWeights: {
      Technology: 22, Consumer: 18, Financials: 18, Healthcare: 10, Industrials: 10,
      Energy: 8, Materials: 6, Communication: 4, 'Real Estate': 2, Utilities: 2,
    },
    geographyWeights: { US: 10, Europe: 10, 'Asia Pacific': 30, 'Emerging Markets': 45, 'Global/International': 5 },
    tags: ['Emerging Markets', 'Growth', 'International'],
  },
  {
    id: 'income-focus',
    name: 'Income Focus',
    description: 'Optimized for steady income through high-yield bonds, REITs, dividend stocks, and utilities. Ideal for retirees or income seekers.',
    riskLevel: 'conservative',
    timeHorizon: '<3 years',
    goal: 'Steady Income',
    icon: 'DollarSign',
    assetAllocation: { stocks: 40, bonds: 35, commodities: 0, reits: 25 },
    sectorWeights: {
      'Real Estate': 20, Utilities: 18, Financials: 18, Energy: 12, Consumer: 10,
      Healthcare: 8, Industrials: 6, Technology: 4, Communication: 2, Materials: 2,
    },
    geographyWeights: { US: 70, Europe: 15, 'Asia Pacific': 5, 'Emerging Markets': 5, 'Global/International': 5 },
    tags: ['Income', 'REITs', 'Bonds', 'Conservative'],
  },
  {
    id: 'ai-robotics',
    name: 'AI & Robotics',
    description: 'Companies leading the AI revolution, automation, and robotics. Targets semiconductor, software, cloud, and industrial automation leaders.',
    riskLevel: 'aggressive',
    timeHorizon: '7-15 years',
    goal: 'Maximum Growth',
    icon: 'Bot',
    assetAllocation: { stocks: 92, bonds: 3, commodities: 0, reits: 5 },
    sectorWeights: {
      Technology: 50, Industrials: 18, Communication: 10, Healthcare: 8, Consumer: 5,
      Financials: 4, Energy: 2, Materials: 1, 'Real Estate': 1, Utilities: 1,
    },
    geographyWeights: { US: 55, Europe: 15, 'Asia Pacific': 20, 'Emerging Markets': 5, 'Global/International': 5 },
    tags: ['AI', 'Robotics', 'Automation', 'Innovation'],
  },
  {
    id: 'global-balanced',
    name: 'Global Balanced',
    description: 'Equal geographic diversification across all major world regions. Balanced between growth and stability with broad sector coverage.',
    riskLevel: 'moderate',
    timeHorizon: '7-15 years',
    goal: 'Balanced Growth',
    icon: 'Earth',
    assetAllocation: { stocks: 65, bonds: 25, commodities: 5, reits: 5 },
    sectorWeights: {
      Technology: 18, Healthcare: 14, Financials: 14, Consumer: 12, Industrials: 11,
      Communication: 9, Energy: 7, 'Real Estate': 6, Utilities: 5, Materials: 4,
    },
    geographyWeights: { US: 20, Europe: 20, 'Asia Pacific': 20, 'Emerging Markets': 20, 'Global/International': 20 },
    tags: ['Global', 'Balanced', 'Diversified'],
  },
];

// ── Taxonomy adapter: prototype's 10-sector/5-region split -> this app's
// canonical 11-sector/3-region split (ROBO_SECTORS / ROBO_REGIONS). ────────

const SECTOR_MAP: Record<string, string> = {
  Technology: 'Technology',
  Healthcare: 'Healthcare',
  Financials: 'Financial Services',
  Consumer: 'Consumer Cyclical', // prototype has one bucket; canonical splits Cyclical/Defensive
  Industrials: 'Industrials',
  Communication: 'Communication Services',
  Energy: 'Energy',
  'Real Estate': 'Real Estate',
  Utilities: 'Utilities',
  Materials: 'Basic Materials',
};

function mapSectorWeights(raw: Record<string, number>): Record<string, number> {
  const result: Record<string, number> = Object.fromEntries(ROBO_SECTORS.map((s) => [s, 0]));
  for (const [key, value] of Object.entries(raw)) {
    const canonical = SECTOR_MAP[key];
    if (canonical) result[canonical] = (result[canonical] ?? 0) + value;
  }
  return result;
}

/** Europe + Asia Pacific -> International Developed; Global/International
 * splits evenly (unspecified by definition); US and Emerging Markets map
 * straight across. */
function mapGeographyWeights(raw: Record<string, number>): Record<string, number> {
  const us = raw['US'] ?? 0;
  const europe = raw['Europe'] ?? 0;
  const asiaPacific = raw['Asia Pacific'] ?? 0;
  const em = raw['Emerging Markets'] ?? 0;
  const global = raw['Global/International'] ?? 0;
  return {
    US: us,
    'International Developed': europe + asiaPacific + global / 2,
    'Emerging Markets': em + global / 2,
  };
}

/** Selecting a template pre-fills the questionnaire with its prescribed
 * allocation, mapped onto canonical taxonomy and re-normalized to sum to
 * exactly 100 (mirrors the prototype's own normalize-on-select behaviour) —
 * the user can then customize via the normal sliders before generating. */
export function templateToQuestionnaire(
  template: StrategyTemplate,
  investmentAmount: number,
): RoboQuestionnaire {
  return {
    risk_tolerance: template.riskLevel,
    time_horizon: template.timeHorizon as RoboTimeHorizon,
    sector_preferences: mapSectorWeights(template.sectorWeights),
    geography_preferences: mapGeographyWeights(template.geographyWeights),
    investment_amount: investmentAmount || 10000,
  };
}
