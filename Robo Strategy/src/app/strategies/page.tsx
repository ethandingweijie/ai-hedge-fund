"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { Label } from "@/components/ui/label";
import { ThemeToggle } from "@/components/theme-toggle";
import { strategies, type StrategyTemplate } from "@/data/strategies";
import { useQuestionnaireStore } from "@/store/questionnaireStore";
import { fetchPortfolio } from "@/lib/fetchPortfolio";
import { adjustWeight, sumWeights } from "@/lib/sliderUtils";
import {
  TrendingUp,
  ArrowLeft,
  ArrowRight,
  X,
  BarChart3,
  Banknote,
  Cpu,
  Shield,
  HeartPulse,
  Leaf,
  Globe,
  DollarSign,
  Bot,
  Earth,
  Check,
} from "lucide-react";

const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  BarChart3, Banknote, Cpu, Shield, HeartPulse, Leaf, Globe, DollarSign, Bot, Earth,
};

export default function StrategiesPage() {
  const router = useRouter();
  const store = useQuestionnaireStore();
  const [selectedStrategy, setSelectedStrategy] = useState<StrategyTemplate | null>(null);
  const [editSector, setEditSector] = useState<Record<string, number> | null>(null);
  const [editGeo, setEditGeo] = useState<Record<string, number> | null>(null);

  const handleSelect = (strategy: StrategyTemplate) => {
    setSelectedStrategy(strategy);
    // Normalize weights to 100% on load
    const sectorSum = sumWeights(strategy.sectorWeights);
    const geoSum = sumWeights(strategy.geographyWeights);
    const normSector: Record<string, number> = {};
    const normGeo: Record<string, number> = {};
    for (const [k, v] of Object.entries(strategy.sectorWeights)) {
      normSector[k] = sectorSum > 0 ? Math.round((v / sectorSum) * 100) : 0;
    }
    for (const [k, v] of Object.entries(strategy.geographyWeights)) {
      normGeo[k] = geoSum > 0 ? Math.round((v / geoSum) * 100) : 0;
    }
    // Fix rounding error on largest sector/region
    const sDiff = 100 - sumWeights(normSector);
    if (sDiff !== 0) {
      const largest = Object.keys(normSector).reduce((a, b) =>
        normSector[a] >= normSector[b] ? a : b,
      );
      normSector[largest] = Math.max(0, normSector[largest] + sDiff);
    }
    const gDiff = 100 - sumWeights(normGeo);
    if (gDiff !== 0) {
      const largest = Object.keys(normGeo).reduce((a, b) =>
        normGeo[a] >= normGeo[b] ? a : b,
      );
      normGeo[largest] = Math.max(0, normGeo[largest] + gDiff);
    }
    setEditSector(normSector);
    setEditGeo(normGeo);
  };

  const handleUseStrategy = async () => {
    if (!selectedStrategy || !editSector || !editGeo) return;
    store.loadFromStrategy({
      riskTolerance: selectedStrategy.riskLevel,
      timeHorizon: selectedStrategy.timeHorizon as never,
      investmentGoal: selectedStrategy.goal as never,
      industryPreferences: editSector,
      geographyPreferences: editGeo,
    });
    store.setSelectedStrategyId(selectedStrategy.id);
    store.setIsLoading(true);
    try {
      const portfolio = await fetchPortfolio(store.answers);
      store.setPortfolio(portfolio);
    } finally {
      store.setIsLoading(false);
      router.push("/results");
    }
  };

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

      <main className="flex-1 container mx-auto px-6 lg:px-16 py-10 max-w-6xl">
        <div className="mb-12">
          <h1 className="heading-xl text-3xl md:text-4xl mb-3">
            Popular Strategy Templates
          </h1>
          <p className="text-muted-foreground text-lg font-medium max-w-2xl">
            Choose a proven investment strategy. Customize it to your liking,
            then generate your personalized portfolio.
          </p>
        </div>

        {/* Strategy Cards Grid */}
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
          {strategies.map((strategy) => {
            const Icon = iconMap[strategy.icon] ?? BarChart3;
            const isSelected = selectedStrategy?.id === strategy.id;

            return (
              <Card
                key={strategy.id}
                className={`cursor-pointer transition-all hover:shadow-md rounded-2xl border-2 ${
                  isSelected
                    ? "border-[#9FE870] ring-0 shadow-md"
                    : "border-transparent hover:border-border"
                }`}
                onClick={() => handleSelect(strategy)}
              >
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className="w-11 h-11 rounded-xl bg-primary/15 flex items-center justify-center">
                      <Icon className="h-5 w-5 text-foreground" />
                    </div>
                    {isSelected ? (
                      <div className="w-6 h-6 rounded-full bg-[#9FE870] flex items-center justify-center">
                        <Check className="h-3.5 w-3.5 text-[#163300]" strokeWidth={3} />
                      </div>
                    ) : (
                      <Badge
                        variant={
                          strategy.riskLevel === "aggressive"
                            ? "destructive"
                            : strategy.riskLevel === "conservative"
                              ? "secondary"
                              : "default"
                        }
                        className="rounded-full text-xs font-semibold"
                      >
                        {strategy.riskLevel}
                      </Badge>
                    )}
                  </div>
                  <CardTitle className="text-lg font-extrabold mt-3">{strategy.name}</CardTitle>
                  <CardDescription className="line-clamp-2 text-sm">
                    {strategy.description}
                  </CardDescription>
                </CardHeader>
                <CardContent className="pt-0">
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {strategy.tags.map((tag) => (
                      <Badge key={tag} variant="outline" className="text-xs rounded-full font-medium">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div className="bg-muted/60 rounded-lg py-2">
                      <div className="text-xs text-muted-foreground">Stocks</div>
                      <div className="font-bold text-sm">{strategy.assetAllocation.stocks}%</div>
                    </div>
                    <div className="bg-muted/60 rounded-lg py-2">
                      <div className="text-xs text-muted-foreground">Bonds</div>
                      <div className="font-bold text-sm">{strategy.assetAllocation.bonds}%</div>
                    </div>
                    <div className="bg-muted/60 rounded-lg py-2">
                      <div className="text-xs text-muted-foreground">Horizon</div>
                      <div className="font-bold text-xs">{strategy.timeHorizon}</div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* Customization Panel */}
        {selectedStrategy && editSector && editGeo && (
          <Card className="mt-10 border-2 border-[#9FE870]/50 rounded-2xl shadow-lg">
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="text-xl font-extrabold">
                    Customize: {selectedStrategy.name}
                  </CardTitle>
                  <p className="text-sm text-muted-foreground mt-1">
                    Adjust the weights below, or use the strategy as-is.
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setSelectedStrategy(null)}
                >
                  <X className="h-5 w-5" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-8">
              {/* Sector Weights */}
              <div>
                <div className="flex items-center justify-between mb-4">
                  <h3 className="heading-md text-base">Sector Allocation</h3>
                  {editSector && (
                    <div
                      className={`px-3 py-1 rounded-full text-xs font-bold border ${
                        sumWeights(editSector) === 100
                          ? "bg-[#9FE870]/15 text-[#163300] dark:text-[#9FE870] border-[#9FE870]/40"
                          : "bg-red-50 text-red-600 border-red-200 dark:bg-red-950 dark:border-red-800"
                      }`}
                    >
                      Total: {sumWeights(editSector)}%
                    </div>
                  )}
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {Object.entries(editSector).map(([sector, weight]) => (
                    <div key={sector} className="space-y-1.5">
                      <div className="flex justify-between text-sm">
                        <Label className="font-semibold">{sector}</Label>
                        <span className="text-muted-foreground font-mono font-semibold">{weight}%</span>
                      </div>
                      <Slider
                        value={[weight]}
                        onValueChange={(val) => {
                          const v = Array.isArray(val) ? val[0] : val;
                          setEditSector(adjustWeight(editSector, sector, v));
                        }}
                        min={0}
                        max={100}
                        step={5}
                      />
                    </div>
                  ))}
                </div>
              </div>

              {/* Geography Weights */}
              <div>
                <div className="flex items-center justify-between mb-4">
                  <h3 className="heading-md text-base">Geography Allocation</h3>
                  {editGeo && (
                    <div
                      className={`px-3 py-1 rounded-full text-xs font-bold border ${
                        sumWeights(editGeo) === 100
                          ? "bg-[#9FE870]/15 text-[#163300] dark:text-[#9FE870] border-[#9FE870]/40"
                          : "bg-red-50 text-red-600 border-red-200 dark:bg-red-950 dark:border-red-800"
                      }`}
                    >
                      Total: {sumWeights(editGeo)}%
                    </div>
                  )}
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {Object.entries(editGeo).map(([region, weight]) => (
                    <div key={region} className="space-y-1.5">
                      <div className="flex justify-between text-sm">
                        <Label className="font-semibold">{region}</Label>
                        <span className="text-muted-foreground font-mono font-semibold">{weight}%</span>
                      </div>
                      <Slider
                        value={[weight]}
                        onValueChange={(val) => {
                          const v = Array.isArray(val) ? val[0] : val;
                          setEditGeo(adjustWeight(editGeo, region, v));
                        }}
                        min={0}
                        max={100}
                        step={5}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div className="flex gap-3 pt-2">
                <Button onClick={handleUseStrategy} size="lg">
                  Generate Portfolio
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
                <Button variant="outline" onClick={() => setSelectedStrategy(null)}>
                  <ArrowLeft className="mr-2 h-4 w-4" />
                  Back
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  );
}
