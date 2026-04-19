import { useRef, useState } from 'react';

interface SwipeAction {
  icon: React.ReactNode;
  color: string;
  onClick: () => void;
}

interface SwipeableCardProps {
  children: React.ReactNode;
  actions: SwipeAction[];
  onClick?: () => void;
  className?: string;
}

const ACTION_WIDTH = 56;

export function SwipeableCard({ children, actions, onClick, className = '' }: SwipeableCardProps) {
  const startXRef = useRef(0);
  const currentXRef = useRef(0);
  const [offset, setOffset] = useState(0);
  const [swiping, setSwiping] = useState(false);
  const maxSwipe = actions.length * ACTION_WIDTH;

  const handleTouchStart = (e: React.TouchEvent) => {
    startXRef.current = e.touches[0].clientX;
    currentXRef.current = offset;
    setSwiping(true);
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    if (!swiping) return;
    const dx = startXRef.current - e.touches[0].clientX;
    setOffset(Math.max(0, Math.min(maxSwipe, currentXRef.current + dx)));
  };

  const handleTouchEnd = () => {
    setSwiping(false);
    setOffset(prev => prev > maxSwipe / 2 ? maxSwipe : 0);
  };

  // Only fire onClick if the tap target is inside a data-tap="open" element.
  // Lets the consumer restrict the click-through region (e.g. ticker column)
  // while the swipe gesture still works across the full card.
  const handleClick = (e: React.MouseEvent) => {
    if (offset > 0) {
      setOffset(0);
      return;
    }
    const target = e.target as Element | null;
    if (target && target.closest('[data-tap="open"]')) {
      onClick?.();
    }
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    startXRef.current = e.clientX;
    currentXRef.current = offset;
    setSwiping(true);
    const handleMouseMove = (ev: MouseEvent) => {
      const dx = startXRef.current - ev.clientX;
      setOffset(Math.max(0, Math.min(maxSwipe, currentXRef.current + dx)));
    };
    const handleMouseUp = () => {
      setSwiping(false);
      setOffset(prev => prev > maxSwipe / 2 ? maxSwipe : 0);
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  };

  const isRevealed = offset > 0;

  return (
    <div
      className={`relative overflow-hidden rounded-xl ${className}`}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      onMouseDown={handleMouseDown}
      onClick={handleClick}
    >
      {/* Card content — stays in place, not translated */}
      {children}

      {/* Action buttons slide in from right, overlaying the score area */}
      {isRevealed && (
        <div
          className="absolute top-0 bottom-0 flex items-stretch z-20"
          style={{
            right: 0,
            width: maxSwipe,
            transform: `translateX(${maxSwipe - offset}px)`,
            transition: swiping ? 'none' : 'transform 0.25s ease-out',
          }}
        >
          {actions.map((action, i) => (
            <button
              key={i}
              onClick={(e) => { e.stopPropagation(); action.onClick(); setOffset(0); }}
              className={`flex items-center justify-center ${action.color} text-white`}
              style={{ width: ACTION_WIDTH }}
            >
              {action.icon}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
