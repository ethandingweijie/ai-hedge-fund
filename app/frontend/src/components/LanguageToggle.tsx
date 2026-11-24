/**
 * LanguageToggle.tsx
 * ==================
 * English / 简体中文 segmented control, shown under settings in the desktop
 * sidebar and the mobile menu (same look as ThemeToggle / LayoutModeToggle).
 * Each option is labelled in its own language so it is findable from either.
 */
import { Languages } from 'lucide-react';
import { LANGS, useI18n } from '@/i18n';

export function LanguageToggle() {
  const { lang, setLang } = useI18n();
  return (
    <div className="flex gap-1.5" role="radiogroup" aria-label="Language / 语言">
      {LANGS.map(({ value, label }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={lang === value}
          lang={value === 'zh' ? 'zh-Hans' : 'en'}
          onClick={() => setLang(value)}
          className={`flex-1 flex flex-col items-center gap-1 py-2 rounded-md transition-colors text-xs font-medium
            ${lang === value ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}`}
        >
          <Languages size={14} />
          {label}
        </button>
      ))}
    </div>
  );
}
