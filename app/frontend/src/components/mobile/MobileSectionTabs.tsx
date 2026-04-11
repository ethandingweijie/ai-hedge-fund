interface MobileSectionTabsProps {
  sections: readonly { id: string; label: string }[];
  activeSection: string;
  onSectionChange: (id: string) => void;
  moreButton?: React.ReactNode;
}

export function MobileSectionTabs({ sections, activeSection, onSectionChange, moreButton }: MobileSectionTabsProps) {
  return (
    <div className="sticky top-[52px] z-30 bg-background/95 backdrop-blur border-b border-border">
      <div className="flex items-center px-4 py-1.5 gap-1">
        {sections.map(s => (
          <button
            key={s.id}
            onClick={() => onSectionChange(s.id)}
            className={`text-[13px] px-4 py-1.5 rounded-full shrink-0 transition-colors font-medium
              ${activeSection === s.id
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-muted'
              }`}
          >
            {s.label}
          </button>
        ))}
        {/* More button pushed to right */}
        {moreButton && (
          <div className="ml-auto shrink-0">
            {moreButton}
          </div>
        )}
      </div>
    </div>
  );
}
