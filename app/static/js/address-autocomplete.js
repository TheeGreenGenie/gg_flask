/* Address autocomplete for the checkout and address-book forms.
   Suggestions come from our own /api/v1/address-suggestions (the HERE key stays on the
   server). Like the old site: nothing is sent until 3 characters, 300 ms after typing stops,
   at most 5 suggestions. Every lookup costs money, so this also: remembers answers in the page,
   skips repeat searches, pauses after "slow down" (429) and switches itself off when the
   service is unavailable (503). Picking a suggestion fills street, city, state, postal code
   and country; the shopper can always ignore it and type. */
(function () {
	'use strict';

	var input = document.querySelector('[data-gg-address] input[name="address1"]');
	if (!input || !window.fetch) { return; }
	var form = input.form;
	var MIN_CHARS = 3;
	var DELAY = 300;
	var answers = {};
	var timer = null;
	var pending = null;
	var pausedUntil = 0;
	var off = false;
	var items = [];
	var active = -1;
	var lastAsked = '';

	var list = document.createElement('ul');
	list.className = 'gg-address-suggest';
	list.id = 'gg-address-suggest';
	list.setAttribute('role', 'listbox');
	list.setAttribute('aria-label', 'Address suggestions');
	list.hidden = true;
	input.parentNode.classList.add('gg-address-anchor');
	input.parentNode.appendChild(list);
	input.setAttribute('role', 'combobox');
	input.setAttribute('aria-autocomplete', 'list');
	input.setAttribute('aria-controls', list.id);
	input.setAttribute('aria-expanded', 'false');

	function field(name) { return form.elements.namedItem(name); }

	function close() {
		list.hidden = true;
		list.textContent = '';
		items = [];
		active = -1;
		input.setAttribute('aria-expanded', 'false');
		input.removeAttribute('aria-activedescendant');
	}

	function highlight(index) {
		var options = list.children;
		if (!options.length) { return; }
		active = (index + options.length) % options.length;
		for (var i = 0; i < options.length; i++) {
			options[i].setAttribute('aria-selected', i === active ? 'true' : 'false');
		}
		input.setAttribute('aria-activedescendant', options[active].id);
	}

	function set(name, value) {
		var el = field(name);
		if (!el || !value) { return; }
		if (el.tagName === 'SELECT') {
			var match = Array.prototype.some.call(el.options, function (o) { return o.value === value; });
			if (!match) { return; }
		}
		el.value = value;
		el.dispatchEvent(new Event('input', { bubbles: true }));
		el.dispatchEvent(new Event('change', { bubbles: true }));
	}

	function choose(index) {
		var item = items[index];
		if (!item) { return; }
		lastAsked = item.address1; // don't look up the address we just filled in
		input.value = item.address1;
		set('country', item.country);
		set('city', item.city);
		set('region', item.region);
		set('postcode', item.postcode);
		close();
		var next = field('address2');
		if (next && next.focus) { next.focus(); }
	}

	function show(found) {
		close();
		items = found || [];
		if (!items.length || document.activeElement !== input) { return; }
		items.forEach(function (item, i) {
			var option = document.createElement('li');
			option.id = 'gg-address-option-' + i;
			option.setAttribute('role', 'option');
			option.setAttribute('aria-selected', 'false');
			option.textContent = item.label;
			// mousedown, not click: runs before the input loses focus and closes the list
			option.addEventListener('mousedown', function (event) { event.preventDefault(); choose(i); });
			list.appendChild(option);
		});
		list.hidden = false;
		input.setAttribute('aria-expanded', 'true');
	}

	function lookup(query) {
		var country = field('country');
		var key = (country ? country.value : '') + '|' + query.toLowerCase();
		if (answers[key]) { show(answers[key]); return; }
		if (off || Date.now() < pausedUntil) { return; }
		if (pending) { pending.abort(); }
		pending = new AbortController();
		var url = '/api/v1/address-suggestions?q=' + encodeURIComponent(query) +
			'&country=' + encodeURIComponent(country ? country.value : '');
		fetch(url, { credentials: 'same-origin', signal: pending.signal, headers: { Accept: 'application/json' } })
			.then(function (response) {
				if (response.status === 429) { pausedUntil = Date.now() + 60000; return null; }
				if (response.status === 503 || response.status === 403) { off = true; return null; }
				return response.ok ? response.json() : null;
			})
			.then(function (data) {
				if (!data) { return; }
				answers[key] = data.items || [];
				show(answers[key]);
			})
			.catch(function () { /* aborted or offline: the shopper keeps typing */ });
	}

	input.addEventListener('input', function () {
		clearTimeout(timer);
		var query = input.value.trim();
		if (query.length < MIN_CHARS || query === lastAsked) { close(); return; }
		timer = setTimeout(function () { lastAsked = query; lookup(query); }, DELAY);
	});

	input.addEventListener('keydown', function (event) {
		if (list.hidden) { return; }
		if (event.key === 'ArrowDown') { event.preventDefault(); highlight(active + 1); }
		else if (event.key === 'ArrowUp') { event.preventDefault(); highlight(active - 1); }
		else if (event.key === 'Enter' && active >= 0) { event.preventDefault(); choose(active); }
		else if (event.key === 'Escape') { close(); }
	});

	input.addEventListener('blur', function () { setTimeout(close, 150); });
})();
