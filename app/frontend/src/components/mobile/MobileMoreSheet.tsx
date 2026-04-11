import { useState, useEffect } from 'react';
import { X, MoreHorizontal } from 'lucide-react';
import { createPortal } from 'react-dom';

interface MobileMoreSheetProps {
  children: React.ReactNode;
}

export function MobileMoreSheet({ children }: MobileMoreSheetProps) {
  const [open, setOpen] = useState(false);

  // Prevent body scroll when sheet is open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
      return () => { document.body.style.overflow = ''; };
    }
  }, [open]);

  return (
    <>
      {/* Trigger button */}
      <button
        onClick={() => setOpen(true)}
        className="flex flex-col items-center justify-center gap-0.5 px-3 py-1.5 rounded-full text-muted-foreground hover:bg-muted transition-colors"
      >
        <MoreHorizontal size={18} />
        <span className="text-[10px] font-medium leading-none">More</span>
      </button>

      {/* Portal to document.body so it's above everything */}
      {open && createPortal(
        <>
          {/* Full-screen backdrop */}
          <div
            className="fixed inset-0 z-[100] bg-black/50"
            onClick={() => setOpen(false)}
          />

          {/* Bottom sheet */}
          <div className="fixed left-0 right-0 bottom-0 z-[101] bg-background rounded-t-2xl shadow-2xl max-h-[80vh] flex flex-col">
            {/* Handle + header */}
            <div className="flex flex-col items-center pt-2 pb-1 shrink-0">
              <div className="w-10 h-1 bg-border rounded-full mb-2" />
              <div className="flex items-center justify-between w-full px-4 pb-2 border-b border-border">
                <span className="text-sm font-semibold">More Sections</span>
                <button
                  onClick={() => setOpen(false)}
                  className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-muted"
                >
                  <X size={16} className="text-muted-foreground" />
                </button>
              </div>
            </div>

            {/* Scrollable content */}
            <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
              {children}
            </div>
          </div>
        </>,
        document.body
      )}
    </>
  );
}
