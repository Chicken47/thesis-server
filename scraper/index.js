// Scraping engine — importable as a standalone module in other projects.
//
// Usage:
//   import { fetchStockSnapshot, searchStocks, buildCompactStockSnapshot } from './scraper/index.js'
//
//   const raw = await fetchStockSnapshot('/company/INFY/consolidated/')
//   const compact = buildCompactStockSnapshot(raw)

export { fetchStockSnapshot, fetchFullStockData, scrapeScreenerPage } from "./screenerScraper.js";
export { getIndianIndices } from "./googleFinanceScraper.js";
export { fetchGoogleNews } from "./googleNewsScraper.js";
export { searchStocks } from "./search.js";
export { buildCompactStockSnapshot } from "./formatter.js";
