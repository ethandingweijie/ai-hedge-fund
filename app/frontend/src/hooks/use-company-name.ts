import { useEffect, useState } from 'react';
import { getCompanyName } from '@/lib/api';

interface CompanyProfile {
  name: string;
  sector: string | null;
  industry: string | null;
}

// Simple in-memory cache so navigating between reports doesn't re-fetch
const cache: Record<string, CompanyProfile> = {};

export function useCompanyName(ticker: string): string | null {
  const profile = useCompanyProfile(ticker);
  return profile?.name ?? null;
}

export function useCompanyProfile(ticker: string): CompanyProfile | null {
  const [profile, setProfile] = useState<CompanyProfile | null>(
    cache[ticker] ?? null,
  );

  useEffect(() => {
    if (!ticker) return;
    if (cache[ticker]) { setProfile(cache[ticker]); return; }
    getCompanyName(ticker)
      .then(res => {
        const p: CompanyProfile = {
          name:     res.name,
          sector:   res.sector ?? null,
          industry: res.industry ?? null,
        };
        cache[ticker] = p;
        setProfile(p);
      })
      .catch(() => { /* silently fall back */ });
  }, [ticker]);

  return profile;
}
