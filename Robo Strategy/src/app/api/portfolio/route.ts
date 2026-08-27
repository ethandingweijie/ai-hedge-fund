import { NextRequest, NextResponse } from "next/server";
import { generatePortfolio } from "@/lib/allocation";
import type { QuestionnaireAnswers } from "@/store/questionnaireStore";
import { getQuotes } from "@/lib/fmp";

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;

    // Parse query parameters into questionnaire answers
    const answers: QuestionnaireAnswers = {
      riskTolerance: (searchParams.get("risk") as QuestionnaireAnswers["riskTolerance"]) ?? "moderate",
      timeHorizon: (searchParams.get("horizon") as QuestionnaireAnswers["timeHorizon"]) ?? "7-15 years",
      investmentGoal: (searchParams.get("goal") as QuestionnaireAnswers["investmentGoal"]) ?? "Balanced Growth",
      industryPreferences: parseJSON(
        searchParams.get("sectors"),
        {
          Technology: 50,
          Healthcare: 50,
          Financials: 50,
          Consumer: 50,
          Energy: 50,
          Industrials: 50,
          "Real Estate": 50,
          Utilities: 50,
          Materials: 50,
          Communication: 50,
        }
      ),
      geographyPreferences: parseJSON(
        searchParams.get("regions"),
        {
          US: 60,
          Europe: 40,
          "Asia Pacific": 30,
          "Emerging Markets": 20,
          "Global/International": 30,
        }
      ),
      investmentAmount: Number(searchParams.get("amount") ?? 10000),
    };

    // Generate portfolio
    const portfolio = generatePortfolio(answers);

    // Try to fetch live prices from FMP
    const allTickers = [
      ...portfolio.etfPortfolio.map((i) => i.ticker),
      ...portfolio.stockPortfolio.map((i) => i.ticker),
    ];

    try {
      const quotes = await getQuotes(allTickers);

      // Update prices where available
      for (const item of portfolio.etfPortfolio) {
        if (quotes[item.ticker]) {
          const q = quotes[item.ticker];
          item.price = q.price;
          item.shares = Math.floor(item.amount / q.price);
        }
      }
      for (const item of portfolio.stockPortfolio) {
        if (quotes[item.ticker]) {
          const q = quotes[item.ticker];
          item.price = q.price;
          item.shares = Math.floor(item.amount / q.price);
        }
      }
    } catch {
      // FMP API unavailable, using mock prices
    }

    return NextResponse.json(portfolio);
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to generate portfolio", details: String(error) },
      { status: 500 }
    );
  }
}

function parseJSON<T>(value: string | null, fallback: T): T {
  if (!value) return fallback;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}
