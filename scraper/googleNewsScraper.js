/**
 * Google News scraper — searches by query and returns recent news items.
 * Extracted from user's Next.js API route for standalone use.
 *
 * Usage:
 *   import { fetchGoogleNews } from "./googleNewsScraper.js";
 *   const news = await fetchGoogleNews("TCS NSE India");
 */

import puppeteer from "puppeteer";

const SELECTORS = {
  titleLink: "a.JtKRv",
  source: "div.vr1PYe",
  time: "time.hvbAAd",
};

/**
 * Search Google News India for a stock-related query.
 *
 * @param {string} query - e.g. "TCS NSE India stock"
 * @param {number} maxResults - max articles to return (default 8)
 * @returns {Promise<Array<{title: string, source: string, time: string, url: string}>>}
 */
export async function fetchGoogleNews(query, maxResults = 8) {
  const encodedQuery = encodeURIComponent(query);
  const url = `https://news.google.com/search?q=${encodedQuery}&hl=en-IN&gl=IN&ceid=IN:en`;

  const browser = await puppeteer.launch({
    headless: "new",
    executablePath:
      process.platform === "darwin"
        ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        : undefined,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  const page = await browser.newPage();
  await page.setUserAgent(
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
  );

  try {
    await page.goto(url, { waitUntil: "networkidle2", timeout: 25000 });

    // Wait for title links — fail silently if nothing loads
    await page
      .waitForSelector(SELECTORS.titleLink, { timeout: 10000 })
      .catch(() => {});

    // Collect all three selectors by index — they appear in the same order on the page
    const articles = await page.evaluate(({ titleLink, source, time }) => {
      const titles = Array.from(document.querySelectorAll(titleLink));
      const sources = Array.from(document.querySelectorAll(source));
      const times = Array.from(document.querySelectorAll(time));

      return titles.slice(0, 12).map((titleEl, i) => {
        const rawHref = titleEl.getAttribute("href") || "";
        const newsUrl = rawHref.startsWith(".")
          ? "https://news.google.com/" + rawHref.slice(2)
          : rawHref;

        return {
          title: titleEl.innerText?.trim() || "",
          source: sources[i]?.innerText?.trim() || "",
          time:
            times[i]?.getAttribute("datetime") ||
            times[i]?.innerText?.trim() ||
            "",
          url: newsUrl,
        };
      }).filter((item) => item.title.length > 0);
    }, SELECTORS);

    return articles.slice(0, maxResults);
  } finally {
    await browser.close();
  }
}
