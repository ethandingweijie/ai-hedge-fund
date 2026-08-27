import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const type = searchParams.get("type") ?? "stock"; // "etf" or "stock"
  const data = searchParams.get("data");

  if (!data) {
    return NextResponse.json({ error: "No data provided" }, { status: 400 });
  }

  try {
    const items = JSON.parse(data) as {
      ticker: string;
      name: string;
      sector: string;
      region: string;
      allocationPercent: number;
      amount: number;
      shares: number;
      price: number;
      category?: string;
      expenseRatio?: number;
      riskLevel: string;
    }[];

    const headers = [
      "Ticker",
      "Name",
      "Sector/Category",
      "Region",
      "Allocation %",
      "Amount ($)",
      "Shares",
      "Price ($)",
      type === "etf" ? "Expense Ratio" : "Risk Level",
    ];

    const rows = items.map((item) =>
      [
        item.ticker,
        `"${item.name}"`,
        item.category ?? item.sector,
        item.region,
        item.allocationPercent.toFixed(2),
        item.amount.toFixed(2),
        String(item.shares),
        item.price.toFixed(2),
        type === "etf"
          ? `${item.expenseRatio?.toFixed(2) ?? "N/A"}%`
          : item.riskLevel,
      ].join(",")
    );

    const csv = [headers.join(","), ...rows].join("\n");

    return new NextResponse(csv, {
      headers: {
        "Content-Type": "text/csv",
        "Content-Disposition": `attachment; filename="portfolio-${type}-${Date.now()}.csv"`,
      },
    });
  } catch {
    return NextResponse.json(
      { error: "Invalid data format" },
      { status: 400 }
    );
  }
}
