/* Homepage enhancement, ported from the WordPress child theme (assets/js/gg-homepage.js).
   Header offset measurement moved to site.js. */
(function () {
	'use strict';

	var home = document.querySelector('[data-gg-home]');
	if (!home) {
		return;
	}

	var root = document.documentElement;
	var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

	function activateTab(group, buttons, panels, nextIndex, moveFocus) {
		buttons.forEach(function (button, index) {
			var selected = index === nextIndex;
			button.setAttribute('aria-selected', selected ? 'true' : 'false');
			button.setAttribute('tabindex', selected ? '0' : '-1');
			if (panels[index]) {
				panels[index].hidden = !selected;
			}
		});
		group.dataset.ggActiveTab = String(nextIndex);
		if (moveFocus && buttons[nextIndex]) {
			buttons[nextIndex].focus();
		}
	}

	function initTabs() {
		var groups = Array.prototype.slice.call(home.querySelectorAll('[data-gg-tabs]'));
		var enhancedGroups = 0;

		groups.forEach(function (group, groupIndex) {
			var list = group.querySelector('[data-gg-tabs-list]');
			var buttons = list ? Array.prototype.slice.call(list.querySelectorAll('[data-gg-tab-target]')) : [];
			var panels = Array.prototype.slice.call(group.querySelectorAll(':scope > [data-gg-tab-panel]'));

			if (!list || !buttons.length || buttons.length !== panels.length) {
				return;
			}

			list.hidden = false;
			list.setAttribute('role', 'tablist');
			list.setAttribute('aria-label', list.dataset.ggTabsLabel || 'Choose a view');

			buttons.forEach(function (button, buttonIndex) {
				var panelId = button.dataset.ggTabTarget;
				var panel = document.getElementById(panelId);
				var buttonId = 'gg-home-tab-' + groupIndex + '-' + buttonIndex;

				if (!panel || panels.indexOf(panel) === -1) {
					return;
				}

				button.id = buttonId;
				button.setAttribute('role', 'tab');
				button.setAttribute('aria-controls', panelId);
				panel.setAttribute('role', 'tabpanel');
				panel.setAttribute('aria-labelledby', buttonId);

				button.addEventListener('click', function () {
					activateTab(group, buttons, panels, buttonIndex, false);
				});

				button.addEventListener('keydown', function (event) {
					var currentIndex = buttons.indexOf(event.currentTarget);
					var nextIndex = currentIndex;

					switch (event.key) {
						case 'ArrowRight':
						case 'ArrowDown':
							nextIndex = (currentIndex + 1) % buttons.length;
							break;
						case 'ArrowLeft':
						case 'ArrowUp':
							nextIndex = (currentIndex - 1 + buttons.length) % buttons.length;
							break;
						case 'Home':
							nextIndex = 0;
							break;
						case 'End':
							nextIndex = buttons.length - 1;
							break;
						default:
							return;
					}

					event.preventDefault();
					activateTab(group, buttons, panels, nextIndex, true);
				});
			});

			activateTab(group, buttons, panels, 0, false);
			enhancedGroups += 1;
		});

		if (enhancedGroups) {
			home.classList.add('gg-home--tabs-ready');
		}
	}

	function setCurrentSection(sectionId) {
		home.querySelectorAll('[data-gg-section-link]').forEach(function (link) {
			if (link.dataset.ggSectionLink === sectionId) {
				link.setAttribute('aria-current', 'location');
			} else {
				link.removeAttribute('aria-current');
			}
		});
	}

	function initSectionNavigation() {
		var sections = Array.prototype.slice.call(home.querySelectorAll('[data-gg-section]'));
		var links = Array.prototype.slice.call(home.querySelectorAll('[data-gg-section-link]'));

		if (!sections.length || !links.length || !('IntersectionObserver' in window)) {
			return false;
		}

		links.forEach(function (link) {
			link.addEventListener('click', function () {
				setCurrentSection(link.dataset.ggSectionLink);
			});
		});

		var visibility = new Map();
		var observer = new IntersectionObserver(
			function (entries) {
				entries.forEach(function (entry) {
					visibility.set(entry.target.id, entry.isIntersecting ? entry.intersectionRatio : 0);
				});

				var currentId = '';
				var currentRatio = 0;
				sections.forEach(function (section) {
					var ratio = visibility.get(section.id) || 0;
					if (ratio > currentRatio) {
						currentRatio = ratio;
						currentId = section.id;
					}
				});

				if (currentId) {
					setCurrentSection(currentId);
				}
			},
			{ root: null, rootMargin: '-20% 0px -55% 0px', threshold: [0, 0.1, 0.25, 0.5, 0.75] }
		);

		sections.forEach(function (section) {
			observer.observe(section);
		});

		// The dots belong to the slides: step aside once the footer scrolls into view.
		var nav = home.querySelector('.gg-home-nav');
		var footer = document.querySelector('.gg-footer');
		if (nav && footer) {
			new IntersectionObserver(function (entries) {
				nav.classList.toggle('gg-home-nav--away', entries[entries.length - 1].isIntersecting);
			}).observe(footer);
		}
		return true;
	}

	function initVideoFacades() {
		home.querySelectorAll('[data-gg-video]').forEach(function (facade) {
			facade.addEventListener(
				'click',
				function () {
					var videoId = facade.dataset.ggVideoId;
					if (!videoId || !/^[A-Za-z0-9_-]{6,20}$/.test(videoId)) {
						return;
					}
					var frame = document.createElement('iframe');
					frame.src = 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(videoId) + '?autoplay=1&rel=0';
					frame.title = 'GG Afrikan Eleganz brand story';
					frame.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
					frame.allowFullscreen = true;
					frame.referrerPolicy = 'strict-origin-when-cross-origin';
					facade.replaceWith(frame);
					frame.focus();
				},
				{ once: true }
			);
		});
	}

	/* Hero image slider: crossfade every 6s; pausable; no autoplay with reduced motion. */
	function initSlider() {
		var stage = home.querySelector('[data-gg-slider]');
		var controls = home.querySelector('[data-gg-slider-controls]');
		if (!stage || !controls) {
			return;
		}
		var slides = Array.prototype.slice.call(stage.querySelectorAll('[data-gg-slide]'));
		var dots = Array.prototype.slice.call(controls.querySelectorAll('[data-gg-slider-dot]'));
		var pause = controls.querySelector('[data-gg-slider-pause]');
		var pauseLabel = controls.querySelector('[data-gg-slider-pause-label]');
		if (slides.length < 2) {
			return;
		}
		var current = 0;
		var timer = null;
		var paused = reducedMotion.matches;

		slides.forEach(function (img) { img.loading = 'eager'; });

		function show(index) {
			current = (index + slides.length) % slides.length;
			slides.forEach(function (img, i) { img.classList.toggle('is-active', i === current); });
			dots.forEach(function (dot, i) {
				if (i === current) { dot.setAttribute('aria-current', 'true'); } else { dot.removeAttribute('aria-current'); }
			});
		}

		function schedule() {
			window.clearInterval(timer);
			timer = paused ? null : window.setInterval(function () { show(current + 1); }, 6000);
			pause.setAttribute('aria-pressed', paused ? 'true' : 'false');
			pauseLabel.textContent = paused ? 'Play slideshow' : 'Pause slideshow';
		}

		dots.forEach(function (dot, i) {
			dot.addEventListener('click', function () { show(i); schedule(); });
		});
		pause.addEventListener('click', function () { paused = !paused; schedule(); });
		document.addEventListener('visibilitychange', function () {
			if (document.hidden) { window.clearInterval(timer); } else { schedule(); }
		});

		controls.hidden = false;
		schedule();
	}

	/* Explore cards: product photos slide through one after another (Find Fabrics newest to
	   oldest, Wishlist most expensive first). Only while the card is on screen; never with
	   reduced motion. Cards are staggered so they don't all move at once. */
	function initCycles() {
		if (reducedMotion.matches || !('IntersectionObserver' in window)) {
			return;
		}
		var INTERVAL_MS = 3400;
		home.querySelectorAll('[data-gg-cycle]').forEach(function (stage, stageIndex) {
			var items = Array.prototype.slice.call(stage.querySelectorAll('[data-gg-cycle-item]'));
			if (items.length < 2) {
				return;
			}
			var current = 0;
			var timer = null;
			var started = false;

			function jump(img, fn) {
				img.style.transition = 'none';
				fn();
				void img.offsetWidth; // apply the position before re-enabling the transition
				img.style.transition = '';
			}

			function step() {
				var leaving = items[current];
				current = (current + 1) % items.length;
				var next = items[current];
				jump(next, function () { next.classList.remove('is-leaving'); });
				next.classList.add('is-active');
				leaving.classList.remove('is-active');
				leaving.classList.add('is-leaving');
				window.setTimeout(function () {
					if (!leaving.classList.contains('is-active')) {
						jump(leaving, function () { leaving.classList.remove('is-leaving'); });
					}
				}, 1200);
			}

			function start() {
				if (timer) { return; }
				if (!started) {
					started = true;
					items.forEach(function (img) { img.loading = 'eager'; });
					timer = window.setTimeout(function () {
						timer = window.setInterval(step, INTERVAL_MS);
						step();
					}, 900 + stageIndex * 1100);
					return;
				}
				timer = window.setInterval(step, INTERVAL_MS);
			}

			function stop() {
				window.clearTimeout(timer);
				window.clearInterval(timer);
				timer = null;
			}

			new IntersectionObserver(function (entries) {
				var visible = entries[entries.length - 1].isIntersecting;
				if (visible && !document.hidden) { start(); } else { stop(); }
			}, { threshold: 0.35 }).observe(stage);
			document.addEventListener('visibilitychange', function () {
				if (document.hidden) { stop(); }
			});
		});
	}

	/* Section snapping (owner request): after 1 s with no scrolling, glide smoothly to the
	   nearest section edge so the page never rests between two screens. Any input cancels
	   the glide. Tall sections can be read freely; they only snap near their edges. */
	function initSnap() {
		if (reducedMotion.matches) {
			return;
		}
		var sections = Array.prototype.slice.call(home.querySelectorAll('[data-gg-section]'));
		if (sections.length < 2) {
			return;
		}
		var IDLE_MS = 1000;
		var idleTimer = null;
		var frame = null;
		var gliding = false;

		function headerOffset() {
			return parseFloat(getComputedStyle(root).getPropertyValue('--gg-header-offset')) || 0;
		}

		function target() {
			var y = window.scrollY;
			var header = headerOffset();
			var viewport = window.innerHeight - header;
			var points = [];
			var lastBottom = 0;
			for (var i = 0; i < sections.length; i += 1) {
				var top = sections[i].getBoundingClientRect().top + y - header;
				var height = sections[i].offsetHeight;
				var bottomAligned = top + height - viewport;
				lastBottom = top + height;
				if (height > viewport + 2) {
					// Reading inside a tall section: leave the reader alone.
					if (y > top + 2 && y < bottomAligned - 2) {
						return null;
					}
					points.push(top, bottomAligned);
				} else {
					points.push(top);
				}
			}
			if (y > lastBottom - viewport / 2) {
				return null; // in the footer
			}
			var best = null;
			points.forEach(function (point) {
				var p = Math.max(0, Math.round(point));
				if (best === null || Math.abs(p - y) < Math.abs(best - y)) {
					best = p;
				}
			});
			return best !== null && Math.abs(best - y) > 2 ? best : null;
		}

		function ease(t) {
			return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; // easeInOutCubic
		}

		function cancelGlide() {
			if (frame) {
				window.cancelAnimationFrame(frame);
				frame = null;
			}
			gliding = false;
		}

		function glideTo(destination) {
			var start = window.scrollY;
			var distance = destination - start;
			var duration = Math.min(1200, Math.max(700, Math.abs(distance) * 1.4));
			var began = null;
			gliding = true;
			function step(now) {
				if (began === null) {
					began = now;
				}
				var t = Math.min(1, (now - began) / duration);
				window.scrollTo({ top: start + distance * ease(t), behavior: 'instant' });
				if (t < 1 && gliding) {
					frame = window.requestAnimationFrame(step);
				} else {
					cancelGlide();
				}
			}
			frame = window.requestAnimationFrame(step);
		}

		function settle() {
			idleTimer = null;
			if (document.querySelector('dialog[open]') || document.activeElement.closest('form')) {
				return;
			}
			var destination = target();
			if (destination !== null) {
				glideTo(destination);
			}
		}

		function activity() {
			cancelGlide();
			window.clearTimeout(idleTimer);
			idleTimer = window.setTimeout(settle, IDLE_MS);
		}

		window.addEventListener('scroll', function () {
			if (!gliding) {
				window.clearTimeout(idleTimer);
				idleTimer = window.setTimeout(settle, IDLE_MS);
			}
		}, { passive: true });
		['wheel', 'touchstart', 'touchmove', 'keydown', 'pointerdown'].forEach(function (type) {
			window.addEventListener(type, activity, { passive: true });
		});
		reducedMotion.addEventListener('change', cancelGlide);
	}

	try {
		initSnap();
		initSlider();
		initCycles();
		initTabs();
		initVideoFacades();
		if (initSectionNavigation()) {
			root.classList.add('gg-home-enhanced');
		}
		root.classList.toggle('gg-home-reduced-motion', reducedMotion.matches);
		if (reducedMotion.addEventListener) {
			reducedMotion.addEventListener('change', function () {
				root.classList.toggle('gg-home-reduced-motion', reducedMotion.matches);
			});
		}
	} catch (error) {
		root.classList.remove('gg-home-enhanced');
		if (window.console && typeof window.console.error === 'function') {
			window.console.error('GG homepage enhancement failed safely.', error);
		}
	}
})();
