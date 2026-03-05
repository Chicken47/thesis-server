import axios from "axios";

/**
 * Searches screener.in for stocks matching the query.
 * @param {string} query - Search term (e.g. "Infosys", "INFY")
 * @returns {Promise<Array>} List of matching companies from screener.in
 */
export const searchStocks = async (query) => {
  const q = String(query || "").trim();
  if (!q) throw new Error("Search query must not be empty.");

  const response = await axios.get(
    "https://www.screener.in/api/company/search/",
    { params: { q } }
  );
  return response.data;
};
