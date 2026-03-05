import puppeteer from "puppeteer";
import { getIndianIndices } from "./googleFinanceScraper.js";

const launchBrowser = () =>
  puppeteer.launch({
    headless: "new",
    executablePath:
      process.platform === "darwin"
        ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        : undefined,
  });

const getAboutText = async (page) => {
  try {
    return await page.$eval(".show-more-box.about > p", (p) =>
      p?.textContent?.trim()
    );
  } catch {
    return "";
  }
};

const getRatios = async (page) => {
  try {
    return await page.$$eval("#top-ratios li", (ratiosList) =>
      ratiosList.map((ratio) => ({
        name: ratio.querySelector(".name")?.textContent?.trim(),
        value: ratio.querySelector(".value .number")?.textContent?.trim(),
      }))
    );
  } catch {
    return [];
  }
};

const getShareholding = async (page) => {
  try {
    return await page.evaluate(() => {
      const table = document.querySelector(
        "#shareholding .responsive-holder .data-table"
      );
      if (!table) return [];

      const headers = Array.from(
        table.querySelectorAll("thead th:not(.text)")
      ).map((th) => th.textContent.trim());

      const rows = table.querySelectorAll("tbody tr:not(.sub)");
      return Array.from(rows).map((row) => {
        const cells = row.querySelectorAll("td");
        const rowData = { category: cells[0]?.textContent.trim() || "" };
        headers.forEach((col, i) => {
          rowData[col] = cells[i + 1]?.textContent.trim() || "";
        });
        return rowData;
      });
    });
  } catch {
    return [];
  }
};

const getQuartersData = async (page) => {
  try {
    return await page.evaluate(() => {
      const section = document.getElementById("quarters");
      const table = section?.querySelector("table.data-table");
      if (!table) return { headings: [], values: [] };

      const headings = Array.from(table.querySelectorAll("thead th"))
        .slice(1)
        .map((th) => th.textContent.trim());

      const rows = Array.from(table.querySelectorAll("tbody tr"));
      const values = rows
        .map((row) => ({
          category: row.querySelector("td.text")?.textContent.trim() || "",
          values: Array.from(row.querySelectorAll("td"))
            .slice(1)
            .map((td) => td.textContent.trim()),
        }))
        .filter((row) => row.category);

      return { headings, values };
    });
  } catch {
    return { headings: [], values: [] };
  }
};

const getProsConsData = async (page) => {
  try {
    return await page.evaluate(() => ({
      pros: Array.from(document.querySelectorAll(".pros li")).map((li) =>
        li.textContent.trim()
      ),
      cons: Array.from(document.querySelectorAll(".cons li")).map((li) =>
        li.textContent.trim()
      ),
    }));
  } catch {
    return { pros: [], cons: [] };
  }
};

/**
 * Generic financial table scraper — reused for P&L, Balance Sheet, Cash Flow, Ratios.
 * All these sections have the same table.data-table structure on Screener.
 */
const scrapeFinancialTable = async (page, sectionId) => {
  try {
    return await page.evaluate((id) => {
      const section = document.getElementById(id);
      const table = section?.querySelector("table.data-table");
      if (!table) return { headings: [], values: [] };

      const headings = Array.from(table.querySelectorAll("thead th"))
        .slice(1)
        .map((th) => th.textContent.trim());

      const rows = Array.from(table.querySelectorAll("tbody tr"));
      const values = rows
        .map((row) => ({
          category: row.querySelector("td.text, td:first-child")?.textContent.trim() || "",
          values: Array.from(row.querySelectorAll("td:not(:first-child)"))
            .map((td) => td.textContent.trim()),
          isTtm: row.classList.contains("bold") || false,
        }))
        .filter((row) => row.category && row.category !== "");

      return { headings, values };
    }, sectionId);
  } catch {
    return { headings: [], values: [] };
  }
};

const getPeerComparison = async (page) => {
  try {
    // The peer table is in #peers-table-placeholder and loads dynamically
    await page
      .waitForSelector("#peers-table-placeholder table.data-table tbody tr", {
        timeout: 10000,
      })
      .catch(() => null);

    return await page.evaluate(() => {
      // Table is inside the placeholder div, NOT directly in #peers
      const table = document.querySelector(
        "#peers-table-placeholder table.data-table"
      );
      if (!table) return { headings: [], peers: [], median: null };

      // Headers are <th> elements in the FIRST <tr> of <tbody> (no <thead> here)
      const headerRow = table.querySelector("tbody tr:first-child");
      const headings = headerRow
        ? Array.from(headerRow.querySelectorAll("th")).map((th) => {
            // Use data-tooltip for friendly column name (e.g. "Current Price")
            const tooltip = th.getAttribute("data-tooltip");
            if (tooltip) return tooltip;
            // Otherwise strip the unit span and return text
            const clone = th.cloneNode(true);
            clone.querySelector("span")?.remove();
            return clone.textContent.trim();
          }).filter((h) => h)
        : [];

      // Data rows: tbody rows that have <td> (skip the header row which has <th>)
      const dataRows = Array.from(table.querySelectorAll("tbody tr")).filter(
        (row) => row.querySelectorAll("td").length > 0
      );

      const peers = dataRows
        .map((row) => {
          const cells = Array.from(row.querySelectorAll("td"));
          const obj = {};
          headings.forEach((h, i) => {
            const cell = cells[i];
            if (!cell) return;
            // For name column, get the link text (strips the serial number td)
            const link = cell.querySelector("a");
            obj[h] = link
              ? link.textContent.trim()
              : cell.textContent.trim();
          });
          return obj;
        })
        .filter((p) => Object.values(p).some((v) => v));

      // Capture median row from <tfoot>
      const medianRow = table.querySelector("tfoot tr");
      let median = null;
      if (medianRow) {
        const cells = Array.from(medianRow.querySelectorAll("td"));
        median = {};
        headings.forEach((h, i) => {
          median[h] = cells[i]?.textContent.trim() || "";
        });
      }

      return { headings, peers, median };
    });
  } catch {
    return { headings: [], peers: [], median: null };
  }
};

