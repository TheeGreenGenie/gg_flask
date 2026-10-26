/* Site-wide progressive enhancement. Every feature here has a working no-JS fallback. */
(function () {
	'use strict';

	var root = document.documentElement;
	root.classList.add('gg-js');

	function storageGet(key) {
		try { return window.localStorage.getItem(key); } catch (e) { return null; }
	}

	function storageSet(key, value) {
		try { window.localStorage.setItem(key, value); } catch (e) { /* storage blocked: ignore */ }
	}

	/* Sticky header: expose its height for scroll offsets and add a shadow once scrolled. */
	function initHeader() {
		var header = document.querySelector('[data-gg-header]');
		if (!header) { return; }

		function measure() {
			root.style.setProperty('--gg-header-offset', Math.round(header.getBoundingClientRect().height) + 'px');
		}
		measure();
		if ('ResizeObserver' in window) {
			new ResizeObserver(measure).observe(header);
		} else {
			window.addEventListener('resize', measure, { passive: true });
		}

		var ticking = false;
		window.addEventListener('scroll', function () {
			if (ticking) { return; }
			ticking = true;
			window.requestAnimationFrame(function () {
				header.classList.toggle('is-stuck', header.getBoundingClientRect().top <= 0 && window.scrollY > 0);
				ticking = false;
			});
		}, { passive: true });
	}

	/* <details> dropdowns: one open at a time, close on outside click or Escape. */
	function initDropdowns() {
		var dropdowns = Array.prototype.slice.call(document.querySelectorAll('details[data-gg-dropdown]'));
		if (!dropdowns.length) { return; }

		dropdowns.forEach(function (dd) {
			dd.addEventListener('toggle', function () {
				if (!dd.open) { return; }
				dropdowns.forEach(function (other) { if (other !== dd) { other.open = false; } });
				var field = dd.querySelector('input[type="search"]');
				if (field) { field.focus(); }
			});
		});

		document.addEventListener('click', function (event) {
			dropdowns.forEach(function (dd) {
				if (dd.open && !dd.contains(event.target)) { dd.open = false; }
			});
		});

		document.addEventListener('keydown', function (event) {
			if (event.key !== 'Escape') { return; }
			dropdowns.forEach(function (dd) {
				if (dd.open) {
					dd.open = false;
					dd.querySelector('summary').focus();
				}
			});
		});
	}

	/* Modal dialogs: close button, backdrop click. Escape is native. */
	function wireDialog(dialog, onClose) {
		dialog.querySelectorAll('[data-gg-modal-close], [data-gg-drawer-close]').forEach(function (btn) {
			btn.addEventListener('click', function () { dialog.close(); });
		});
		dialog.addEventListener('click', function (event) {
			if (event.target === dialog) { dialog.close(); }
		});
		if (onClose) { dialog.addEventListener('close', onClose); }
	}

	/* Mobile menu drawer. Without JS the menu button jumps to the footer navigation. */
	function initDrawer() {
		var drawer = document.getElementById('gg-drawer');
		if (!drawer || typeof drawer.showModal !== 'function') { return; }
		var opener = null;

		wireDialog(drawer, function () { if (opener) { opener.focus(); } });

		document.querySelectorAll('[data-gg-drawer-open]').forEach(function (btn) {
			btn.setAttribute('role', 'button');
			btn.addEventListener('click', function (event) {
				event.preventDefault();
				opener = btn;
				drawer.showModal();
			});
		});

		// Close the drawer if the viewport grows to desktop size.
		window.matchMedia('(min-width: 64rem)').addEventListener('change', function (mq) {
			if (mq.matches && drawer.open) { drawer.close(); }
		});
	}

	/* Newsletter pop-up: once per visitor, after a short delay, never over an open dialog. */
	function initNewsletter() {
		var dialog = document.querySelector('[data-gg-newsletter]');
		var KEY = 'gg-newsletter-seen';
		if (!dialog || typeof dialog.showModal !== 'function' || storageGet(KEY)) { return; }

		wireDialog(dialog, function () { storageSet(KEY, '1'); });
		dialog.querySelector('form').addEventListener('submit', function () { storageSet(KEY, '1'); });

		window.setTimeout(function () {
			if (document.querySelector('dialog[open]') || storageGet(KEY)) { return; }
			dialog.showModal();
		}, 12000);
	}

	/* Quantity steppers around number inputs. */
	function initQuantity() {
		document.querySelectorAll('[data-gg-qty]').forEach(function (wrap) {
			var input = wrap.querySelector('input');
			wrap.querySelectorAll('[data-gg-qty-step]').forEach(function (btn) {
				btn.addEventListener('click', function () {
					var step = parseInt(btn.getAttribute('data-gg-qty-step'), 10);
					var min = input.min === '' ? 0 : parseInt(input.min, 10);
					var max = input.max === '' ? 999 : parseInt(input.max, 10);
					var next = Math.min(max, Math.max(min, (parseInt(input.value, 10) || 0) + step));
					input.value = String(next);
					input.dispatchEvent(new Event('change', { bubbles: true }));
				});
			});
		});
	}

	/* Sort dropdowns submit on change; the Sort button stays for no-JS. */
	function initAutosubmit() {
		document.querySelectorAll('form[data-gg-autosubmit] select, [data-gg-autosubmit-field]').forEach(function (field) {
			field.addEventListener('change', function () { field.form.submit(); });
		});
	}

	/* Filters start collapsed on phones (they stay open without JS). */
	function initFilters() {
		var filters = document.querySelector('[data-gg-filters]');
		if (filters && window.matchMedia('(max-width: 63.9375rem)').matches) { filters.open = false; }
	}

	/* Accessible tabs; panels stay as stacked sections without JS. The homepage has its own copy. */
	function initTabs() {
		document.querySelectorAll('[data-gg-tabs]').forEach(function (group, groupIndex) {
			if (group.closest('[data-gg-home]')) { return; }
			var list = group.querySelector('[data-gg-tabs-list]');
			var buttons = list ? Array.prototype.slice.call(list.querySelectorAll('[data-gg-tab-target]')) : [];
			var panels = buttons.map(function (b) { return document.getElementById(b.getAttribute('data-gg-tab-target')); });
			if (!buttons.length || panels.indexOf(null) !== -1) { return; }

			function select(index, focus) {
				buttons.forEach(function (b, i) {
					b.setAttribute('aria-selected', i === index ? 'true' : 'false');
					b.tabIndex = i === index ? 0 : -1;
					panels[i].hidden = i !== index;
				});
				if (focus) { buttons[index].focus(); }
			}

			list.hidden = false;
			list.setAttribute('role', 'tablist');
			list.setAttribute('aria-label', list.getAttribute('data-gg-tabs-label') || 'Choose a view');
			buttons.forEach(function (b, i) {
				b.id = 'gg-tab-' + groupIndex + '-' + i;
				b.setAttribute('role', 'tab');
				b.setAttribute('aria-controls', panels[i].id);
				panels[i].setAttribute('role', 'tabpanel');
				panels[i].setAttribute('aria-labelledby', b.id);
				b.addEventListener('click', function () { select(i, false); });
				b.addEventListener('keydown', function (event) {
					var keys = { ArrowRight: 1, ArrowLeft: -1 };
					if (event.key in keys) {
						event.preventDefault();
						select((i + keys[event.key] + buttons.length) % buttons.length, true);
					} else if (event.key === 'Home' || event.key === 'End') {
						event.preventDefault();
						select(event.key === 'Home' ? 0 : buttons.length - 1, true);
					}
				});
			});
			group.classList.add('gg-tabs--ready');
			select(0, false);
		});
	}

	[initHeader, initDropdowns, initDrawer, initNewsletter, initQuantity, initAutosubmit, initFilters, initTabs].forEach(function (fn) {
		try { fn(); } catch (error) {
			if (window.console) { window.console.error('GG enhancement failed safely:', fn.name, error); }
		}
	});
})();
