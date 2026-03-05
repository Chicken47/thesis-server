/**
 * generateSectorPrompt.js
 *
 * Interactive sector research scraper.
 *
 * Flow:
 *   1. Enter a sector name → see matching sector profiles (numbered)
 *   2. Pick profiles by number  e.g.  0,2
 *   3. Script scrapes Google News for every sub-topic in chosen profiles
 *   4. Saves two files to /output/:
 *        sector_prompt_{SLUG}.txt   → paste into Claude Sonnet 4.6 (web)
 *        sector_context_{SLUG}.md   → placeholder at knowledge_base/sectors/
 *
 * Run: node generateSectorPrompt.js
 *
 * NOTE:  macro_context.md is assumed to already exist.  This prompt explicitly
 *        tells Claude NOT to repeat macro-level content (rates, GDP, FII flows,
 *        INR, crude) — keep the output sector-specific and concise.
 */

import fs from "fs";
import path from "path";
import readline from "readline";
import { fileURLToPath } from "url";
import { fetchGoogleNews } from "./googleNewsScraper.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.join(__dirname, "..");
const OUTPUT_DIR = path.join(PROJECT_ROOT, "output");
const KB_SECTORS = path.join(PROJECT_ROOT, "knowledge_base", "sectors");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ─────────────────────────────────────────────────────────────────────────────
// SECTOR PROFILES
// Each profile = display name + a set of targeted queries.
// Queries are sector-specific — macro (rates, FII, GDP, INR) deliberately excluded.
// ─────────────────────────────────────────────────────────────────────────────
const SECTORS = [
  {
    key: "railways",
    label: "Railways & Rail Infrastructure",
    queries: [
      { label: "Railway Budget & Capex", query: "India railway budget capex investment 2025 2026" },
      { label: "IRCTC Performance & Outlook", query: "IRCTC earnings revenue quarterly results 2025 2026" },
      { label: "Rail PSU stocks (RVNL, IRFC, Titagarh)", query: "RVNL IRFC Titagarh rail stocks India 2025 2026" },
      { label: "Railway Privatisation & Freight", query: "Indian Railways privatisation freight corridor Vande Bharat 2025 2026" },
    ],
  },
  {
    key: "it_services",
    label: "IT Services & Outsourcing",
    queries: [
      { label: "TCS Infosys Wipro Quarterly Results", query: "TCS Infosys Wipro HCL quarterly results revenue 2025 2026" },
      { label: "IT Deal Wins & Pipeline", query: "India IT services deal wins TCV outsourcing 2025 2026" },
      { label: "AI Impact on Indian IT", query: "AI artificial intelligence impact Indian IT sector jobs margins 2025 2026" },
      { label: "US Tech Spending & Visa Policy", query: "US tech spending IT budget H1B visa India impact 2025 2026" },
    ],
  },
  {
    key: "banking_psu",
    label: "PSU Banking",
    queries: [
      { label: "PSU Bank Earnings & NPA", query: "SBI PNB Bank of Baroda PSU bank NPA earnings results 2025 2026" },
      { label: "PSU Bank Recapitalisation & Credit Growth", query: "PSU bank recapitalisation credit growth India 2025 2026" },
      { label: "RBI NPA & Asset Quality Rules", query: "RBI NPA provisioning asset quality banking India 2025 2026" },
    ],
  },
  {
    key: "banking_private",
    label: "Private Sector Banking",
    queries: [
      { label: "HDFC ICICI Kotak Axis Results", query: "HDFC ICICI Kotak Axis private bank quarterly results 2025 2026" },
      { label: "Net Interest Margin Outlook", query: "private bank NIM net interest margin India 2025 2026" },
      { label: "Microfinance & Retail Credit Stress", query: "microfinance MFI retail credit stress India 2025 2026" },
    ],
  },
  {
    key: "nbfc",
    label: "NBFCs & Fintech",
    queries: [
      { label: "NBFC Credit Growth & Regulation", query: "NBFC credit growth regulation RBI India 2025 2026" },
      { label: "Bajaj Finance Shriram Muthoot Results", query: "Bajaj Finance Shriram Muthoot NBFC quarterly results 2025 2026" },
      { label: "Digital Lending & Fintech Regulation", query: "digital lending fintech BNPL regulation India SEBI RBI 2025 2026" },
    ],
  },
  {
    key: "telecom",
    label: "Telecom",
    queries: [
      { label: "Jio Airtel Vi ARPU & Tariff Hike", query: "Jio Airtel Vi telecom ARPU tariff hike quarterly results 2025 2026" },
      { label: "5G Rollout & Capex", query: "India 5G rollout spectrum capex Jio Airtel 2025 2026" },
      { label: "Telecom Regulation & AGR Dues", query: "TRAI regulation AGR dues spectrum auction India 2025 2026" },
    ],
  },
  {
    key: "renewable_energy",
    label: "Renewable Energy & Green Power",
    queries: [
      { label: "Solar Wind Capacity Additions India", query: "India solar wind renewable energy capacity additions 2025 2026" },
      { label: "IREDA NTPC Green NHPC Results", query: "IREDA NTPC Green Energy NHPC RE results quarterly 2025 2026" },
      { label: "Government PLI & Energy Transition Policy", query: "India green energy PLI policy subsidy solar manufacturing 2025 2026" },
      { label: "RE Sector Challenges: Land, Grid, Financing", query: "India renewable energy land acquisition grid curtailment financing challenges 2025 2026" },
    ],
  },
  {
    key: "capital_goods",
    label: "Capital Goods & Engineering",
    queries: [
      { label: "L&T Siemens ABB Order Wins", query: "L&T Siemens ABB Thermax capital goods order wins results 2025 2026" },
      { label: "Defence PLI & Indigenisation", query: "India defence sector PLI indigenisation HAL BEL order book 2025 2026" },
      { label: "Infrastructure Capex & Execution", query: "India infrastructure capex execution government spending 2025 2026" },
    ],
  },
  {
    key: "pharma",
    label: "Pharmaceuticals & Healthcare",
    queries: [
      { label: "Sun Cipla Dr Reddy Quarterly", query: "Sun Pharma Cipla Dr Reddy quarterly results earnings 2025 2026" },
      { label: "US Generic Drug Market & USFDA", query: "India pharma US generics USFDA warning letter 2025 2026" },
      { label: "API & Domestic Formulations Growth", query: "India API active pharmaceutical ingredient domestic formulations growth 2025 2026" },
    ],
  },
  {
    key: "fmcg",
    label: "FMCG & Consumer Staples",
    queries: [
      { label: "HUL Nestle Dabur ITC Volume Growth", query: "HUL Nestle Dabur ITC FMCG volume growth quarterly 2025 2026" },
      { label: "Rural Consumption Recovery", query: "India rural consumption demand recovery FMCG 2025 2026" },
      { label: "Input Cost & Margin Trends", query: "FMCG input cost raw material margin India palm oil 2025 2026" },
    ],
  },
  {
    key: "auto",
    label: "Automobiles & Auto Ancillaries",
    queries: [
      { label: "Maruti Tata Motors M&M Volumes", query: "Maruti Suzuki Tata Motors Mahindra auto sales volume 2025 2026" },
      { label: "EV Adoption & Transition", query: "India EV electric vehicle adoption sales two-wheeler four-wheeler 2025 2026" },
      { label: "Auto Component & Ancillary Outlook", query: "India auto ancillary component exports PLI 2025 2026" },
    ],
  },
  {
    key: "real_estate",
    label: "Real Estate & Housing",
    queries: [
      { label: "Residential Sales Volume & Prices", query: "India residential real estate housing sales launches prices 2025 2026" },
      { label: "DLF Godrej Oberoi Prestige Results", query: "DLF Godrej Properties Oberoi Prestige real estate quarterly 2025 2026" },
      { label: "Affordable Housing & PMAY", query: "India affordable housing PMAY government policy 2025 2026" },
    ],
  },
  {
    key: "metals_mining",
    label: "Metals & Mining",
    queries: [
      { label: "Steel Aluminium Copper Prices India", query: "India steel aluminium copper prices Tata Steel Hindalco 2025 2026" },
      { label: "China Demand & Global Commodity Cycle", query: "China steel demand global commodity cycle metals 2025 2026" },
      { label: "Coal & Iron Ore Mining India", query: "India coal iron ore mining Coal India production 2025 2026" },
    ],
  },
  {
    key: "oil_gas",
    label: "Oil, Gas & Petrochemicals",
    queries: [
      { label: "Reliance RIL O2C & Petchem", query: "Reliance Industries O2C petrochemicals refining margins 2025 2026" },
      { label: "ONGC Oil India Upstream Results", query: "ONGC Oil India upstream production quarterly results 2025 2026" },
      { label: "CGD Gas Distribution IGL Mahanagar", query: "IGL Mahanagar Gas city gas distribution CNG PNG India 2025 2026" },
    ],
  },
  {
    key: "paints_chemicals",
    label: "Paints & Specialty Chemicals",
    queries: [
      { label: "Asian Paints Berger Kansai Competition", query: "Asian Paints Berger Kansai paints India competition margins 2025 2026" },
      { label: "Specialty Chemicals Export & China+1", query: "India specialty chemicals export China plus one 2025 2026" },
    ],
  },
  {
    key: "data_analytics",
    label: "Data Analytics & AI Services",
    queries: [
      { label: "Latentview KPIT Mphasis AI Results", query: "Latentview KPIT Mphasis Happiest Minds data analytics AI quarterly 2025 2026" },
      { label: "GenAI Adoption & Enterprise Spend", query: "India data analytics AI enterprise spending GenAI adoption 2025 2026" },
      { label: "Small IT & Analytics Valuations", query: "India mid-small IT analytics valuation multiples 2025 2026" },
    ],
  },
  {
    key: "insurance",
    label: "Insurance",
    queries: [
      { label: "LIC HDFC Life SBI Life ICICI Lombard Results", query: "LIC HDFC Life SBI Life ICICI Lombard insurance quarterly 2025 2026" },
      { label: "Health Insurance Growth & Regulation", query: "India health insurance growth IRDAI regulation 2025 2026" },
    ],
  },
  {
    key: "microfinance_sme",
    label: "Microfinance & SME Lending",
    queries: [
      { label: "MFI Asset Quality Stress", query: "India microfinance MFI NPA stress borrower overleveraging 2025 2026" },
      { label: "SME MSME Credit Growth", query: "India SME MSME lending credit growth fintech 2025 2026" },
    ],
  },
  {
    key: "aviation_logistics",
    label: "Aviation & Logistics",
    queries: [
      { label: "IndiGo Air India Aviation Outlook", query: "IndiGo Air India aviation passenger traffic yields 2025 2026" },
      { label: "Logistics & Warehousing Growth", query: "India logistics warehousing Delhivery Bluedart 3PL growth 2025 2026" },
    ],
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────────────────────
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
const ask = (q) => new Promise((res) => rl.question(q, res));

function fuzzyMatch(input, label, key) {
  const q = input.toLowerCase().replace(/[^a-z0-9]/g, "");
  const haystack = (label + " " + key).toLowerCase().replace(/[^a-z0-9 ]/g, "");
  return haystack.split(" ").some((word) => word.startsWith(q)) || haystack.replace(/ /g, "").includes(q);
}

function slugify(label) {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
}

// ─────────────────────────────────────────────────────────────────────────────
// SCRAPE
// ─────────────────────────────────────────────────────────────────────────────
async function scrapeQueries(queries) {
  const results = [];
  for (let i = 0; i < queries.length; i++) {
    const { label, query } = queries[i];
    process.stdout.write(`  [${i + 1}/${queries.length}] ${label} ... `);
    try {
      const articles = await fetchGoogleNews(query, 7);
      results.push({ label, query, articles });
      console.log(`${articles.length} articles`);
    } catch (err) {
      console.log(`FAILED: ${err.message}`);
      results.push({ label, query, articles: [] });
    }
    if (i < queries.length - 1) await sleep(2500);
  }
  return results;
}

// ─────────────────────────────────────────────────────────────────────────────
// PROMPT BUILDER
// ─────────────────────────────────────────────────────────────────────────────
function buildPrompt(sectorLabel, results, scrapedAt) {
  const newsBlock = results
    .map(({ label, articles }) => {
      if (!articles.length) return `### ${label}\n(No articles retrieved)\n`;
      const lines = articles
        .map(
          (a, i) =>
            `${i + 1}. [${a.source}] ${a.title}\n   Published: ${a.time}\n   URL: ${a.url}`
        )
        .join("\n\n");
      return `### ${label}\n${lines}`;
    })
    .join("\n\n---\n\n");

  return `You are a senior Indian equity research analyst specialising in sector analysis.

The Indian macro context (RBI rates, GDP outlook, FII/DII flows, INR, crude oil, fiscal policy, SEBI) is already documented in a separate macro_context.md file.

DO NOT repeat any macro-level content. Focus ONLY on what is specific to the ${sectorLabel} sector.

Below is fresh news scraped from Google News India on ${scrapedAt} across key sub-topics for this sector.

---

## RAW NEWS DATA — ${sectorLabel.toUpperCase()}

${newsBlock}

---

## OUTPUT FORMAT

Generate a concise sector context file in Markdown. Keep it brief — 400–600 words max. No fluff. Analyst-style prose and bullet points only.

Use exactly this structure:

# ${sectorLabel} — Sector Context
*Last updated: ${scrapedAt}*

## Growth Outlook
- Key growth drivers specific to this sector right now (cite numbers/dates from headlines)
- Demand trends, volume data, capacity additions
- Government scheme or policy tailwind directly relevant to this sector (not generic GDP/capex)

## Key Listed Players & Recent Positioning
- Bullet per major listed company: one-liner on their current position (results, deal wins, concerns)
- Who is gaining share vs losing ground

## Sector-Specific Risks
- Risks unique to this sector NOT already covered in macro (e.g. regulatory, tech disruption, competition, margin pressure)
- Do NOT mention: RBI rates, general FII selling, INR depreciation, or crude oil unless they are THE defining risk for this sector

## Valuation Context
- Current P/E band or EV/EBITDA range for the sector (if available in data)
- Whether the sector is at premium/discount vs historical or vs broader Nifty
- Any re-rating or de-rating catalysts visible

## What to Watch (Next Quarter)
- 2–3 specific data points or events that will move sector stocks in the next 90 days
- Named triggers (policy decisions, results dates, macro data specific to sector)

---

Rules:
- Be specific — use actual company names, numbers, and dates from the news above
- Do NOT hallucinate data not present in the headlines
- If a section has no supporting data, write: "(Insufficient data — monitor)"
- Write in crisp analyst prose — no marketing language
- Total output: 400–600 words. Concise is better than comprehensive here.`;
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN
// ─────────────────────────────────────────────────────────────────────────────
async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  fs.mkdirSync(KB_SECTORS, { recursive: true });

  console.log("\n╔══════════════════════════════════════════════╗");
  console.log("║   Sector Context Generator                   ║");
  console.log("╚══════════════════════════════════════════════╝\n");

  // ── Step 1: sector search ────────────────────────────────────────────────
  const input = (await ask("Enter sector name (e.g. railways, banking, IT): ")).trim();
  if (!input) { console.log("No input. Exiting."); rl.close(); return; }

  const matches = SECTORS.filter((s) => fuzzyMatch(input, s.label, s.key));

  if (!matches.length) {
    console.log("\nNo matching sectors found. Available sectors:\n");
    SECTORS.forEach((s, i) => console.log(`  ${i.toString().padStart(2)}. ${s.label}`));
    const fallback = (await ask("\nEnter number(s) to select (e.g. 0,3,5): ")).trim();
    const picked = fallback.split(",").map((n) => parseInt(n.trim(), 10)).filter((n) => !isNaN(n) && SECTORS[n]);
    if (!picked.length) { console.log("Nothing selected. Exiting."); rl.close(); return; }
    matches.push(...picked.map((i) => SECTORS[i]));
  } else {
    console.log(`\nFound ${matches.length} match(es):\n`);
    matches.forEach((s, i) => console.log(`  ${i}. ${s.label}  (${s.queries.length} sub-topics)`));
    console.log("");
    const sel = (await ask("Select by number(s) (e.g. 0,2) or press Enter for all: ")).trim();
    if (sel) {
      const idxs = sel.split(",").map((n) => parseInt(n.trim(), 10)).filter((n) => !isNaN(n) && matches[n]);
      if (!idxs.length) { console.log("Nothing selected. Exiting."); rl.close(); return; }
      matches.splice(0, matches.length, ...idxs.map((i) => matches[i]));
    }
  }

  rl.close();

  // ── Step 2: build combined query list ───────────────────────────────────
  const combinedLabel = matches.map((s) => s.label).join(" + ");
  const allQueries = matches.flatMap((s) => s.queries);
  const slug = matches.length === 1 ? matches[0].key : slugify(combinedLabel);

  console.log(`\nSector  : ${combinedLabel}`);
  console.log(`Queries : ${allQueries.length}`);
  console.log(`\nScraping Google News India...\n`);

  // ── Step 3: scrape ───────────────────────────────────────────────────────
  const scrapedAt = new Date().toISOString().split("T")[0];
  const results = await scrapeQueries(allQueries);
  const totalArticles = results.reduce((sum, r) => sum + r.articles.length, 0);

  // ── Step 4: save prompt ──────────────────────────────────────────────────
  const prompt = buildPrompt(combinedLabel, results, scrapedAt);
  const promptPath = path.join(OUTPUT_DIR, `sector_prompt_${slug}.txt`);
  fs.writeFileSync(promptPath, prompt, "utf8");

  // ── Step 5: save placeholder .md in knowledge_base/sectors/ ─────────────
  const mdPath = path.join(KB_SECTORS, `sector_context_${slug}.md`);
  const mdPlaceholder = `# ${combinedLabel} — Sector Context
*Last updated: ${scrapedAt}*

> **TODO:** Paste Claude's response from \`output/sector_prompt_${slug}.txt\` here.
> Delete this line once filled in.
`;

  // Only write placeholder if .md doesn't already exist
  if (!fs.existsSync(mdPath)) {
    fs.writeFileSync(mdPath, mdPlaceholder, "utf8");
  }

  // ── Done ─────────────────────────────────────────────────────────────────
  console.log("\n══════════════════════════════════════════════════");
  console.log(`Sector          : ${combinedLabel}`);
  console.log(`Articles scraped: ${totalArticles} across ${results.length} topics`);
  console.log(`\nFiles saved:`);
  console.log(`  Prompt  → output/sector_prompt_${slug}.txt`);
  console.log(`  Context → knowledge_base/sectors/sector_context_${slug}.md  (placeholder)`);
  console.log("══════════════════════════════════════════════════");
  console.log("\nNext steps:");
  console.log("  1. Open the prompt file and paste into Claude Sonnet 4.6 (web)");
  console.log("  2. Copy Claude's response into the .md placeholder file");
  console.log(`     knowledge_base/sectors/sector_context_${slug}.md`);
}

main().catch((err) => {
  console.error("\nFatal error:", err);
  rl.close();
  process.exit(1);
});
