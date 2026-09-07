# Воспроизведение source-first регрессии

Этот запуск использует неизменный тестовый код из `e15ad4e`. Новая инструкция
не устанавливается в агент. Выполняйте команды из корня репозитория; для нового
опыта выберите другие каталоги, согласуйте бюджет и используйте только разрешённые
материалы. `--execute` обращается к модели через сеть и расходует средства/лимиты.

## Проверка без модельных обращений

```bash
python3 -m unittest discover -s tests -v
python3 tools/validate_package.py
git diff --check
```

Для выполненного опыта отдельно наблюдались Python 3.14.6 и Codex CLI 0.153.4.
Per-attempt records не содержат автоматически измеренной CLI-версии. Тестовый
код стандартной библиотеки рассчитан на Python 3.10+; другие среды здесь заново
не проверялись. Авторизацию настраивайте отдельно, ключи в материалы не помещайте.

## Зафиксировать входы и выполнить 12 редактур

```bash
python3 tools/run_revision.py edit-prepare \
  --dest .local/editing-source-first-2026-09-07-run \
  --dataset evals/reliability-next/regression.json \
  --protocol evals/reliability-source-first-2026-09-07/PROTOCOL.md \
  --rubric evals/reliability-next/JUDGE.md \
  --editor evals/reliability-source-first-2026-09-07/EDITOR.md

python3 tools/run_revision.py edit-run \
  --run-dir .local/editing-source-first-2026-09-07-run --execute
```

Исходники берутся из прежнего dataset, а не из `*-edited.txt`. Начатые попытки
не повторяются. Не удаляйте markers и не меняйте замороженные файлы. Сбой —
не проверенный исходник. Старые и новые IDs совпадают из-за фиксированного seed:
различайте записи по каталогу **и manifest SHA**, не объединяйте только по ID.

## Слепое сравнение: до четырёх рецензий

```bash
python3 tools/run_revision.py edit-blind \
  --run-dir .local/editing-source-first-2026-09-07-run \
  --dest .local/editing-source-first-2026-09-07-blind

python3 tools/review_ab.py prepare \
  --packet .local/editing-source-first-2026-09-07-blind/packet.json \
  --rubric .local/editing-source-first-2026-09-07-run/snapshots/judge.txt \
  --dest .local/editing-source-first-2026-09-07-judges

python3 tools/review_ab.py run \
  --dest .local/editing-source-first-2026-09-07-judges --execute
```

## Экспорт точных ответов

```bash
python3 tools/run_revision.py edit-export \
  --run-dir .local/editing-source-first-2026-09-07-run \
  --judges-dir .local/editing-source-first-2026-09-07-judges \
  --dest evals/reliability-source-first-2026-09-07/results
```

Экспорт сохраняет сбои и разногласия, проверяет исходник/финал и сырые события;
существующий каталог не перезаписывается. `quality_gate: not-adjudicated` не
равен успеху. Итог требует чтения всех окончательных текстов по источникам и
критериям [протокола](PROTOCOL.md), отдельно от сохранённых судейских вердиктов.

Подробности отказов и ограничений неизменного runner — в
[руководстве первого опыта](../reliability-next/RUNNING.md). Новый запуск
стохастичен; эти открытые задания больше не являются новой приёмочной выборкой.
Сырые логи в `.local/` не предназначены для публичной выкладки. Ни одна команда
не публикует репозиторий и не изменяет рабочий профиль Hermes.
