import { create } from "zustand";
import { adjustWeight } from "@/lib/sliderUtils";

export type RiskLevel = "conservative" | "moderate" | "aggressive";
export type TimeHorizon = "<3 years" | "3-7 years" | "7-15 years" | "15+ years";
export type InvestmentGoal =
  | "Capital Preservation"
  | "Steady Income"
  | "Balanced Growth"
  | "Maximum Growth";

export interface QuestionnaireAnswers {
  riskTolerance: RiskLevel;
  timeHorizon: TimeHorizon;
  investmentGoal: InvestmentGoal;
  industryPreferences: Record<string, number>; // sector name -> weight (0-100)
  geographyPreferences: Record<string, number>; // region name -> weight (0-100)
  investmentAmount: number;
}

export interface PortfolioResult {
  etfPortfolio: PortfolioItem[];
  stockPortfolio: PortfolioItem[];
  sectorBreakdown: Record<string, number>;
  geographyBreakdown: Record<string, number>;
  riskBreakdown: Record<string, number>;
  totalInvestment: number;
}

export interface PortfolioItem {
  ticker: string;
  name: string;
  category?: string;
  sector: string;
  region: string;
  allocationPercent: number;
  amount: number;
  shares: number;
  price: number;
  expenseRatio?: number;
  riskLevel: string;
}

const defaultAnswers: QuestionnaireAnswers = {
  riskTolerance: "moderate",
  timeHorizon: "7-15 years",
  investmentGoal: "Balanced Growth",
  industryPreferences: {
    Technology: 10,
    Healthcare: 10,
    Financials: 10,
    Consumer: 10,
    Energy: 10,
    Industrials: 10,
    "Real Estate": 10,
    Utilities: 10,
    Materials: 10,
    Communication: 10,
  },
  geographyPreferences: {
    US: 30,
    Europe: 25,
    "Asia Pacific": 20,
    "Emerging Markets": 10,
    "Global/International": 15,
  },
  investmentAmount: 10000,
};

interface QuestionnaireStore {
  answers: QuestionnaireAnswers;
  currentStep: number;
  selectedStrategyId: string | null;
  portfolio: PortfolioResult | null;
  isLoading: boolean;

  setAnswer: (key: keyof QuestionnaireAnswers, value: unknown) => void;
  setCurrentStep: (step: number) => void;
  setIndustryWeight: (sector: string, weight: number) => void;
  setGeographyWeight: (region: string, weight: number) => void;
  setSelectedStrategyId: (id: string | null) => void;
  loadFromStrategy: (answers: Partial<QuestionnaireAnswers>) => void;
  setPortfolio: (portfolio: PortfolioResult) => void;
  setIsLoading: (loading: boolean) => void;
  reset: () => void;
}

export const useQuestionnaireStore = create<QuestionnaireStore>((set) => ({
  answers: { ...defaultAnswers },
  currentStep: 0,
  selectedStrategyId: null,
  portfolio: null,
  isLoading: false,

  setAnswer: (key, value) =>
    set((state) => ({
      answers: { ...state.answers, [key]: value },
    })),

  setCurrentStep: (step) => set({ currentStep: step }),

  setIndustryWeight: (sector, weight) =>
    set((state) => {
      const updated = adjustWeight(state.answers.industryPreferences, sector, weight);
      return {
        answers: {
          ...state.answers,
          industryPreferences: updated,
        },
      };
    }),

  setGeographyWeight: (region, weight) =>
    set((state) => {
      const updated = adjustWeight(state.answers.geographyPreferences, region, weight);
      return {
        answers: {
          ...state.answers,
          geographyPreferences: updated,
        },
      };
    }),

  setSelectedStrategyId: (id) => set({ selectedStrategyId: id }),

  loadFromStrategy: (answers) =>
    set((state) => ({
      answers: { ...state.answers, ...answers },
      selectedStrategyId: null,
    })),

  setPortfolio: (portfolio) => set({ portfolio }),
  setIsLoading: (loading) => set({ isLoading: loading }),

  reset: () =>
    set({
      answers: { ...defaultAnswers },
      currentStep: 0,
      selectedStrategyId: null,
      portfolio: null,
      isLoading: false,
    }),
}));
