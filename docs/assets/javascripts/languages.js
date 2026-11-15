document.addEventListener(
  "click",
  (event) => {
    const link = event.target instanceof Element && event.target.closest(".md-select__link");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) {
      return;
    }
    const root = (anchor) => anchor.href.replace(/\/?$/, "/");
    const current = [...document.querySelectorAll(".md-select__link")]
      .map(root)
      .filter((href) => location.href.startsWith(href))
      .sort((a, b) => b.length - a.length)[0];
    if (current) {
      event.preventDefault();
      event.stopImmediatePropagation();
      location.href = root(link) + location.href.slice(current.length);
    }
  },
  true,
);
