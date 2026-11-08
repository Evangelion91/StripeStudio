"""Sequential Cartesian-product rendering; no previews or background service required."""
import json
import math
import threading
import time
from pathlib import Path

PHOTO_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.bmp', '.tif', '.tiff'}
VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.mkv', '.webm', '.avi'}


def media_info(engine, path):
    result = engine.command([engine.binary('ffprobe'), '-v', 'error', '-show_format', '-show_streams',
                             '-show_chapters', '-of', 'json', str(path)], 60)
    if result.returncode:
        raise ValueError('Не удалось проверить результат.')
    return json.loads(result.stdout)


def verify_clean(engine, path):
    info = media_info(engine, path)
    if info.get('chapters') or set(info['format'].get('tags', {})) - {'major_brand', 'minor_version', 'compatible_brands'}:
        raise ValueError('В результате остались метаданные.')
    for stream in info['streams']:
        tags = stream.get('tags', {})
        if set(tags) - {'language', 'handler_name', 'vendor_id'}:
            raise ValueError('В дорожке остались метаданные.')
        if tags.get('language', 'und') != 'und' or tags.get('handler_name', '') not in ('', 'VideoHandler', 'SoundHandler'):
            raise ValueError('В дорожке остались пользовательские данные.')
    return info


def clean_metadata(engine, source, target):
    args = [engine.binary('ffmpeg'), '-v', 'error', '-nostdin', '-n', '-i', str(source),
            '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-map_metadata', '-1', '-map_metadata:s', '-1',
            '-map_chapters', '-1', '-fflags', '+bitexact', '-flags:v', '+bitexact', '-flags:a', '+bitexact',
            '-metadata', 'encoder=', '-metadata:s:v:0', 'encoder=', '-metadata:s:a:0', 'encoder=',
            '-metadata:s:v:0', 'handler_name=', '-metadata:s:a:0', 'handler_name=',
            '-bsf:v', 'filter_units=remove_types=6', '-movflags', '+faststart', str(target)]
    result = engine.command(args, 300)
    if result.returncode:
        raise ValueError('Не удалось очистить метаданные результата.')
    return verify_clean(engine, target)


def split_plan(frames, fps, maximum):
    # Strictly less than maximum, including exact integer-second boundaries.
    capacity = math.ceil(maximum * fps) - 1
    if capacity < 1:
        raise ValueError('Максимальная часть должна быть длиннее одного кадра.')
    count = math.ceil(frames / capacity)
    bounds = [round(i * frames / count) for i in range(count + 1)]
    return [(bounds[i], bounds[i + 1] - bounds[i]) for i in range(count)]


def collect(folder, extensions):
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir():
        raise ValueError('Ожидается папка с исходниками.')
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions),
                  key=lambda p: (p.name.casefold(), p.name))


def public_batch(state):
    return {k: state[k] for k in ('id', 'status', 'completed', 'total', 'files', 'error')}


def start_batch(engine, payload):
    photos = collect(payload['photoDir'], PHOTO_EXT)
    videos = collect(payload['videoDir'], VIDEO_EXT)
    if not photos or not videos:
        raise ValueError('Нужны хотя бы одно фото и одно видео.')
    split = payload.get('split', False)
    if not isinstance(split, bool):
        raise ValueError('split должен быть boolean.')
    threshold = float(payload.get('splitThreshold', 15))
    maximum = float(payload.get('maxPartSeconds', 10))
    if not math.isfinite(threshold) or not math.isfinite(maximum) or threshold <= 0 or maximum <= 0:
        raise ValueError('Параметры нарезки должны быть положительными конечными числами.')
    out = Path(payload['outputDir']).resolve()
    if out in {p.parent for p in photos + videos}:
        raise ValueError('Папка результата должна отличаться от папок исходников.')
    template = payload.get('template', {})
    if not isinstance(template, dict):
        raise ValueError('template должен быть объектом.')
    fps = engine.validate_settings(engine.settings_from_template(template), {'duration': 1})['fps']
    if split:
        split_plan(1, fps, maximum)
    # Refuse pair collisions before starting any render (including previous split parts).
    for i in range(1, len(photos) + 1):
        for j in range(1, len(videos) + 1):
            if (out / f'{i}_{j}.mp4').exists() or list(out.glob(f'{i}_{j}_part*.mp4')):
                raise ValueError(f'Результат пары {i}_{j} уже существует.')
    out.mkdir(parents=True, exist_ok=True)
    ident = engine.secrets.token_hex(12)
    state = {'id': ident, 'status': 'running', 'completed': 0, 'total': len(photos)*len(videos),
             'files': [], 'error': None, 'cancel': threading.Event(), 'current': None}
    with engine.LOCK:
        if any(s['status'] == 'running' for s in engine.BATCHES.values()) or any(j['status'] == 'running' for j in engine.JOBS.values()):
            raise ValueError('Дождитесь завершения текущего рендера или пачки.')
        engine.BATCHES[ident] = state
    threading.Thread(target=run_batch, args=(engine, state, photos, videos, out, dict(template), split, threshold, maximum), daemon=True).start()
    return state