const getDocumentLinks = async (page) => {
  try {
    return await page.evaluate(() => {
      const section = document.getElementById("documents");
      if (!section) return [];

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
          return "audio"; // skip these — no text to extract
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
        const months =
          /\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/i;
        const m = title.match(months);
        return m ? `${m[1]} ${m[2]}` : null;
      };

      return Array.from(section.querySelectorAll("a[href]"))
        .map((a) => {
          // Clean up multi-line whitespace in titles
          const rawTitle = a.textContent || "";
          const title = rawTitle.replace(/\s+/g, " ").trim();
          const url = a.href;
          if (!title || !url.startsWith("http")) return null;

          const isPdf = url.toLowerCase().endsWith(".pdf") ||
            url.includes("corpfiling") || url.includes("AttachLive") || url.includes("AttachHis");
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
        .filter(Boolean);
    });
  } catch {
    return [];
  }
};

/**
 * Scrapes the screener.in company page for chart data and all financial tables.
 * @param {string} screenerLink - e.g. "/company/INFY/consolidated/"
 * @returns {object} Raw snapshot data
 */
export const scrapeScreenerPage = async (screenerLink) => {
  const browser = await launchBrowser();
  const page = await browser.newPage();
  await page.setRequestInterception(true);

  let stockChartResponse = null;
  let encodedSecret = "";

  page.on("request", (req) => {
    const url = req.url();
    if (!encodedSecret && url.includes("/chart/")) {
      encodedSecret = Buffer.from(url).toString("base64");
    }
    req.continue();
  });

  page.on("response", async (res) => {
    if (
      res.url().includes("https://www.screener.in/api/company/") &&
      res.url().includes("/chart/")
    ) {
      try {
        stockChartResponse = await res.json();
      } catch {
        stockChartResponse = null;
      }
    }
  });

  try {
    await page.goto(`https://screener.in${screenerLink}`, {
      waitUntil: "networkidle2",
    });
    await new Promise(resolve => setTimeout(resolve, 2000));

    const [aboutText, ratios, shareholding, quartersData, prosConsData] =
      await Promise.all([
        getAboutText(page),
        getRatios(page),
        getShareholding(page),
        getQuartersData(page),
        getProsConsData(page),
      ]);

    return {
      stockChartResponse,
      aboutText,
      ratios,
      shareholding,
      quartersData,
      prosConsData,
      encodedSecret,
    };
  } finally {
    await browser.close();
  }
};

/**
 * Full snapshot: scrapes screener.in + Google Finance for a given stock.
 * @param {string} screenerLink - e.g. "/company/INFY/consolidated/"
 * @returns {object} Combined raw snapshot
 */
export const fetchStockSnapshot = async (screenerLink) => {
  if (!String(screenerLink || "").includes("/company/")) {
    throw new Error("Invalid screener URL path.");
  }

  const [screenData, indicesData] = await Promise.all([
    scrapeScreenerPage(screenerLink),
    getIndianIndices(screenerLink),
  ]);

  return { ...screenData, ...indicesData };
};

/**
 * Full deep scrape: gets ALL financial tabs from Screener in one browser session.
 * Returns: everything in fetchStockSnapshot PLUS annual P&L, balance sheet,
 * cash flows, 10Y ratio history, peer comparison, and document links.
 *
 * @param {string} screenerLink - e.g. "/company/TCS/consolidated/"
 * @returns {object} Full raw dataset
 */
export const fetchFullStockData = async (screenerLink) => {
  if (!String(screenerLink || "").includes("/company/")) {
    throw new Error("Invalid screener URL path.");
  }

  const browser = await launchBrowser();
  const page = await browser.newPage();
  await page.setRequestInterception(true);

  let stockChartResponse = null;
  let encodedSecret = "";

  page.on("request", (req) => {
    const url = req.url();
    if (!encodedSecret && url.includes("/chart/")) {
      encodedSecret = Buffer.from(url).toString("base64");
    }
    req.continue();
  });

  page.on("response", async (res) => {
    if (
      res.url().includes("https://www.screener.in/api/company/") &&
      res.url().includes("/chart/")
    ) {
      try {
        stockChartResponse = await res.json();
      } catch {
        stockChartResponse = null;
      }
    }
  });

  try {
    await page.goto(`https://screener.in${screenerLink}`, {
      waitUntil: "networkidle2",
    });
    await new Promise((resolve) => setTimeout(resolve, 2000));

    // Scrape all sections in parallel — they're all on the same loaded page
    const [
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
    ] = await Promise.all([
      getAboutText(page),
      getRatios(page),
      getShareholding(page),
      getQuartersData(page),
      getProsConsData(page),
      scrapeFinancialTable(page, "profit-loss"),
      scrapeFinancialTable(page, "balance-sheet"),
      scrapeFinancialTable(page, "cash-flow"),
      scrapeFinancialTable(page, "ratios"),
      getPeerComparison(page),
      getDocumentLinks(page),
    ]);

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
  } finally {
    await browser.close();
  }
};
