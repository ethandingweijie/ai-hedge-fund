import * as React from "react"

// Mobile app build — always return true so mobile layout is shown
// regardless of viewport width. The web app (port 5174) uses its own
// responsive breakpoints independently.
export function useIsMobile() {
  return true
}
