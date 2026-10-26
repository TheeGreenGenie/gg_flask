/* Find Fabrics: a swipeable card stack of in-stock fabrics.
   Right (drag, button, →) saves; left (drag, button, ←) skips; tap/Enter opens the preview.
   Saves go to the server-side wishlist (window.GGWishlist from site.js). Which cards have
   been seen is remembered in this browser only (bounded, versioned key, W-004).
   Without JS the page shows a plain product grid instead. */
(function () {
	'use strict';

	var root = document.querySelector('[data-gg-ff]');
	if (!root || !window.fetch) { return; }

	var LEGACY_SAVED_KEY = 'gg-ff-saved-v1'; // saves from before the server wishlist
	var SEEN_KEY = 'gg-ff-seen-v1';
	var MAX_SEEN = 1000;
	var STACK_DEPTH = 3; // cards rendered at once
	var SWIPE_DISTANCE = 110; // px past which a release counts as a swipe
	var SWIPE_VELOCITY = 0.6; // px/ms flick also counts
	var TAP_MAX = 8; // px of movement still treated as a tap

	var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
	var stackEl = root.querySelector('[data-gg-ff-stack]');
	var emptyEl = root.querySelector('[data-gg-ff-empty]');
	var controls = root.querySelector('[data-gg-ff-controls]');
	var savedList = root.querySelector('[data-gg-ff-saved]');
	var savedEmpty = root.querySelector('[data-gg-ff-saved-empty]');
	var savedCount = root.querySelector('[data-gg-ff-saved-count]');
	var clearBtn = root.querySelector('[data-gg-ff-clear]');
	var current = root.querySelector('[data-gg-ff-current]');
	var toast = document.querySelector('[data-gg-ff-toast]');

	var category = root.getAttribute('data-category') || 'fabrics';
	var queue = [];
	var cursor = 0;
	var exhausted = false;
	var loading = false;
	var busy = false;

	/* ---------- storage (fails soft in private mode) ---------- */
	function load(key) {
		try {
			var v = JSON.parse(window.localStorage.getItem(key) || '[]');
			return Array.isArray(v) ? v : [];
		} catch (e) { return []; }
	}
	function store(key, value) {
		try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ }
	}

	var wishlist = window.GGWishlist;
	var saved = []; // mirror of the server wishlist: [{slug, name, price, image, url, preview}]
	var seen = load(SEEN_KEY).filter(function (s) { return typeof s === 'string'; });

	function markSeen(slug) {
		if (seen.indexOf(slug) === -1) {
			seen.push(slug);
			if (seen.length > MAX_SEEN) { seen = seen.slice(-MAX_SEEN); }
			store(SEEN_KEY, seen);
		}
	}

	/* ---------- feed ---------- */
	function fetchMore() {
		if (loading || exhausted) { return Promise.resolve(); }
		loading = true;
		var url = root.getAttribute('data-feed') + '?cursor=' + encodeURIComponent(cursor) +
			'&category=' + encodeURIComponent(category);
		var requested = category;
		return fetch(url, { credentials: 'same-origin' })
			.then(function (r) { if (!r.ok) { throw new Error('feed ' + r.status); } return r.json(); })
			.then(function (data) {
				if (requested !== category) { return; } // category changed while loading
				(data.items || []).forEach(function (item) {
					if (seen.indexOf(item.slug) === -1) { queue.push(item); }
				});
				if (data.next_cursor === null || data.next_cursor === undefined) {
					exhausted = true;
				} else {
					cursor = data.next_cursor;
				}
			})
			.catch(function () { exhausted = true; showToast('Could not load more fabrics.'); })
			.then(function () { loading = false; });
	}

	/* Warm the browser cache with the next photos so cards appear instantly. */
	var preloaded = {};
	function preloadUpcoming() {
		queue.slice(STACK_DEPTH, STACK_DEPTH + 6).forEach(function (item) {
			if (!preloaded[item.image]) {
				preloaded[item.image] = true;
				var img = new Image();
				img.decoding = 'async';
				img.src = item.image;
			}
		});
	}

	function ensureQueue() {
		// Keep a few cards ready; skip whole pages of already-seen fabrics.
		if (queue.length >= STACK_DEPTH + 2 || exhausted) { preloadUpcoming(); return Promise.resolve(); }
		return fetchMore().then(function () {
			if (queue.length < STACK_DEPTH + 2 && !exhausted) { return ensureQueue(); }
		});
	}

	/* ---------- rendering ---------- */
	function el(tag, cls, text) {
		var n = document.createElement(tag);
		if (cls) { n.className = cls; }
		if (text) { n.textContent = text; }
		return n;
	}

	function buildCard(item) {
		var card = el('article', 'gg-ff-card');
		card.setAttribute('aria-label', item.name + ', ' + item.price);
		var img = el('img', 'gg-ff-card__img');
		img.src = item.image;
		img.alt = item.alt || item.name;
		img.draggable = false;
		img.width = 600;
		img.height = 750;
		var stampSave = el('span', 'gg-ff-card__stamp gg-ff-card__stamp--save', 'Save');
		var stampSkip = el('span', 'gg-ff-card__stamp gg-ff-card__stamp--skip', 'Skip');
		var body = el('div', 'gg-ff-card__body');
		body.append(el('p', 'gg-ff-card__cat', item.category), el('h2', 'gg-ff-card__name', item.name));
		var price = el('p', 'gg-ff-card__price');
		if (item.compare_at) {
			price.append(el('del', '', item.compare_at), ' ', el('ins', '', item.price));
		} else {
			price.textContent = item.price;
		}
		body.append(price);
		card.append(img, stampSave, stampSkip, body);
		card._item = item;
		return card;
	}

	function render() {
		var cards = Array.prototype.slice.call(stackEl.children);
		// Add cards underneath until the stack is full.
		for (var i = cards.length; i < Math.min(STACK_DEPTH, queue.length); i += 1) {
			stackEl.prepend(buildCard(queue[i]));
		}
		cards = Array.prototype.slice.call(stackEl.children);
		cards.forEach(function (c, index) {
			var depth = cards.length - 1 - index; // 0 = top card
			c.style.setProperty('--depth', depth);
			c.classList.toggle('is-top', depth === 0);
			c.tabIndex = depth === 0 ? 0 : -1;
			c.setAttribute('aria-hidden', depth === 0 ? 'false' : 'true');
		});
		var top = topCard();
		var empty = !top && exhausted;
		emptyEl.hidden = !empty;
		controls.hidden = empty;
		stackEl.classList.toggle('is-loading', !top && !exhausted);
		if (top) {
			current.hidden = false;
			current.querySelector('.gg-ff__side-cat').textContent = top._item.category;
			current.querySelector('[data-gg-ff-cat-link]').href = top._item.category_url;
		} else {
			current.hidden = true;
		}
	}

	function topCard() { return stackEl.lastElementChild; }

	function renderSaved() {
		savedList.textContent = '';
		saved.slice().reverse().forEach(function (f) {
			var li = el('li', 'gg-ff__saved-item');
			var a = el('a', 'gg-ff__saved-link');
			a.href = f.url;
			a.setAttribute('data-gg-preview', f.preview); // opens the quick-view preview
			var img = el('img');
			img.src = f.image;
			img.alt = '';
			img.width = 56;
			img.height = 56;
			img.loading = 'lazy';
			var text = el('span', 'gg-ff__saved-text');
			text.append(el('span', 'gg-ff__saved-name', f.name), el('span', 'gg-ff__saved-price', f.price));
			a.append(img, text);
			var remove = el('button', 'gg-ff__saved-remove');
			remove.type = 'button';
			remove.setAttribute('aria-label', 'Remove ' + f.name + ' from saved');
			remove.textContent = '×';
			remove.addEventListener('click', function () {
				saved = saved.filter(function (s) { return s.slug !== f.slug; });
				renderSaved();
				if (wishlist) { wishlist.remove(f.slug); }
			});
			li.append(a, remove);
			savedList.append(li);
		});
		savedEmpty.hidden = saved.length > 0;
		clearBtn.hidden = saved.length === 0;
		savedCount.textContent = '(' + saved.length + ')';
	}

	var toastTimer = null;
	function showToast(message) {
		if (!toast) { return; }
		window.clearTimeout(toastTimer);
		toast.textContent = message;
		toast.classList.add('is-visible');
		toastTimer = window.setTimeout(function () { toast.classList.remove('is-visible'); }, 2200);
	}

	/* ---------- actions ---------- */
	function decide(direction) {
		var card = topCard();
		if (!card || busy) { return; }
		busy = true;
		var item = card._item;
		queue.shift();
		markSeen(item.slug);

		if (direction > 0) {
			if (!saved.some(function (s) { return s.slug === item.slug; })) {
				saved.push(item);
				renderSaved();
			}
			showToast('Saved to your wishlist ♥');
			if (wishlist) {
				wishlist.add(item.slug).then(function (data) {
					if (!data.ok) {
						saved = saved.filter(function (s) { return s.slug !== item.slug; });
						renderSaved();
						showToast(data.message);
					}
				}).catch(function () { showToast('Could not save — check your connection.'); });
			}
		}

		function done() {
			card.remove();
			busy = false;
			render();
			ensureQueue().then(render);
			var next = topCard();
			if (next && root.contains(document.activeElement) && document.activeElement !== next) {
				// keep keyboard users on the stack
			}
		}

		if (reduced.matches) {
			done();
			return;
		}
		card.classList.add('is-leaving');
		var x = direction * (window.innerWidth * 0.9 + card.offsetWidth);
		var rot = direction * 24;
		card.style.transform = 'translate(' + x + 'px, -40px) rotate(' + rot + 'deg)';
		card.style.opacity = '0';
		window.setTimeout(done, 320);
	}

	function openDetails() {
		var card = topCard();
		if (!card) { return; }
		var item = card._item;
		if (window.GGQuickView) {
			window.GGQuickView.open(item.preview, item.url, card);
		} else {
			window.location.href = item.url;
		}
	}

	/* ---------- dragging (mouse, touch, pen via Pointer Events) ---------- */
	var drag = null;

	stackEl.addEventListener('pointerdown', function (event) {
		var card = topCard();
		if (!card || busy || event.target.closest('.gg-ff-card') !== card) { return; }
		if (event.pointerType === 'mouse' && event.button !== 0) { return; }
		drag = { card: card, id: event.pointerId, x0: event.clientX, y0: event.clientY, t0: event.timeStamp, dx: 0, dy: 0 };
		card.setPointerCapture(event.pointerId);
		card.classList.add('is-dragging');
	});

	stackEl.addEventListener('pointermove', function (event) {
		if (!drag || event.pointerId !== drag.id) { return; }
		drag.dx = event.clientX - drag.x0;
		drag.dy = event.clientY - drag.y0;
		var rot = drag.dx / 18;
		drag.card.style.transform = 'translate(' + drag.dx + 'px,' + (drag.dy * 0.35) + 'px) rotate(' + rot + 'deg)';
		var strength = Math.min(1, Math.abs(drag.dx) / SWIPE_DISTANCE);
		drag.card.style.setProperty('--save', drag.dx > 0 ? strength : 0);
		drag.card.style.setProperty('--skip', drag.dx < 0 ? strength : 0);
	});

	function endDrag(event) {
		if (!drag || event.pointerId !== drag.id) { return; }
		var d = drag;
		drag = null;
		d.card.classList.remove('is-dragging');
		var elapsed = Math.max(1, event.timeStamp - d.t0);
		var velocity = Math.abs(d.dx) / elapsed;
		var moved = Math.hypot(d.dx, d.dy);
		if (Math.abs(d.dx) > SWIPE_DISTANCE || (velocity > SWIPE_VELOCITY && Math.abs(d.dx) > 40)) {
			decide(d.dx > 0 ? 1 : -1);
			return;
		}
		d.card.style.transform = '';
		d.card.style.setProperty('--save', 0);
		d.card.style.setProperty('--skip', 0);
		if (moved < TAP_MAX && event.type === 'pointerup') { openDetails(); }
	}
	stackEl.addEventListener('pointerup', endDrag);
	stackEl.addEventListener('pointercancel', endDrag);

	/* ---------- buttons and keyboard ---------- */
	root.querySelector('[data-gg-ff-skip]').addEventListener('click', function () { decide(-1); });
	root.querySelector('[data-gg-ff-save]').addEventListener('click', function () { decide(1); });
	root.querySelector('[data-gg-ff-info]').addEventListener('click', openDetails);

	document.addEventListener('keydown', function (event) {
		if (document.querySelector('dialog[open]')) { return; }
		var t = event.target;
		if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) { return; }
		if (event.key === 'ArrowRight') { event.preventDefault(); decide(1); }
		else if (event.key === 'ArrowLeft') { event.preventDefault(); decide(-1); }
		else if (event.key === 'Enter' && t === topCard()) { event.preventDefault(); openDetails(); }
	});

	clearBtn.addEventListener('click', function () {
		saved = [];
		renderSaved();
		if (wishlist) { wishlist.clear(); }
	});

	function restart() {
		queue = [];
		cursor = 0;
		exhausted = false;
		loading = false;
		stackEl.textContent = '';
		render();
		ensureQueue().then(render);
	}

	root.querySelector('[data-gg-ff-reset]').addEventListener('click', function () {
		seen = [];
		store(SEEN_KEY, seen);
		restart();
	});

	/* Category chips: links without JS; with JS they switch the stack in place. */
	var chips = Array.prototype.slice.call(root.querySelectorAll('[data-gg-ff-cat]'));
	var emptyCat = root.querySelector('[data-gg-ff-empty-cat]');
	function setCategory(slug, label) {
		category = slug;
		chips.forEach(function (c) {
			if (c.getAttribute('data-gg-ff-cat') === slug) { c.setAttribute('aria-current', 'true'); }
			else { c.removeAttribute('aria-current'); }
		});
		if (emptyCat) { emptyCat.textContent = slug === 'fabrics' ? '' : 'in ' + label; }
	}
	chips.forEach(function (chip) {
		chip.addEventListener('click', function (event) {
			if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) { return; }
			event.preventDefault();
			var slug = chip.getAttribute('data-gg-ff-cat');
			if (slug === category) { return; }
			setCategory(slug, chip.firstChild.textContent.trim());
			if (window.history && window.history.replaceState) {
				window.history.replaceState(null, '', chip.href);
			}
			restart();
		});
	});
	var initial = chips.filter(function (c) { return c.getAttribute('data-gg-ff-cat') === category; })[0];
	if (initial) { setCategory(category, initial.firstChild.textContent.trim()); }

	/* ---------- start: swap the no-JS grid for the stack ---------- */
	var fallback = document.querySelector('[data-gg-ff-fallback]');
	if (fallback) { fallback.hidden = true; }
	root.hidden = false;
	renderSaved();
	render();
	ensureQueue().then(render);
	loadWishlist();

	/* Show the server wishlist, first moving over any saves kept only in this browser. */
	function loadWishlist() {
		if (!wishlist) { return; }
		var legacy = load(LEGACY_SAVED_KEY).filter(function (f) { return f && typeof f.slug === 'string'; });
		var moves = legacy.map(function (f) { return wishlist.add(f.slug).catch(function () {}); });
		Promise.all(moves)
			.then(function () {
				if (legacy.length) {
					try { window.localStorage.removeItem(LEGACY_SAVED_KEY); } catch (e) { /* ignore */ }
				}
				return wishlist.list();
			})
			.then(function (data) {
				// API lists newest first; the panel renders newest first from the end.
				saved = (data.items || []).slice().reverse();
				renderSaved();
			})
			.catch(function () { /* panel stays as is */ });
	}
})();
