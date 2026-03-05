/**
 * generateDiffPrompt.js
 *
 * Diff engine — decides if a stock needs a full re-analysis.
 *
 * What it compares:
 *   OLD news  = headlines stored in stock_cache at the time of last analysis
 *   NEW news  = freshly scraped headlines right now
 *   DELTA     = headlines in new but not in old (genuinely new information)
 *
 * Financial/screener data stays quarterly (uses existing cache unless --force).
 * News is ALWAYS re-fetched so the delta is fresh.
 *
 * Also pulls sector name from Supabase to include in prompt context.
 *
 * Output:
 *   /output/diff_prompt_{SYMBOL}.txt   → prompt (also paste into Haiku manually)
 *   /output/diff_result_{SYMBOL}.json  → Mistral's parsed decision
 *
 * Usage:
 *   node generateDiffPrompt.js --symbol TCS
 *   node generateDiffPrompt.js --symbol TCS --force
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { execSync } from "child_process";
import { createClient } from "@supabase/supabase-js";
import axios from "axios";
import dotenv from "dotenv";
import { fetchGoogleNews } from "./googleNewsScraper.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.join(__dirname, "..");
const CACHE_DIR = path.join(PROJECT_ROOT, "stock_cache");
const OUTPUT_DIR = path.join(PROJECT_ROOT, "output");

dotenv.config({ path: path.join(PROJECT_ROOT, ".env") });

// ── CLI args ──────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const getArg = (flag, fallback = null) => {
  const i = args.indexOf(flag);
  return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
};
const hasFlag = (flag) => args.includes(flag);

const symbol = (getArg("--symbol") || "").toUpperCase();
const force  = hasFlag("--force");

if (!symbol) {
  console.error("Usage: node generateDiffPrompt.js --symbol SYMBOL [--force]");
  process.exit(1);
}

// ── Cache helpers ─────────────────────────────────────────────────────────────
const metaPath = () => path.join(CACHE_DIR, symbol, "meta.json");
const rawPath  = () => path.join(CACHE_DIR, symbol, "raw_full.json");

const loadMeta = () => {
  try { return JSON.parse(fs.readFileSync(metaPath(), "utf8")); }
  catch { return null; }
};

const loadRaw = () => {
  try { return JSON.parse(fs.readFileSync(rawPath(), "utf8")); }
  catch { return null; }
};

const isCacheAbsent = () => !fs.existsSync(rawPath());

const getScreenerPath = () => {
  const meta = loadMeta();
  return meta?.screener_path || `/company/${symbol}/consolidated/`;
};

// ── Full re-scrape (financial data, quarterly) ────────────────────────────────
const runFullScrape = () => {
  const screenerPath = getScreenerPath();
  console.log(`[diff] Running full scrape for ${symbol}...`);

  const scraperScript = path.join(__dirname, "run_full_scrape.js");
  const raw = execSync(`node "${scraperScript}" "${screenerPath}"`, {
    cwd: PROJECT_ROOT,
    timeout: 180_000,
    maxBuffer: 50 * 1024 * 1024,
  }).toString();

  const data = JSON.parse(raw);
  const stockDir = path.join(CACHE_DIR, symbol);
  fs.mkdirSync(stockDir, { recursive: true });
  fs.writeFileSync(rawPath(), JSON.stringify(data, null, 2));

  const quarter = (() => {
    const d = new Date();
    return `${d.getFullYear()}Q${Math.ceil((d.getMonth() + 1) / 3)}`;
  })();
  fs.writeFileSync(metaPath(), JSON.stringify({
    ticker: symbol,
    screener_path: data.screenerPath || screenerPath,
    quarter,
    scraped_at: new Date().toISOString(),
    has_pl:    !!(data.annualPL?.values?.length),
    has_bs:    !!(data.balanceSheet?.values?.length),
    has_cf:    !!(data.cashFlows?.values?.length),
    has_peers: !!(data.peerComparison?.peers?.length),
    doc_count:  data.documents?.length || 0,
    news_count: data.news?.length || 0,
  }, null, 2));

  console.log(`[diff] Full scrape done for ${symbol}`);
  return data;
};

// ── Local analysis fallback: data/{SYMBOL}_analysis.json ─────────────────────
const loadLocalAnalysis = () => {
  try {
    const p = path.join(PROJECT_ROOT, "data", `${symbol}_analysis.json`);
    const data = JSON.parse(fs.readFileSync(p, "utf8"));
    // Skip failed parses — they have no usable structured output
    if (data.parse_failed || data.verdict === "parse_error") return null;
    // Normalise field names to match DB schema
    return {
      verdict:                data.verdict,
      conviction:             data.conviction,
      key_strengths:          data.key_strengths          || [],
      key_risks:              data.key_risks               || [],
      red_flags:              data.red_flags               || [],
      invalidation_triggers:  data.invalidation_triggers   || [],
      watch_next_quarter:     data.watch_for_next_quarter  || data.watch_next_quarter || "",
      summary:                data.summary                 || "",
      created_at:             data.created_at              || null,
      _source:                "local_file",
    };
  } catch { return null; }
};

// ── Supabase: fetch last analysis + sector name ───────────────────────────────
const fetchSupabaseContext = async () => {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  const hasSupabase = url && key && !url.includes("your_");

  let lastAnalysis = null;
  let sectorName   = null;
  let parentSector = null;

  if (hasSupabase) {
    const supabase = createClient(url, key);
    const [analysisRes, stockRes] = await Promise.all([
      supabase
        .from("analyses")
        .select("verdict, conviction, key_strengths, key_risks, red_flags, invalidation_triggers, watch_next_quarter, summary, created_at, news_sentiment")
        .eq("stock_symbol", symbol)
        .order("created_at", { ascending: false })
        .limit(1)
        .single(),
      supabase
        .from("stocks")
        .select("name, sectors(name, parent_sector)")
        .eq("symbol", symbol)
        .single(),
    ]);

    if (!analysisRes.error) lastAnalysis = analysisRes.data;
    sectorName   = stockRes.data?.sectors?.name          ?? null;
    parentSector = stockRes.data?.sectors?.parent_sector ?? null;
  }

  // Fall back to local data/ file if Supabase has nothing
  if (!lastAnalysis) {
    lastAnalysis = loadLocalAnalysis();
    if (lastAnalysis) console.log(`[diff] Using local analysis file as fallback`);
  }

  return { lastAnalysis, sectorName, parentSector };
};

// ── News helpers ──────────────────────────────────────────────────────────────
const buildNewsQuery = (raw) => {
  const about = raw?.aboutText || "";
  const hint  = about ? about.split(/\s+/).slice(0, 4).join(" ") : symbol;
  return `${hint} NSE stock India`;
};

const buildSectorQuery = (sectorName) =>
  `${sectorName} sector India NSE stocks outlook`;

// Titles already seen at last-analysis time (stored in raw_full.json cache)
const getOldNewsTitles = (raw) =>
  new Set((raw?.news || []).map(n => (n.title || "").toLowerCase().trim()));

// Headlines in newNews that weren't in oldTitles
const getDelta = (newNews, oldTitles) =>
  newNews.filter(n => !oldTitles.has((n.title || "").toLowerCase().trim()));

// Sector news cache: stored per sector slug in stock_cache/_sectors/
const sectorSlug = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, "_");

const loadOldSectorNews = (sectorName) => {
  try {
    const p = path.join(CACHE_DIR, "_sectors", `${sectorSlug(sectorName)}.json`);
    const data = JSON.parse(fs.readFileSync(p, "utf8"));
    return data.news || [];
  } catch { return []; }
};

const saveNewSectorNews = (sectorName, news) => {
  const dir = path.join(CACHE_DIR, "_sectors");
  fs.mkdirSync(dir, { recursive: true });
  const p = path.join(dir, `${sectorSlug(sectorName)}.json`);
  fs.writeFileSync(p, JSON.stringify({ cached_at: new Date().toISOString(), news }, null, 2));
};

// ── Prompt builder ────────────────────────────────────────────────────────────
const buildPrompt = ({ raw, lastAnalysis, sectorName, parentSector, oldNews, newNews, deltaNews, oldSectorNews, newSectorNews, deltaSectorNews, today }) => {

  // ── Sector block ─────────────────────────────────────────────────────────
  const sectorBlock = sectorName
    ? `Sector : ${sectorName}${parentSector ? ` (${parentSector})` : ""}`
    : `Sector : Unknown`;

  // ── Previous analysis block ───────────────────────────────────────────────
  let prevBlock;
  if (lastAnalysis) {
    const date      = lastAnalysis.created_at?.slice(0, 10) ?? "unknown";
    const strengths = (lastAnalysis.key_strengths         || []).map(s => `  - ${s}`).join("\n") || "  (none)";
    const risks     = (lastAnalysis.key_risks             || []).map(r => `  - ${r}`).join("\n") || "  (none)";
    const redFlags  = (lastAnalysis.red_flags             || []).map(f => `  - ${f}`).join("\n") || "  (none)";
    const triggers  = (lastAnalysis.invalidation_triggers || []).map(t => `  - ${t}`).join("\n") || "  (none)";

    prevBlock = `## PREVIOUS ANALYSIS (${date})
Verdict    : ${(lastAnalysis.verdict || "unknown").toUpperCase()}
Conviction : ${lastAnalysis.conviction ?? "?"}/10
Summary    : ${lastAnalysis.summary || "(none)"}

Key Strengths:
${strengths}

Key Risks:
${risks}

Red Flags:
${redFlags}

Invalidation Triggers (re-analyse immediately if any of these hit):
${triggers}

Watch Next Quarter: ${lastAnalysis.watch_next_quarter || "(none)"}`;
  } else {
    prevBlock = `## PREVIOUS ANALYSIS\n(None — stock has never been analysed before. Recommend full analysis.)`;
  }

  // ── Old news block (known at last analysis time) ──────────────────────────
  const oldNewsBlock = oldNews.length
    ? oldNews.slice(0, 10).map((n, i) =>
        `${i + 1}. [${n.source || ""}] ${n.title || ""} (${n.time || ""})`
      ).join("\n")
    : "(No news was stored at last analysis)";

  // ── New news block (freshly scraped today) ────────────────────────────────
  const newNewsBlock = newNews.length
    ? newNews.slice(0, 10).map((n, i) =>
        `${i + 1}. [${n.source || ""}] ${n.title || ""} (${n.time || ""})`
      ).join("\n")
    : "(No news scraped)";

  // ── Delta block (what's genuinely new) ───────────────────────────────────
  const deltaBlock = deltaNews.length
    ? deltaNews.map((n, i) =>
        `${i + 1}. [${n.source || ""}] ${n.title || ""} (${n.time || ""})`
      ).join("\n")
    : "(No new headlines — all current news was already known at last analysis)";

  // ── Sector news blocks ────────────────────────────────────────────────────
  const oldSectorBlock = oldSectorNews.length
    ? oldSectorNews.slice(0, 6).map((n, i) =>
        `${i + 1}. [${n.source || ""}] ${n.title || ""} (${n.time || ""})`
      ).join("\n")
    : "(none cached)";

  const newSectorBlock = newSectorNews.length
    ? newSectorNews.slice(0, 6).map((n, i) =>
        `${i + 1}. [${n.source || ""}] ${n.title || ""} (${n.time || ""})`
      ).join("\n")
    : "(none scraped)";

  const deltaSectorBlock = deltaSectorNews.length
    ? deltaSectorNews.map((n, i) =>
        `${i + 1}. [${n.source || ""}] ${n.title || ""} (${n.time || ""})`
      ).join("\n")
    : "(no new sector-level headlines)";

  // ── Current ratios ────────────────────────────────────────────────────────
  const ratios = (raw?.ratios || [])
    .filter(r => r.name && r.value)
    .map(r => `  ${r.name}: ${r.value}`)
    .join("\n") || "  (unavailable)";

  return `You are a senior Indian equity analyst gatekeeper. Today is ${today}.
Your job: decide if ${symbol} needs a full re-analysis based on what has changed since the last one.

${sectorBlock}

---

${prevBlock}

---

## NEWS AT TIME OF LAST ANALYSIS (old — already factored in)

${oldNewsBlock}

---

## FRESH NEWS (scraped today — ${today})

${newNewsBlock}

---

## DELTA — NEW STOCK HEADLINES NOT SEEN BEFORE

${deltaBlock}

---

## SECTOR NEWS AT TIME OF LAST ANALYSIS (${sectorName ?? "unknown sector"})

${oldSectorBlock}

---

## FRESH SECTOR NEWS (today — ${today})

${newSectorBlock}

---

## SECTOR DELTA — NEW SECTOR HEADLINES

${deltaSectorBlock}

---

## CURRENT RATIOS (quarterly data from screener)

${ratios}

---

## YOUR TASK

Focus on the DELTA sections (stock and sector). Use sector delta to distinguish company-specific issues from sector-wide headwinds (sector headwinds alone are lower urgency; company-specific events are higher urgency).

Trigger a re-analysis if ANY of these are true:
- A listed invalidation trigger has been hit
- New red flag not previously known (fraud, pledge, regulatory action, management exit)
- Major earnings surprise vs expectations
- Large corporate event (acquisition, fundraise, demerger, SEBI order)
- Competitive shift that changes the thesis
- Conviction would likely move by 2+ points given the delta

Skip re-analysis if:
- Delta is empty or contains only routine price/market noise
- New headlines repeat themes already known from old news
- No event that would change verdict or conviction materially

Respond ONLY with valid JSON, no other text:
{
  "should_reanalyse": true or false,
  "urgency": "high" or "medium" or "low",
  "reason": "one concise sentence",
  "key_changes": ["headline or event 1", "headline or event 2"]
}`;
};

// ── Ollama: run mistral locally ───────────────────────────────────────────────
const runMistral = async (prompt) => {
  const response = await axios.post(
    "http://localhost:11434/api/chat",
    {
      model: "mistral:latest",
      stream: false,
      messages: [{ role: "user", content: prompt }],
    },
    { timeout: 120_000 }
  );
  return response.data?.message?.content?.trim() ?? "";
};

// ── Main ──────────────────────────────────────────────────────────────────────
const main = async () => {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  // Step 1: Ensure financial cache exists (full scrape only if absent or --force)
  if (force || isCacheAbsent()) {
    const why = force ? "--force flag" : "no cache found";
    console.log(`[diff] Scraping financial data for ${symbol} (${why})...`);
    try {
      runFullScrape();
    } catch (err) {
      console.error(`[diff] Scrape failed: ${err.message}`);
      process.exit(1);
    }
  } else {
    const meta = loadMeta();
    console.log(`[diff] Using cached financials for ${symbol} (quarter: ${meta?.quarter}, scraped: ${meta?.scraped_at?.slice(0, 10)})`);
  }

  const raw = loadRaw();
  if (!raw) {
    console.error(`[diff] Could not load raw_full.json for ${symbol}`);
    process.exit(1);
  }

  // Step 2: Fetch last analysis + sector from Supabase
  console.log(`[diff] Fetching analysis + sector from Supabase...`);
  const { lastAnalysis, sectorName, parentSector } = await fetchSupabaseContext();
  if (lastAnalysis) {
    console.log(`[diff] Last analysis: ${lastAnalysis.verdict} (${lastAnalysis.conviction}/10) on ${lastAnalysis.created_at?.slice(0, 10)}`);
  } else {
    console.log(`[diff] No previous analysis found`);
  }
  if (sectorName) console.log(`[diff] Sector: ${sectorName}`);

  // Step 3: Always scrape fresh stock news
  const newsQuery = buildNewsQuery(raw);
  console.log(`[diff] Scraping fresh stock news: "${newsQuery}"...`);
  let newNews = [];
  try {
    newNews = await fetchGoogleNews(newsQuery, 10);
    console.log(`[diff] Got ${newNews.length} fresh stock headlines`);
  } catch (err) {
    console.error(`[diff] Stock news scrape failed (non-fatal): ${err.message}`);
  }

  // Step 4: Stock news delta (old = stored in raw_full.json cache)
  const oldNews   = raw?.news || [];
  const oldTitles = getOldNewsTitles(raw);
  const deltaNews = getDelta(newNews, oldTitles);
  console.log(`[diff] Stock news — old: ${oldNews.length} | new: ${newNews.length} | delta: ${deltaNews.length}`);

  // Step 5: Sector news delta
  let newSectorNews = [], oldSectorNews = [], deltaSectorNews = [];
  if (sectorName) {
    oldSectorNews = loadOldSectorNews(sectorName);
    const sectorQuery = buildSectorQuery(sectorName);
    console.log(`[diff] Scraping fresh sector news: "${sectorQuery}"...`);
    try {
      newSectorNews = await fetchGoogleNews(sectorQuery, 8);
      console.log(`[diff] Got ${newSectorNews.length} fresh sector headlines`);
      saveNewSectorNews(sectorName, newSectorNews);
    } catch (err) {
      console.error(`[diff] Sector news scrape failed (non-fatal): ${err.message}`);
    }
    const oldSectorTitles = new Set(oldSectorNews.map(n => (n.title || "").toLowerCase().trim()));
    deltaSectorNews = getDelta(newSectorNews, oldSectorTitles);
    console.log(`[diff] Sector news — old: ${oldSectorNews.length} | new: ${newSectorNews.length} | delta: ${deltaSectorNews.length}`);
  }

  // Step 6: Build prompt
  const today = new Date().toISOString().slice(0, 10);
  const prompt = buildPrompt({ raw, lastAnalysis, sectorName, parentSector, oldNews, newNews, deltaNews, oldSectorNews, newSectorNews, deltaSectorNews, today });

  const outputPath = path.join(OUTPUT_DIR, `diff_prompt_${symbol}.txt`);
  fs.writeFileSync(outputPath, prompt, "utf8");

  console.log("\n==========================================");
  console.log(`Symbol        : ${symbol}`);
  console.log(`Sector        : ${sectorName ?? "unknown"}`);
  console.log(`Stock news    : old ${oldNews.length} → new ${newNews.length} (delta: ${deltaNews.length})`);
  console.log(`Sector news   : old ${oldSectorNews.length} → new ${newSectorNews.length} (delta: ${deltaSectorNews.length})`);
  console.log(`Prev verdict  : ${lastAnalysis ? `${lastAnalysis.verdict} ${lastAnalysis.conviction}/10` : "none"}`);
  console.log(`Prompt        : ${outputPath}`);
  console.log("==========================================");

  // Step 7: Run Mistral
  console.log("\n[diff] Running mistral:latest via Ollama...");
  let mistralRaw = "";
  try {
    mistralRaw = await runMistral(prompt);
  } catch (err) {
    console.error(`[diff] Ollama call failed: ${err.message}`);
    console.log("[diff] Prompt still saved — paste into Claude Haiku manually.");
    return;
  }

  // Parse JSON (mistral sometimes wraps in markdown fences)
  let result;
  try {
    const jsonMatch = mistralRaw.match(/\{[\s\S]*\}/);
    result = JSON.parse(jsonMatch ? jsonMatch[0] : mistralRaw);
  } catch {
    console.log("\n[diff] Could not parse Mistral response as JSON. Raw output:");
    console.log(mistralRaw);
    return;
  }

  const should  = result.should_reanalyse;
  const urgency = (result.urgency || "low").toUpperCase();
  const reason  = result.reason || "";
  const changes = result.key_changes || [];

  console.log("\n==========================================");
  console.log(`  Decision : ${should ? "RE-ANALYSE" : "SKIP"}`);
  console.log(`  Urgency  : ${urgency}`);
  console.log(`  Reason   : ${reason}`);
  if (changes.length) {
    console.log(`  Changes  :`);
    changes.forEach(c => console.log(`    - ${c}`));
  }
  console.log("==========================================");

  const resultPath = path.join(OUTPUT_DIR, `diff_result_${symbol}.json`);
  fs.writeFileSync(resultPath, JSON.stringify(
    { symbol, sector: sectorName, ...result, ran_at: new Date().toISOString() },
    null, 2
  ));
  console.log(`\nResult saved: ${resultPath}`);
};

main().catch((err) => {
  console.error("Fatal error:", err.message);
  process.exit(1);
});
