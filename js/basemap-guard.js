// Keeps the basemap from staying blank. A weak connection can fail the first read of the map file (a Range request to the data
// site); MapLibre does not retry a source that failed to load, so the map used to stay grey until the page was reloaded.
// (The PMTiles library's own cache of that failure is dropped in js/map.js, so a retry really reaches the network.)
//
// "Drawn" means basemap features are on screen (queryRenderedFeatures on the basemap's layers), never the old
// `basemapDrawn` flag, which was true while the map was blank. Until the basemap has drawn:
//  - a failure (a map error, or SLOW_MS with nothing drawn and no error) is retried by re-setting the source URL, which reloads
//    the source and its tiles: up to RETRIES times, after DELAYS_MS[0], then [1], then [2] (about 1 s, 3 s, 8 s), never faster;
//  - when the last retry has also failed, "Map not loaded. Tap to retry" shows with a button; a tap rebuilds the source and
//    starts the retries again. The message goes away by itself as soon as the map draws.
// Once the basemap has drawn the guard stops watching. It makes no request of its own and keeps nothing: it only asks MapLibre to
// reload the same source from the same address.
export const DELAYS_MS = [1000, 3000, 8000];
export const SLOW_MS = 15_000;
const POLL_MS = 500;

/** True when the basemap's layers have features on screen. [layerIds] are the style's layers that read the basemap source. */
export function basemapOnScreen(map, layerIds) {
  try {
    if (!layerIds?.length) return false;
    return map.queryRenderedFeatures({ layers: layerIds }).length > 0;
  } catch {
    return false;
  }
}

/**
 * Watches [map]'s basemap [sourceId]. [box] is the message element, [button] its retry button, [url] the source's address.
 * Options (tests): delays, slowMs, pollMs. Returns {onScreen(), stop()}.
 */
export function guardBasemap(map, { sourceId, url, box, button, delays = DELAYS_MS, slowMs = SLOW_MS, pollMs = POLL_MS }) {
  let layerIds = [];
  const refreshLayers = () => {
    layerIds = (map.getStyle()?.layers || []).filter((layer) => layer.source === sourceId).map((layer) => layer.id);
  };
  const onScreen = () => {
    if (!layerIds.length) {
      try { refreshLayers(); } catch { return false; }
    }
    return basemapOnScreen(map, layerIds);
  };

  let attempt = 0;            // retries made since the last (re)start
  let attemptStartedAt = Date.now();
  let failedAt = null;        // when the current attempt was seen to fail (a map error, or the slow limit)
  let givenUp = false;        // the retries are used up: the message is showing
  let finished = false;
  let timer = null;

  const showMessage = (visible) => {
    if (!box) return;
    box.hidden = !visible;
  };

  const onError = () => {
    if (!finished && failedAt === null) failedAt = Date.now();
  };
  map.on("error", onError);

  const reload = () => {
    attemptStartedAt = Date.now();
    failedAt = null;
    try {
      map.getSource(sourceId)?.setUrl(url);
    } catch {
      failedAt = Date.now();
    }
  };

  const tick = () => {
    if (finished) return;
    const now = Date.now();
    if (onScreen()) {
      finished = true;
      showMessage(false);
      stop();
      return;
    }
    if (givenUp) return;
    if (failedAt === null && now - attemptStartedAt >= slowMs) failedAt = now;
    if (failedAt === null) return;
    if (attempt >= delays.length) {      // the last retry failed too
      givenUp = true;
      showMessage(true);
      return;
    }
    if (now - failedAt >= delays[attempt]) {
      attempt += 1;
      reload();
    }
  };

  function stop() {
    finished = true;
    clearInterval(timer);
    map.off("error", onError);
    button?.removeEventListener("click", onTap);
  }

  function onTap() {
    attempt = 0;
    givenUp = false;
    showMessage(false);
    reload();
  }
  button?.addEventListener("click", onTap);

  showMessage(false);
  timer = setInterval(tick, pollMs);
  return { onScreen, stop };
}
