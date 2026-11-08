# StripeStudio

Цветное статичное фото + чёрно-белое видео в параллельных полосах → MP4. Windows, Python 3.10+ (рекомендуется 3.12), FFmpeg и FFprobe в PATH.

1. Скачайте/клонируйте репозиторий и запустите `powershell -File .\install.ps1`.
2. В новом терминале: `StripeStudio render --photo "photo.jpg" --video "video.mp4" --out "result.mp4"`.
3. GUI: ярлык **StripeStudio** или `StripeStudio serve`. CLI не требует фонового сервера.

По умолчанию: 80% видео + 20% фото внутри видеополос, фиксированные ширина и промежутки, один случайный общий угол на каждый экспорт. Для фиксированного угла: `--angle -26`.

**Для агента установки:** выполните [AGENTS.md](AGENTS.md). API: `StripeStudio serve --no-open`, документация по адресу `/api/docs` запущенного процесса.

Рендер автоматически использует NVIDIA NVENC, если он доступен; иначе CPU. Неподвижный слой рассчитывается один раз. Принудительный режим: `--encoder cpu` или `--encoder nvenc`. Скорость зависит от GPU, исходников и разрешения.

Пачка «каждое фото × каждое видео» (JPG/PNG/HEIC и др.):
```powershell
StripeStudio batch --photos "D:\photos" --videos "D:\videos" --out "D:\result" --split --split-threshold 15 --max-part-seconds 10
```
Без `--split` нарезка выключена. При включении режутся только результаты длиннее `--split-threshold`; каждая часть строго короче `--max-part-seconds`. Части примерно равны, без пропусков кадров; длинная версия не сохраняется. Имена `1_1.mp4` или `1_1_part01.mp4`, индексы с 1 по имени файла. Существующие результаты не перезаписываются. Метаданные очищаются всегда (кроме необходимых технических полей MP4). CLI печатает JSON-прогресс. Сопоставление индексов и углов: `user-data/batch-ID/manifest.json`.

API (с токеном сервера): `POST /api/batches` с JSON `{"photoDir":"D:/photos","videoDir":"D:/videos","outputDir":"D:/result","split":true,"splitThreshold":15,"maxPartSeconds":10,"template":{"encoder":"auto"}}`. Статус: `GET /api/batches/ID`, отмена: `POST /api/batches/ID/cancel` с `{}`. Параметры эффекта/разрешения задаются через `--preset` или `template`. Уже готовые пары сохраняются при отмене; исходники не удаляются.
