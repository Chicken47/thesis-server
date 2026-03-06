/**
 * Screener.in scraper — axios + cheerio, no browser required.
 * Replaces the Puppeteer-based implementation for server environments with limited CPU.
 */

import axios from "axios";
import * as cheerio from "cheerio";

const PAGE_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  Accept:
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
  "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
  "Accept-Encoding": "gzip, deflate, br",
  Connection: "keep-alive",
  "Upgrade-Insecure-Requests": "1",
};

const API_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
  Accept: "application/json, text/javascript, */*; q=0.01",
  Referer: "https://www.screener.in/",
  "X-Requested-With": "XMLHttpRequest",
};

async function fetchHtml(url) {
  const { data } = await axios.get(url, {
    headers: PAGE_HEADERS,
    timeout: 30000,
    responseType: "text",
  });
  return data;
}

/** Extract Screener's internal company numeric ID from embedded HTML */
function extractCompanyId($) {
  // 1. data attribute on chart container
  const fromAttr =
    $("[data-company_id]").attr("data-company_id") ||
    $("[data-company-id]").attr("data-company-id");
  if (fromAttr) return fromAttr;

  // 2. Script tag: var id = 12345  or  id: 12345
  let id = null;
  $("script").each((_, el) => {
    const text = $(el).html() || "";
    const m =
      text.match(/\bvar\s+id\s*=\s*['"']?(\d+)['"']?/) ||
      text.match(/"company_id"\s*:\s*(\d+)/) ||
      text.match(/company\.id\s*=\s*(\d+)/);
    if (m) {
      id = m[1];
      return false;
    }
  });
  if (id) return id;

  // 3. Meta tag
  const meta = $('meta[name="company_id"]').attr("content");
  if (meta) return meta;

  return null;
}

async function fetchChartData(companyId, isConsolidated) {
  try {
    const q = isConsolidated ? "consolidated=true" : "consolidated=false";
    const url = `https://www.screener.in/api/company/${companyId}/chart/?q=Price&days=365&${q}`;
    const { data } = await axios.get(url, {
      headers: API_HEADERS,
      timeout: 15000,
    });
    return data;
  } catch {
    return null;
  }
}

async function fetchPeerComparison(companyId) {
  try {
    const url = `https://www.screener.in/api/company/${companyId}/peers/`;
    const { data: html } = await axios.get(url, {
      headers: { ...API_HEADERS, Accept: "text/html, */*" },
      timeout: 15000,
      responseType: "text",
    });

    const $ = cheerio.load(html);
    const table = $("table.data-table").first();
    if (!table.length) return { headings: [], peers: [], median: null };

    // Screener injects the table with headers in the first tbody <tr> (no <thead>)
    const headerRow = table.find("tbody tr:first-child");
    const headings = headerRow
      .find("th")
      .map((_, th) => {
        const tooltip = $(th).attr("data-tooltip");
        if (tooltip) return tooltip;
        return $(th).text().replace(/\s+/g, " ").trim();
      })
      .get()
      .filter((h) => h);

    const peers = table
      .find("tbody tr")
      .filter((_, row) => $(row).find("td").length > 0)
      .map((_, row) => {
        const cells = $(row).find("td");
        const obj = {};
        headings.forEach((h, i) => {
          const cell = cells.eq(i);
          const link = cell.find("a");
          obj[h] = link.length ? link.text().trim() : cell.text().trim();
        });
        return obj;
      })
      .get()
      .filter((p) => Object.values(p).some((v) => v));

    const medianRow = table.find("tfoot tr");
    let median = null;
    if (medianRow.length) {
      const cells = medianRow.find("td");
      median = {};
      headings.forEach((h, i) => {
        median[h] = cells.eq(i).text().trim() || "";
      });
    }

    return { headings, peers, median };
  } catch {
    return { headings: [], peers: [], median: null };
  }
}

// ── HTML parsers (all sync, operate on a loaded cheerio $ instance) ────────────

function getAboutText($) {
  return $(".show-more-box.about > p").text().trim() || "";
}

function getRatios($) {
  return $("#top-ratios li")
    .map((_, el) => ({
      name: $(el).find(".name").text().trim(),
      value: $(el).find(".value .number").text().trim(),
    }))
    .get()
    .filter((r) => r.name);
}

function getShareholding($) {
  const table = $(
    "#shareholding .responsive-holder .data-table"
  ).first();
  if (!table.length) return [];

  const headers = table
    .find("thead th:not(.text)")
    .map((_, th) => $(th).text().trim())
    .get();

  return table
    .find("tbody tr:not(.sub)")
    .map((_, row) => {
      const cells = $(row).find("td");
      const rowData = { category: cells.eq(0).text().trim() || "" };
      headers.forEach((col, i) => {
        rowData[col] = cells.eq(i + 1).text().trim() || "";
      });
      return rowData;
    })
    .get()
    .filter((r) => r.category);
}

