/**
 * Google Finance scraper — returns empty data.
 * The news it previously fetched is replaced by the Google News RSS scraper.
 * The marketIndicators it fetched are supplementary and not used in the core analysis.
 * Keeping the export signature so callers don't break.
 */

export const getIndianIndices = async (_screenerLink) => {
  return { financialData: [], newsDetails: [] };
};
