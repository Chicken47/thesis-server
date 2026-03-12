/**
 * News scraper — Bing News RSS feed via axios.
 * Bing RSS provides real article snippets in <description>, unlike Google News RSS
 * which only contains HTML tables of related stories.
 *
 * RSS endpoint: https://www.bing.com/news/search?q=...&format=rss
 */

import axios from "axios";
import * as cheerio from "cheerio";

/**
 * Parse an RFC 822 / HTTP-date string into a Date object.
 * Returns null if unparseable.
 */
function parseNewsDate(str) {
  if (!str) return null;
  try {
    const d = new Date(str);
    return isNaN(d.getTime()) ? null : d;
  } catch {
    return null;
  }
}

/**
 * Strip HTML tags and collapse whitespace to get a plain-text snippet.
 */
function stripHtml(html) {
  if (!html) return "";
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Fetch news for a stock query via Bing News RSS.
 * Returns articles sorted newest-first, with title, source, ISO date, description, and url.
 *
 * @param {string} query    - e.g. "Reliance Industries"
 * @param {number} maxResults
 * @returns {Promise<Array<{title, source, isoDate, description, url}>>}
 */
export async function fetchGoogleNews(query, maxResults = 10) {
  const encodedQuery = encodeURIComponent(query);
  const url = `https://www.bing.com/news/search?q=${encodedQuery}&format=rss`;

  try {
    const { data: xml } = await axios.get(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        Accept: "application/rss+xml, application/xml, text/xml, */*",
      },
      timeout: 15000,
      responseType: "text",
    });

    const $ = cheerio.load(xml, { xmlMode: true });

    const items = [];
    $("item").each((_, el) => {
      const $el = $(el);

      const title = $el.find("title").first().text().trim();
      if (!title) return;

      // Bing RSS puts the article snippet in <description>
      const rawDesc = $el.find("description").first().text().trim();
      const description = stripHtml(rawDesc).slice(0, 200) || "";

      const pubDate = $el.find("pubDate").first().text().trim();
      const date = parseNewsDate(pubDate);

      // link comes as text node after <link> in RSS 2.0
      const link =
        $el.find("link").first().text().trim() ||
        $el.find("guid").first().text().trim() ||
        "";

      // Bing uses <News:Source> namespace element; also try <source>
      let source =
        $el.find("News\\:Source").first().text().trim() ||
        $el.find("source").first().text().trim() ||
        "";

      // Fallback: source often appears in description as "Source Name - "
      if (!source && description) {
        const m = description.match(/^([A-Z][A-Za-z\s&\.]+?)\s*[-–]\s/);
        if (m) source = m[1].trim();
      }

      items.push({ title, source, pubDate, isoDate: date ? date.toISOString() : pubDate, date, description, url: link });
    });

    // Sort newest first
    items.sort((a, b) => (b.date?.getTime() ?? 0) - (a.date?.getTime() ?? 0));

    return items.slice(0, maxResults).map(({ title, source, isoDate, description, url }) => ({
      title,
      source,
      time: isoDate,
      description,
      url,
    }));
  } catch (err) {
    process.stderr.write(`[newsScraper] Bing RSS fetch failed: ${err.message}\n`);
    return [];
  }
}
