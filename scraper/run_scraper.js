/**
 * CLI bridge: Python calls this script via subprocess.
 * Usage: node scraper/run_scraper.js <screenerPath>
 * Example: node scraper/run_scraper.js /company/INFY/consolidated/
 *
 * Outputs: JSON to stdout (compact snapshot), errors to stderr.
 */

import { fetchStockSnapshot, buildCompactStockSnapshot } from "./index.js";
import { fetchGoogleNews } from "./googleNewsScraper.js";

const screenerPath = process.argv[2];

if (!screenerPath) {
  console.error("Usage: node scraper/run_scraper.js <screenerPath>");
  console.error("Example: node scraper/run_scraper.js /company/INFY/consolidated/");
  process.exit(1);
}

// Extract ticker from screener path: /company/INFY/consolidated/ → INFY
function extractTicker(path) {
  const match = path.match(/\/company\/([^/]+)\//);
  return match ? match[1].toUpperCase() : path;
}

try {
  const ticker = extractTicker(screenerPath);

  // Fetch Screener data
  const raw = await fetchStockSnapshot(screenerPath);
  const compact = buildCompactStockSnapshot(raw);

  const newsQuery = `${ticker} share`;

  // Fetch news — non-fatal, Screener data still flows through on failure
  let newsResults = [];
  try {
    process.stderr.write(`[run_scraper] Fetching news: "${newsQuery}"\n`);
    newsResults = await fetchGoogleNews(newsQuery, 15);
    process.stderr.write(`[run_scraper] Got ${newsResults.length} news items\n`);
  } catch (newsErr) {
    process.stderr.write(`[run_scraper] News error (non-fatal): ${newsErr.message}\n`);
  }

  if (newsResults.length > 0) {
    compact.news = newsResults.map((item) => ({
      source: item.source,
      time: item.time,
      title: item.title,
      description: item.description || "",
      link: item.url,
    }));
  }

  process.stdout.write(JSON.stringify(compact));
} catch (err) {
  console.error("Scraper error:", err.message || err);
  process.exit(1);
}
