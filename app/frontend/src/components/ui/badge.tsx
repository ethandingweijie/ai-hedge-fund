import { cva, type VariantProps } from "class-variance-authority"
import * as React from "react"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        secondary:
          "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
        // Monochrome: red is reserved for price change. A destructive badge
        // reads through full-contrast inversion, not hue.
        destructive:
          "border-transparent bg-destructive text-destructive-foreground shadow hover:bg-destructive/80",
        // Amber survives — the reserved-colour rule covers green and red only.
        warning:
          "border-transparent bg-warning text-black shadow hover:bg-warning/80",
        // "success" must NOT be green: it is a status, not a price move.
        success:
          "border border-[var(--hairline)] bg-surface-2 text-content-high",
        outline: "text-foreground",
      },
    }
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
