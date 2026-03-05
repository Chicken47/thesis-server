/**
 * screenerMarketScraper.js
 *
 * Scrapes screener.in/market/ for all sectors and their constituent stocks.
 * Generates two output files in /output/:
 *
 *   sectors.csv         → upload directly to the sectors table in Supabase
 *   stocks_insert.sql   → run in Supabase SQL editor AFTER uploading sectors.csv
 *                         (resolves sector_id by name via subquery)
 *
 * Run: node screenerMarketScraper.js
 */

import puppeteer from "puppeteer";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = path.join(__dirname, "../output");

const SCREENER_BASE = "https://www.screener.in";

const PARENT_SECTOR_MAP = {
  IN01: "Mining & Minerals",
  IN02: "Consumer Goods & Services",
  IN03: "Energy",
  IN04: "FMCG & Agriculture",
  IN05: "Financial Services",
  IN06: "Healthcare",
  IN07: "Industrial & Manufacturing",
  IN08: "IT & Technology",
  IN09: "Services & Transportation",
  IN10: "Telecom",
  IN11: "Utilities",
  IN12: "Diversified",
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const launchBrowser = () =>
  puppeteer.launch({
    headless: "new",
    executablePath:
      process.platform === "darwin"
        ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        : undefined,
  });

const csvEscape = (val) => {
  if (val == null) return "";
  const s = String(val);
  // Wrap in quotes if the value contains a comma, quote, or newline
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
};

const sqlEscape = (val) => {
  if (val == null) return "NULL";
  return `'${String(val).replace(/'/g, "''")}'`;
};

/**
 * Scrapes /market/ and returns all leaf-level industry sectors.
 */
const scrapeAllSectors = async (page) => {
  await page.goto(`${SCREENER_BASE}/market/`, { waitUntil: "networkidle2" });
  await sleep(2000);

  return await page.evaluate(() => {
    // Industry links live in td.text inside the market table
    return Array.from(document.querySelectorAll("table td.text a[href]"))
      .map((a) => ({
        name: a.textContent.trim(),
        url: a.getAttribute("href"),
      }))
      .filter(
        (s) =>
          s.name &&
          s.url &&
          // 4-level market URL: /market/IN##/IN####/IN######/IN#########/
          // Last segment varies (9-10 digits), so use \d+ for each level
          /^\/market\/IN\d+\/IN\d+\/IN\d+\/IN\d+\/$/.test(s.url)
      );
  });
};

/**
 * Scrapes all stock rows for a given sector URL.
 * Returns: [{ name, symbol, screener_path }]
 */
const scrapeStocksForSector = async (page, sectorUrl) => {
  await page.goto(`${SCREENER_BASE}${sectorUrl}`, { waitUntil: "networkidle2" });
  await sleep(1500);

  return await page.evaluate(() => {
    return Array.from(document.querySelectorAll("table.data-table tbody tr"))
      .map((row) => {
        const link = row.querySelector("td.text a[href*='/company/']");
        if (!link) return null;
        const href = link.getAttribute("href");
        const match = href.match(/\/company\/([^/]+)\//);
        if (!match) return null;
        return {
          name: link.textContent.trim(),
          symbol: match[1],
          screener_path: href,
        };
      })
      .filter(Boolean);
  });
};

const getParentSector = (url) => {
  const match = url.match(/\/market\/(IN\d{2})\//);
  return match ? (PARENT_SECTOR_MAP[match[1]] ?? null) : null;
};

const main = async () => {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const browser = await launchBrowser();
  const page = await browser.newPage();
  page.on("console", () => {});

  // Collect all data first, then write files
  const sectorRows = [];  // { name, parent_sector, screener_url }
  const stockRows = [];   // { symbol, name, sector_name }
  const failedSectors = [];

  try {
    console.log("Scraping sector list from screener.in/market/ ...");
    const sectors = await scrapeAllSectors(page);
    console.log(`Found ${sectors.length} sectors\n`);

    for (let i = 0; i < sectors.length; i++) {
      const sector = sectors[i];
      const parentSector = getParentSector(sector.url);

      console.log(`[${i + 1}/${sectors.length}] ${sector.name}`);

      sectorRows.push({
        name: sector.name,
        parent_sector: parentSector ?? "",
        screener_url: sector.url,
      });

      let stocks = [];
      try {
        stocks = await scrapeStocksForSector(page, sector.url);
      } catch (err) {
        console.error(`  Scrape failed: ${err.message}`);
        failedSectors.push(sector.name);
        await sleep(2000);
        continue;
      }

      console.log(`  ${stocks.length} stocks`);
      for (const s of stocks) {
        stockRows.push({
          symbol: s.symbol,
          name: s.name,
          screener_path: s.screener_path,
          sector_name: sector.name,
        });
      }

      await sleep(2000);
    }
  } finally {
    await browser.close();
  }

  // ── Write sectors.csv ──────────────────────────────────────────────────────
  const sectorsPath = path.join(OUTPUT_DIR, "sectors.csv");
  const sectorsHeader = "name,parent_sector,screener_url";
  const sectorsLines = sectorRows.map(
    (r) =>
      [csvEscape(r.name), csvEscape(r.parent_sector), csvEscape(r.screener_url)].join(",")
  );
  fs.writeFileSync(sectorsPath, [sectorsHeader, ...sectorsLines].join("\n"), "utf8");
  console.log(`\nWrote ${sectorRows.length} sectors → ${sectorsPath}`);

  // ── Write stocks_insert.sql ────────────────────────────────────────────────
  // De-duplicate: a stock can appear in multiple sectors (rare but possible).
  // Keep the first occurrence.
  const seen = new Set();
  const uniqueStocks = stockRows.filter((s) => {
    if (seen.has(s.symbol)) return false;
    seen.add(s.symbol);
    return true;
  });

  const sqlPath = path.join(OUTPUT_DIR, "stocks_insert.sql");
  const sqlLines = [
    "-- Run this in Supabase SQL editor AFTER uploading sectors.csv",
    "-- sector_id is resolved by name — no manual UUID lookup needed.",
    "",
    "INSERT INTO stocks (symbol, name, screener_path, sector_id)",
    "VALUES",
  ];

  const valueLines = uniqueStocks.map((s, i) => {
    const comma = i < uniqueStocks.length - 1 ? "," : "";
    return (
      `  (${sqlEscape(s.symbol)}, ${sqlEscape(s.name)}, ${sqlEscape(s.screener_path)}, ` +
      `(SELECT id FROM sectors WHERE name = ${sqlEscape(s.sector_name)} LIMIT 1))${comma}`
    );
  });

  sqlLines.push(...valueLines);
  sqlLines.push(
    "ON CONFLICT (symbol) DO UPDATE",
    "  SET name          = EXCLUDED.name,",
    "      screener_path = EXCLUDED.screener_path,",
    "      sector_id     = EXCLUDED.sector_id;"
  );

  fs.writeFileSync(sqlPath, sqlLines.join("\n"), "utf8");
  console.log(`Wrote ${uniqueStocks.length} stocks  → ${sqlPath}`);

  // ── Summary ────────────────────────────────────────────────────────────────
  console.log("\n==========================================");
  console.log(`Sectors : ${sectorRows.length}`);
  console.log(`Stocks  : ${uniqueStocks.length} (unique symbols)`);
  if (failedSectors.length > 0) {
    console.log(`\nFailed sectors (${failedSectors.length}):`);
    failedSectors.forEach((s) => console.log(`  - ${s}`));
  }
  console.log("==========================================");
  console.log("\nNext steps:");
  console.log("  1. Upload output/sectors.csv  → Supabase table editor (sectors table)");
  console.log("  2. Run output/stocks_insert.sql → Supabase SQL editor");
};

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
