# Воспроизведение проверки Astra

Команды из корня репозитория. Для повторного опыта выбрать новые каталоги;
старые файлы и markers не удалять. `--execute` расходует лимиты/бюджет модели.
Не запускать повторно начатую попытку после обрыва. Подробности ограничений —
в [протоколе](PROTOCOL.md). `--model` — новый параметр только тестовой процедуры;
исторический default остаётся Sol, прежние prompts и manifests воспроизводимы.

```bash
python3 -m unittest discover -s tests -v
python3 tools/validate_package.py
git diff --check

python3 tools/run_revision.py edit-prepare \
  --dest .local/editor-astra-2026-09-07-run \
  --dataset evals/reliability-next/regression.json \
  --protocol evals/editor-astra-2026-09-07/PROTOCOL.md \
  --rubric evals/reliability-next/JUDGE.md \
  --editor evals/reliability-source-first-2026-09-07/EDITOR.md \
  --model gpt-6-astra
python3 tools/run_revision.py edit-run \
  --run-dir .local/editor-astra-2026-09-07-run --execute
python3 tools/run_revision.py edit-blind \
  --run-dir .local/editor-astra-2026-09-07-run \
  --dest .local/editor-astra-2026-09-07-blind
python3 tools/review_ab.py prepare \
  --packet .local/editor-astra-2026-09-07-blind/packet.json \
  --rubric .local/editor-astra-2026-09-07-run/snapshots/judge.txt \
  --dest .local/editor-astra-2026-09-07-judges
python3 tools/review_ab.py run \
  --dest .local/editor-astra-2026-09-07-judges --execute
python3 tools/run_revision.py edit-export \
  --run-dir .local/editor-astra-2026-09-07-run \
  --judges-dir .local/editor-astra-2026-09-07-judges \
  --dest evals/editor-astra-2026-09-07/results
```

12 редактур + до 4 отзывов, без повторов. Результаты включают точные исходники,
финалы, замороженные инструкции и оригинальные отзывы; raw logs остаются локальными.
Экспорт не присваивает редакторский PASS: источники и критерии проверяются отдельно.
Рабочий скилл, установка и публикация этими командами не меняются.
