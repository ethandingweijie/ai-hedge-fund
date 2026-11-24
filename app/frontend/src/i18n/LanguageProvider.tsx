/**
 * LanguageProvider — holds the chosen language (see ./index.ts for the design).
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { I18nContext, STORAGE_KEY, currentLang, setCurrentLang, translate, type I18nValue, type Lang } from './index';

function applyHtmlLang(l: Lang) {
  try {
    document.documentElement.lang = l === 'zh' ? 'zh-Hans' : 'en';
  } catch { /* SSR / tests */ }
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(currentLang());

  useEffect(() => { applyHtmlLang(lang); }, [lang]);

  const setLang = useCallback((l: Lang) => {
    setCurrentLang(l);
    try { localStorage.setItem(STORAGE_KEY, l); } catch { /* private mode */ }
    setLangState(l);
  }, []);

  const value = useMemo<I18nValue>(() => ({
    lang,
    setLang,
    t: (text, vars) => translate(text, vars, lang),
  }), [lang, setLang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
