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

  // Build search query: use first few words of aboutText for company name hint
  let companyHint = ticker;
  const about = compact.aboutText || "";
  if (about) {
    const words = about.split(/\s+/).slice(0, 4).join(" ");
    companyHint = words || ticker;
  }
  const newsQuery = `${companyHint} NSE stock India`;

  // Fetch Google News — non-fatal, Screener data still flows through on failure
  let googleNews = [];
  try {
    process.stderr.write(`[run_scraper] Fetching Google News: "${newsQuery}"\n`);
    googleNews = await fetchGoogleNews(newsQuery, 8);
    process.stderr.write(`[run_scraper] Got ${googleNews.length} news items\n`);
  } catch (newsErr) {
    process.stderr.write(`[run_scraper] Google News error (non-fatal): ${newsErr.message}\n`);
  }

  // Replace stale Google Finance news with Google News results if we got any
  if (googleNews.length > 0) {
    compact.news = googleNews.map((item) => ({
      source: item.source,
      time: item.time,
      title: item.title,
      link: item.url,
    }));
  }

  process.stdout.write(JSON.stringify(compact));
} catch (err) {
  console.error("Scraper error:", err.message || err);
  process.exit(1);
}
