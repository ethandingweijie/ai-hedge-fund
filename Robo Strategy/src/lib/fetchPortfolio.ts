import type { QuestionnaireAnswers, PortfolioResult } from "@/store/questionnaireStore";

/**
 * Fetches a portfolio from the /api/portfolio endpoint, which enriches
 * the allocation engine's output with live FMP prices when available.
 * Falls back to the client-side generatePortfolio() if the API is unreachable.
 */
export async function fetchPortfolio(
  answers: QuestionnaireAnswers,
): Promise<PortfolioResult> {
  const params = new URLSearchParams({
    risk: answers.riskTolerance,
    horizon: answers.timeHorizon,
    goal: answers.investmentGoal,
    sectors: JSON.stringify(answers.industryPreferences),
    regions: JSON.stringify(answers.geographyPreferences),
    amount: String(answers.investmentAmount),
  });

  try {
    const res = await fetch(`/api/portfolio?${params.toString()}`);
    if (!res.ok) throw new Error(`API returned ${res.status}`);
    return (await res.json()) as PortfolioResult;
  } catch (err) {
    // Fallback: use client-side generation with mock prices
    console.warn("FMP API unavailable, falling back to mock prices:", err);
    const { generatePortfolio } = await import("@/lib/allocation");
    return generatePortfolio(answers);
  }
}
