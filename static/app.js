// Filter dropdown toggle
(function () {
  const toggleBtn = document.getElementById("filter-toggle-btn");
  const dropdown = document.getElementById("filter-dropdown");
  if (!toggleBtn || !dropdown) return;

  toggleBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    dropdown.hidden = !dropdown.hidden;
  });
  dropdown.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => {
    dropdown.hidden = true;
  });
})();

// Inline composer: click the compose bar to expand the full share form in place.
(function () {
  const bar = document.getElementById("compose-bar-toggle");
  const form = document.getElementById("composer-form");
  const cancelBtn = document.getElementById("composer-cancel");
  if (!bar || !form) return;

  bar.addEventListener("click", () => {
    bar.hidden = true;
    form.hidden = false;
    const title = form.querySelector('[name="title"]');
    if (title) title.focus();
  });
  if (cancelBtn) {
    cancelBtn.addEventListener("click", () => {
      form.hidden = true;
      bar.hidden = false;
    });
  }

  // Suggested-tag pills fill the Tool field first, then Category, so one click
  // usefully tags a new post without the user having to type anything.
  form.querySelectorAll(".tag-suggest").forEach((pill) => {
    pill.addEventListener("click", () => {
      const tag = pill.dataset.tag;
      const toolInput = document.getElementById("composer-tool");
      const categoryInput = document.getElementById("composer-category");
      if (toolInput && !toolInput.value) {
        toolInput.value = tag;
      } else if (categoryInput && !categoryInput.value) {
        categoryInput.value = tag;
      }
    });
  });
})();

// Digest carousel: auto-advances every data-interval ms, sliding right
// to left. Manual prev/next lets people read earlier digest cards, and resets
// the auto-timer so a manual click doesn't get immediately undone by the next tick.
(function () {
  const root = document.getElementById("digest-carousel");
  if (!root) return;
  const track = root.querySelector(".digest-carousel-track");
  const cards = Array.from(root.querySelectorAll(".digest-card"));
  const dots = Array.from(root.querySelectorAll(".digest-dot"));
  const prevBtn = root.querySelector(".digest-nav-prev");
  const nextBtn = root.querySelector(".digest-nav-next");
  if (!cards.length) return;

  const interval = parseInt(root.dataset.interval, 10) || 10000;
  let current = 0;
  let timer = null;

  // Short/full body text is decided server-side by character count (same
  // truncation convention this app already uses for post excerpts), so
  // "Read more" just swaps which paragraph is visible - no DOM-measurement
  // heuristics that can behave inconsistently across browsers.
  function collapseReadMore(card) {
    const short = card.querySelector(".digest-body-short");
    const full = card.querySelector(".digest-body-full");
    const btn = card.querySelector(".digest-readmore");
    if (!short || !full || !btn) return;
    short.hidden = false;
    full.hidden = true;
    btn.textContent = "Read more";
  }

  // The track's height is driven by JS (not CSS) because the cards are
  // absolutely positioned for the slide transition, so the track can't just
  // auto-size to content - and animating this height is what makes expanding
  // a card, or switching to a longer/shorter one, push the page below it
  // smoothly instead of jumping or clipping text. A floor is enforced so a
  // very short digest doesn't shrink the carousel enough for the prev/next
  // arrows (vertically centered on the whole carousel) to collide with the text.
  const MIN_TRACK_HEIGHT = 110;
  function resizeTrack() {
    track.style.height = Math.max(cards[current].offsetHeight, MIN_TRACK_HEIGHT) + "px";
  }

  function show(index) {
    const from = current;
    current = (index + cards.length) % cards.length;
    if (current === from) return;

    cards[from].classList.remove("digest-card-active");
    cards[from].classList.add("digest-card-exit");
    if (dots[from]) dots[from].classList.remove("digest-dot-active");

    requestAnimationFrame(() => {
      cards[current].classList.add("digest-card-active");
      if (dots[current]) dots[current].classList.add("digest-dot-active");
      collapseReadMore(cards[current]);
      resizeTrack();
    });

    setTimeout(() => cards[from].classList.remove("digest-card-exit"), 550);
  }

  function restart() {
    if (timer) clearInterval(timer);
    timer = setInterval(() => show(current + 1), interval);
  }

  cards.forEach((card) => {
    const short = card.querySelector(".digest-body-short");
    const full = card.querySelector(".digest-body-full");
    const btn = card.querySelector(".digest-readmore");
    if (btn && short && full) {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const expanding = full.hidden;
        short.hidden = expanding;
        full.hidden = !expanding;
        btn.textContent = expanding ? "Show less" : "Read more";
        resizeTrack();
      });
    }
  });

  if (nextBtn) nextBtn.addEventListener("click", () => { show(current + 1); restart(); });
  if (prevBtn) prevBtn.addEventListener("click", () => { show(current - 1); restart(); });
  window.addEventListener("resize", resizeTrack);

  resizeTrack();
  if (cards.length > 1) restart();
})();

// Little "spark" burst on click - purely cosmetic, community/flashy feel.
// Fires on primary buttons and the star-rating labels.
(function () {
  const layer = document.getElementById("spark-layer");
  if (!layer) return;

  const COLORS = ["#0096AF", "#662984", "#96C800", "#D0811C", "#F3B827"];

  function burst(x, y) {
    const count = 10;
    for (let i = 0; i < count; i++) {
      const dot = document.createElement("span");
      dot.className = "spark-dot";
      dot.style.background = COLORS[i % COLORS.length];
      dot.style.left = x + "px";
      dot.style.top = y + "px";
      const angle = (Math.PI * 2 * i) / count;
      const distance = 40 + Math.random() * 30;
      dot.style.setProperty("--dx", Math.cos(angle) * distance + "px");
      dot.style.setProperty("--dy", Math.sin(angle) * distance + "px");
      layer.appendChild(dot);
      dot.addEventListener("animationend", () => dot.remove());
    }
  }

  document.addEventListener("click", (e) => {
    const target = e.target.closest(".btn-spark, .btn-primary, .star-input label");
    if (!target) return;
    burst(e.clientX, e.clientY);
  });
})();
