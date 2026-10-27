/* Staff Studio phone keys (WebAuthn passkeys).
   [data-gg-key-register]: create a key after the owner confirmed it (Studio > Sign-in key).
   [data-gg-key-approve]:  approve one's own sign-in on the waiting screen.
   [data-gg-password-swap]: verify tgg's key and issue a one-display temporary password.
   Each button carries data-options (GET, JSON options) and data-submit (POST the result). */
(function () {
	'use strict';
	var passwordClearTimer = null;

	function toBytes(value) {
		var base64 = value.replace(/-/g, '+').replace(/_/g, '/');
		var padded = base64 + '==='.slice((base64.length + 3) % 4);
		var raw = atob(padded);
		var out = new Uint8Array(raw.length);
		for (var i = 0; i < raw.length; i += 1) { out[i] = raw.charCodeAt(i); }
		return out.buffer;
	}

	function toBase64url(buffer) {
		var bytes = new Uint8Array(buffer);
		var text = '';
		for (var i = 0; i < bytes.length; i += 1) { text += String.fromCharCode(bytes[i]); }
		return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
	}

	function csrf() {
		var meta = document.querySelector('meta[name="csrf-token"]');
		return meta ? meta.getAttribute('content') : '';
	}

	function showError(message) {
		var box = document.querySelector('[data-gg-key-error]');
		if (box) { box.textContent = message; box.hidden = false; }
	}

	function getJSON(url) {
		return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
			.then(function (r) { return r.json().then(function (d) { if (!r.ok) { throw new Error(d.error || 'Please try again.'); } return d; }); });
	}

	function postJSON(url, body) {
		return fetch(url, {
			method: 'POST',
			credentials: 'same-origin',
			headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': csrf() },
			body: JSON.stringify(body)
		}).then(function (r) { return r.json().then(function (d) { if (!r.ok) { throw new Error(d.error || 'Please try again.'); } return d; }); });
	}

	function encodeCredential(cred) {
		var out = { id: cred.id, rawId: toBase64url(cred.rawId), type: cred.type, response: {}, clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {} };
		var r = cred.response;
		out.response.clientDataJSON = toBase64url(r.clientDataJSON);
		if (r.attestationObject) {
			out.response.attestationObject = toBase64url(r.attestationObject);
			if (r.getTransports) { out.response.transports = r.getTransports(); }
		} else {
			out.response.authenticatorData = toBase64url(r.authenticatorData);
			out.response.signature = toBase64url(r.signature);
			out.response.userHandle = r.userHandle ? toBase64url(r.userHandle) : null;
		}
		if (cred.authenticatorAttachment) { out.authenticatorAttachment = cred.authenticatorAttachment; }
		return out;
	}

	function run(button, create, bodyFactory, onDone) {
		if (!window.PublicKeyCredential) {
			showError('This browser cannot use sign-in keys. Use Safari on iPhone or Chrome on Android.');
			return;
		}
		button.disabled = true;
		getJSON(button.dataset.options).then(function (o) {
			o.challenge = toBytes(o.challenge);
			if (create) {
				o.user.id = toBytes(o.user.id);
				(o.excludeCredentials || []).forEach(function (c) { c.id = toBytes(c.id); });
				return navigator.credentials.create({ publicKey: o });
			}
			(o.allowCredentials || []).forEach(function (c) { c.id = toBytes(c.id); });
			return navigator.credentials.get({ publicKey: o });
		}).then(function (cred) {
			if (!cred) { throw new Error('No key was chosen.'); }
			var encoded = encodeCredential(cred);
			return postJSON(button.dataset.submit, bodyFactory ? bodyFactory(encoded) : encoded);
		}).then(function (result) {
			if (onDone) { onDone(result); } else { window.location.assign(result.next); }
		}).catch(function (error) {
			button.disabled = false;
			showError(error && error.name === 'NotAllowedError' ? 'The key request was cancelled or timed out. Try again.' : (error.message || 'Please try again.'));
		});
	}

	document.querySelectorAll('[data-gg-key-register]').forEach(function (button) {
		button.addEventListener('click', function () { run(button, true); });
	});
	document.querySelectorAll('[data-gg-key-approve]').forEach(function (button) {
		button.addEventListener('click', function () { run(button, false); });
	});
	document.querySelectorAll('[data-gg-password-swap]').forEach(function (button) {
		button.addEventListener('click', function () {
			run(button, false, function (credential) {
				var target = document.querySelector('[data-gg-password-target]');
				return { username: target ? target.value : '', credential: credential };
			}, function (result) {
				var panel = document.querySelector('[data-gg-password-result]');
				var username = document.querySelector('[data-gg-password-username]');
				var password = document.querySelector('[data-gg-temporary-password]');
				if (username) { username.textContent = result.username; }
				if (password) { password.textContent = result.temporary_password; }
				if (panel) { panel.hidden = false; panel.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
				if (passwordClearTimer) { window.clearTimeout(passwordClearTimer); }
				passwordClearTimer = window.setTimeout(function () {
					if (password) { password.textContent = ''; }
					if (panel) { panel.hidden = true; }
				}, 120000);
				button.disabled = false;
			});
		});
	});

	var copy = document.querySelector('[data-gg-copy-password]');
	if (copy) {
		copy.addEventListener('click', function () {
			var password = document.querySelector('[data-gg-temporary-password]');
			if (!password || !navigator.clipboard) { showError('Copy is unavailable in this browser.'); return; }
			navigator.clipboard.writeText(password.textContent || '').then(function () { copy.textContent = 'Copied'; });
		});
	}
	var share = document.querySelector('[data-gg-share-password]');
	if (share && !navigator.share) { share.hidden = true; }
	if (share && navigator.share) {
		share.addEventListener('click', function () {
			var username = document.querySelector('[data-gg-password-username]');
			var password = document.querySelector('[data-gg-temporary-password]');
			navigator.share({
				title: 'Temporary Staff Studio password',
				text: 'Username: ' + (username ? username.textContent : '') + '\nTemporary password: ' + (password ? password.textContent : '') + '\nChange it immediately after signing in.'
			}).catch(function (error) { if (error && error.name !== 'AbortError') { showError('The password could not be shared.'); } });
		});
	}
})();