def cancel_batch(engine, state):
    state['cancel'].set()
    if state['current'] is not None:
        engine.cancel_job(state['current'])


def run_batch(engine, state, photos, videos, out, template, split, threshold, maximum):
    folder = engine.DATA / ('batch-' + state['id'])
    manifest = {'photos': {str(i):p.name for i,p in enumerate(photos,1)},
                'videos': {str(i):p.name for i,p in enumerate(videos,1)}, 'pairs': []}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        video_records = [engine.prepare_media(p, 'video', preview_video=False) for p in videos]
        for i, photo in enumerate(photos, 1):
            photo_record = engine.prepare_media(photo, 'photo')
            for j, video_record in enumerate(video_records, 1):
                if state['cancel'].is_set():
                    raise InterruptedError('Пачка отменена.')
                resolved = engine.resolve_template(template)
                cfg = engine.validate_settings(engine.settings_from_template(resolved), video_record)
                frames = max(1, math.ceil(cfg['duration'] * cfg['fps'] - 1e-6))
                plan = split_plan(frames, cfg['fps'], maximum) if split and cfg['duration'] > threshold else [(0, frames)]
                staged = []
                names = []
                for part, (offset, length) in enumerate(plan, 1):
                    if state['cancel'].is_set():
                        raise InterruptedError('Пачка отменена.')
                    name = f'{i}_{j}_part{part:02d}.mp4' if len(plan) > 1 else f'{i}_{j}.mp4'
                    target = folder / name
                    segment = {**resolved, 'start': cfg['start'] + offset / cfg['fps'],
                               'stripeTime': cfg['stripeTime'] + offset / cfg['fps'],
                               'duration': min(length / cfg['fps'], cfg['duration'] - offset / cfg['fps'])}
                    job = engine.start_render({'photo': photo_record['id'], 'video': video_record['id'],
                                               'template': segment, '_batch': state['id']}, target)
                    state['current'] = job
                    while job['status'] == 'running':
                        if state['cancel'].is_set():
                            engine.cancel_job(job)
                        time.sleep(.1)
                    state['current'] = None
                    if job['status'] != 'done':
                        raise InterruptedError('Пачка отменена.') if state['cancel'].is_set() else RuntimeError(job.get('error'))
                    info = verify_clean(engine, target)
                    actual = float(info['format']['duration'])
                    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
                    if len(plan) > 1 and (actual >= maximum or abs(int(v['nb_frames']) - length) > 0):
                        raise ValueError('Части не прошли проверку длины или количества кадров.')
                    staged.append(target)
                    names.append(name)
                if state['cancel'].is_set():
                    raise InterruptedError('Пачка отменена.')
                for source, name in zip(staged, names):
                    # Exclusive create prevents overwriting files added during the render.
                    with (out / name).open('xb') as dest, source.open('rb') as src:
                        engine.shutil.copyfileobj(src, dest)
                    source.unlink()
                manifest['pairs'].append({'photo': i, 'video': j, 'angle': resolved['angle'], 'files': names})
                (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
                state['files'].extend(names)
                state['completed'] += 1
        state['status'] = 'done'
    except InterruptedError:
        state['status'] = 'cancelled'
    except Exception as exc:
        state['error'] = str(exc)
        state['status'] = 'error'
