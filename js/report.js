// Reporting (WARN Drive only, a later step). The ONLY module that may ever write to the WARN database, and only
// warn.html may load it; index.html (WARN Map) must never import it, directly or through another module.
export {};
