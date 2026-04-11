import * as React from "react"
import { Capacitor } from '@capacitor/core'

const MOBILE_BREAKPOINT = 768

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState(() =>
    Capacitor.isNativePlatform() || window.innerWidth < MOBILE_BREAKPOINT
  )

  React.useEffect(() => {
    if (Capacitor.isNativePlatform()) return
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
    const onChange = () => setIsMobile(window.innerWidth < MOBILE_BREAKPOINT)
    mql.addEventListener("change", onChange)
    return () => mql.removeEventListener("change", onChange)
  }, [])

  return isMobile
}
