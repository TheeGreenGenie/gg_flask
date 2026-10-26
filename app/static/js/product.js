/* Product gallery and option picker, used on the product page and inside the quick-view
   preview. Exposes window.GGProduct.init(root) so site.js can set up a freshly loaded preview. */
(function () {
	'use strict';

	function init(root) {
		if (!root || root.dataset.ggProductReady) { return; }
		root.dataset.ggProductReady = '1';

		var main = root.querySelector('[data-gg-gallery-main]');
		var thumbs = Array.prototype.slice.call(root.querySelectorAll('[data-gg-gallery-thumb]'));

		function showImage(src) {
			if (!main || !src) { return; }
			main.src = src;
			thumbs.forEach(function (t) {
				t.setAttribute('aria-current', t.getAttribute('href') === src ? 'true' : 'false');
			});
		}

		/* Thumbnails link to the full image without JS; with JS they swap the main image. */
		thumbs.forEach(function (thumb) {
			thumb.addEventListener('click', function (event) {
				event.preventDefault();
				showImage(thumb.getAttribute('href'));
			});
		});

		/* Option picker updates photo, label, price, SKU, stock message and quantity limit. */
		var label = root.querySelector('[data-gg-variant-label]');
		var price = root.querySelector('[data-gg-price]');
		var sku = root.querySelector('[data-gg-sku]');
		var stock = root.querySelector('[data-gg-stock]');
		var qty = root.querySelector('[data-gg-qty-input]');
		var add = root.querySelector('.gg-buy__add');

		root.querySelectorAll('input[name="variant"][type="radio"]').forEach(function (radio) {
			radio.addEventListener('change', function () {
				var n = parseInt(radio.dataset.stock, 10) || 0;
				if (radio.dataset.image) { showImage(radio.dataset.image); }
				if (label) { label.textContent = radio.dataset.label; }
				if (sku && radio.dataset.sku) { sku.textContent = radio.dataset.sku; }
				if (price && radio.dataset.price) {
					price.textContent = '';
					if (radio.dataset.compare) {
						var del = document.createElement('del');
						del.textContent = radio.dataset.compare;
						var ins = document.createElement('ins');
						ins.textContent = radio.dataset.price;
						price.append(del, ' ', ins);
					} else {
						price.append(radio.dataset.price);
					}
				}
				if (stock) {
					stock.textContent = n <= 0 ? 'Out of stock' : (n <= 3 ? 'Only ' + n + ' left in stock' : 'In stock');
					stock.className = 'gg-stock' + (n <= 0 ? ' gg-stock--out' : (n <= 3 ? ' gg-stock--low' : ''));
				}
				if (qty) {
					qty.max = String(Math.max(1, Math.min(n, 99)));
					if (parseInt(qty.value, 10) > n) { qty.value = String(Math.max(1, n)); }
				}
				if (add) { add.disabled = n <= 0; }
			});
		});

		/* Related products: arrow buttons scroll the track (it also scrolls by touch). */
		var carousel = root.querySelector('[data-gg-carousel]');
		if (carousel) {
			var track = carousel.querySelector('[data-gg-carousel-track]');
			var controls = carousel.querySelector('[data-gg-carousel-controls]');
			if (track && controls && track.scrollWidth > track.clientWidth + 4) {
				controls.hidden = false;
				carousel.querySelector('[data-gg-carousel-prev]').addEventListener('click', function () {
					track.scrollBy({ left: -track.clientWidth, behavior: 'smooth' });
				});
				carousel.querySelector('[data-gg-carousel-next]').addEventListener('click', function () {
					track.scrollBy({ left: track.clientWidth, behavior: 'smooth' });
				});
			}
		}
	}

	window.GGProduct = { init: init };

	/* Product page: init the page and its phone sticky buy bar. */
	var page = document.querySelector('.gg-product[data-gg-product]');
	if (!page) { return; }
	init(page);
	var bar = document.querySelector('[data-gg-sticky-buy]');
	var add = page.querySelector('.gg-buy__add');
	if (bar && add && 'IntersectionObserver' in window) {
		new IntersectionObserver(function (entries) {
			var e = entries[0];
			bar.hidden = e.isIntersecting || e.boundingClientRect.top > 0;
		}).observe(add);
	}
})();
