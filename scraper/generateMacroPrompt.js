/**
 * generateMacroPrompt.js
 *
 * Scrapes Google News across macro + SEBI topics for Indian markets,
 * assembles all results into a single prompt file.
 *
 * Output: /output/macro_prompt.txt
 * → Paste this into Claude Sonnet 4.6 (extended thinking) to generate macro_context.md
 *
 * Run: node generateMacroPrompt.js
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { fetchGoogleNews } from "./googleNewsScraper.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = path.join(__dirname, "../output");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── Search queries ────────────────────────────────────────────────────────────
// Each has a label (used as section heading in the prompt) and a query string.
const MACRO_QUERIES = [
  {
    label: "RBI Monetary Policy & Interest Rates",
    query: "RBI monetary policy repo rate interest rate India 2025",
  },
  {
    label: "Inflation (CPI / WPI)",
    query: "India CPI WPI inflation retail wholesale price 2025",
  },
  {
    label: "GDP Growth & Economic Outlook",
    query: "India GDP growth forecast economic outlook 2025",
  },
  {
    label: "FII / DII Flows",
    query: "FII DII foreign institutional investor flows NSE BSE India 2025",
  },
  {
    label: "INR / USD Exchange Rate",
    query: "Indian rupee USD exchange rate RBI intervention forex 2025",
  },
  {
    label: "Crude Oil & Commodity Prices",
    query: "crude oil prices India impact Brent WTI 2025",
  },
  {
    label: "Government Budget & Fiscal Policy",
    query: "India government budget fiscal deficit capex spending 2025",
  },
  {
    label: "Banking System Liquidity & Credit Growth",
    query: "India banking liquidity credit growth RBI 2025",
  },
  {
    label: "Nifty / Sensex Market Sentiment",
    query: "Nifty Sensex India stock market outlook valuation 2025",
  },
  {
    label: "SEBI Notices, Enforcement & Compliance",
    query: "SEBI notice enforcement action compliance order India 2025",
  },
];

// ── Scrape all queries ────────────────────────────────────────────────────────
const scrapeAll = async () => {
  const results = [];

  for (let i = 0; i < MACRO_QUERIES.length; i++) {
    const { label, query } = MACRO_QUERIES[i];
    console.log(`[${i + 1}/${MACRO_QUERIES.length}] ${label}`);

    try {
      const articles = await fetchGoogleNews(query, 8);
      results.push({ label, query, articles });
      console.log(`  ${articles.length} articles`);
    } catch (err) {
      console.error(`  Failed: ${err.message}`);
      results.push({ label, query, articles: [] });
    }

    // Polite delay between browser launches
    if (i < MACRO_QUERIES.length - 1) await sleep(3000);
  }

  return results;
};

// ── Build prompt ─────────────────────────────────────────────────────────────
const buildPrompt = (results, scrapedAt) => {
  const newsBlock = results
    .map(({ label, articles }) => {
      if (articles.length === 0) {
        return `### ${label}\n(No articles retrieved)\n`;
      }
      const lines = articles
        .map(
          (a, i) =>
            `${i + 1}. [${a.source}] ${a.title}\n   Published: ${a.time}\n   URL: ${a.url}`
        )
        .join("\n\n");
      return `### ${label}\n${lines}`;
    })
    .join("\n\n---\n\n");

  return `You are a senior Indian equity research analyst with deep knowledge of macroeconomics, RBI policy, and SEBI regulation.

Below is a set of recent news headlines scraped from Google News on ${scrapedAt}, covering key macroeconomic and regulatory topics relevant to Indian equity markets.

Your task is to synthesize all of this into a structured macro context document in Markdown format.

---

## RAW NEWS DATA

${newsBlock}

---

## OUTPUT FORMAT

Generate a file called macro_context.md with exactly these sections:

# Indian Macroeconomic Context
*Last updated: ${scrapedAt}*

## 1. Monetary Policy
- Current repo rate, RBI stance (accommodative / neutral / hawkish)
- Direction of travel (hiking cycle, pause, cutting cycle)
- Key implications for equity markets

## 2. Inflation Environment
- Current CPI and WPI levels and trend
- Components driving inflation (food, fuel, core)
- RBI comfort zone and outlook

## 3. Growth Outlook
- Latest GDP growth estimate and forecast
- PMI, consumption, capex signals
- Sectors likely to benefit or suffer

## 4. Liquidity & Credit
- Banking system liquidity (surplus / deficit)
- Credit growth trends
- Transmission of rate changes to lending rates

## 5. FII / DII Flows
- Recent FII net buying or selling trend
- DII counter-flow pattern
- What is driving foreign flows in/out

## 6. Currency & External Sector
- INR/USD current level and trend
- RBI intervention posture
- CAD, forex reserves, import cover

## 7. Commodity Context
- Crude oil price level and direction (key for India as importer)
- Impact on inflation, CAD, and oil-linked sectors
- Other relevant commodities (metals, agri)

## 8. Fiscal Policy
- Budget deficit status vs target
- Government capex execution pace
- Key spending themes (infra, defence, PLI)

## 9. SEBI & Regulatory Environment
- Recent SEBI enforcement actions or policy changes
- Any notable compliance orders or market structure changes
- Regulatory risk to watch

## 10. Overall Market Positioning
- Nifty/Sensex valuation context (PE, earnings growth)
- Key tailwinds for equities right now
- Key risks and what could derail the market

## 11. Analyst Watchlist
- Top 3 macro factors to monitor over the next quarter
- Data releases / events that could move markets

---

Be specific — include actual numbers, dates, and named entities from the news where available.
Do not hallucinate data that is not supported by the headlines above.
If a section has insufficient data, note it as "Insufficient data — monitor" rather than guessing.
Write in a crisp, analyst-style tone. No fluff.`;
};

// ── Main ──────────────────────────────────────────────────────────────────────
const main = async () => {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const scrapedAt = new Date().toISOString().split("T")[0]; // YYYY-MM-DD

  console.log("Scraping macro news...\n");
  const results = await scrapeAll();

  const prompt = buildPrompt(results, scrapedAt);
  const outputPath = path.join(OUTPUT_DIR, "macro_prompt.txt");
  fs.writeFileSync(outputPath, prompt, "utf8");

  const totalArticles = results.reduce((sum, r) => sum + r.articles.length, 0);

  console.log("\n==========================================");
  console.log(`Topics scraped : ${results.length}`);
  console.log(`Total articles : ${totalArticles}`);
  console.log(`Output         : ${outputPath}`);
  console.log("==========================================");
  console.log("\nNext: paste macro_prompt.txt into Claude Sonnet 4.6 (extended thinking)");
  console.log("Save the response as knowledge_base/macro/macro_context.md");
};

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
