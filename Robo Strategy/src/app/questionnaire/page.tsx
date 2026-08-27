"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  useQuestionnaireStore,
  type RiskLevel,
  type TimeHorizon,
  type InvestmentGoal,
} from "@/store/questionnaireStore";
import { fetchPortfolio } from "@/lib/fetchPortfolio";
import { sumWeights } from "@/lib/sliderUtils";
import {
  ArrowLeft,
  ArrowRight,
  Shield,
  Clock,
  Target,
  Factory,
  Globe2,
  DollarSign,
  TrendingUp,
  Check,
} from "lucide-react";

const STEPS = [
  { title: "Risk Tolerance", icon: Shield },
  { title: "Time Horizon", icon: Clock },
  { title: "Investment Goal", icon: Target },
  { title: "Industry Preferences", icon: Factory },
  { title: "Geography Preferences", icon: Globe2 },
  { title: "Investment Amount", icon: DollarSign },
];

const riskOptions: { value: RiskLevel; label: string; desc: string }[] = [
  { value: "conservative", label: "Conservative", desc: "Prioritize capital preservation with lower expected returns" },
  { value: "moderate", label: "Moderate", desc: "Balance growth potential with some downside protection" },
  { value: "aggressive", label: "Aggressive", desc: "Maximize long-term growth, accept higher volatility" },
];

const horizonOptions: { value: TimeHorizon; label: string; desc: string }[] = [
  { value: "<3 years", label: "< 3 years", desc: "Short-term investment" },
  { value: "3-7 years", label: "3-7 years", desc: "Medium-term investment" },
  { value: "7-15 years", label: "7-15 years", desc: "Long-term investment" },
  { value: "15+ years", label: "15+ years", desc: "Very long-term / retirement" },
];

const goalOptions: { value: InvestmentGoal; label: string; desc: string }[] = [
  { value: "Capital Preservation", label: "Capital Preservation", desc: "Keep your money safe, minimize losses" },
  { value: "Steady Income", label: "Steady Income", desc: "Generate regular dividends and interest" },
  { value: "Balanced Growth", label: "Balanced Growth", desc: "Grow your portfolio with moderate risk" },
  { value: "Maximum Growth", label: "Maximum Growth", desc: "Aggressive appreciation, highest potential returns" },
];

const SECTORS = [
  "Technology", "Healthcare", "Financials", "Consumer", "Energy",
  "Industrials", "Real Estate", "Utilities", "Materials", "Communication",
];

const REGIONS = ["US", "Europe", "Asia Pacific", "Emerging Markets", "Global/International"];

export default function QuestionnairePage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const store = useQuestionnaireStore();
  const progress = ((step + 1) / STEPS.length) * 100;

  const handleNext = async () => {
    if (step < STEPS.length - 1) {
      setStep(step + 1);
    } else {
      store.setIsLoading(true);
      try {
        const portfolio = await fetchPortfolio(store.answers);
        store.setPortfolio(portfolio);
      } finally {
        store.setIsLoading(false);
        router.push("/results");
      }
    }
  };

  const handleBack = () => {
    if (step > 0) setStep(step - 1);
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

      <main className="flex-1 container mx-auto px-6 lg:px-16 py-10 max-w-3xl">
        {/* Progress */}
        <div className="space-y-3 mb-10">
          <div className="flex justify-between text-sm font-semibold text-muted-foreground">
            <span>
              Step {step + 1} of {STEPS.length}: {STEPS[step].title}
            </span>
            <span>{Math.round(progress)}%</span>
          </div>
          <Progress value={progress} className="h-2 rounded-full bg-muted" />
        </div>

        {/* Step Content */}
        <Card className="border-0 shadow-lg rounded-2xl">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-3 text-2xl font-extrabold">
              {(() => {
                const Icon = STEPS[step].icon;
                return <Icon className="h-7 w-7 text-[#163300] dark:text-[#9FE870]" />;
              })()}
              {STEPS[step].title}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {step === 0 && (
              <SelectionStep
                options={riskOptions}
                value={store.answers.riskTolerance}
                onChange={(v) => store.setAnswer("riskTolerance", v)}
              />
            )}
            {step === 1 && (
              <SelectionStep
                options={horizonOptions}
                value={store.answers.timeHorizon}
                onChange={(v) => store.setAnswer("timeHorizon", v)}
              />
            )}
            {step === 2 && (
              <SelectionStep
                options={goalOptions}
                value={store.answers.investmentGoal}
                onChange={(v) => store.setAnswer("investmentGoal", v)}
              />
            )}
            {step === 3 && <IndustryStep />}
            {step === 4 && <GeographyStep />}
            {step === 5 && (
              <AmountStep
                value={store.answers.investmentAmount}
                onChange={(v) => store.setAnswer("investmentAmount", v)}
              />
            )}
          </CardContent>
        </Card>

        {/* Navigation */}
        <div className="flex justify-between mt-8">
          <Button variant="outline" onClick={handleBack} disabled={step === 0}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back
          </Button>
          <Button onClick={handleNext}>
            {step === STEPS.length - 1 ? (
              "Generate Portfolio"
            ) : (
              <>
                Next
                <ArrowRight className="ml-2 h-4 w-4" />
              </>
            )}
          </Button>
        </div>
      </main>
    </div>
  );
}

