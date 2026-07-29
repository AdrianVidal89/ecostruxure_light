/* Shared "view large, without leaving the tool" popup for uploaded files.
 * PDFs render in an <iframe>, images (incl. SVG) in an <img>; anything else
 * falls back to a "Download instead" message. Modal shell lives once in
 * base.html (#file-preview-modal) via components/file_preview_modal.html. */
(function () {
  var IMAGE_EXTS = ['svg', 'png', 'jpg', 'jpeg', 'gif', 'webp'];

  function modal() { return document.getElementById('file-preview-modal'); }
  function body() { return document.getElementById('file-preview-body'); }

  window.openFilePreview = function (url, label) {
    var m = modal();
    var b = body();
    var download = document.getElementById('file-preview-download');
    var title = document.getElementById('file-preview-title');
    if (!m || !b) return;

    var clean = url.split('?')[0].split('#')[0];
    var ext = (clean.split('.').pop() || '').toLowerCase();

    title.textContent = label || '';
    download.setAttribute('href', url);
    b.innerHTML = '';

    if (ext === 'pdf') {
      var sep = url.indexOf('?') === -1 ? '?' : '&';
      var iframe = document.createElement('iframe');
      iframe.src = url + sep + 'inline=1';
      iframe.className = 'w-full h-[75vh] rounded-lg border border-line';
      b.appendChild(iframe);
    } else if (IMAGE_EXTS.indexOf(ext) !== -1) {
      var img = document.createElement('img');
      img.src = url;
      img.className = 'max-w-full max-h-[75vh] mx-auto rounded-lg border border-line';
      b.appendChild(img);
    } else {
      var p = document.createElement('p');
      p.className = 'text-sm text-ink-muted';
      p.textContent = "Preview isn't available for this file type — use Download instead.";
      b.appendChild(p);
    }

    m.classList.remove('hidden');
  };

  window.closeFilePreview = function () {
    var m = modal();
    var b = body();
    if (m) m.classList.add('hidden');
    // Clear the body so a loading PDF/image doesn't keep fetching in the background.
    if (b) b.innerHTML = '';
  };

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') window.closeFilePreview();
  });
})();
