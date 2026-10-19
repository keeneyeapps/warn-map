// Reporting (WARN Drive, the future drive.html, a later step). The ONLY module that may ever write to the WARN
// database, and only drive.html may load it; index.html (WARN Map) and warn.html (WARN Alerts) must never import
// it, directly or through another module (CLAUDE.md, "Family tree").
export {};
