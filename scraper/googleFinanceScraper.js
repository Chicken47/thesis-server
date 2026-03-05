import puppeteer from "puppeteer";

const extractTickerFromScreenerUrl = (link) => {
  if (link?.includes("/consolidated/")) {
    const match = link?.match(/\/company\/(.*?)\/consolidated\//);
    return match ? match[1] : null;
  }
  const match = link?.match(/\/company\/(.*?)\//);
  return match ? match[1] : null;
};

/**
 * Scrapes Google Finance for a given stock.
 * @param {string} screenerLink - e.g. "/company/INFY/consolidated/"
 * @returns {{ financialData: Array, newsDetails: Array }}
 */
export const getIndianIndices = async (screenerLink) => {
  const browser = await puppeteer.launch({
    headless: "new",
    executablePath:
      process.platform === "darwin"
        ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        : undefined,
  });
  const page = await browser.newPage();

  try {
    const listingName = extractTickerFromScreenerUrl(screenerLink);
    if (!listingName) {
      throw new Error("Could not derive exchange symbol from screener URL.");
    }

    await page.goto(`https://www.google.com/finance/quote/${listingName}:NSE`, {
      waitUntil: "networkidle2",
    });
    await page.waitForSelector(".lkR3Y", { timeout: 15000 });

    const newsDetails = await page.evaluate(() =>
      Array.from(document.querySelectorAll(".yY3Lee")).map((el) => ({
        newsCompany: el.querySelector(".sfyJob")?.innerText || "",
        time: el.querySelector(".Adak")?.innerText || "",
        title: el.querySelector(".Yfwt5")?.innerText || "",
        imgSrc: el.querySelector(".Z4idke")?.getAttribute("src") || "",
        linkToNews: el.querySelector(".z4rs2b a")?.getAttribute("href") || "",
      }))
    );

    const financialData = await page.evaluate(() =>
      Array.from(document.querySelectorAll(".lkR3Y")).map((element) => ({
        name: element.querySelector(".pKBk1e")?.innerText || "",
        value: element.querySelector(".YMlKec")?.innerText || "",
        percentage: element.querySelector(".JwB6zf.V7hZne")?.innerText || "",
      }))
    );

    return { financialData, newsDetails };
  } finally {
    await browser.close();
  }
};
