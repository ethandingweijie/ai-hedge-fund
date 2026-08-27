> **Archived prototype — not the shipped feature.**
>
> Robo Strategy ships as a feature *inside* the AI Hedge Fund app:
>
> | | |
> |---|---|
> | Route | `/robo-strategy` (`app/frontend/src/pages/RoboStrategyPage.tsx`) |
> | API | `app/backend/routes/robo_strategy.py` → `/robo-strategy/generate` |
> | Engine | `app/backend/services/robo_strategy_service.py` |
> | UI parts | `app/frontend/src/components/robo/` |
>
> This directory is the standalone Next.js original that the Python engine
> was ported from. It still runs its OWN allocation engine
> (`src/lib/allocation.ts`) and does not call the backend, so it does not
> carry anything added since the port — notably the equity look-through that
> resolves the fund plan to the companies it actually holds.
>
> It is kept committed so the TypeScript original and the Python port are
> versioned together and drift between them is visible in history. Because it
> has its own `package.json`, editors may offer to import it as a separate
> project — that is expected; it is reference material, not a second app to
> run.

This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
