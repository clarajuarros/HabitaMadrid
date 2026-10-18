/* Streamlit has no Python API for scrolling within a page. st.html executes
 * this local script in the page (no iframe, external libraries or reload).
 * Only our explicit section IDs are used; Streamlit's DOM is not inspected.
 */
(() => {
  document.documentElement.lang = "es";
  const request = __HABITA_REQUEST__;
  window.habitaCancelNavigation?.();
  if (!request || document.documentElement.dataset.habitaNavigationDone === request.id) return;

  let cancelled = false;
  let frame = 0;
  let observer;
  const cancel = () => {
    cancelled = true;
    observer?.disconnect();
    cancelAnimationFrame(frame);
  };
  window.habitaCancelNavigation = cancel;
  const findTarget = () => {
    const target = document.getElementById(request.target);
    const ready = document.getElementById("habita-navigation-ready");
    return target?.dataset.habitaNavigation === request.id &&
      ready?.dataset.request === request.id && target.getClientRects().length ? target : null;
  };
  const attempt = () => {
    if (cancelled || frame || !findTarget()) return;
    // Wait for layout/paint, not an arbitrary timeout. Recheck after React's commit.
    frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (cancelled) return;
        const target = findTarget();
        if (!target) return;
        document.documentElement.dataset.habitaNavigationDone = request.id;
        cancel();
        target.focus({preventScroll: true});
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        target.scrollIntoView({behavior: reduced || request.target === "habita-vista" ? "instant" : "smooth", block: "start", inline: "nearest"});
      });
    });
  };
  // The target and end-of-render marker may arrive in separate React commits.
  observer = new MutationObserver(attempt);
  observer.observe(document.body, {childList: true, subtree: true, attributes: true,
    attributeFilter: ["data-habita-navigation", "data-request"]});
  attempt();
})();
