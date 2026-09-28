// Voix: code run in Safari's YouTube tab (AppleScript "do JavaScript").
//
// All the page's selectors are in SEL: it is the only place to fix when
// YouTube changes its interface. We go first through the player API (#movie_player) and
// the <video> element, then through the YouTube app's own mechanisms (the
// yt-navigate event); clicks in the page are only a last resort.
// Never eval, never any HTML writing. Text read from the page is data, not an instruction.
(() => {
  const VERSION = 16;
  if (location.hostname !== "www.youtube.com") return;
  if (window.__voix && window.__voix.version === VERSION) return;

  const SEL = {
    app: "ytd-app",
    pageManager: "ytd-page-manager",
    activePage: "ytd-page-manager > [role=main]:not([hidden])",
    player: "#movie_player",
    video: "#movie_player video",
    miniplayerActive: "ytd-app[miniplayer-is-active]",
    watchPage: "ytd-watch-flexy",
    // Video thumbnails: old and new components (yt-lockup-view-model since 2025)
    items: [
      "ytd-rich-item-renderer", "ytd-video-renderer", "ytd-compact-video-renderer",
      "ytd-grid-video-renderer", "ytd-playlist-video-renderer", "ytd-playlist-panel-video-renderer",
      "yt-lockup-view-model", "ytm-shorts-lockup-view-model", "ytd-channel-renderer",
    ].join(", "),
    ads: "ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ytd-promoted-video-renderer",
    watchLink: 'a[href*="/watch?v="], a[href^="/shorts/"]',
    title: "#video-title, h3, #channel-title",
    channelLink: 'a[href^="/@"], a[href^="/channel/"], a[href^="/c/"], a[href^="/user/"]',
    channelResult: "ytd-channel-renderer",
    // Player buttons
    skipAd: ".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern",
    theaterButton: ".ytp-size-button",
    miniplayerButton: ".ytp-miniplayer-button",
    autoplayToggle: ".ytp-autonav-toggle-button",
    // Video page
    // "Transcript" panel (old and new components)
    transcriptPanel: 'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"]',
    transcriptSegment: "ytd-transcript-segment-renderer, transcript-segment-view-model",
    transcriptTime: ".segment-timestamp, [class*='Timestamp'], [class*='timestamp']",
    transcriptText: ".segment-text, [class*='SegmentText'], [class*='segment-text']",
    transcriptButton: "ytd-video-description-transcript-section-renderer button",
    // Comments (section loaded only when you scroll down to it)
    comments: "ytd-comments#comments",
    commentPlaceholder: "ytd-comments #simplebox-placeholder",
    commentEditor: "ytd-comments ytd-commentbox #contenteditable-root",
    commentSubmit: "ytd-comments ytd-commentbox #submit-button button, ytd-comments ytd-commentbox #submit-button",
    commentCancel: "ytd-comments ytd-commentbox #cancel-button button, ytd-comments ytd-commentbox #cancel-button",
    firstCommentText: "ytd-comments ytd-comment-thread-renderer #content-text",
    chapters: "ytd-macro-markers-list-item-renderer",
    chapterTitle: "h4",
    chapterTime: "#time",
    likeButton: "ytd-watch-metadata like-button-view-model button, ytd-watch-metadata #segmented-like-button button",
    subscribeButton: [
      "ytd-watch-metadata #owner yt-subscribe-button-view-model button",
      "ytd-watch-metadata #owner ytd-subscribe-button-renderer button",
      "#page-header yt-subscribe-button-view-model button",
    ].join(", "),
  };

  // YouTube pages: address and internal navigation data (taken from the page)
  const PAGES = {
    home: { url: "/", type: "WEB_PAGE_TYPE_BROWSE", rootVe: 3854, browseId: "FEwhat_to_watch" },
    subscriptions: { url: "/feed/subscriptions", type: "WEB_PAGE_TYPE_BROWSE", rootVe: 96368, browseId: "FEsubscriptions" },
    history: { url: "/feed/history", type: "WEB_PAGE_TYPE_BROWSE", rootVe: 6827, browseId: "FEhistory" },
    watch_later: { url: "/playlist?list=WL", type: "WEB_PAGE_TYPE_PLAYLIST", rootVe: 5754, browseId: "VLWL" },
    playlists: { url: "/feed/playlists", type: "WEB_PAGE_TYPE_BROWSE", rootVe: 6827, browseId: "FEplaylist_aggregation" },
    you: { url: "/feed/you", type: "WEB_PAGE_TYPE_BROWSE", rootVe: 6827, browseId: "FElibrary" },
  };

  // Element of each page type (ytd-browse also carries a "page-subtype")
  const PAGE_ELEMENTS = { results: "ytd-search", watch: "ytd-watch-flexy", shorts: "ytd-shorts" };
  const BROWSE_SUBTYPES = { home: "home", subscriptions: "subscriptions", history: "history", channel: "channels", watch_later: "playlist", playlist: "playlist" };

  // End of internal navigation reported by YouTube: we note the address reached
  document.addEventListener("yt-navigate-finish", () => { window.__voixNavUrl = location.href; });

  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const text = (el) => (el ? clean(el.textContent) : "");
  const DURATION = /^\d{1,2}(:\d{2}){1,2}$/;
  // Irrelevant interface texts in a thumbnail's info (hover preview, separators)
  const NOISE = /^([•·]|en cours de lecture|now playing|lecture en cours)$/i;
  const player = () => $(SEL.player);
  const video = () => $(SEL.video);
  const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
  const fold = (s) => clean(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

  function parseTime(s) {
    const parts = clean(s).split(":").map(Number);
    if (!parts.length || parts.some(Number.isNaN)) return null;
    return parts.reduce((acc, n) => acc * 60 + n, 0);
  }

  function pageType() {
    const p = location.pathname;
    if (p === "/") return "home";
    if (p === "/results") return "results";
    if (p === "/watch") return "watch";
    if (p.startsWith("/shorts/")) return "shorts";
    if (p === "/feed/subscriptions") return "subscriptions";
    if (p === "/feed/history") return "history";
    if (p === "/feed/playlists") return "playlists";
    if (p === "/feed/you") return "you";
    if (p === "/playlist") return new URLSearchParams(location.search).get("list") === "WL" ? "watch_later" : "playlist";
    if (/^\/(@|channel\/|c\/|user\/)/.test(p)) return "channel";
    return "other";
  }

  // --- List of displayed videos ------------------------------------------

  function isOnScreen(r) {
    return r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
  }

  // Displayed page: YouTube keeps the pages already seen in the document, hidden
  function activePage() {
    const manager = $(SEL.pageManager);
    const current = manager && typeof manager.getCurrentPage === "function" ? manager.getCurrentPage() : null;
    return current && !current.hasAttribute("hidden") ? current : $(SEL.activePage);
  }

  // Does the displayed page match the address?
  function pageReady() {
    const el = activePage();
    if (!el || document.readyState !== "complete") return false;
    const type = pageType();
    const tag = el.tagName.toLowerCase();
    if (PAGE_ELEMENTS[type]) return tag === PAGE_ELEMENTS[type];
    if (tag !== "ytd-browse") return false;
    return !BROWSE_SUBTYPES[type] || el.getAttribute("page-subtype") === BROWSE_SUBTYPES[type];
  }

  // List items, in reading order: row by row, from left to right
  function listElements(onlyOnScreen) {
    const root = activePage() || document;
    const els = $$(SEL.items, root)
      .filter((el) => !el.parentElement.closest(SEL.items) && !el.closest(SEL.ads))
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.width > 0 && r.height > 0 && (!onlyOnScreen || isOnScreen(r)))
      .sort((a, b) => a.r.top - b.r.top);
    const rows = [];
    for (const item of els) {
      const row = rows[rows.length - 1];
      if (row && Math.abs(item.r.top - row[0].r.top) < 12) row.push(item);
      else rows.push([item]);
    }
    return rows.flatMap((row) => row.sort((a, b) => a.r.left - b.r.left).map(({ el }) => el));
  }

  function describe(el) {
    if (el.matches(SEL.channelResult)) {
      const link = $(SEL.channelLink, el);
      return { kind: "channel", name: text($(SEL.title, el)), url: link ? link.getAttribute("href") : null };
    }
    const link = $(SEL.watchLink, el);
    const href = link ? link.getAttribute("href") : "";
    const id = (href.match(/[?&]v=([\w-]{11})/) || href.match(/\/shorts\/([\w-]{11})/) || [])[1] || null;
    const titleEl = $(SEL.title, el);
    const title = clean((titleEl && (titleEl.getAttribute("title") || titleEl.textContent)) || (link && link.getAttribute("title")));
    const channelEl = $(SEL.channelLink, el);
    const channel = channelEl ? text(channelEl) || null : null;
    const lines = (el.innerText || "").split("\n").map(clean).filter(Boolean);
    // Duration: in the thumbnail's data (immediate), otherwise in the badge displayed later
    const length = el.data && el.data.lengthText;
    const fromData = length ? clean(length.simpleText || (length.runs || []).map((r) => r.text).join("")) : "";
    const duration = (DURATION.test(fromData) ? fromData : null) || lines.find((l) => DURATION.test(l)) || null;
    const info = lines
      .filter((l) => l !== title && l !== channel && l !== duration && l.length < 80 && !NOISE.test(l))
      .slice(0, 3)
      .join(" · ");
    return { kind: href.startsWith("/shorts/") ? "short" : "video", id, title, channel, duration, info };
  }

  function items(onlyOnScreen = true) {
    return listElements(onlyOnScreen).map((el, i) => ({ index: i + 1, ...describe(el) }));
  }

  // --- Player ---------------------------------------------------------------

  function hasPlayer() {
    const p = player();
    return !!(p && p.getPlayerState && (pageType() === "watch" || $(SEL.miniplayerActive)));
  }

  // Chapters: first in the video page's data (progress bar),
  // otherwise in the list in the description
  function chapters() {
    const found = new Map();
    const page = $(SEL.watchPage);
    try {
      const bar = page.data.playerOverlays.playerOverlayRenderer.decoratedPlayerBarRenderer
        .decoratedPlayerBarRenderer.playerBar.multiMarkersPlayerBarRenderer;
      for (const marker of bar.markersMap || []) {
        for (const c of marker.value.chapters || []) {
          const r = c.chapterRenderer;
          const title = clean(r.title.simpleText || (r.title.runs || []).map((x) => x.text).join(""));
          found.set(Math.round(r.timeRangeStartMillis / 1000), title);
        }
      }
    } catch (e) {
      // no chapters in the data: we try the description
    }
    if (!found.size && page) {
      for (const el of $$(SEL.chapters, page)) {
        const start = parseTime(text($(SEL.chapterTime, el)));
        const title = text($(SEL.chapterTitle, el));
        if (title && start !== null && !found.has(start)) found.set(start, title);
      }
    }
    return [...found].map(([start, title]) => ({ start, title })).sort((a, b) => a.start - b.start);
  }

  function currentChapter(list, t) {
    let current = null;
    for (const c of list) if (c.start <= t + 0.5) current = c;
    return current;
  }

  function autoplayOn() {
    const t = $(SEL.autoplayToggle);
    return t ? t.getAttribute("aria-checked") === "true" : null;
  }

  function captionTrack() {
    try {
      const t = player().getOption("captions", "track");
      return t && t.languageCode ? t : null;
    } catch (e) {
      return null;
    }
  }

  const STATES = { "-1": "unstarted", 0: "ended", 1: "playing", 2: "paused", 3: "buffering", 5: "cued" };

  function playerState() {
    if (!hasPlayer()) return null;
    const p = player();
    const v = video();
    const data = p.getVideoData() || {};
    const list = chapters();
    const t = p.getCurrentTime();
    const track = captionTrack();
    const current = currentChapter(list, t);
    return {
      video_id: data.video_id || null,
      title: data.title || null,
      channel: data.author || null,
      state: STATES[p.getPlayerState()] || "unknown",
      position: Math.round(t),
      duration: Math.round(p.getDuration()),
      speed: p.getPlaybackRate(),
      volume: p.getVolume(),
      muted: p.isMuted(),
      captions: track ? track.displayName || track.languageCode : null,
      quality: p.getPlaybackQuality(),
      qualities: p.getAvailableQualityLevels(),
      fullscreen: !!(document.fullscreenElement || document.webkitFullscreenElement),
      theater: !!($(SEL.watchPage) && $(SEL.watchPage).hasAttribute("theater")),
      miniplayer: !!$(SEL.miniplayerActive),
      subscribed: subscribed(),
      pip: v ? v.webkitPresentationMode === "picture-in-picture" || document.pictureInPictureElement === v : false,
      autoplay: autoplayOn(),
      loop: v ? v.loop : null,
      ad: p.classList.contains("ad-showing"),
      chapter: current ? current.title : null,
      chapters: list,
    };
  }

  function requirePlayer() {
    if (!hasPlayer()) throw new Error("no_player");
    return player();
  }

  function click(selector, what) {
    const el = $(selector);
    if (!el) throw new Error(`not_found:${what}`);
    el.click();
    return true;
  }

  function seekChapter(step) {
    const p = requirePlayer();
    const list = chapters();
    if (!list.length) throw new Error("no_chapters");
    const t = p.getCurrentTime();
    let i = list.indexOf(currentChapter(list, t));
    // "previous chapter" after the first 3 seconds: back to the start of the current chapter
    if (step < 0 && i >= 0 && t - list[i].start > 3) step = 0;
    i = clamp(i + step, 0, list.length - 1);
    p.seekTo(list[i].start, true);
    return list[i].title;
  }

  function setToggle(selector, isOn, want, what) {
    const on = isOn();
    if (on === null) throw new Error(`not_found:${what}`);
    if (want === undefined || want === null) want = !on;
    if (on !== want) click(selector, what);
    return want;
  }

  const PLAYER_ACTIONS = {
    play: () => (requirePlayer().playVideo(), true),
    pause: () => (requirePlayer().pauseVideo(), true),
    toggle: () => {
      const p = requirePlayer();
      if (p.getPlayerState() === 1) p.pauseVideo();
      else p.playVideo();
      return p.getPlayerState() !== 1;
    },
    seek_by: (s) => {
      const p = requirePlayer();
      const to = clamp(p.getCurrentTime() + Number(s), 0, p.getDuration());
      p.seekTo(to, true);
      return Math.round(to);
    },
    seek_back: (s) => PLAYER_ACTIONS.seek_by(-Number(s)),
    seek_to: (s) => (requirePlayer().seekTo(Number(s), true), Number(s)),
    seek_fraction: (f) => {
      const p = requirePlayer();
      const to = p.getDuration() * clamp(Number(f), 0, 1);
      p.seekTo(to, true);
      return Math.round(to);
    },
    restart: () => (requirePlayer().seekTo(0, true), 0),
    chapter_next: () => seekChapter(1),
    chapter_previous: () => seekChapter(-1),
    chapter: (name) => {
      const p = requirePlayer();
      const wanted = fold(name);
      const c = chapters().find((ch) => fold(ch.title).includes(wanted));
      if (!c) throw new Error("chapter_not_found");
      p.seekTo(c.start, true);
      return c.title;
    },
    speed: (rate) => {
      const p = requirePlayer();
      const rates = p.getAvailablePlaybackRates();
      const r = clamp(Number(rate), Math.min(...rates), Math.max(...rates));
      p.setPlaybackRate(r);
      return p.getPlaybackRate();
    },
    faster: () => PLAYER_ACTIONS.speed(requirePlayer().getPlaybackRate() + 0.25),
    slower: () => PLAYER_ACTIONS.speed(requirePlayer().getPlaybackRate() - 0.25),
    volume: (n) => {
      const p = requirePlayer();
      p.unMute();
      p.setVolume(clamp(Math.round(Number(n)), 0, 100));
      return p.getVolume();
    },
    volume_up: () => PLAYER_ACTIONS.volume(requirePlayer().getVolume() + 10),
    volume_down: () => PLAYER_ACTIONS.volume(requirePlayer().getVolume() - 10),
    mute: () => (requirePlayer().mute(), true),
    unmute: () => (requirePlayer().unMute(), true),
    // Full screen: often refused without a real user gesture, Python checks afterwards
    fullscreen: () => {
      const p = requirePlayer();
      const request = p.requestFullscreen || p.webkitRequestFullscreen;
      if (request) {
        const r = request.call(p);
        if (r && r.catch) r.catch(() => {});
      }
      return "requested";
    },
    exit_fullscreen: () => {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (document.fullscreenElement || document.webkitFullscreenElement) exit.call(document);
      return true;
    },
    theater: (want) => setToggle(SEL.theaterButton, () => ($(SEL.watchPage) ? $(SEL.watchPage).hasAttribute("theater") : null), want, "theater"),
    miniplayer: () => click(SEL.miniplayerButton, "miniplayer"),
    pip: () => {
      const v = video();
      if (!v) throw new Error("no_player");
      if (v.webkitSetPresentationMode) v.webkitSetPresentationMode(v.webkitPresentationMode === "picture-in-picture" ? "inline" : "picture-in-picture");
      else if (v.requestPictureInPicture) v.requestPictureInPicture().catch(() => {});
      return "requested";
    },
    captions: (want) => {
      const p = requirePlayer();
      const on = !!captionTrack();
      if (want === undefined || want === null) want = !on;
      if (want) {
        p.loadModule("captions");
        if (!captionTrack()) p.toggleSubtitles();
      } else if (on) {
        p.toggleSubtitles();
      }
      return want;
    },
    captions_language: (code) => {
      const p = requirePlayer();
      p.loadModule("captions");
      const tracks = p.getOption("captions", "tracklist") || [];
      const own = tracks.find((t) => (t.languageCode || "").startsWith(code));
      if (own) {
        p.setOption("captions", "track", own);
        return own.displayName || own.languageCode;
      }
      // No track in this language: automatic translation of an existing track
      const langs = p.getOption("captions", "translationLanguages") || [];
      const lang = langs.find((l) => (l.languageCode || "").startsWith(code));
      if (!tracks.length || !lang) throw new Error("captions_language_unavailable");
      p.setOption("captions", "track", { ...tracks[0], translationLanguage: lang });
      return `${tracks[0].displayName || tracks[0].languageCode} → ${lang.languageName || lang.languageCode}`;
    },
    quality: (q) => {
      const p = requirePlayer();
      if (q === "auto") {
        p.setPlaybackQualityRange("auto", "auto");
        return "auto";
      }
      const levels = p.getAvailableQualityLevels().filter((l) => l !== "auto");
      const choice = levels.includes(q) ? q : levels[0]; // the highest available
      p.setPlaybackQualityRange(choice, choice);
      return choice;
    },
    autoplay: (want) => setToggle(SEL.autoplayToggle, autoplayOn, want, "autoplay"),
    loop: (want) => {
      const v = video();
      if (!v) throw new Error("no_player");
      v.loop = want === undefined || want === null ? !v.loop : !!want;
      return v.loop;
    },
    skip_ad: () => {
      const b = $(SEL.skipAd);
      if (!b) return player() && player().classList.contains("ad-showing") ? "not_skippable_yet" : "no_ad";
      b.click();
      return "skipped";
    },
  };

  // --- Navigation --------------------------------------------------------------

  // YouTube's internal navigation (without reloading the page), like a click on a link
  function navigate(url, pageTypeName, rootVe, endpoint) {
    const app = $(SEL.app);
    if (!app) throw new Error("not_found:app");
    window.__voixNavUrl = null;
    const command = { commandMetadata: { webCommandMetadata: { url, webPageType: pageTypeName, rootVe } }, ...endpoint };
    app.dispatchEvent(new CustomEvent("yt-navigate", { bubbles: true, composed: true, detail: { endpoint: command } }));
    return url;
  }

  function openPage(name) {
    const p = PAGES[name];
    if (!p) throw new Error(`unknown_page:${name}`);
    return navigate(p.url, p.type, p.rootVe, { browseEndpoint: { browseId: p.browseId } });
  }

  function search(query, params) {
    let url = `/results?search_query=${encodeURIComponent(query).replace(/%20/g, "+")}`;
    if (params) url += `&sp=${encodeURIComponent(params)}`;
    return navigate(url, "WEB_PAGE_TYPE_SEARCH", 4724, { searchEndpoint: { query, params: params || undefined } });
  }

  function watch(id) {
    if (!/^[\w-]{11}$/.test(id)) throw new Error("bad_video_id");
    return navigate(`/watch?v=${id}`, "WEB_PAGE_TYPE_WATCH", 3832, { watchEndpoint: { videoId: id } });
  }

  function playIndex(index) {
    const el = listElements(true)[Number(index) - 1];
    if (!el) throw new Error("no_such_item");
    const link = el.matches(SEL.channelResult) ? $(SEL.channelLink, el) : $(SEL.watchLink, el);
    if (!link) throw new Error("not_found:link");
    window.__voixNavUrl = null;
    link.click(); // the thumbnail's link: YouTube handles the navigation itself
    return describe(el);
  }

  const NAV_ACTIONS = {
    scroll_down: () => (scrollBy({ top: innerHeight * 0.8 }), scrollY),
    scroll_up: () => (scrollBy({ top: -innerHeight * 0.8 }), scrollY),
    top: () => (scrollTo({ top: 0 }), 0),
    more: () => (scrollTo({ top: document.documentElement.scrollHeight }), scrollY),
    back: () => (history.back(), true),
    forward: () => (history.forward(), true),
    next: () => (requirePlayer().nextVideo(), true),
    previous: () => (requirePlayer().previousVideo(), true),
  };

  // --- Account ------------------------------------------------------------------

  // Displayed subscription: "S'abonner" / "Subscribe", or "Abonné" / "Subscribed" (null if there is no button)
  function subscribed() {
    const b = $(SEL.subscribeButton);
    if (!b) return null;
    const label = fold(text(b) || b.getAttribute("aria-label"));
    return label.startsWith("abonne") || label.startsWith("subscribed");
  }

  // Internal command of the YouTube app (the one its own menus send)
  function resolveCommand(command) {
    const app = $(SEL.app);
    if (!app || typeof app.resolveCommand !== "function") throw new Error("no_resolve_command");
    app.resolveCommand(command);
  }

  function currentVideoId() {
    const id = (requirePlayer().getVideoData() || {}).video_id;
    if (!id) throw new Error("no_video");
    return id;
  }

  // Same command as YouTube's "Save" menu
  function editWatchLater(action) {
    resolveCommand({
      commandMetadata: { webCommandMetadata: { sendPost: true, apiUrl: "/youtubei/v1/browse/edit_playlist" } },
      playlistEditEndpoint: { playlistId: "WL", actions: [action] },
    });
    return action.addedVideoId || action.removedVideoId;
  }

  function setLike(status, apiUrl) {
    const id = currentVideoId();
    resolveCommand({
      commandMetadata: { webCommandMetadata: { sendPost: true, apiUrl } },
      likeEndpoint: { status, target: { videoId: id } },
    });
    return id;
  }

  // Channel of the current video, or of the displayed channel page
  function channelId() {
    if (hasPlayer() && pageType() === "watch") {
      const response = player().getPlayerResponse();
      const id = response && response.videoDetails && response.videoDetails.channelId;
      if (id) return id;
    }
    const page = activePage();
    const meta = page && page.data && page.data.metadata && page.data.metadata.channelMetadataRenderer;
    if (meta && meta.externalId) return meta.externalId;
    throw new Error("no_channel");
  }

  function setSubscription(endpoint, apiUrl) {
    const id = channelId();
    resolveCommand({ commandMetadata: { webCommandMetadata: { sendPost: true, apiUrl } }, [endpoint]: { channelIds: [id] } });
    return id;
  }

  const ACCOUNT_ACTIONS = {
    // Like: the command the button sends (a simulated click on the button is ignored by YouTube).
    // The button only changes its appearance on the next page load.
    like: () => setLike("LIKE", "/youtubei/v1/like/like"),
    unlike: () => setLike("INDIFFERENT", "/youtubei/v1/like/removelike"),
    watch_later: () => editWatchLater({ addedVideoId: currentVideoId(), action: "ACTION_ADD_VIDEO" }),
    watch_later_remove: () => editWatchLater({ removedVideoId: currentVideoId(), action: "ACTION_REMOVE_VIDEO_BY_VIDEO_ID" }),
    // Subscription: the spoken confirmation is done on the Python side before getting here
    subscribe: () => setSubscription("subscribeEndpoint", "/youtubei/v1/subscription/subscribe"),
    unsubscribe: () => setSubscription("unsubscribeEndpoint", "/youtubei/v1/subscription/unsubscribe"),
  };

  // --- Dictated comment ------------------------------------------------------------
  // Writes the text in the comment box; posting is a separate step,
  // done only after the user's "yes" (see safety.youtube_comment).

  const COMMENT_ACTIONS = {
    load: () => {
      const section = $(SEL.comments);
      if (!section || pageType() !== "watch") throw new Error("no_video");
      section.scrollIntoView({ block: "start" });
      return !!($(SEL.commentPlaceholder) || $(SEL.commentEditor));
    },
    fill: (text) => {
      if (!$(SEL.commentEditor)) {
        const placeholder = $(SEL.commentPlaceholder);
        if (placeholder) placeholder.click();
        return null; // the editor appears right after: Python asks again
      }
      const editor = $(SEL.commentEditor);
      editor.focus();
      document.execCommand("selectAll", false);
      document.execCommand("insertText", false, text);
      return clean(editor.textContent);
    },
    submit: () => click(SEL.commentSubmit, "comment_submit"),
    cancel: () => {
      const button = $(SEL.commentCancel);
      if (button) button.click();
      scrollTo({ top: 0 });
      return true;
    },
    first: () => text($(SEL.firstCommentText)),
  };

  // --- Diagnostic --------------------------------------------------------------

  function probe() {
    const p = player();
    const methods = [
      "getPlayerState", "getCurrentTime", "getDuration", "seekTo", "playVideo", "pauseVideo", "nextVideo",
      "previousVideo", "setPlaybackRate", "getAvailablePlaybackRates", "setVolume", "getVolume", "mute", "unMute",
      "isMuted", "getVideoData", "getAvailableQualityLevels", "setPlaybackQualityRange", "getPlaybackQuality",
      "toggleSubtitles", "isSubtitlesOn", "loadModule", "getOption", "setOption",
    ];
    const selectors = {};
    for (const [key, s] of Object.entries(SEL)) selectors[key] = $$(s).length;
    const counts = {};
    for (const s of SEL.items.split(", ")) counts[s] = $$(s, activePage() || document).length;
    const page = activePage();
    return {
      page: pageType(),
      url: location.pathname + location.search,
      active_page: page ? `${page.tagName.toLowerCase()}[${page.getAttribute("page-subtype") || ""}]` : null,
      page_ready: pageReady(),
      player_methods: p ? Object.fromEntries(methods.map((m) => [m, typeof p[m] === "function"])) : null,
      selectors,
      item_counts: counts,
      first_items: items(true).slice(0, 3),
      chapters: chapters().slice(0, 5),
      like_pressed: $(SEL.likeButton) ? $(SEL.likeButton).getAttribute("aria-pressed") : null,
      subscribe_label: $(SEL.subscribeButton) ? text($(SEL.subscribeButton)) : null,
      resolve_command: typeof ($(SEL.app) || {}).resolveCommand,
      fullscreen_api: p ? typeof (p.requestFullscreen || p.webkitRequestFullscreen) : null,
      caption_tracks: p && p.getOption ? (() => { try { p.loadModule("captions"); return (p.getOption("captions", "tracklist") || []).map((t) => t.languageCode); } catch (e) { return String(e); } })() : null,
    };
  }

  // --- Entry point ------------------------------------------------------------

  // --- Transcript (for "résume cette vidéo") ------------------------------------
  // YouTube itself loads the text into its "Transcript" panel: Boulito opens this panel
  // (internal command, otherwise the button in the description), reads the segments, then closes it.
  // A direct call to the API (get_transcript) is refused, and the caption track requires a player token.

  const TRANSCRIPT_PANEL = "engagement-panel-searchable-transcript";
  let transcriptOpenedByUs = false;

  function transcriptPanel() {
    return $(SEL.transcriptPanel);
  }

  function setTranscriptPanel(visibility) {
    resolveCommand({ changeEngagementPanelVisibilityAction: { targetId: TRANSCRIPT_PANEL, visibility } });
  }

  function transcriptSegments() {
    const panel = transcriptPanel();
    if (!panel) return [];
    return [...panel.querySelectorAll(SEL.transcriptSegment)].map((el) => {
      const time = text($(SEL.transcriptTime, el));
      const body = $(SEL.transcriptText, el);
      const words = (body ? body.textContent : el.textContent.replace(time, "")).replace(/\s+/g, " ").trim();
      return { t: parseTime(time) || 0, text: words };
    }).filter((x) => x.text);
  }

  const TRANSCRIPT_ACTIONS = {
    open: () => {
      if (!new URL(location.href).searchParams.get("v")) throw new Error("no_player");
      const panel = transcriptPanel();
      if (!panel) throw new Error("no_transcript");
      transcriptOpenedByUs = panel.getAttribute("visibility") !== "ENGAGEMENT_PANEL_VISIBILITY_EXPANDED";
      if (transcriptOpenedByUs) {
        try {
          setTranscriptPanel("ENGAGEMENT_PANEL_VISIBILITY_EXPANDED");
        } catch (e) {
          const button = $(SEL.transcriptButton);
          if (!button) throw new Error("no_transcript");
          button.click();
        }
      }
      return true;
    },
    read: () => {
      const segments = transcriptSegments();
      return { count: segments.length, segments: segments.length ? segments : [] };
    },
    close: () => {
      if (transcriptOpenedByUs) setTranscriptPanel("ENGAGEMENT_PANEL_VISIBILITY_HIDDEN");
      transcriptOpenedByUs = false;
      return true;
    },
  };

  const FUNCTIONS = {
    state: () => {
      const onScreen = items(true);
      const seen = new Set(onScreen.map((i) => i.id));
      return {
        page: pageType(),
        url: location.pathname + location.search,
        query: new URLSearchParams(location.search).get("search_query"),
        items: onScreen,
        // Videos already loaded but off screen (further down): referred to by their ID
        more: listElements(false).map(describe).filter((i) => i.id && !seen.has(i.id) && i.kind !== "channel").slice(0, 10),
        player: playerState(),
      };
    },
    // navigated: null if no internal navigation is being tracked, otherwise true once it has finished
    fullscreen_state: () => !!(document.fullscreenElement || document.webkitFullscreenElement),
    status: () => ({
      page: pageType(),
      url: location.pathname + location.search,
      ready: pageReady(),
      navigated: window.__voixNavUrl === undefined ? null : window.__voixNavUrl === location.href,
      items: pageReady() ? listElements(true).length : 0,
      player: hasPlayer(),
    }),
    open_page: openPage,
    search,
    watch,
    play_index: playIndex,
    player: (action, value) => {
      const f = PLAYER_ACTIONS[action];
      if (!f) throw new Error(`unknown_action:${action}`);
      return f(value);
    },
    nav: (action) => {
      const f = NAV_ACTIONS[action];
      if (!f) throw new Error(`unknown_action:${action}`);
      return f();
    },
    comment: (action, value) => {
      const f = COMMENT_ACTIONS[action];
      if (!f) throw new Error(`unknown_action:${action}`);
      return f(value);
    },
    account: (action) => {
      const f = ACCOUNT_ACTIONS[action];
      if (!f) throw new Error(`unknown_action:${action}`);
      return f();
    },
    transcript: (action) => {
      const f = TRANSCRIPT_ACTIONS[action];
      if (!f) throw new Error(`unknown_action:${action}`);
      return f();
    },
    probe,
  };

  window.__voix = {
    version: VERSION,
    run(name, args) {
      try {
        const f = FUNCTIONS[name];
        if (!f) throw new Error(`unknown_function:${name}`);
        return JSON.stringify({ ok: true, value: f(...args) });
      } catch (e) {
        return JSON.stringify({ ok: false, error: e && e.message ? e.message : String(e) });
      }
    },
  };
})();
