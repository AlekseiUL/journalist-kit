# Финальный аудит Journalist Kit v2

Статус: HOLD

## Результат

Утверждённый exact-one этап завершён и проверен, но candidate не прошёл все пять locked gates. Заявлять пользу, сливать ветку в `main` или выпускать её нельзя.

## Границы и состояние

- GitHub API подтверждает private branch `improve/source-first-v0.2-candidate` на evidence commit `d103f59435230e6e8597987eb57ebc5610dcca51`.
- GitHub API подтверждает, что `main` остался на `96658be7eb9e2aeb2a3bfc494377ea1c5debcbf1`; локальные refs совпадают.
- CI run `34163614392` для evidence commit завершён со статусом `success`.
- Pre-call evidence зафиксирован до первого вызова. Author manifest: `9da745eea56b782ead0d4ad84c3112ddad9e38f7d77726689b9bfa0168a4cad5`; reviewer manifest: `370eefc8b9cf16f2f5b76579081b620634d76435da8aad7378c5be2748f3ee38`.
- Frozen cases, protocol snapshot, rubric, prompts, mapping, runner state, candidate skill и обязательные references совпали с зафиксированными SHA-256. Export seal охватывает точный набор publishable results; hashes raw stdout/stderr и финальных outputs совпали с terminal records.

## Exact-one выполнение

- Author: 24 запланировано, 24 attempt markers, 24 уникальных terminal records, 24 completed, 0 failures/timeouts.
- Reviewers: 4 запланировано, 4 attempt markers, 4 уникальных terminal records, 4 completed, 0 failures/timeouts.
- Во всех 28 terminal records присутствует usage; каждый вызов имеет один started и один completed turn. Лишних attempt/record/raw файлов, retry, resume или replacement evidence не обнаружено.
- Все запланированные jobs сохранены в denominator; исключённых failures/timeouts нет.

## Пять locked gates

1. FAIL — требовалось 0 substantive distortions. В candidate pair `p04` добавлена конкретная сцена «в одном ящике морозилки», которой нет в единственном source: он говорит только о семи подписанных контейнерах в морозилке.
2. PASS WITH CAVEAT — candidate не хуже baseline по числу substantive distortions. Это сравнительный gate, не подтверждение точности: в обеих arms есть source-overreach, а baseline дополнительно меняет условие `от 18 лет` и приписывает оговорку организаторам.
3. FAIL — при пороге не менее 10/12 для каждого blinded reviewer candidate получил 2/12 у judge 1 и 3/12 у judge 2. Consensus: candidate 2, baseline 3, tie 5, disagreement 2.
4. FAIL — candidate input: 138 090 tokens, baseline: 107 720; рост 28,193465% при лимите 15%.
5. FAIL — private/restricted quote, fabricated search/interview и ложной publication-ready заявки не найдено, но подтверждённая выдуманная сцена в `p04` нарушает составной gate.

Источник истины отделён от reader preference: независимая source-fidelity проверка сравнила точный source с output и подтвердила дефект `p04`; расхождение модельных reviewers этот вывод не меняет. Freshness подтверждена только в границах репозитория: 6 новых cases, 0 exact ID/request/source overlaps против 55 prior cases в 8 datasets.

## Проверка и changed-files audit

- `python3 -m unittest discover -s tests -v`: 127/127 PASS.
- `python3 tools/validate_package.py`: PASS.
- `git diff --check`: PASS.
- Evidence commit относительно pre-execution `f8a7f552f3bcc669695cc0554ae5b6f2166b98df`: 38 files, +3142/-16.
- Полная candidate branch относительно untouched `main`: 52 files, +3350/-41.
- Candidate bytes после outputs не менялись. Этот terminal stage добавляет локально только данный `FINAL_AUDIT.md`; remote branch и candidate не изменены.

## Rollback

- Убрать только evidence commit: вернуть private candidate branch к `f8a7f552f3bcc669695cc0554ae5b6f2166b98df`.
- Отбросить candidate целиком: использовать tag `nacho-audit-96658be` на `96658be7eb9e2aeb2a3bfc494377ea1c5debcbf1`.
- До отдельного разрешённого этапа ветку не merge и exact-one run не повторять.

## Оставшийся риск и следующий ход

Неизвестны backend model snapshot, provider seed, temperature, hidden host instructions, денежная стоимость, статистическая значимость и эффект на общей популяции. Оба blinded reviewer identities использовали одну модель и не являются независимыми людьми.

Следующий допустимый ход — отдельная новая гипотеза: убрать генерацию неподдержанных конкретных сцен/provenance и сократить injected input ниже 15%; проверять её можно только новым отдельно разрешённым holdout, без retry этого frozen run.