function getQuartersData($) {
  const section = $("#quarters");
  const table = section.find("table.data-table").first();
  if (!table.length) return { headings: [], values: [] };

  const headings = table
    .find("thead th")
    .slice(1)
    .map((_, th) => $(th).text().trim())
    .get();

  const values = table
    .find("tbody tr")
    .map((_, row) => {
      const $row = $(row);
      const category = $row.find("td.text").text().trim() || "";
      const vals = $row
        .find("td")
        .slice(1)
        .map((_, td) => $(td).text().trim())
        .get();
      return { category, values: vals };
    })
    .get()
    .filter((r) => r.category);

  return { headings, values };
}

function getProsConsData($) {
  return {
    pros: $(".pros li")
      .map((_, li) => $(li).text().trim())
      .get(),
    cons: $(".cons li")
      .map((_, li) => $(li).text().trim())
      .get(),
  };
}

function scrapeFinancialTable($, sectionId) {
  const section = $(`#${sectionId}`);
  const table = section.find("table.data-table").first();
  if (!table.length) return { headings: [], values: [] };

  const headings = table
    .find("thead th")
    .slice(1)
    .map((_, th) => $(th).text().trim())
    .get();

  const values = table
    .find("tbody tr")
    .map((_, row) => {
      const $row = $(row);
      const category = $row
        .find("td.text, td:first-child")
        .first()
        .text()
        .trim();
      const vals = $row
        .find("td:not(:first-child)")
        .map((_, td) => $(td).text().trim())
        .get();
      const isTtm = $row.hasClass("bold");
      return { category, values: vals, isTtm };
    })
    .get()
    .filter((r) => r.category && r.category !== "");

  return { headings, values };
}


function getDocumentLinks($) {
  const section = $("#documents");
  if (!section.length) return [];

  const categorize = (title, url) => {
    const t = title.toLowerCase();
    if (t.includes("financial year") || t.includes("annual report"))
      return "annual_report";
    if (
      t === "transcript" ||
      t.includes("earnings call transcript") ||
      t.includes("concall transcript") ||
      t.includes("investor call transcript")
    )
      return "concall";
    if (t.includes("audio recording") || t.includes("audio call"))
      return "audio";
    if (
      t === "all" ||
      url.includes("/corp-announcements/") ||
      t.includes("drhp") ||
      t.includes("rating update") ||
      t.includes("credit rating") ||
      t.includes("rating reaffirm")
    )
      return "skip";
    return "announcement";
  };

  const extractYear = (title) => {
    const m = title.match(/\b(20\d{2})\b/);
    return m ? parseInt(m[1]) : null;
  };

  const extractDate = (title) => {
    const m = title.match(
      /\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/i
    );
    return m ? `${m[1]} ${m[2]}` : null;
  };

  return section
    .find("a[href]")
    .map((_, a) => {
      const rawTitle = $(a).text() || "";
      const title = rawTitle.replace(/\s+/g, " ").trim();
      const href = $(a).attr("href") || "";
      const url = href.startsWith("http")
        ? href
        : `https://www.screener.in${href}`;
      if (!title || !url.startsWith("http")) return null;

      const isPdf =
        url.toLowerCase().endsWith(".pdf") ||
        url.includes("corpfiling") ||
        url.includes("AttachLive") ||
        url.includes("AttachHis");
      const category = categorize(title, url);
      if (category === "skip" || category === "audio") return null;

      return {
        title,
        url,
        type: isPdf ? "pdf" : "link",
        category,
        year: extractYear(title),
        date: extractDate(title),
      };
    })
    .get()
    .filter(Boolean);
}

// ── Public API ─────────────────────────────────────────────────────────────────

/**
 * Scrapes the screener.in company page (basic snapshot).
 * Used by run_scraper.js → fetch_compact_snapshot in Python.
 */
