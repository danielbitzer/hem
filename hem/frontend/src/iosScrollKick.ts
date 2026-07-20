// Home Assistant's iOS companion app is a WKWebView that renders ingress
// add-on pages inside an <iframe>. WebKit has a long-standing bug where a
// subframe's document doesn't get its touch-scrolling wired up until the
// frame receives focus — so the HEM page won't scroll to a swipe until the
// user taps a focusable element (the Dashboard/Settings buttons). Safari
// opened directly doesn't nest the page in a subframe, so it's unaffected.
//
// The fix is to give the frame focus + a 1px scroll "kick" so WebKit
// activates the scroll view without a tap: once on mount, and once more on
// the first interaction (belt-and-braces, since the mount kick fires before
// the plan loads and the page is tall enough to scroll). Everything here is
// invisible and a no-op on browsers that already scroll normally.
export function installIosScrollKick(): () => void {
  const kick = () => {
    try {
      window.focus();
      const y = window.scrollY;
      // Nudge and restore — enough to make WebKit set up the subframe's
      // scroll view; clamps harmlessly to 0 when the page isn't scrollable.
      window.scrollTo(0, y + 1);
      window.scrollTo(0, y);
    } catch {
      // window access can throw in exotic embeddings — ignore.
    }
  };

  const onFirstInteraction = () => {
    kick();
    remove();
  };
  const remove = () => {
    window.removeEventListener("touchstart", onFirstInteraction);
    window.removeEventListener("focus", onFirstInteraction);
  };

  kick();
  window.addEventListener("touchstart", onFirstInteraction, { passive: true });
  window.addEventListener("focus", onFirstInteraction);
  return remove;
}
