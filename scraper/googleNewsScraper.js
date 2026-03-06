/**
 * Google News scraper — uses RSS feed via axios, no browser required.
 * RSS endpoint: https://news.google.com/rss/search?q=...&hl=en-IN&gl=IN&ceid=IN:en
 */

import axios from "axios";
import * as cheerio from "cheerio";

/**
 * Search Google News India for a stock-related query via RSS.
 *
 * @param {string} query - e.g. "TCS NSE India stock"
 * @param {number} maxResults - max articles to return (default 8)
 * @returns {Promise<Array<{title: string, source: string, time: string, url: string}>>}
 */
export async function fetchGoogleNews(query, maxResults = 8) {
  const encodedQuery = encodeURIComponent(query);
  const url = `https://news.google.com/rss/search?q=${encodedQuery}&hl=en-IN&gl=IN&ceid=IN:en`;

  try {
    const { data: xml } = await axios.get(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        Accept: "application/rss+xml, application/xml, text/xml, */*",
      },
      timeout: 15000,
      responseType: "text",
    });

    const $ = cheerio.load(xml, { xmlMode: true });

    const items = [];
    $("item").each((_, el) => {
      if (items.length >= maxResults) return false;
      const $el = $(el);
      const title = $el.find("title").first().text().trim();
      const link =
        $el.find("link").first().text().trim() ||
        $el.find("guid").first().text().trim();
      const pubDate = $el.find("pubDate").first().text().trim();
      const source = $el.find("source").first().text().trim() || "";
      if (!title) return;
      items.push({ title, source, time: pubDate, url: link });
    });

    return items;
  } catch (err) {
    process.stderr.write(
      `[googleNewsScraper] RSS fetch failed: ${err.message}\n`
    );
    return [];
  }
}
