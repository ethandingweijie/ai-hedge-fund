import Link from "next/link";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  BarChart3,
  Globe2,
  Layers,
  ArrowRight,
  TrendingUp,
} from "lucide-react";

export default function HomePage() {
  return (
    <div className="flex flex-col min-h-screen">
      {/* Header - Wise style sticky nav */}
      <header className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 sticky top-0 z-50">
        <div className="container mx-auto flex h-[76px] items-center justify-between px-6 lg:px-16">
          <div className="flex items-center gap-3">
            <TrendingUp className="h-7 w-7 text-foreground" strokeWidth={2.5} />
            <span className="text-xl font-extrabold tracking-tight">
              Robo Strategy
            </span>
          </div>
          <nav className="hidden md:flex items-center gap-8 text-sm font-semibold text-muted-foreground">
            <Link href="/questionnaire" className="hover:text-foreground transition-colors">
              Build Portfolio
            </Link>
            <Link href="/strategies" className="hover:text-foreground transition-colors">
              Strategies
            </Link>
          </nav>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1">
        {/* Hero Section - Wise style: large bold heading, spacious */}
        <section className="container mx-auto px-6 lg:px-16 pt-20 pb-16 md:pt-28 md:pb-24">
          <div className="max-w-4xl space-y-8">
            <h1 className="heading-xl text-4xl sm:text-5xl md:text-6xl lg:text-7xl">
              Invest Smart,{" "}
              <span className="text-[#163300] dark:text-[#9FE870]">
                Everywhere
              </span>
            </h1>
            <p className="text-lg md:text-xl text-muted-foreground max-w-2xl font-medium leading-relaxed">
              10 strategies. 80+ stocks. 30+ ETFs. Answer a few questions about
              your goals — or pick a proven portfolio template. Get your
              personalized investment plan in seconds.
            </p>
            <div className="flex flex-col sm:flex-row gap-4 pt-2">
              <Link href="/questionnaire">
                <Button size="lg" className="text-base font-semibold">
                  Build Custom Portfolio
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
              <Link href="/strategies">
                <Button variant="outline" size="lg" className="text-base font-semibold">
                  Browse Strategies
                </Button>
              </Link>
            </div>
          </div>
        </section>

        {/* Feature Section - Wise style: image + text pairs */}
        <section className="border-t bg-muted/40">
          <div className="container mx-auto px-6 lg:px-16 py-20 md:py-28">
            <div className="grid md:grid-cols-3 gap-12 md:gap-8">
              {/* Feature 1 */}
              <div className="space-y-5">
                <div className="w-14 h-14 rounded-2xl bg-primary/20 flex items-center justify-center">
                  <BarChart3 className="h-7 w-7 text-foreground" />
                </div>
                <h3 className="heading-lg text-xl">Risk-Based Allocation</h3>
                <p className="text-muted-foreground leading-relaxed">
                  Portfolios tailored to your risk tolerance and time horizon.
                  From conservative income to aggressive growth — we match your
                  profile.
                </p>
              </div>

              {/* Feature 2 */}
              <div className="space-y-5">
                <div className="w-14 h-14 rounded-2xl bg-primary/20 flex items-center justify-center">
                  <Globe2 className="h-7 w-7 text-foreground" />
                </div>
                <h3 className="heading-lg text-xl">
                  Industry & Geography
                </h3>
                <p className="text-muted-foreground leading-relaxed">
                  Tilt your portfolio toward sectors and regions you believe in.
                  From US tech to emerging markets — your preferences, your
                  portfolio.
                </p>
              </div>

              {/* Feature 3 */}
              <div className="space-y-5">
                <div className="w-14 h-14 rounded-2xl bg-primary/20 flex items-center justify-center">
                  <Layers className="h-7 w-7 text-foreground" />
                </div>
                <h3 className="heading-lg text-xl">
                  Popular Strategies
                </h3>
                <p className="text-muted-foreground leading-relaxed">
                  Choose from 10+ proven strategies: Boglehead 3-Fund, Dividend
                  Aristocrats, AI & Robotics, and more. Customize any template
                  to your liking.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* How It Works - Wise style: numbered steps */}
        <section className="container mx-auto px-6 lg:px-16 py-20 md:py-28">
          <h2 className="heading-xl text-3xl md:text-4xl mb-16 text-center">
            How It Works
          </h2>
          <div className="grid md:grid-cols-3 gap-12 max-w-4xl mx-auto">
            <div className="text-center space-y-4">
              <div className="w-12 h-12 rounded-full bg-primary flex items-center justify-center mx-auto text-primary-foreground font-bold text-lg">
                1
              </div>
              <h3 className="heading-md text-lg">Tell Us Your Goals</h3>
              <p className="text-muted-foreground text-sm">
                Risk tolerance, time horizon, industry and geography
                preferences. Or pick a strategy template.
              </p>
            </div>
            <div className="text-center space-y-4">
              <div className="w-12 h-12 rounded-full bg-primary flex items-center justify-center mx-auto text-primary-foreground font-bold text-lg">
                2
              </div>
              <h3 className="heading-md text-lg">We Build Your Portfolio</h3>
              <p className="text-muted-foreground text-sm">
                Our engine selects ETFs or individual stocks that match your
                preferences, with live prices from FMP.
              </p>
            </div>
            <div className="text-center space-y-4">
              <div className="w-12 h-12 rounded-full bg-primary flex items-center justify-center mx-auto text-primary-foreground font-bold text-lg">
                3
              </div>
              <h3 className="heading-md text-lg">Export & Invest</h3>
              <p className="text-muted-foreground text-sm">
                Review your allocation, charts, and breakdowns. Export as CSV
                and take it to your broker.
              </p>
            </div>
          </div>
        </section>
      </main>

      {/* Footer - Wise style: light gray bg */}
      <footer className="border-t bg-muted/60 py-10">
        <div className="container mx-auto px-6 lg:px-16">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <TrendingUp className="h-5 w-5 text-muted-foreground" />
              <span className="font-bold text-sm">Robo Strategy</span>
            </div>
            <p className="text-sm text-muted-foreground text-center md:text-right max-w-md">
              For educational purposes only. Not financial advice. Always consult
              a qualified financial advisor before investing.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
