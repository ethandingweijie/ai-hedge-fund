/**
 * i18n — app language (owner, 2026-11-25: English + Simplified Chinese,
 * toggled under settings).
 *
 * Deliberately tiny and dependency-free:
 *   - the English source text IS the key: `t('Screener')`. A string with no
 *     Chinese entry falls back to its English text, so untranslated corners
 *     degrade gracefully and nothing ever renders a raw key;
 *   - `{name}` placeholders are filled from the second argument:
 *     `t('{n} messages', { n: 3 })`;
 *   - the choice persists in localStorage ('lang') and sets <html lang>, so
 *     the browser picks CJK-appropriate fonts and screen readers the right voice.
 *
 * Every component that shows translated text calls `useI18n()`, so switching
 * language re-renders the open screen in place (no reload, no lost state).
 * Non-React helpers (timeAgo, formatters) read `currentLang()`.
 *
 * Scope: interface text. AI-written report content (thesis, research, news)
 * stays in the language it was generated in.
 *
 * The provider lives in ./LanguageProvider.tsx (component-only file, so React
 * fast refresh keeps working).
 */
import { createContext, useContext } from 'react';
import { ZH } from './zh';

export type Lang = 'en' | 'zh';
export const LANGS: { value: Lang; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'zh', label: '简体中文' },
];

export const STORAGE_KEY = 'lang';

function readStored(): Lang {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'zh' ? 'zh' : 'en';
  } catch {
    return 'en';
  }
}

let _lang: Lang = readStored();

/** Current language for non-React code (formatters, utils). */
export function currentLang(): Lang {
  return _lang;
}

/** Set by the provider; keeps non-React readers in step. */
export function setCurrentLang(l: Lang) {
  _lang = l;
}

function fill(text: string, vars?: Record<string, string | number>): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}

/** Translate outside React (same lookup as the hook's `t`). */
export function translate(text: string, vars?: Record<string, string | number>, lang: Lang = _lang): string {
  const base = lang === 'zh' ? (ZH[text] ?? text) : text;
  return fill(base, vars);
}

export interface I18nValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (text: string, vars?: Record<string, string | number>) => string;
}

export const I18nContext = createContext<I18nValue>({
  lang: 'en',
  setLang: () => {},
  t: (text, vars) => fill(text, vars),
});

export const useI18n = () => useContext(I18nContext);