function SelectionStep<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string; desc: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="grid gap-3 pt-2">
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`text-left p-5 rounded-xl border-2 transition-all relative ${
            value === opt.value
              ? "border-[#9FE870] bg-[#9FE870]/10 dark:bg-[#9FE870]/5"
              : "border-border hover:border-muted-foreground/30"
          }`}
        >
          {value === opt.value && (
            <div className="absolute top-4 right-4 w-6 h-6 rounded-full bg-[#9FE870] flex items-center justify-center">
              <Check className="h-3.5 w-3.5 text-[#163300]" strokeWidth={3} />
            </div>
          )}
          <div className="font-bold text-base">{opt.label}</div>
          <div className="text-sm text-muted-foreground mt-1">{opt.desc}</div>
        </button>
      ))}
    </div>
  );
}

function IndustryStep() {
  const prefs = useQuestionnaireStore((s) => s.answers.industryPreferences);
  const setWeight = useQuestionnaireStore((s) => s.setIndustryWeight);
  const total = sumWeights(prefs);

  return (
    <div className="space-y-6 pt-2">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground font-medium">
          Set your preference weight for each industry sector. Adjusting one slider
          proportionally adjusts the others so the total always equals 100%.
        </p>
        <div
          className={`ml-4 shrink-0 px-3 py-1 rounded-full text-xs font-bold border ${
            total === 100
              ? "bg-[#9FE870]/15 text-[#163300] dark:text-[#9FE870] border-[#9FE870]/40"
              : "bg-red-50 text-red-600 border-red-200 dark:bg-red-950 dark:border-red-800"
          }`}
        >
          Total: {total}%
        </div>
      </div>
      {SECTORS.map((sector) => (
        <div key={sector} className="space-y-2">
          <div className="flex justify-between">
            <Label className="font-semibold text-sm">{sector}</Label>
            <span className="text-sm text-muted-foreground font-mono font-semibold">
              {prefs[sector] ?? 0}%
            </span>
          </div>
          <Slider
            value={[prefs[sector] ?? 0]}
            onValueChange={(val) => {
              const v = Array.isArray(val) ? val[0] : val;
              setWeight(sector, v);
            }}
            min={0}
            max={100}
            step={5}
          />
        </div>
      ))}
    </div>
  );
}

function GeographyStep() {
  const prefs = useQuestionnaireStore((s) => s.answers.geographyPreferences);
  const setWeight = useQuestionnaireStore((s) => s.setGeographyWeight);
  const total = sumWeights(prefs);

  return (
    <div className="space-y-6 pt-2">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground font-medium">
          Set your preference weight for each geographic region. Adjusting one slider
          proportionally adjusts the others so the total always equals 100%.
        </p>
        <div
          className={`ml-4 shrink-0 px-3 py-1 rounded-full text-xs font-bold border ${
            total === 100
              ? "bg-[#9FE870]/15 text-[#163300] dark:text-[#9FE870] border-[#9FE870]/40"
              : "bg-red-50 text-red-600 border-red-200 dark:bg-red-950 dark:border-red-800"
          }`}
        >
          Total: {total}%
        </div>
      </div>
      {REGIONS.map((region) => (
        <div key={region} className="space-y-2">
          <div className="flex justify-between">
            <Label className="font-semibold text-sm">{region}</Label>
            <span className="text-sm text-muted-foreground font-mono font-semibold">
              {prefs[region] ?? 0}%
            </span>
          </div>
          <Slider
            value={[prefs[region] ?? 0]}
            onValueChange={(val) => {
              const v = Array.isArray(val) ? val[0] : val;
              setWeight(region, v);
            }}
            min={0}
            max={100}
            step={5}
          />
        </div>
      ))}
    </div>
  );
}

function AmountStep({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-6 pt-2">
      <p className="text-sm text-muted-foreground font-medium">
        Enter the total amount you want to invest.
      </p>
      <div className="space-y-3">
        <Label htmlFor="amount" className="font-semibold">Investment Amount (USD)</Label>
        <div className="relative">
          <span className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground text-lg font-semibold">
            $
          </span>
          <Input
            id="amount"
            type="number"
            min={100}
            step={100}
            value={value}
            onChange={(e) => onChange(Number(e.target.value))}
            className="pl-8 text-xl h-14 rounded-xl font-bold"
          />
        </div>
      </div>
      <div className="flex gap-2 flex-wrap">
        {[1000, 5000, 10000, 25000, 50000, 100000].map((amt) => (
          <Button
            key={amt}
            variant={value === amt ? "default" : "outline"}
            onClick={() => onChange(amt)}
          >
            ${amt.toLocaleString()}
          </Button>
        ))}
      </div>
    </div>
  );
}
