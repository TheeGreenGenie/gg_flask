/* Product page: gallery, colorway picker, related carousel, sticky phone buy bar. */
(function () {
	'use strict';

	var page = document.querySelector('[data-gg-product]');
	if (!page) { return; }

	var main = page.querySelector('[data-gg-gallery-main]');
	var thumbs = Array.prototype.slice.call(page.querySelectorAll('[data-gg-gallery-thumb]'));

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

	/* Colorway picker updates the photo, label, stock message and quantity limit. */
	var label = page.querySelector('[data-gg-variant-label]');
	var stock = page.querySelector('[data-gg-stock]');
	var qty = page.querySelector('#qty');
	var add = page.querySelector('.gg-buy__add');

	page.querySelectorAll('input[name="variant"]').forEach(function (radio) {
		radio.addEventListener('change', function () {
			var n = parseInt(radio.dataset.stock, 10) || 0;
			showImage(radio.dataset.image);
			if (label) { label.textContent = radio.dataset.label; }
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
	var carousel = page.querySelector('[data-gg-carousel]');
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

	/* Phones: show a sticky bar once the Add to cart button scrolls out of view. */
	var bar = document.querySelector('[data-gg-sticky-buy]');
	if (bar && add && 'IntersectionObserver' in window) {
		new IntersectionObserver(function (entries) {
			var e = entries[0];
			bar.hidden = e.isIntersecting || e.boundingClientRect.top > 0;
		}).observe(add);
	}
})();
