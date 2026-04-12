// Always return true — this deployed app uses mobile layout everywhere.
// The desktop graph IDE (Layout.tsx) is not used in this build.
export function useIsMobile() {
  return true
}
