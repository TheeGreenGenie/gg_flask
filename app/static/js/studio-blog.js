/* Staff Studio blog editor: the "Show products in the article" picker.
   [data-gg-shop-picker] holds a search box; results insert a line such as
   "[product: wax-print-blue]" into the article body at the cursor, on its own line. */
(function () {
	'use strict';
	var picker = document.querySelector('[data-gg-shop-picker]');
	var body = document.querySelector('textarea[name="body"]');
	if (!picker || !body) { return; }
	var input = picker.querySelector('input[type="search"]');
	var list = picker.querySelector('[data-gg-shop-results]');
	var status = picker.querySelector('[data-gg-shop-status]');
	var timer = null;
	var labels = { product: 'Product', category: 'Category', collection: 'Collection' };

	function insert(line) {
		var start = body.selectionStart || body.value.length;
		var before = body.value.slice(0, start).replace(/\s*$/, '');
		var after = body.value.slice(start).replace(/^\s*/, '');
		var text = (before ? before + '\n\n' : '') + line + (after ? '\n\n' + after : '\n');
		body.value = text;
		var caret = (before ? before.length + 2 : 0) + line.length;
		body.focus();
		body.setSelectionRange(caret, caret);
		status.textContent = 'Added ' + line + ' to the article. Save to keep it.';
	}

	function render(items) {
		list.textContent = '';
		if (!items.length) { status.textContent = 'Nothing matches. Try another word.'; return; }
		status.textContent = items.length + ' found. Tap one to add it.';
		items.forEach(function (item) {
			var li = document.createElement('li');
			var button = document.createElement('button');
			button.type = 'button';
			button.className = 'gg-studio__pick-button';
			if (item.image) {
				var img = document.createElement('img');
				img.src = item.image; img.alt = ''; img.width = 48; img.height = 48; img.loading = 'lazy';
				button.appendChild(img);
			}
			var words = document.createElement('span');
			var name = document.createElement('strong');
			name.textContent = item.name;
			var kind = document.createElement('small');
			kind.textContent = labels[item.kind] + (item.note ? ' · ' + item.note : '');
			words.appendChild(name); words.appendChild(kind);
			button.appendChild(words);
			button.addEventListener('click', function () { insert('[' + item.kind + ': ' + item.slug + ']'); });
			li.appendChild(button);
			list.appendChild(li);
		});
	}

	function search() {
		var q = input.value.trim();
		if (q.length < 2) { list.textContent = ''; status.textContent = 'Type at least 2 letters.'; return; }
		status.textContent = 'Searching…';
		fetch(picker.getAttribute('data-url') + '?q=' + encodeURIComponent(q), {
			credentials: 'same-origin', headers: { Accept: 'application/json' }
		}).then(function (r) { return r.json(); })
			.then(function (data) { render(data.items || []); })
			.catch(function () { status.textContent = 'Search failed. Please try again.'; });
	}

	input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(search, 250); });
	input.addEventListener('keydown', function (event) {
		if (event.key === 'Enter') { event.preventDefault(); clearTimeout(timer); search(); }
	});
}());
