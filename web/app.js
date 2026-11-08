(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const token = document.querySelector('meta[name="template-token"]').content;
  const MIN_DURATION = .1;
  const DEFAULTS = { schema: 'stripe-studio/v1', angle: -26, period: 34, share: 42, irregularity: 0, seed: 1234, videoOpacity: .80, randomAngle: true, format: '9:16', resolution: 1080, fps: 30, start: 0, duration: 0, audio: true, photo: { zoom: 1, x: .5, y: .5, mirror: false }, video: { zoom: 1, x: .5, y: .5, mirror: false } };
  let settings = structuredClone(DEFAULTS);
  let photoSource = null, videoSource = null, photoImage = null, videoElement = null;
  let cropKind = 'photo', uploading = false, rendering = false, activeJob = null, pollTimer = null, pollFailures = 0;
  let previewMuted = true, playing = false, desiredPlaying = true, dirty = true, maskPath = null, maskKey = '', photoKey = '', playerIconState = null;
  const canvas = $('preview'), context = canvas.getContext('2d', { alpha: false });
  const photoCanvas = document.createElement('canvas'), photoContext = photoCanvas.getContext('2d', { alpha: false });
  const videoCanvas = document.createElement('canvas'), videoContext = videoCanvas.getContext('2d', { alpha: false });
  const sources = () => ({ photo: photoSource, video: videoSource });
  const clamp = (n, min, max) => Math.min(max, Math.max(min, n));
  const number = (value, fallback, min, max) => typeof value === 'number' && Number.isFinite(value) ? clamp(value, min, max) : fallback;
  const clone = (value) => structuredClone(value);

  function sanitizeSettings(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw) || raw.schema !== DEFAULTS.schema) throw new Error('Это не шаблон Stripe Studio. Нужен JSON с версией stripe-studio/v1.');
    const crop = (kind) => {
      const item = raw[kind] && typeof raw[kind] === 'object' ? raw[kind] : {};
      return { zoom: number(item.zoom, 1, 1, 3), x: number(item.x, .5, 0, 1), y: number(item.y, .5, 0, 1), mirror: typeof item.mirror === 'boolean' ? item.mirror : false };
    };
    const duration = number(raw.duration, 0, 0, 86400);
    return { schema: DEFAULTS.schema, angle: number(raw.angle, DEFAULTS.angle, -80, 80), period: number(raw.period, DEFAULTS.period, 8, 90), share: number(raw.share, DEFAULTS.share, 10, 90), irregularity: number(raw.irregularity, DEFAULTS.irregularity, 0, 100), seed: Math.round(number(raw.seed, DEFAULTS.seed, 0, 2147483647)), videoOpacity: number(raw.videoOpacity, DEFAULTS.videoOpacity, 0, 1), randomAngle: typeof raw.randomAngle === 'boolean' ? raw.randomAngle : DEFAULTS.randomAngle, format: ['9:16', '1:1', '16:9', '4:5'].includes(raw.format) ? raw.format : '9:16', resolution: [720, 1080, 2160].includes(raw.resolution) ? raw.resolution : 1080, fps: [24, 25, 30, 60].includes(raw.fps) ? raw.fps : 30, start: number(raw.start, 0, 0, 86400), duration: duration > 0 ? Math.max(MIN_DURATION, duration) : 0, audio: typeof raw.audio === 'boolean' ? raw.audio : true, photo: crop('photo'), video: crop('video') };
  }

  function randomAngle() {
    const value = new Uint32Array(1); crypto.getRandomValues(value);
    return -80 + Math.floor(value[0] / 4294967296 * 161);
  }

  function dimensions(preset = settings) {
    const r = preset.resolution;
    if (preset.format === '16:9') return { width: Math.round(r * 16 / 9 / 2) * 2, height: r };
    if (preset.format === '1:1') return { width: r, height: r };
    if (preset.format === '4:5') return { width: r, height: Math.round(r * 5 / 4 / 2) * 2 };
    return { width: r, height: Math.round(r * 16 / 9 / 2) * 2 };
  }

  function bandHash(band, seed, channel) {
    let n = (Math.imul(band | 0, 374761393) + Math.imul(seed | 0, 668265263) + Math.imul(channel, 2246822519)) | 0;
    n = Math.imul(n ^ (n >>> 13), 1274126177);
    n ^= n >>> 16;
    return (n >>> 0) / 4294967295;
  }

  function createMaskPath(width, height, preset = settings) {
    const path = new Path2D();
    const p = preset.period * Math.min(width, height) / 1080, length = Math.hypot(width, height) * .7;
    const irregularity = preset.irregularity / 100, white = p * preset.share / 100, gap = p - white;
    const jitter = Math.min(p * .25 * irregularity, gap * .28, white * .28);
    const widthVariation = Math.min(white * .3, gap * .18) * irregularity;
    const angle = preset.angle * Math.PI / 180, cosine = Math.cos(angle), sine = Math.sin(angle);
    const point = (u, v) => [width / 2 + u * cosine - v * sine, height / 2 + u * sine + v * cosine];
    for (let i = Math.floor(-length / p) - 2; i <= Math.ceil(length / p) + 2; i++) {
      const left = i * p + (bandHash(i, preset.seed, 1) * 2 - 1) * jitter;
      const right = left;
      const leftWidth = white + (bandHash(i, preset.seed, 3) * 2 - 1) * widthVariation;
      const rightWidth = leftWidth;
      const points = [point(-length, left), point(length, right), point(length, right + rightWidth), point(-length, left + leftWidth)];
      path.moveTo(...points[0]);
      points.slice(1).forEach((item) => path.lineTo(...item));
      path.closePath();
    }
    return path;
  }

  function buildMaskCanvas(preset = settings) {
    const snapshot = sanitizeSettings(preset), size = dimensions(snapshot), mask = document.createElement('canvas');
    mask.width = size.width; mask.height = size.height;
    const ctx = mask.getContext('2d', { alpha: false });
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, size.width, size.height);
    ctx.fillStyle = '#fff'; ctx.fill(createMaskPath(size.width, size.height, snapshot));
    return mask;
  }

  function notify(message, error = false) {
    $('app-status').textContent = message;
    $('app-status').classList.toggle('error', error);
  }

  async function request(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.method && options.method !== 'GET') headers['X-Template-Token'] = token;
    let response;
    try { response = await fetch(url, { ...options, headers }); }
    catch { throw new Error('Нет связи с локальным сервером. Проверьте, что Stripe Studio запущена.'); }
    let body;
    try { body = await response.json(); }
    catch { throw new Error(`Сервер вернул неожиданный ответ (${response.status}).`); }
    if (!response.ok) throw new Error(body.error || `Ошибка сервера (${response.status}).`);
    return body;
  }

  function formatTime(seconds, precise = false) {
    if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
    const whole = Math.floor(seconds), minutes = Math.floor(whole / 60), rest = String(whole % 60).padStart(2, '0');
    return `${minutes}:${rest}${precise ? '.' + Math.floor(seconds % 1 * 10) : ''}`;
  }

  function sourceDuration() {
    const duration = videoElement && Number.isFinite(videoElement.duration) ? videoElement.duration : videoSource?.duration;
    return duration > 0 ? duration : 8;
  }

  function segment() {
    const total = sourceDuration(), start = clamp(settings.start, 0, Math.max(0, total - MIN_DURATION));
    const end = settings.duration > 0 ? Math.min(total, start + settings.duration) : total;
    return { start, end, duration: Math.max(MIN_DURATION, end - start) };
  }

  function setRangeFill(input) {
    const min = Number(input.min) || 0, max = Number(input.max) || 1;
    input.style.setProperty('--fill', `${clamp((Number(input.value) - min) / (max - min) * 100, 0, 100)}%`);
  }

  function updateCropControls() {
    const crop = settings[cropKind];
    document.querySelectorAll('[data-crop]').forEach((input) => {
      const key = input.dataset.crop;
      if (input.type === 'checkbox') input.checked = crop[key]; else input.value = crop[key];
      if (input.type === 'range') setRangeFill(input);
    });
    $('crop-zoom-value').textContent = `${Math.round(crop.zoom * 100)}%`;
    $('crop-x-value').textContent = `${Math.round(crop.x * 100)}%`;
    $('crop-y-value').textContent = `${Math.round(crop.y * 100)}%`;
    ['photo', 'video'].forEach((kind) => { $('crop-' + kind).classList.toggle('active', kind === cropKind); $('crop-' + kind).setAttribute('aria-pressed', String(kind === cropKind)); });
  }

  function syncControls() {
    document.querySelectorAll('[data-setting]').forEach((input) => {
      const value = settings[input.dataset.setting];
      if (input.type === 'checkbox') input.checked = value; else input.value = input.dataset.setting === 'videoOpacity' ? Math.round(value * 100) : value;
      if (input.type === 'range') setRangeFill(input);
    });
    $('angle-value').textContent = `${settings.angle < 0 ? '−' : ''}${Math.abs(settings.angle).toFixed(0)}°`;
    $('period-value').textContent = `${settings.period.toFixed(0)} px`;
    $('share-value').textContent = `${settings.share.toFixed(0)}%`;
    $('irregularity-value').textContent = `${settings.irregularity.toFixed(0)}%`;
    $('videoOpacity-value').textContent = `${Math.round(settings.videoOpacity * 100)}% / ${Math.round((1 - settings.videoOpacity) * 100)}%`;
    document.querySelectorAll('[data-format]').forEach((button) => {
      const selected = button.dataset.format === settings.format;
      button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected));
    });
    updateCropControls();
    const size = dimensions();
    $('stage-label').textContent = `${settings.format} · ${size.width} × ${size.height}`;
    $('output-dimensions').textContent = `${size.width} × ${size.height}`;
    const factor = Math.min(720 / size.width, 720 / size.height, 1);
    const w = Math.round(size.width * factor), h = Math.round(size.height * factor);
    if (canvas.width !== w || canvas.height !== h || photoCanvas.width !== w || photoCanvas.height !== h) {
      canvas.width = w; canvas.height = h;
      photoCanvas.width = videoCanvas.width = w; photoCanvas.height = videoCanvas.height = h;
      maskKey = ''; photoKey = '';
    }
    canvas.style.aspectRatio = `${size.width} / ${size.height}`;
    const part = segment();
    $('seek').max = part.duration;
    $('total-time').textContent = formatTime(part.duration);
    if (videoElement && (videoElement.currentTime < part.start || videoElement.currentTime > part.end)) videoElement.currentTime = part.start;
    dirty = true;
  }

  function updateAvailability() {
    const busy = uploading || rendering;
    document.querySelectorAll('[data-lock]').forEach((control) => { control.disabled = busy; });
    document.querySelectorAll('[data-file-label]').forEach((label) => { label.dataset.disabled = String(busy); label.setAttribute('aria-disabled', String(busy)); });
    const both = Boolean(photoSource?.id && videoSource?.id);
    $('render').disabled = busy || !both;
    $('export-hint').textContent = uploading ? 'Подготавливаем материал. Это может занять немного времени.' : rendering ? 'Настройки зафиксированы на время экспорта.' : both ? `Готово к экспорту · ${formatTime(segment().duration)} видео` : 'Добавьте фото и видео, чтобы сохранить результат.';
    const count = Number(Boolean(photoSource?.id)) + Number(Boolean(videoSource?.id));
    $('preview-mode').textContent = both ? 'Ваши материалы' : count ? `${count} из 2 материалов` : 'Демо';
    $('preview-caption').replaceChildren();
    const dot = document.createElement('span'); dot.textContent = '●'; dot.setAttribute('aria-hidden', 'true');
    $('preview-caption').append(dot, document.createTextNode(both ? 'Цветное фото + чёрно-белое видео. Один наклон на весь ролик.' : 'Демонстрация эффекта. Загрузите фото и видео для экспорта.'));
  }

  function invalidateExport() {
    $('download-block').hidden = true;
    $('download').removeAttribute('href');
    $('download').removeAttribute('download');
  }

  function updateSourceCard(kind) {
    const source = sources()[kind];
    $(kind + '-drop').classList.toggle('ready', Boolean(source?.id));
    $(kind + '-name').textContent = source?.name || `Демонстрационное ${kind === 'photo' ? 'фото' : 'видео'}`;
    $(kind + '-name').title = source?.name || '';
    $(kind + '-meta').textContent = source ? `${source.width} × ${source.height}${kind === 'video' ? ' · ' + formatTime(source.duration || sourceDuration()) + (source.hasAudio ? ' · со звуком' : ' · без звука') : ' · неподвижный кадр'}${source.id ? '' : ' · демо'}` : 'Демонстрация загружается…';
    const label = document.querySelector(`label[for="${kind}-file"]`);
    label.firstChild.textContent = source?.id ? 'Заменить ' + (kind === 'photo' ? 'фото' : 'видео') : 'Выбрать ' + (kind === 'photo' ? 'фото' : 'видео');
  }

  function cover(ctx, media, crop, width, height) {
    const sw = media.videoWidth || media.naturalWidth || media.width, sh = media.videoHeight || media.naturalHeight || media.height;
    if (!sw || !sh) return;
    const scale = Math.max(width / sw, height / sh) * crop.zoom;
    const cw = width / scale, ch = height / scale, sx = Math.max(0, sw - cw) * crop.x, sy = Math.max(0, sh - ch) * crop.y;
    ctx.save();
    if (crop.mirror) { ctx.translate(width, 0); ctx.scale(-1, 1); }
    ctx.drawImage(media, sx, sy, cw, ch, 0, 0, width, height);
    ctx.restore();
  }

  function drawPlaceholder(ctx, width, height) {
    const gradient = ctx.createLinearGradient(0, 0, width, height);
    gradient.addColorStop(0, '#57664a'); gradient.addColorStop(.5, '#9b987a'); gradient.addColorStop(1, '#303b2a');
    ctx.fillStyle = gradient; ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = '#d7c5a9'; ctx.beginPath(); ctx.ellipse(width * .48, height * .37, width * .2, height * .18, -.15, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#343a29'; ctx.beginPath(); ctx.ellipse(width * .55, height * .94, width * .43, height * .39, .15, 0, Math.PI * 2); ctx.fill();
  }

  function drawPreview() {
    const w = canvas.width, h = canvas.height;
    const newMaskKey = [w, h, settings.angle, settings.period, settings.share, settings.irregularity, settings.seed].join('|');
    if (newMaskKey !== maskKey) { maskPath = createMaskPath(w, h); maskKey = newMaskKey; }
    const newPhotoKey = [w, h, photoSource?.url, JSON.stringify(settings.photo)].join('|');
    if (newPhotoKey !== photoKey) {
      photoContext.fillStyle = '#242b20'; photoContext.fillRect(0, 0, w, h);
      if (photoImage) cover(photoContext, photoImage, settings.photo, w, h); else drawPlaceholder(photoContext, w, h);
      photoKey = newPhotoKey;
    }
    context.globalAlpha = 1; context.drawImage(photoCanvas, 0, 0);
    context.save(); context.clip(maskPath); context.globalAlpha = settings.videoOpacity;
    if (videoElement && videoElement.readyState >= 2) {
      videoContext.filter = 'grayscale(1)';
      videoContext.fillStyle = '#242424'; videoContext.fillRect(0, 0, w, h);
      cover(videoContext, videoElement, settings.video, w, h);
      videoContext.filter = 'none'; context.drawImage(videoCanvas, 0, 0);
    } else { context.filter = 'grayscale(1)'; context.drawImage(photoCanvas, 0, 0); context.filter = 'none'; }
    context.restore();
    dirty = false;
  }

  function updatePlayer() {
    const part = segment(), position = videoElement ? clamp(videoElement.currentTime - part.start, 0, part.duration) : 0;
    $('seek').value = position; setRangeFill($('seek'));
    $('current-time').textContent = formatTime(position);
    $('total-time').textContent = formatTime(part.duration);
    $('play-toggle').setAttribute('aria-label', playing ? 'Пауза' : 'Воспроизвести');
    $('play-toggle').title = playing ? 'Пауза' : 'Воспроизвести';
    if (playerIconState !== playing) {
      $('play-icon').innerHTML = playing ? '<path d="M9 5v14M15 5v14"/>' : '<path d="m9 5 10 7-10 7Z"/>';
      playerIconState = playing;
    }
  }

  let lastFrame = 0;
  function animate(timestamp) {
    if (timestamp - lastFrame >= 1000 / 30) {
      lastFrame = timestamp;
      if (videoElement && playing) {
        const part = segment();
        if (videoElement.currentTime >= part.end - .025 || videoElement.currentTime < part.start) videoElement.currentTime = part.start;
      }
      if (dirty || playing) drawPreview();
      if (playing) updatePlayer();
    }
    requestAnimationFrame(animate);
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image); image.onerror = () => reject(new Error('Не удалось открыть изображение. Выберите другой файл.')); image.src = url;
    });
  }

  function loadVideo(url) {
    return new Promise((resolve, reject) => {
      const video = document.createElement('video');
      video.playsInline = true; video.preload = 'auto'; video.muted = previewMuted;
      const fail = () => { video.remove(); reject(new Error('Не удалось открыть видео для предпросмотра. Выберите другой файл.')); };
      video.addEventListener('error', fail, { once: true });
      video.addEventListener('loadeddata', () => { video.removeEventListener('error', fail); resolve(video); }, { once: true });
      video.src = url; $('media-bin').append(video); video.load();
    });
  }

  function adoptVideo(video) {
    if (videoElement) { videoElement.pause(); videoElement.removeAttribute('src'); videoElement.load(); videoElement.remove(); }
    videoElement = video; video.muted = previewMuted;
    video.addEventListener('play', () => { playing = true; updatePlayer(); });
    video.addEventListener('pause', () => { playing = false; updatePlayer(); dirty = true; });
    video.addEventListener('seeked', () => { dirty = true; updatePlayer(); });
    video.addEventListener('ended', () => { if (desiredPlaying) { video.currentTime = segment().start; video.play().catch(() => {}); } });
    video.currentTime = segment().start;
    if (desiredPlaying && !rendering) video.play().catch(() => { playing = false; updatePlayer(); });
    dirty = true; updatePlayer();
  }

  async function upload(kind, file) {
    if (!file || uploading || rendering) return;
    const expected = kind === 'photo' ? 'image/' : 'video/';
    if (file.type && !file.type.startsWith(expected)) { $(kind + '-error').textContent = `Выберите ${kind === 'photo' ? 'изображение' : 'видеофайл'}.`; $(kind + '-error').hidden = false; return; }
    uploading = true; updateAvailability();
    $(kind + '-error').hidden = true; $(kind + '-drop').classList.add('uploading');
    $(kind + '-meta').textContent = 'Загружаем и подготавливаем…';
    notify(`Подготавливаем ${kind === 'photo' ? 'фото' : 'видео'} «${file.name}»…`);
    try {
      const data = await request(`/api/upload?kind=${kind}&name=${encodeURIComponent(file.name)}`, { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file });
      if (!data.id || !data.url) throw new Error('Сервер не вернул подготовленный материал. Попробуйте ещё раз.');
      if (kind === 'photo') { const image = await loadImage(data.url); photoImage = image; photoSource = { ...data, width: data.width || image.naturalWidth, height: data.height || image.naturalHeight }; photoKey = ''; }
      else { const video = await loadVideo(data.url); videoSource = { ...data, width: data.width || video.videoWidth, height: data.height || video.videoHeight, duration: data.duration || video.duration }; settings.start = 0; settings.duration = 0; adoptVideo(video); }
      invalidateExport(); syncControls(); notify(`${kind === 'photo' ? 'Фото' : 'Видео'} готово. ${photoSource?.id && videoSource?.id ? 'Можно экспортировать композицию.' : 'Добавьте второй материал.'}`);
    } catch (error) { $(kind + '-error').textContent = error.message; $(kind + '-error').hidden = false; notify(error.message, true); }
    finally { uploading = false; $(kind + '-drop').classList.remove('uploading'); updateSourceCard(kind); updateAvailability(); $(kind + '-file').value = ''; }
  }

  function applySettings(next) {
    settings = sanitizeSettings(next);
    if (videoSource?.id) {
      settings.start = Math.min(settings.start, Math.max(0, sourceDuration() - MIN_DURATION));
      if (settings.duration > 0) settings.duration = Math.min(settings.duration, sourceDuration() - settings.start);
    }
    invalidateExport(); syncControls(); updateAvailability(); updatePlayer();
  }

  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  function saveSettings() {
    saveBlob(new Blob([JSON.stringify(settings, null, 2) + '\n'], { type: 'application/json' }), 'stripe-studio-preset.json');
    notify('Шаблон сохранён. Материалы в JSON не включены.');
  }

  function finishJob(job) {
    clearTimeout(pollTimer); activeJob = null; rendering = false;
    $('cancel-render').disabled = false; $('cancel-render').textContent = 'Отменить'; $('retry-status').hidden = true;
    if (job.status === 'done') {
      $('render-message').textContent = 'Экспорт завершён'; $('render-percent').textContent = '100%'; $('render-progress').style.width = '100%';
      $('download').href = job.url; $('download').download = job.name || 'stripe-studio.mp4'; $('download-block').hidden = false;
      $('render-status').hidden = true; notify('Видео готово. Скачайте MP4 в панели справа.');
    } else if (job.status === 'cancelled') { $('render-status').hidden = true; notify('Экспорт отменён. Настройки сохранены.'); }
    else { $('render-status').hidden = true; $('render-error').textContent = job.error || 'Не удалось экспортировать видео. Попробуйте ещё раз.'; $('render-error').hidden = false; notify($('render-error').textContent, true); }
    updateAvailability();
  }

  async function pollJob() {
    if (!activeJob || !rendering) return;
    const id = activeJob;
    clearTimeout(pollTimer);
    try {
      const job = await request('/api/jobs/' + encodeURIComponent(id));
      if (activeJob !== id) return;
      pollFailures = 0; $('retry-status').hidden = true; $('render-error').hidden = true;
      if (['done', 'error', 'cancelled'].includes(job.status)) { finishJob(job); return; }
      const progress = clamp(Number(job.progress) || 0, 0, 100);
      $('render-message').textContent = $('cancel-render').disabled ? 'Останавливаем экспорт…' : 'Собираем видео…';
      $('render-percent').textContent = `${Math.floor(progress)}%`; $('render-progress').style.width = progress + '%';
      pollTimer = setTimeout(pollJob, 600);
    } catch (error) {
      if (activeJob !== id) return;
      pollFailures++;
      $('render-error').textContent = error.message + ' Настройки экспорта сохранены.'; $('render-error').hidden = false;
      if (pollFailures < 3) pollTimer = setTimeout(pollJob, 1800);
      else { $('retry-status').hidden = false; $('render-message').textContent = 'Ожидаем связь с сервером'; notify('Не удалось проверить экспорт. Нажмите «Проверить статус».', true); }
    }
  }

  async function render() {
    if (uploading || rendering || !photoSource?.id || !videoSource?.id) return;
    if (settings.randomAngle) { settings.angle = randomAngle(); syncControls(); drawPreview(); }
    const snapshot = { ...clone(settings), randomAngle: false }, size = dimensions(snapshot);
    rendering = true; pollFailures = 0; desiredPlaying = false; videoElement?.pause();
    updateAvailability(); $('render-error').hidden = true; $('download-block').hidden = true; $('render-status').hidden = false;
    $('render-message').textContent = 'Подготавливаем маску…'; $('render-percent').textContent = '0%'; $('render-progress').style.width = '0%'; $('cancel-render').disabled = true;
    notify(`Экспортируем MP4. Общий наклон полос: ${snapshot.angle}°. Настройки зафиксированы.`);
    try {
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const mask = buildMaskCanvas(snapshot).toDataURL('image/png').split(',')[1];
      const job = await request('/api/render', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ photo: photoSource.id, video: videoSource.id, template: snapshot, settings: { ...size, fps: snapshot.fps, start: snapshot.start, duration: snapshot.duration, audio: snapshot.audio, videoOpacity: snapshot.videoOpacity, photo: snapshot.photo, video: snapshot.video }, mask }) });
      if (!job.id) throw new Error('Сервер не вернул номер экспорта. Попробуйте ещё раз.');
      activeJob = job.id; $('cancel-render').disabled = false; $('render-message').textContent = 'Собираем видео…'; pollJob();
    } catch (error) { finishJob({ status: 'error', error: error.message }); }
  }

  document.querySelectorAll('[data-setting]').forEach((input) => {
    const event = input.tagName === 'SELECT' || input.type === 'checkbox' || input.type === 'number' ? 'change' : 'input';
    input.addEventListener(event, () => {
      if (uploading || rendering) return;
      const key = input.dataset.setting;
      settings[key] = input.type === 'checkbox' ? input.checked : key === 'format' ? input.value : Number(input.value) / (key === 'videoOpacity' ? 100 : 1);
      settings = sanitizeSettings(settings);
      if (key === 'start') settings.start = Math.min(settings.start, Math.max(0, sourceDuration() - MIN_DURATION));
      if ((key === 'start' || key === 'duration') && settings.duration > 0) settings.duration = Math.min(settings.duration, sourceDuration() - settings.start);
      invalidateExport(); syncControls(); updateAvailability(); updatePlayer();
    });
    if (input.type === 'number') input.addEventListener('change', () => { syncControls(); });
  });
  document.querySelectorAll('[data-crop]').forEach((input) => input.addEventListener(input.type === 'checkbox' ? 'change' : 'input', () => {
    if (uploading || rendering) return;
    settings[cropKind][input.dataset.crop] = input.type === 'checkbox' ? input.checked : Number(input.value);
    settings = sanitizeSettings(settings); invalidateExport(); updateCropControls(); dirty = true;
  }));
  document.querySelectorAll('[data-format]').forEach((button) => button.addEventListener('click', () => { settings.format = button.dataset.format; invalidateExport(); syncControls(); }));
  ['photo', 'video'].forEach((kind) => {
    $('crop-' + kind).addEventListener('click', () => { cropKind = kind; updateCropControls(); });
    $(kind + '-file').addEventListener('change', (event) => upload(kind, event.target.files[0]));
    const zone = $(kind + '-drop');
    ['dragenter', 'dragover'].forEach((name) => zone.addEventListener(name, (event) => { event.preventDefault(); if (!uploading && !rendering) zone.classList.add('drag-over'); }));
    ['dragleave', 'drop'].forEach((name) => zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove('drag-over'); }));
    zone.addEventListener('drop', (event) => upload(kind, event.dataTransfer.files[0]));
  });
  $('randomize').addEventListener('click', () => {
    settings.angle = randomAngle(); settings.irregularity = 0;
    invalidateExport(); syncControls(); notify(`Новый общий наклон: ${settings.angle}°. Шаг и ширина полос равномерные.`);
  });
  $('reference').addEventListener('click', () => { ['angle', 'period', 'share', 'irregularity', 'seed', 'videoOpacity', 'randomAngle'].forEach((key) => { settings[key] = DEFAULTS[key]; }); invalidateExport(); syncControls(); notify('Восстановлены сохранённые настройки эффекта. При экспорте будет выбран один случайный наклон.'); });
  $('play-toggle').addEventListener('click', async () => {
    if (!videoElement) return;
    if (playing) { desiredPlaying = false; videoElement.pause(); }
    else { desiredPlaying = true; if (videoElement.currentTime >= segment().end - MIN_DURATION) videoElement.currentTime = segment().start; try { await videoElement.play(); } catch { notify('Браузер не смог воспроизвести видео. Попробуйте загрузить файл ещё раз.', true); } }
  });
  $('reset-playback').addEventListener('click', () => { if (videoElement) videoElement.currentTime = segment().start; dirty = true; updatePlayer(); });
  $('seek').addEventListener('input', () => { if (videoElement) videoElement.currentTime = Math.min(segment().end - .02, segment().start + Number($('seek').value)); dirty = true; updatePlayer(); });
  $('mute-toggle').addEventListener('click', () => {
    previewMuted = !previewMuted; if (videoElement) videoElement.muted = previewMuted;
    $('mute-toggle').setAttribute('aria-label', previewMuted ? 'Включить звук предпросмотра' : 'Выключить звук предпросмотра'); $('mute-toggle').title = previewMuted ? 'Включить звук предпросмотра' : 'Выключить звук предпросмотра';
    $('mute-icon').innerHTML = previewMuted ? '<path d="m11 4-5 4H3v8h3l5 4ZM16 9l5 6M21 9l-5 6"/>' : '<path d="m11 4-5 4H3v8h3l5 4ZM16 8c3 2 3 6 0 8M19 5c5 4 5 10 0 14"/>';
  });
  $('save-settings').addEventListener('click', saveSettings);
  $('settings-file').addEventListener('change', async (event) => {
    const file = event.target.files[0]; if (!file || uploading || rendering) return;
    try { if (file.size > 1024 * 1024) throw new Error('Файл шаблона слишком большой. Выберите JSON настроек до 1 МБ.'); applySettings(JSON.parse(await file.text())); notify('Шаблон открыт. Ваши фото и видео сохранены в композиции.'); }
    catch (error) { notify(error instanceof SyntaxError ? 'Не удалось прочитать JSON. Выберите сохранённый шаблон Stripe Studio.' : error.message, true); }
    finally { event.target.value = ''; }
  });
  $('save-mask').addEventListener('click', () => {
    try { const size = dimensions(); buildMaskCanvas().toBlob((blob) => { if (!blob) { notify('Не удалось создать маску PNG.', true); return; } saveBlob(blob, `stripe-mask-${size.width}x${size.height}.png`); notify('Маска PNG сохранена в полном разрешении. Белое — видео, чёрное — фото.'); }, 'image/png'); }
    catch (error) { notify('Не удалось сохранить маску: ' + error.message, true); }
  });
  $('render').addEventListener('click', render);
  $('retry-status').addEventListener('click', () => { pollFailures = 0; $('retry-status').hidden = true; pollJob(); });
  $('cancel-render').addEventListener('click', async () => {
    if (!activeJob) return;
    $('cancel-render').disabled = true; $('cancel-render').textContent = 'Останавливаем…';
    try { await request(`/api/jobs/${encodeURIComponent(activeJob)}/cancel`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); pollJob(); }
    catch (error) { $('cancel-render').disabled = false; $('cancel-render').textContent = 'Отменить'; $('render-error').textContent = error.message; $('render-error').hidden = false; }
  });

  async function initialize() {
    $('format').tabIndex = -1; $('format').setAttribute('aria-hidden', 'true');
    if (settings.randomAngle) settings.angle = randomAngle();
    syncControls(); updateAvailability(); drawPreview(); requestAnimationFrame(animate);
    try {
      const info = await request('/api/info');
      $('app-version').textContent = 'STRIPE STUDIO ' + (info.version || '');
      const demoTasks = await Promise.allSettled([
        info.photoDemo ? loadImage(info.photoDemo).then((image) => { if (!photoSource?.id) { photoImage = image; photoSource = { name: 'Демонстрационное фото', url: info.photoDemo, width: image.naturalWidth, height: image.naturalHeight }; photoKey = ''; updateSourceCard('photo'); dirty = true; } }) : Promise.resolve(),
        info.videoDemo ? loadVideo(info.videoDemo).then((video) => { if (!videoSource?.id) { videoSource = { name: 'Демонстрационное видео', url: info.videoDemo, width: video.videoWidth, height: video.videoHeight, duration: video.duration, hasAudio: false }; adoptVideo(video); updateSourceCard('video'); } else { video.remove(); } }) : Promise.resolve()
      ]);
      if (demoTasks.some((result) => result.status === 'rejected')) notify('Демонстрация загружена не полностью. Вы можете выбрать свои материалы.', true);
      else notify('Готово. Выберите фото и видео или настройте рисунок на демо.');
      if (!info.ffmpeg) notify('FFmpeg не найден. Предпросмотр доступен, для экспорта установите FFmpeg.', true);
      syncControls(); updateAvailability();
    } catch (error) { notify(error.message, true); updateSourceCard('photo'); updateSourceCard('video'); }
  }

  window.StripeStudio = { getSettings: () => clone(settings), applySettings, buildMaskCanvas, createMaskPath, bandHash, dimensions, exportDimensions: dimensions, sanitizeSettings, getSources: () => clone(sources()), drawPreview };
  initialize();
})();
