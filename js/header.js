// Fills the header band's variant name (Map, Meter, Alerts) from the page's <html data-app-name>, so the band's
// markup is identical on every page. No imports, no network: it only sets text. A page without the band does nothing.
const variant = document.querySelector("#app-header-variant");
if (variant) {
  const name = document.documentElement.dataset.appName || "";
  variant.textContent = name.replace(/^WARN\s+/, "");
}