export const scrapeScreenerPage = async (screenerLink) => {
  const url = `https://www.screener.in${screenerLink}`;
  process.stderr.write(`[screenerScraper] GET ${url}\n`);

  const html = await fetchHtml(url);
  const $ = cheerio.load(html);

  const isConsolidated = screenerLink.includes("/consolidated/");
  const companyId = extractCompanyId($);
  process.stderr.write(`[screenerScraper] company_id=${companyId || "not found"}\n`);

  const [stockChartResponse, peerComparison] = await Promise.all([
    companyId ? fetchChartData(companyId, isConsolidated) : Promise.resolve(null),
    companyId ? fetchPeerComparison(companyId) : Promise.resolve({ headings: [], peers: [], median: null }),
  ]);

  const encodedSecret = companyId
    ? Buffer.from(
        `https://www.screener.in/api/company/${companyId}/chart/?q=Price&days=365&consolidated=${isConsolidated}`
      ).toString("base64")
    : "";

  const aboutText = getAboutText($);
  const ratios = getRatios($);
  const shareholding = getShareholding($);
  const quartersData = getQuartersData($);
  const prosConsData = getProsConsData($);

  const log = (k, v) => process.stderr.write(`[scrape] ${k}: ${v}\n`);
  log("aboutText", aboutText ? `${aboutText.slice(0, 80)}...` : "EMPTY");
  log("ratios", `${ratios.length} items — first: ${ratios[0] ? `${ratios[0].name}=${ratios[0].value}` : "none"}`);
  log("shareholding", `${shareholding.length} rows`);
  log("quartersData", `${quartersData.headings.length} cols, ${quartersData.values.length} rows`);
  log("prosConsData", `pros=${prosConsData.pros.length} cons=${prosConsData.cons.length}`);
  log("peerComparison", `${peerComparison.peers.length} peers`);
  log("chart", stockChartResponse ? `datasets=${stockChartResponse.datasets?.length ?? 0}` : "EMPTY");

  return {
    stockChartResponse,
    aboutText,
    ratios,
    shareholding,
    quartersData,
    prosConsData,
    peerComparison,
    encodedSecret,
  };
};

/**
 * Full deep scrape: all financial tabs, peers, documents.
 * Used by run_full_scrape.js → get_or_fetch in Python.
 */
export const fetchFullStockData = async (screenerLink) => {
  if (!String(screenerLink || "").includes("/company/")) {
    throw new Error("Invalid screener URL path.");
  }

  const url = `https://www.screener.in${screenerLink}`;
  process.stderr.write(`[screenerScraper] Full scrape GET ${url}\n`);

  const html = await fetchHtml(url);
  const $ = cheerio.load(html);

  const isConsolidated = screenerLink.includes("/consolidated/");
  const companyId = extractCompanyId($);
  process.stderr.write(`[screenerScraper] company_id=${companyId || "not found"}\n`);

  const [stockChartResponse, peerComparison] = await Promise.all([
    companyId ? fetchChartData(companyId, isConsolidated) : Promise.resolve(null),
    companyId ? fetchPeerComparison(companyId) : Promise.resolve({ headings: [], peers: [], median: null }),
  ]);

  const encodedSecret = companyId
    ? Buffer.from(
        `https://www.screener.in/api/company/${companyId}/chart/?q=Price&days=365&consolidated=${isConsolidated}`
      ).toString("base64")
    : "";

  const aboutText = getAboutText($);
  const ratios = getRatios($);
  const shareholding = getShareholding($);
  const quartersData = getQuartersData($);
  const prosConsData = getProsConsData($);
  const annualPL = scrapeFinancialTable($, "profit-loss");
  const balanceSheet = scrapeFinancialTable($, "balance-sheet");
  const cashFlows = scrapeFinancialTable($, "cash-flow");
  const ratiosHistory = scrapeFinancialTable($, "ratios");
  const documents = getDocumentLinks($);

  const log = (k, v) => process.stderr.write(`[scrape] ${k}: ${v}\n`);
  log("aboutText", aboutText ? `${aboutText.slice(0, 80)}...` : "EMPTY");
  log("ratios", `${ratios.length} items — first: ${ratios[0] ? `${ratios[0].name}=${ratios[0].value}` : "none"}`);
  log("shareholding", `${shareholding.length} rows`);
  log("quartersData", `${quartersData.headings.length} cols, ${quartersData.values.length} rows`);
  log("prosConsData", `pros=${prosConsData.pros.length} cons=${prosConsData.cons.length}`);
  log("annualPL", `${annualPL.headings.length} cols, ${annualPL.values.length} rows`);
  log("balanceSheet", `${balanceSheet.headings.length} cols, ${balanceSheet.values.length} rows`);
  log("cashFlows", `${cashFlows.headings.length} cols, ${cashFlows.values.length} rows`);
  log("ratiosHistory", `${ratiosHistory.headings.length} cols, ${ratiosHistory.values.length} rows`);
  log("peerComparison", `${peerComparison.peers.length} peers`);
  log("documents", `${documents.length} docs (concalls=${documents.filter(d => d.category === "concall").length})`);
  log("chart", stockChartResponse ? `datasets=${stockChartResponse.datasets?.length ?? 0}` : "EMPTY");

  return {
    stockChartResponse,
    encodedSecret,
    aboutText,
    ratios,
    shareholding,
    quartersData,
    prosConsData,
    annualPL,
    balanceSheet,
    cashFlows,
    ratiosHistory,
    peerComparison,
    documents,
    scrapedAt: new Date().toISOString(),
  };
};

/**
 * Compact snapshot (used by run_scraper.js for the analyze pipeline).
 */
export const fetchStockSnapshot = async (screenerLink) => {
  if (!String(screenerLink || "").includes("/company/")) {
    throw new Error("Invalid screener URL path.");
  }
  return scrapeScreenerPage(screenerLink);
};
