# Воспроизведение проверки шести форм

Команды выполняются из корня репозитория. Нужна точная beta.3 из данного опыта
или её замороженные документы в изолированной копии: prepare читает текущий навык.
Повторный опыт использует новые каталоги и отдельный разрешённый бюджет.
`--execute` вызывает модель через сеть. Начатые попытки не повторяются.

```bash
python3 -m unittest discover -s tests -v
python3 tools/validate_package.py
git diff --check

python3 tools/run_ab.py prepare \
  --dest .local/product-six-forms-2026-09-07-run \
  --dataset evals/product-six-forms-2026-09-07/cases.json \
  --protocol evals/product-six-forms-2026-09-07/PROTOCOL.md \
  --rubric evals/reliability-next/JUDGE.md
python3 tools/run_ab.py run \
  --run-dir .local/product-six-forms-2026-09-07-run --execute
python3 tools/run_ab.py blind \
  --run-dir .local/product-six-forms-2026-09-07-run \
  --dest .local/product-six-forms-2026-09-07-blind
python3 tools/review_ab.py prepare \
  --packet .local/product-six-forms-2026-09-07-blind/packet.json \
  --rubric .local/product-six-forms-2026-09-07-run/snapshots/judge.txt \
  --dest .local/product-six-forms-2026-09-07-judges
python3 tools/review_ab.py run \
  --dest .local/product-six-forms-2026-09-07-judges --execute
python3 tools/summarize_ab.py \
  --run-dir .local/product-six-forms-2026-09-07-run \
  --judges-dir .local/product-six-forms-2026-09-07-judges \
  --dest evals/product-six-forms-2026-09-07/results
```

Параметры `--protocol` и `--rubric` у A/B prepare новые; без них сохраняется
старое поведение. Данные и документы замораживаются до первого ответа. Сохранены
все 24 авторские попытки и до 4 отзывов. Ограничения и критерии — в
[протоколе](PROTOCOL.md). Это полная загрузка методических документов, не тест
нативного хоста и не исполнение диагностического Python внутри автора.

Читательский пакет должен содержать только запросы и точные варианты A/B,
без mapping, модельных оценок или источников с внутренними редакционными notes.
Его передача людям и возврат реальных ответов выполняются владельцем отдельно.
До этого человеческих оценок нет. Источники доступны разработчику для фактчека,
но читательская оценка касается восприятия, не удостоверяет достоверность.

## Перепроверить сохранённую post-hoc диагностику

Этот дополнительный API-прогон не входит в авторскую генерацию. Исходные
подробные `kind` новых фикстур не соответствуют трём типам checker; явное
сопоставление и адаптированные пакеты сохранены в `CHECKER.json`. Не изменяйте
задним числом frozen dataset. Следующая команда не вызывает модели и ничего
не записывает; она сверяет точные сохранённые диагностические результаты:

```bash
python3 - <<'PY'
import importlib.util, json
from pathlib import Path
d = Path('evals/product-six-forms-2026-09-07')
recorded = json.loads((d / 'CHECKER.json').read_text(encoding='utf-8'))
cases = {c['id']: c for c in json.loads((d / 'cases.json').read_text(encoding='utf-8'))['cases']}
spec = importlib.util.spec_from_file_location('checker', 'skills/source-and-voice/scripts/editorial_check.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
for row in recorded['records']:
    case = cases[row['case_id']]
    path = d / 'results/outputs' / f"{row['case_id']}-r{row['repeat']}-{row['arm']}.txt"
    actual = checker.analyze(path.read_text(encoding='utf-8'),
        sources=recorded['adapted_source_packets'][row['case_id']],
        min_words=case['min_words'], max_words=case['max_words'])
    assert actual == row['diagnostic']
print('24 recorded diagnostics reproduced; semantic review remains NOT_REVIEWED')
PY
```
