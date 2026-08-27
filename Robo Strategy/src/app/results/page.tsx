"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/theme-toggle";
import { useQuestionnaireStore } from "@/store/questionnaireStore";
import { fetchPortfolio } from "@/lib/fetchPortfolio";
import type { PortfolioItem, PortfolioResult } from "@/store/questionnaireStore";
import {
  TrendingUp,
  RotateCcw,
  Download,
  ClipboardList,
  Layers,
  PieChart as PieChartIcon,
} from "lucide-react";
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";

const COLORS = [
  "#9FE870",
  "#163300",
  "#0B4C72",
  "#E2F6D5",
  "#454745",
  "#2E8B57",
  "#4DA3E0",
  "#8DD460",
  "#6B7280",
  "#1E3A14",
];

export default function ResultsPage() {
  const store = useQuestionnaireStore();
  const portfolio = store.portfolio;

  const handleRegenerate = async () => {
    store.setIsLoading(true);
    try {
      const newPortfolio = await fetchPortfolio(store.answers);
      store.setPortfolio(newPortfolio);
    } finally {
      store.setIsLoading(false);
    }
  };

  const handleExportCSV = (type: "etf" | "stock") => {
    if (!portfolio) return;
    const items = type === "etf" ? portfolio.etfPortfolio : portfolio.stockPortfolio;
    const headers = [
      "Ticker", "Name", "Sector", "Region", "Allocation %", "Amount ($)", "Shares", "Price ($)",
    ];
    const rows = items.map((i: PortfolioItem) => [
      i.ticker,
      `"${i.name}"`,
      i.sector,
      i.region,
      i.allocationPercent.toFixed(2),
      i.amount.toFixed(2),
      i.shares,
      i.price.toFixed(2),
    ]);
    const csv = [headers.join(","), ...rows.map((r) => r.map(String).join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `portfolio-${type}-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!portfolio) {
    return (
      <div className="flex flex-col min-h-screen items-center justify-center">
        <p className="text-muted-foreground font-medium">
          No portfolio generated yet. Complete the questionnaire or select a strategy.
        </p>
        <Link href="/questionnaire" className="mt-4">
          <Button>Go to Questionnaire</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-screen">
      {/* Header */}
      <header className="border-b bg-background/95 backdrop-blur sticky top-0 z-50">
        <div className="container mx-auto flex h-[76px] items-center justify-between px-6 lg:px-16">
          <Link href="/" className="flex items-center gap-3">
            <TrendingUp className="h-7 w-7 text-foreground" strokeWidth={2.5} />
            <span className="text-xl font-extrabold tracking-tight">Robo Strategy</span>
          </Link>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 container mx-auto px-6 lg:px-16 py-10 max-w-7xl">
        {/* Page Title */}
        <div className="mb-8">
          <h1 className="heading-xl text-3xl md:text-4xl">Your Portfolio</h1>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          {[
            { label: "Total Investment", value: `$${portfolio.totalInvestment.toLocaleString()}` },
            { label: "ETF Holdings", value: portfolio.etfPortfolio.length },
            { label: "Stock Holdings", value: portfolio.stockPortfolio.length },
            { label: "Sectors", value: Object.keys(portfolio.sectorBreakdown).length },
          ].map((card) => (
            <Card key={card.label} className="rounded-2xl border-0 shadow-sm bg-muted/40">
              <CardContent className="pt-6">
                <p className="text-xs text-muted-foreground font-semibold uppercase tracking-wide">
                  {card.label}
                </p>
                <p className="text-2xl font-extrabold mt-1">{card.value}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Action Buttons */}
        <div className="flex flex-wrap gap-3 mb-8">
          <Button variant="outline" onClick={handleRegenerate}>
            <RotateCcw className="mr-2 h-4 w-4" />
            Regenerate
          </Button>
          <Link href="/questionnaire">
            <Button variant="outline">
              <ClipboardList className="mr-2 h-4 w-4" />
              Retake Questionnaire
            </Button>
          </Link>
          <Link href="/strategies">
            <Button variant="outline">
              <Layers className="mr-2 h-4 w-4" />
              Browse Strategies
            </Button>
          </Link>
        </div>

        {/* Portfolio Tabs */}
        <PortfolioTabs portfolio={portfolio} onExportCSV={handleExportCSV} />

        {/* Charts Section */}
        <div className="grid md:grid-cols-2 gap-6 mt-10">
          <Card className="rounded-2xl border-0 shadow-sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-lg font-extrabold">
                <PieChartIcon className="h-5 w-5 text-[#163300] dark:text-[#9FE870]" />
                Sector Breakdown
              </CardTitle>
            </CardHeader>
            <CardContent>
              <PieChartComponent data={portfolio.sectorBreakdown} />
            </CardContent>
          </Card>
          <Card className="rounded-2xl border-0 shadow-sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-lg font-extrabold">
                <PieChartIcon className="h-5 w-5 text-[#163300] dark:text-[#9FE870]" />
                Geography Breakdown
              </CardTitle>
            </CardHeader>
            <CardContent>
              <PieChartComponent data={portfolio.geographyBreakdown} />
            </CardContent>
          </Card>
        </div>

        {/* Risk Distribution */}
        <Card className="mt-6 rounded-2xl border-0 shadow-sm">
          <CardHeader>
            <CardTitle className="text-lg font-extrabold">Risk Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            <RiskChart data={portfolio.riskBreakdown} />
          </CardContent>
        </Card>
      </main>

      {/* Footer */}
      <footer className="border-t bg-muted/60 py-8">
        <div className="container mx-auto px-6 lg:px-16 text-center text-sm text-muted-foreground font-medium">
          Robo Strategy is for educational purposes only. Not financial advice.
        </div>
      </footer>
    </div>
  );
}

function PortfolioTabs({
  portfolio,
  onExportCSV,
}: {
  portfolio: PortfolioResult;
  onExportCSV: (type: "etf" | "stock") => void;
}) {
  const [activeTab, setActiveTab] = useState<"etf" | "stock">("etf");

  return (
    <div>
      <div className="flex items-center gap-2 mb-5 flex-wrap">
        <Button
          variant={activeTab === "etf" ? "default" : "outline"}
          onClick={() => setActiveTab("etf")}
        >
          <Layers className="mr-2 h-4 w-4" />
          Diversified ETFs
        </Button>
        <Button
          variant={activeTab === "stock" ? "default" : "outline"}
          onClick={() => setActiveTab("stock")}
        >
          <TrendingUp className="mr-2 h-4 w-4" />
          Individual Stocks
        </Button>
        <div className="ml-auto">
          <Button variant="outline" onClick={() => onExportCSV(activeTab)}>
            <Download className="mr-2 h-4 w-4" />
            Export CSV
          </Button>
        </div>
      </div>

      {activeTab === "etf" ? (
        <ETFTable items={portfolio.etfPortfolio} />
      ) : (
        <StockTable items={portfolio.stockPortfolio} />
      )}
    </div>
  );
}

function ETFTable({ items }: { items: PortfolioItem[] }) {
  return (
    <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/40">
            <TableHead className="font-bold text-xs uppercase tracking-wider">Ticker</TableHead>
            <TableHead className="font-bold text-xs uppercase tracking-wider">Name</TableHead>
            <TableHead className="font-bold text-xs uppercase tracking-wider">Category</TableHead>
            <TableHead className="text-right font-bold text-xs uppercase tracking-wider">Allocation</TableHead>
            <TableHead className="text-right font-bold text-xs uppercase tracking-wider">Amount</TableHead>
            <TableHead className="text-right font-bold text-xs uppercase tracking-wider">Shares</TableHead>
            <TableHead className="text-right font-bold text-xs uppercase tracking-wider">Price</TableHead>
            <TableHead className="text-right font-bold text-xs uppercase tracking-wider">Expense</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.ticker}>
              <TableCell className="font-mono font-bold">{item.ticker}</TableCell>
              <TableCell className="font-semibold">{item.name}</TableCell>
              <TableCell>
                <Badge variant="secondary" className="rounded-full">{item.category}</Badge>
              </TableCell>
              <TableCell className="text-right font-bold">{item.allocationPercent.toFixed(1)}%</TableCell>
              <TableCell className="text-right">${item.amount.toLocaleString(undefined, { minimumFractionDigits: 2 })}</TableCell>
              <TableCell className="text-right">{item.shares}</TableCell>
              <TableCell className="text-right">${item.price.toFixed(2)}</TableCell>
              <TableCell className="text-right text-muted-foreground">{item.expenseRatio?.toFixed(2)}%</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

function StockTable({ items }: { items: PortfolioItem[] }) {
  return (
    <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/40">
            <TableHead className="font-bold text-xs uppercase tracking-wider">Ticker</TableHead>
            <TableHead className="font-bold text-xs uppercase tracking-wider">Name</TableHead>
            <TableHead className="font-bold text-xs uppercase tracking-wider">Sector</TableHead>
            <TableHead className="font-bold text-xs uppercase tracking-wider">Region</TableHead>
            <TableHead className="text-right font-bold text-xs uppercase tracking-wider">Allocation</TableHead>
            <TableHead className="text-right font-bold text-xs uppercase tracking-wider">Amount</TableHead>
            <TableHead className="text-right font-bold text-xs uppercase tracking-wider">Shares</TableHead>
            <TableHead className="text-right font-bold text-xs uppercase tracking-wider">Price</TableHead>
            <TableHead className="font-bold text-xs uppercase tracking-wider">Risk</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.ticker}>
              <TableCell className="font-mono font-bold">{item.ticker}</TableCell>
              <TableCell className="font-semibold">{item.name}</TableCell>
              <TableCell>
                <Badge variant="secondary" className="rounded-full">{item.sector}</Badge>
              </TableCell>
              <TableCell>{item.region}</TableCell>
              <TableCell className="text-right font-bold">{item.allocationPercent.toFixed(1)}%</TableCell>
              <TableCell className="text-right">${item.amount.toLocaleString(undefined, { minimumFractionDigits: 2 })}</TableCell>
              <TableCell className="text-right">{item.shares}</TableCell>
              <TableCell className="text-right">${item.price.toFixed(2)}</TableCell>
              <TableCell>
                <Badge
                  className={`rounded-full font-semibold ${
                    item.riskLevel === "high"
                      ? "bg-destructive/10 text-destructive"
                      : item.riskLevel === "low"
                        ? "bg-[#E2F6D5] text-[#163300]"
                        : "bg-muted text-foreground"
                  }`}
                >
                  {item.riskLevel}
                </Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

function PieChartComponent({ data }: { data: Record<string, number> }) {
  const chartData = useMemo(
    () =>
      Object.entries(data)
        .filter(([, v]) => v > 0)
        .map(([name, value]) => ({ name, value: Math.round(value * 10) / 10 }))
        .sort((a, b) => b.value - a.value),
    [data]
  );

  if (chartData.length === 0) {
    return <div className="h-64 flex items-center justify-center text-muted-foreground">No data</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <PieChart>
        <Pie
          data={chartData}
          cx="50%"
          cy="50%"
          innerRadius={65}
          outerRadius={115}
          paddingAngle={3}
          dataKey="value"
          label={({ name, value }) => `${name}: ${value}%`}
        >
          {chartData.map((_, index) => (
            <Cell key={index} fill={COLORS[index % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip formatter={(value) => [`${value}%`, "Allocation"]} />
      </PieChart>
    </ResponsiveContainer>
  );
}

function RiskChart({ data }: { data: Record<string, number> }) {
  const chartData = useMemo(
    () =>
      Object.entries(data)
        .filter(([, v]) => v > 0)
        .map(([name, value]) => ({
          name: name.charAt(0).toUpperCase() + name.slice(1),
          value: Math.round(value * 10) / 10,
        })),
    [data]
  );

  const riskColors: Record<string, string> = {
    Low: "#9FE870",
    Medium: "#0B4C72",
    High: "#CB272F",
  };

  return (
    <ResponsiveContainer width="100%" height={250}>
      <BarChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="name" />
        <YAxis unit="%" />
        <Tooltip formatter={(value) => [`${value}%`, "Allocation"]} />
        <Bar dataKey="value" radius={[8, 8, 0, 0]}>
          {chartData.map((entry) => (
            <Cell key={entry.name} fill={riskColors[entry.name] ?? "#9FE870"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
