const sanitize = (value) =>
  typeof value === "string" ? value.trim() : "";

const IMPORTANT_QUARTERLY_ROWS = [
  "Sales",
  "Revenue",
  "Net Profit",
  "EPS in Rs",
  "OPM %",
  "ROE %",
  "ROCE %",
  "Debt",
];

/**
 * Compacts a raw stock snapshot into a leaner, LLM-friendly structure.
 * @param {object} snapshot - Raw output from fetchStockSnapshot
 * @returns {object} Compact snapshot
 */
export const buildCompactStockSnapshot = (snapshot = {}) => ({
  aboutText: sanitize(snapshot.aboutText),

  ratios: (snapshot.ratios || [])
    .filter((r) => r?.name)
    .slice(0, 24)
    .map((r) => ({ name: sanitize(r.name), value: sanitize(r.value) })),

  shareholding: (snapshot.shareholding || [])
    .filter((row) => row?.category)
    .slice(0, 12)
    .map((row) => ({ ...row })),

  quarterly: (() => {
    const headings = Array.isArray(snapshot.quartersData?.headings)
      ? snapshot.quartersData.headings.slice(0, 8)
      : [];
    const values = Array.isArray(snapshot.quartersData?.values)
      ? snapshot.quartersData.values
      : [];

    return {
      headings,
      values: values
        .filter((row) =>
          IMPORTANT_QUARTERLY_ROWS.some(
            (candidate) =>
              sanitize(row?.category).toLowerCase() === candidate.toLowerCase()
          )
        )
        .slice(0, 8)
        .map((row) => ({
          category: sanitize(row.category),
          values: Array.isArray(row.values) ? row.values.slice(0, 8) : [],
        })),
    };
  })(),

  pros: Array.isArray(snapshot.prosConsData?.pros)
    ? snapshot.prosConsData.pros.slice(0, 12)
    : [],

  cons: Array.isArray(snapshot.prosConsData?.cons)
    ? snapshot.prosConsData.cons.slice(0, 12)
    : [],

  marketIndicators: (snapshot.financialData || [])
    .slice(0, 12)
    .map((item) => ({
      name: sanitize(item?.name),
      value: sanitize(item?.value),
      percentage: sanitize(item?.percentage),
    })),

  news: (snapshot.newsDetails || [])
    .slice(0, 12)
    .map((item) => ({
      source: sanitize(item?.newsCompany),
      time: sanitize(item?.time),
      title: sanitize(item?.title),
      link: sanitize(item?.linkToNews),
    })),
});
