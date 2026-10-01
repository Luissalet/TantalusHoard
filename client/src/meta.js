// Vocabulary shared with the backend: event types, availability states, modes, verdicts. Labels live in i18n.js.
export const EVENT_TYPES = [
  "RESTOCK", "LOCAL_RESTOCK", "PREORDER_OPEN", "PRICE_DROP", "PRICE_THRESHOLD_CROSSED", "NEW_SKU", "RESTOCK_DATE_CONFIRMED",
  "SOLD_OUT", "NEW_LISTING", "LISTING_PRICE_DROP", "INFO_CHANGE", "CANDIDATE_FOUND", "NEEDS_HUMAN", "MAIL_DEAL",
];
// Availability alerts a watcher can be told to send (secondhand / information types are raised by their own sentries).
export const ALERT_TYPES = ["RESTOCK", "LOCAL_RESTOCK", "PREORDER_OPEN", "PRICE_DROP", "PRICE_THRESHOLD_CROSSED", "NEW_SKU", "RESTOCK_DATE_CONFIRMED", "SOLD_OUT"];
export const EVENT_STATUSES = ["pending", "confirmed", "logged", "dismissed"];
export const STATES = ["IN_STOCK", "LOCAL_PICKUP", "PREORDER", "RESTOCK_SCHEDULED", "OUT_OF_STOCK", "UNAVAILABLE_REGION", "MARKETPLACE_ONLY", "UNKNOWN"];
export const MODES = ["availability", "secondhand", "information"];
export const SELLER_POLICIES = ["retail_only", "retail_plus_marketplace", "any_below"];

// colour + inline SVG path (24x24 stroke icon) per event type
export const EVENT_META = {
  RESTOCK: { color: "#4fb286", icon: "M3 12a9 9 0 0115-6.7L21 8M21 3v5h-5M21 12a9 9 0 01-15 6.7L3 16M3 21v-5h5" },
  LOCAL_RESTOCK: { color: "#4fb286", icon: "M12 21s-7-6.2-7-11a7 7 0 0114 0c0 4.8-7 11-7 11zM12 12a2.5 2.5 0 100-5 2.5 2.5 0 000 5z" },
  PREORDER_OPEN: { color: "#7cc3e6", icon: "M6 3h12v18l-6-4-6 4zM9 8h6" },
  PRICE_DROP: { color: "#c79bf0", icon: "M3 7l6 6 4-4 8 8M21 11v6h-6" },
  PRICE_THRESHOLD_CROSSED: { color: "#c79bf0", icon: "M4 12h16M12 4v16M8 8l4 4 4-4" },
  NEW_SKU: { color: "#e0a43a", icon: "M12 3l2.5 5.5 6 .7-4.4 4.1 1.2 5.9L12 15.3 6.7 19.2l1.2-5.9L3.5 9.2l6-.7z" },
  RESTOCK_DATE_CONFIRMED: { color: "#7cc3e6", icon: "M5 5h14v15H5zM5 10h14M9 3v4M15 3v4" },
  SOLD_OUT: { color: "#e5604b", icon: "M12 21a9 9 0 100-18 9 9 0 000 18zM5.6 5.6l12.8 12.8" },
  NEW_LISTING: { color: "#ecd9ff", icon: "M4 7l8-4 8 4v10l-8 4-8-4zM4 7l8 4 8-4M12 11v10" },
  LISTING_PRICE_DROP: { color: "#c79bf0", icon: "M4 6h16M4 12h10M4 18h6M18 12v7M15 16l3 3 3-3" },
  INFO_CHANGE: { color: "#7cc3e6", icon: "M5 4h14v16H5zM8 9h8M8 13h8M8 17h5" },
  CANDIDATE_FOUND: { color: "#e0a43a", icon: "M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-5-5M11 8v6M8 11h6" },
  NEEDS_HUMAN: { color: "#e5604b", icon: "M12 3l10 18H2zM12 10v5M12 18h.01" },
  MAIL_DEAL: { color: "#e0a43a", icon: "M3 6h18v12H3zM3 7l9 7 9-7" },
};

export const STATE_META = {
  IN_STOCK: { color: "#4fb286", cls: "chip-ok" },
  LOCAL_PICKUP: { color: "#4fb286", cls: "chip-ok" },
  PREORDER: { color: "#7cc3e6", cls: "chip-info" },
  RESTOCK_SCHEDULED: { color: "#e0a43a", cls: "chip-amber" },
  OUT_OF_STOCK: { color: "#e5604b", cls: "chip-danger" },
  UNAVAILABLE_REGION: { color: "#b56a5c", cls: "chip-danger" },
  MARKETPLACE_ONLY: { color: "#e0a43a", cls: "chip-amber" },
  UNKNOWN: { color: "#9a8cab", cls: "" },
};

export const BUYABLE = new Set(["IN_STOCK", "LOCAL_PICKUP", "PREORDER"]);

export const MODE_META = {
  availability: { icon: "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10" },
  secondhand: { icon: "M4 4h16v6H4zM6 10v10h12V10M10 14h4" },
  information: { icon: "M4 5h16v14H4zM8 9h8M8 13h8M8 17h4" },
};

export const VERDICT_META = {
  confirmed: { cls: "chip-ok" },
  leak: { cls: "chip-amber" },
  estimate: { cls: "chip-info" },
  irrelevant: { cls: "" },
  unknown: { cls: "" },
};

export const CHANNELS = ["toast", "hub", "ntfy", "telegram", "email"];
export const SEVERITIES = ["low", "medium", "high"];
