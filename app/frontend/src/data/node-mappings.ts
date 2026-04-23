/**
 * Node ID helpers.
 *
 * Note: this module used to host the ReactFlow node-type registry for the
 * legacy VSCode-IDE layout. After that layout was removed, only
 * `extractBaseAgentKey` remained in active use (consumed by `services/api.ts`,
 * `services/backtest-api.ts`, and `utils/text-utils.ts` to recover the base
 * agent key from a unique node ID produced by the backend run stream).
 */

/**
 * Extract the base agent key from a unique node ID.
 *
 * @param uniqueId The unique node ID with suffix (e.g., "warren_buffett_abc123")
 * @returns The base agent key (e.g., "warren_buffett")
 */
export const extractBaseAgentKey = (uniqueId: string): string => {
  // For agent nodes, remove the last underscore and 6-character suffix.
  // For other nodes like portfolio_manager, also remove the suffix.
  const parts = uniqueId.split('_');
  if (parts.length >= 2) {
    const lastPart = parts[parts.length - 1];
    // If the last part is a 6-character alphanumeric string, it's likely our suffix
    if (lastPart.length === 6 && /^[a-z0-9]+$/.test(lastPart)) {
      return parts.slice(0, -1).join('_');
    }
  }
  return uniqueId; // Return original if no suffix pattern found
};
