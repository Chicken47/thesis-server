/**
 * Full deep scrape for one stock — outputs complete JSON to stdout.
 * Python calls this to populate the per-stock cache.
 *
 * Usage: node scraper/run_full_scrape.js /company/TCS/consolidated/
 * Output: JSON with all financials tabs + peers + documents
 */

import { fetchFullStockData } from "./index.js";
import { fetchGoogleNews } from "./googleNewsScraper.js";

const screenerPath = process.argv[2];

if (!screenerPath) {
  console.error("Usage: node scraper/run_full_scrape.js <screenerPath>");
  console.error("Example: node scraper/run_full_scrape.js /company/TCS/consolidated/");
  process.exit(1);
}

function extractTicker(path) {
  const match = path.match(/\/company\/([^/]+)\//);
  return match ? match[1].toUpperCase() : path;
}

try {
  const ticker = extractTicker(screenerPath);
  process.stderr.write(`[full_scrape] Scraping all tabs for ${ticker}...\n`);

  const data = await fetchFullStockData(screenerPath);
  process.stderr.write(`[full_scrape] Screener done. Sections: P&L=${data.annualPL?.values?.length || 0} rows, BS=${data.balanceSheet?.values?.length || 0} rows, CF=${data.cashFlows?.values?.length || 0} rows, Peers=${data.peerComparison?.peers?.length || 0}, Docs=${data.documents?.length || 0}\n`);

  const newsQuery = `${ticker} share`;

  let news = [];
  try {
    process.stderr.write(`[full_scrape] Fetching news: "${newsQuery}"\n`);
    news = await fetchGoogleNews(newsQuery, 15);
    process.stderr.write(`[full_scrape] Got ${news.length} news items\n`);
  } catch (e) {
    process.stderr.write(`[full_scrape] News error (non-fatal): ${e.message}\n`);
  }

  const output = {
    ...data,
    ticker,
    screenerPath,
    news: news.map((n) => ({
      title: n.title,
      source: n.source,
      time: n.time,
      description: n.description || "",
      url: n.url,
    })),
  };

  process.stdout.write(JSON.stringify(output));
} catch (err) {
  console.error("Full scrape error:", err.message || err);
  process.exit(1);
}
