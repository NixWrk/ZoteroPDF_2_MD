# Translation Batch Protocol Hardening Plan

**Last updated:** 2026-04-20 (Phase 2 marked implemented; Phase 3 remains planned)
**Scope:** `src/zoteropdf2md/translategemma.py`
**Status:**
- **Phase 1 (v2 id-protocol + cascade):** implemented in commits `4a43d65`, `5cce8e0`.
- **Phase 2 (cascade hardening):** implemented in commit `e8a7435`. См. §0.
- **Phase 3 (abbreviation mask wiring + heading separator):** design, not yet implemented. См. §-1 ниже (новый, критический).

---

## -1. Phase 3: регрессия защиты аббревиатур и сепаратора heading-merge

### -1.1 Симптом (прогон 2026-04-19 по Wang LC Sensor)

Заголовок статьи перевёлся как:

> «Новая схема считывания внутричерепного давления для пассивной беспроводной
> **передачи данных Индуктивно-ёмкостная цепь датчик**»

Оригинал: `A Novel Intracranial Pressure Readout Circuit for Passive Wireless LC Sensor`
(в HTML `<h1>…Passive Wireless <i>LC</i> Sensor</h1>` — 3 текстовых узла).

Три отдельных дефекта в одном заголовке:

1. **«LC» развернулось в «Индуктивно-ёмкостная цепь».** Модель сама расшифровала
   аббревиатуру LC (= Inductor-Capacitor), хотя она должна была остаться как
   `LC` (защищённая Latin-аббревиатура).
2. **«передачи данных» — галлюцинация.** В оригинале после «Passive Wireless»
   идёт «LC Sensor», никаких «data transmission» нет. Модель дописала свой
   smooth-завершение фразы, потому что не получила непрерывного контекста.
3. **«датчик» висит в конце оторванно.** После «Индуктивно-ёмкостная цепь»
   должно было идти согласование (родительный падеж, единое словосочетание),
   но «датчик» пришёл отдельным хвостом — как будто переводился изолированно.

### -1.2 Корневые причины (проверено чтением кода)

**RC1: `_apply_abbrev_mask` определена, но НИГДЕ НЕ ВЫЗЫВАЕТСЯ.**

`src/zoteropdf2md/translategemma.py:441` — функция `_apply_abbrev_mask(text)`
маскирует `\b[A-Z]{2,5}\d*\b` (регексп `_ABBREV_PATTERN`, строка 55) в токены
`<z2m-a id="N"/>`, где N — индекс в сохранённом маппинге. Есть также
`_restore_abbrev_mask` (строка 462) для обратной подстановки. Обе функции —
мёртвый код: `grep '_apply_abbrev_mask|_restore_abbrev_mask'` по файлу даёт
**только определения**, ни одного call-site.

В batch-пути v2 (строки 657-662) применяется только `_apply_formula_mask`:

```python
# translategemma.py:657-662
masked_segs: list[str] = []
fmaps: list[dict[str, str]] = []
for seg in segments:
    masked, fmap = _apply_formula_mask(seg)   # ← только формулы
    masked_segs.append(masked)
    fmaps.append(fmap)
```

Результат: **LC, SNR, ICP, IEEE, VNA, MEMS, FPGA, ADC, GAI, RF, DC, AC, USB,
MIMO и любые другие 2-5-буквенные латинские аббревиатуры физически попадают
в LLM** в исходном виде. Инструкция в промпте («keep uppercase Latin
acronyms») — просьба, а не гарантия; Gemma-4B регулярно её нарушает.

**RC2: U+E001 как сепаратор heading-merge — ненадёжен на выходе модели.**

`_merge_heading_text_nodes` (строка 1083) склеивает текстовые узлы заголовка
через `\uE001` (Private Use Area). Для Wang h1 получается:

```
"A Novel ... Passive Wireless\uE001LC\uE001Sensor"
```

Это **один** сегмент, его батчит id-протокол. Проблема: U+E001 — символ PUA,
не имеющий ни смысла, ни закреплённого токена. Поведение модели на нём
непредсказуемо:

- может выдать его как unknown-token (≈ пустая строка);
- может «склеить» слева и справа текст без сепаратора;
- может интерпретировать как soft-разрыв и породить parallel-перевод
  (именно это и произошло в Wang — модель потеряла связку между фрагментами
  и «дописала» «передачи данных» как окончание первого фрагмента).

При расщеплении в `_split_heading_text_nodes` (строка 1159) код проверяет
`"\uE001" not in merged_text` → значит, если сепаратор потерян, вся
heading-merge гирлянда падает в safety-net:

```python
# translategemma.py:1287
parts = [p.replace("\uE001", " ") for p in parts]
```

→ все три узла Wang получают склеенный-и-плохо-переведённый текст, разнесённый
пробелами. Визуально это и есть «передачи данных Индуктивно-ёмкостная цепь
датчик».

**RC3: `_merge_heading_text_nodes` игнорирует содержимое inline-тегов.**

Функция собирает только **текстовые узлы** между heading-тегами. Для
`<h1>…Passive Wireless <i>LC</i> Sensor</h1>` в обход попадают текстовые
узлы «…Passive Wireless», «LC», «Sensor», но **сам тег `<i>` теряется**
при merge (ведь merged_text = join of text-only). После расщепления
оригинальная структура `<i>LC</i>` восстанавливается только если сепаратор
выжил; иначе inline-тег вставится не туда. Это амплифицирует RC2.

### -1.3 Почему prompt-инструкция не спасает

В `translategemma.py:1500` инструкция просит «keep every sequence of 2 or
more uppercase Latin letters». Но:

- Gemma-4B по-разному слушается на разных входах (особенно на коротких
  фрагментах типа «LC»).
- Расшифровка LC = «Inductor-Capacitor» — устойчивый паттерн в корпусе
  модели; soft-constraint из prompt его не перевешивает.
- Отдельный узел длиной 2 символа («LC») — deadly: модель интерпретирует
  его как «переведи аббревиатуру».

**Hard-маска (токен-плейсхолдер) — единственный надёжный способ.** Она уже
реализована (`_apply_abbrev_mask`), но не подключена. Подключение —
одностраничная правка.

### -1.4 План исправления (Phase 3, три шага)

**Шаг X1 — подключить `_apply_abbrev_mask` в batch-путь (obligatory).**

В `_try_batch_translate_with_reason` (строки 648-758) после `_apply_formula_mask`
применять `_apply_abbrev_mask`. Соответственно — в обратном порядке при
восстановлении. Порядок важен: формулы маскируются первыми (внутри них
могут быть capitalised latin tokens, которые не должны трактоваться как
аббревиатуры).

```python
# Новый цикл preparation (на месте строк 657-662):
masked_segs: list[str] = []
fmaps: list[dict[str, str]] = []
amaps: list[dict[str, str]] = []
for seg in segments:
    masked, fmap = _apply_formula_mask(seg)
    masked, amap = _apply_abbrev_mask(masked)   # ← новое
    masked_segs.append(masked)
    fmaps.append(fmap)
    amaps.append(amap)

# В пост-обработке (переработать существующий зависимый код):
#   core = _restore_abbrev_mask(core, amap)     ← новое, ПЕРЕД formula-restore
#   core = _restore_formula_mask(core, fmap)
```

Также надо добавить валидацию placeholder-integrity для abbrev-токенов
(аналогично существующей валидации `_FORMULA_TOKEN_PATTERN` в строках
736-746): если модель дропнула/продублировала `<z2m-a id="N"/>` — fail
окно c reason `abbrev_placeholder_mismatch`, чтобы cascade мог бисекцией
свернуть в leaf per-segment.

Аналогично подключить в fallback-путь `_translate_single_segment` (строки,
где вызывается formula-маска на одиночный сегмент). Это закроет дыру и
для per-segment режима, не только batch.

**Шаг X2 — заменить U+E001 heading separator на защищённый XML-токен.**

Заменить `"\uE001"` на `"<z2m-h-sep/>"` (или `<z2m-h{idx}/>` с id,
аналогично id-протоколу). Причины:

- Gemma-4B уже умеет сохранять самозакрывающиеся XML-теги (это её
  повседневный вход с `<z2m-i{n}/>`).
- Self-closing XML-тег не ломает lattice токенизации (он токенизируется
  как 4-5 обычных ASCII-токенов, которые модель уважает).
- Вероятность дропа на порядок ниже, чем у PUA-символа.

Изменения:

```python
# translategemma.py:1144 (_merge_heading_text_nodes)
merged_text = "<z2m-hsep/>".join(texts_to_merge)

# translategemma.py:1187 (_split_heading_text_nodes)
if not merged_text or "<z2m-hsep/>" not in merged_text:
    ...
split_texts = merged_text.split("<z2m-hsep/>")

# safety-net (строки 1287, 1342):
parts = [re.sub(r"<z2m-hsep\s*/>", " ", p) for p in parts]
```

Поскольку `<z2m-hsep/>` — это уже XML-подобный токен, он пройдёт через
split-regex `_TAG_SPLIT_PATTERN` как обычный тег. Значит надо
**исключить** его из `_TAG_SPLIT_PATTERN` split (или обрабатывать до split),
чтобы он не считался структурным тегом. Вариант: склеивать через текст
`@@Z2M_HSEP@@` (plain ASCII, тоже стабильно токенизируется, но не лезет
в tag-aware regex).

**Рекомендую: `@@Z2M_HSEP@@`** — ASCII-sentinel, не попадает под
`<[^>]+>`, не требует корректировки других regex'ов, имеет уникальную
форму «@@» которую модель почти не производит спонтанно.

**Шаг X3 — валидация на post-translation: обратный grep.**

После восстановления масок добавить пост-чек: если в переведённом сегменте
встречается одна из Russian-развёрток защищённых аббревиатур (например
«Индуктивно-ёмкостная цепь», «виртуальный сетевой анализатор»,
«внутричерепное давление» ← осторожно, ICP = intracranial pressure,
тут развёртка — **валидна**), **И** соответствующая аббревиатура отсутствует
в оригинальном сегменте, — flag как прошедшая через prompt-leak, вызвать
pre-segment fallback.

Этот шаг менее приоритетный, потому что Шаг X1 должен закрыть подавляющее
большинство кейсов. Включить, если после X1+X2 в логах всё ещё появляются
Russian-развёртки.

### -1.5 Явный invariants

1. **Abbrev-маска применяется ПЕРЕД id-обёрткой.** Порядок:
   `segment → formula-mask → abbrev-mask → id-wrap → LLM → parse → abbrev-restore → formula-restore`.
2. **`<z2m-a id="N"/>` и `<z2m-i{n}/>` — раздельные namespaces.** Парсер
   id-протокола (`_BATCH_ITEM_PATTERN`, строка 341) не должен случайно
   матчить abbrev-токены. Текущий regex `<z2m-i(\d+)\s*/>` это уже
   обеспечивает (разные префиксы `-a` vs `-i`).
3. **Heading-separator `@@Z2M_HSEP@@` не конфликтует с `_TAG_SPLIT_PATTERN`.**
   ASCII-sentinel не попадает под `<[^>]+>`, split работает как раньше.
4. **Валидация placeholder-integrity — в том же месте, что и
   formula-integrity.** Если any abbrev-токен потерян/дублирован — fail
   окно, дать cascade свернуться в leaf-per-segment.

### -1.6 Acceptance criteria Phase 3

| Метрика | Сейчас | Цель |
|---|---|---|
| `grep -c 'LC Индуктивно\|Индуктивно-ёмкостная' RU HTML` | ≥1 в Wang | 0 |
| `grep -c 'виртуальный сетевой анализатор' RU HTML` (VNA) | возможен | 0 |
| `grep -E '\b(LC\|SNR\|ICP\|VNA\|IEEE\|FPGA\|MEMS\|ADC)\b' RU HTML` | < ожидаемого | ≥ числа вхождений в EN |
| Заголовок Wang `<h1>` | «LC Индуктивно-ёмкостная цепь датчик» | содержит подстроку «LC» или «LC-датчик(а)» |
| Логи cascade на Wang | 0 инцидентов (но качество плохое) | 0 инцидентов (качество OK) |

### -1.7 Тесты (`tests/test_translategemma_html.py`)

1. Вход `"A novel LC sensor operating at 5 MHz"`, fake `translate_text`
   который возвращает вход буквально → ожидать, что в результате `LC`
   осталась `LC` (а не «Индуктивно-ёмкостная цепь»).
2. Вход `"<h1>Wang <i>LC</i> sensor</h1>"` через полный путь
   `translate_html_text_nodes` с fake, сохраняющим все токены →
   ожидать, что `<i>LC</i>` остаётся в результирующем HTML, а
   `@@Z2M_HSEP@@` нигде не утекает в вывод.
3. Fake `translate_text`, дропающий `<z2m-a id="N"/>` у одного из
   segment'ов → ожидать reason `abbrev_placeholder_mismatch` и
   срабатывание cascade (свёртка в leaf per-segment для этого сегмента,
   но не глобальный fallback).
4. Регресс: `"ICP monitoring"` (ICP — защищённая) должно остаться как
   «ICP мониторинг», а НЕ превратиться в «внутричерепное давление
   мониторинг» (сейчас так и происходит в ряде мест Teo).

### -1.8 Критические файлы

- `src/zoteropdf2md/translategemma.py:441-476` — существующие
  `_apply_abbrev_mask` / `_restore_abbrev_mask` (готовы, надо подключить).
- `src/zoteropdf2md/translategemma.py:648-758` —
  `_try_batch_translate_with_reason` (Шаг X1: добавить abbrev-маску в
  prep-цикл + restore в post-loop + placeholder-integrity check).
- `src/zoteropdf2md/translategemma.py:1083-1200` —
  `_merge_heading_text_nodes`/`_split_heading_text_nodes` (Шаг X2:
  заменить `\uE001` на `@@Z2M_HSEP@@`).
- `src/zoteropdf2md/translategemma.py:1287, 1342` — safety-net
  replace (Шаг X2: обновить).
- `src/zoteropdf2md/abbreviations.py` — словарь (если нужно расширить
  защиту: добавить LC, SNR, ICP, VNA, IEEE, FPGA, MEMS если их там
  ещё нет).
- `tests/test_translategemma_html.py` — 4 теста (§-1.7).

### -1.9 Порядок работ Phase 3 относительно Phase 2

Два Phase'а **независимы**:

- Phase 2 чинит **устойчивость** cascade (не роняться глобально).
- Phase 3 чинит **качество** перевода (не терять смысл аббревиатур).

Рекомендуемый порядок:

1. **Phase 3 первой** — она выше по impact на пользовательский результат.
   Один прогон без fallback, но с «Индуктивно-ёмкостная цепь» в заголовке
   хуже, чем прогон с fallback, но с правильным «LC».
2. **Phase 2 второй** — подчищает оставшийся риск срыва cascade.

Оба Phase можно мерджить раздельно, тесты изолированы.

---


## 0. Phase 2: результаты v2 и статус реализации

**Status update (2026-04-20):** Шаги A+B+C из §0.4 реализованы в коммите
`e8a7435` (`Harden windowed translation cascade and add trailing-eos lenient recovery`).
План в этом разделе сохраняется как дизайн-обоснование и acceptance-база.

### 0.1 Что подтвердилось

v2 id-протокол работает: positional loss `<z2m-sep/>` убран, структурный парсер
по `<z2m-i{n}/>` корректно детектирует потерю/дубли ids, cascade частично
восстанавливает окно через bisect. Ссылки/URL/якоря не пострадали (подтверждено
архитектурно в §2 ниже: `_SKIP_TRANSLATION_TAGS` физически не пускает их в LLM).

### 0.2 Что всё равно уронило документ в глобальный per-segment

Прогон 2026-04-18 11:59, файл `Wang et al. - 2017 … LC Sensor.html`:

```
[FALLBACK] reason=window_failed core=[84:86) extended=[83:87)
           reason=retry_failed id_mismatch missing=[3,4] extra=[]
                              | id_mismatch missing=[3,4] extra=[]
```

Картина:

1. Внешнее окно (8 сегментов) сломалось → cascade запустил bisect.
2. Bisect дошёл до core размером 2 (`[84:86)`, extended `[83:87)` из-за overlap=1).
3. Обе попытки (L2 retry) вернули **один и тот же** `missing=[3,4]` —
   модель остановилась на EOS после ids 1,2 (хвост окна обрезан).
4. В `_try_windowed_batch_translate_with_reason` (translategemma.py:810)
   срабатывает `if core_len <= 2: return False, "window_failed ..."` —
   bisect дальше не идёт, ядро из 2 сегментов **не** обрабатывается
   per-segment локально, и весь документ (306 сегментов) уходит в
   глобальный per-segment fallback.

### 0.3 Три корневые причины

**C1. Bisect отказывается делить ядра размера ≤ 2.**
Условие `core_len <= 2` → `window_failed`. Вместо локального L4 (перевести
эти 1–2 сегмента per-segment и записать в `translated[core_start:core_end]`)
cascade капитулирует и зовёт глобальный fallback для **всех** N сегментов.

**C2. L2 retry с `temperature=0` бесполезен.**
Декод детерминирован: при одинаковом промпте ответ побайтово одинаков.
В логе это видно попарно: LLM 20/21, 23/24, 25/26 — все пары `chars=…`
идентичны, обе «done» за близкое время, обе с тем же mismatch. Retry в
текущем виде просто удваивает стоимость.

**C3. Lenient recovery допускает пропуск только 1 id.**
Here-and-now модель теряет **хвост** (ids 3,4 подряд — classic early-EOS).
`lenient_missing_limit = len(segments) // 10` для окна из 4 = 0, и даже для
окна из 10 разрешён только 1 пропуск. Когда модель детерминированно режет
последний сегмент-два, lenient не включается.

### 0.4 План Phase 2 (минимальный, в три шага)

**Шаг A — локальный per-segment на дне bisect (obligatory).**
В `_try_windowed_batch_translate_with_reason`, вместо
`if core_len <= 2: return False, "window_failed"` делать:

```
for idx in range(core_start, core_end):
    # перевести сегмент в одиночку, использовать существующий
    # per-segment путь (mask/unmask, refusal-guard и т. п.)
    translated[idx] = _translate_single_segment(segments[idx], translate_text)
return True, "ok_leaf_per_segment"
```

Эффект: срыв окна теряет **до 2 сегментов** (которые всё равно переведутся,
просто в изоляции), а не весь документ. `per_segment_fallback_rate` у
документа становится долей 2/N вместо 1.0. Это и есть оригинальный
L4, но локальный, как и задумывалось в §5 исходного плана.

**Шаг B — убрать холостой L2 retry.**
Заменить `for _ in range(2): …` на один вызов. Retry добавить обратно
**только** если появится источник реальной вариативности: либо
`do_sample=True, temperature=0.2–0.4` на второй попытке, либо переформулировка
промпта (например, явно перечислить ожидаемые ids в инструкции). Без этого
retry — чистая потеря времени и токенов.

**Шаг C — Lenient для trailing-EOS.**
Расширить lenient-ветку: если `missing_ids` — это суффикс
`[len-K+1, …, len]` (подряд идущие последние K ids), `extra_ids` пусто,
и `K ≤ max(1, len(segments) // 3)` — подставить оригиналы для этих ids и
пометить `ok_lenient_trailing_eos k={K}`. Логика безопасна: оригинал
сегмента валиден как fallback (как и в существующем single-missing
lenient пути).

### 0.5 Опциональные улучшения (если после A+B+C fallback > 5%)

- Снизить `_WINDOW_BATCH_TARGET_SEGMENTS` с 8 до 5–6. Вероятность
  early-EOS на хвосте растёт с длиной окна.
- Добавить терминатор `<z2m-end/>` после последнего сегмента в промпте.
  Модели легче «дождаться» эксплицитного маркера, чем угадывать
  момент остановки.
- Sampling retry (`temperature=0.3`, `top_p=0.9`) как Шаг B-alt.

### 0.6 Acceptance criteria Phase 2

| Метрика | v2 сейчас | Цель Phase 2 |
|---|---|---|
| Глобальный per-segment fallback на документ | возможен при ≥1 failed leaf | **0** (только локальные leaf-перевод) |
| Сегментов, переведённых per-segment | весь документ при срыве | ≤ 2 × (число упавших окон) |
| Лишних LLM-вызовов от L2 retry | +1 на каждый упавший batch | 0 (retry убран) |
| `ok_lenient_trailing_eos` | недоступно | работает при потере ≤ ⅓ хвоста |

### 0.7 Тесты

Расширить `tests/test_translategemma_html.py`:

1. Fake `translate_text`, который для окна из 4 сегментов возвращает
   ответ только с ids 1,2 → ожидаем, что итоговый результат содержит все
   4 перевода (2 от batch + 2 от leaf per-segment), не `None`.
2. Fake, возвращающий trailing `missing=[N-1,N]` для окна из 6 → ожидаем
   `ok_lenient_trailing_eos k=2`.
3. Fake, всегда роняющий batch (любой id_mismatch) → ожидаем, что функция
   возвращает **полный** результат через leaf per-segment, а не `None`.
4. Retry-счётчик: fake считает число вызовов; для окна, которое сразу
   успешно парсится, число вызовов = 1 (а не 1 успех + 1 холостой).

---

## 1. Проблема: позиционная хрупкость `<z2m-sep/>`
(исторический раздел: описывает v1-протокол до коммита `4a43d65`.)


### 1.1 Что происходит сегодня

Batch-перевод собирает N текстовых сегментов в одну строку через
`<z2m-sep/>` разделитель и шлёт модели за один вызов:

```python
# translategemma.py:338
_BATCH_SEPARATOR = "\n<z2m-sep/>\n"

# translategemma.py:528
batch_text = _BATCH_SEPARATOR.join(masked_segs)
translated_batch = translate_text(batch_text)
translated_parts = _BATCH_SEP_PATTERN.split(translated_batch)

# translategemma.py:545
if len(translated_parts) != len(segments):
    return None, f"separator_mismatch expected={len(segments)} got={len(translated_parts)}"
```

Если модель дропнула или дублировала **хотя бы один** `<z2m-sep/>`,
количество кусков не совпадёт — весь window отбрасывается, и
`_try_windowed_batch_translate_with_reason` возвращает `None`, что в
`translate_html_text_nodes` ведёт к **полному fallback на per-segment**:

```
[12:57:17] TranslateGemma progress: 306/306 segments (100%)
[12:57:17] TranslateGemma failed: ... separator_mismatch expected=10 got=9
           → весь документ переведён в fallback (306 отдельных LLM-вызовов)
```

### 1.2 Почему это не просто «медленнее»

1. **Качество перевода падает**: per-segment режим переводит каждый
   узел в изоляции. Глоссарий, тон, стилистика расплываются по
   статье, падежи и согласования рвутся (эффект аналогичен BUG-B
   «Sensor → Датчик»).
2. **x10–x40 медленнее**: 306 вызовов вместо 30–40 batch-окон.
3. **Больше рисков галлюцинации**: prompt-leak, meta-commentary,
   повторения — всё чаще встречаются на коротких single-segment
   вызовах.

### 1.3 Почему модель теряет разделители

* `<z2m-sep/>` — это **одна** уникальная последовательность токенов.
  Если модель «решила» вставить перевод фразы через запятую / тире,
  она скорее склеит два сегмента, чем напишет `\n<z2m-sep/>\n`.
* Positional decode означает: **любая единичная ошибка ломает весь
  window целиком**. Нет частичного recovery.
* При overlap=1 и window=8 окно получает 9–10 сегментов; чем больше
  сегментов, тем выше вероятность потери хотя бы одного разделителя.

---

## 2. Что находится «в опасной зоне»? → **Ничего, кроме plain-text**

Критичный архитектурный факт, который снимает большинство опасений:

### 2.1 Как работает текущий split HTML

```python
# translategemma.py:32
_TAG_SPLIT_PATTERN = re.compile(r"(<[^>]+>)")

# translategemma.py:36
_SKIP_TRANSLATION_TAGS = {
    "script", "style", "code", "pre", "math", "svg", "a", "sup", "sub"
}

# translategemma.py:1001
parts = _TAG_SPLIT_PATTERN.split(html)
```

`parts[]` после split — **чередование** текстовых узлов и HTML-тегов:

```
[text, "<h1>", text, "<i>", text, "</i>", text, "</h1>", text, "<sup>", ...]
```

В batch-перевод попадают **только text-узлы**, и только те, что **не
внутри** `_SKIP_TRANSLATION_TAGS`.

### 2.2 Что это значит на практике

| Элемент | Попадает в batch? | Перевод может сломать? |
|---------|-------------------|------------------------|
| `<sup><a href="#ref-12">12</a></sup>` | ❌ нет, это теги | ❌ невозможно |
| `<a href="https://example.com">text</a>` | ❌ нет, `a` в skip-list | ❌ невозможно |
| Атрибуты `id="ref-42"`, `href="#ref-42"` | ❌ нет, внутри открывающего тега | ❌ невозможно |
| `<img src="data:image/..." alt="...">` | ❌ нет, атрибуты | ❌ невозможно |
| `<figure>`, `<math>`, `<code>` | ❌ нет, skip-list или структура | ❌ невозможно |
| Plain-text «as shown in Figure 2» | ✅ да | ⚠️ да, **только смысл текста** |

Другими словами: **смена batch-протокола не может потерять ссылки, URL,
якоря, id или атрибуты**. Эти элементы физически не доходят до
модели — они живут в `parts[i]` с чётной индексацией (теги) и
пересобираются обратно через `"".join(parts)` нетронутыми.

### 2.3 Citation linking работает ПОСЛЕ перевода, на собранном HTML

`_add_reference_ids_and_citation_links()`, `_link_paren_ref_citations()`,
`_autolink_plain_urls()` и т.п. вызываются в `polish_html_document()`
поверх уже собранного (переведённого) HTML. Они работают как на EN,
так и на RU и не зависят от того, как batch-протокол
организован — лишь бы результат был валидным HTML с сохранённой
структурой.

**Вывод:** любые изменения в batch-протоколе безопасны по отношению к
ссылкам, URL, атрибутам и якорям библиографии.

---

## 3. Цели улучшения

1. Убрать **позиционную** fragility: потеря одного маркера не должна
   ронять весь window.
2. **Сохранить context-window** как можно дольше: не скатываться в
   per-segment при первой же проблеме.
3. **Явная валидация** структуры вывода (coverage, uniqueness, formula
   placeholders).
4. **Reason-rich diagnostics** для каждого уровня fallback — чтобы
   видеть, какие кейсы доминируют в реальных прогонах.

---

## 4. Решение

### 4.1 Замена протокола: id-addressed XML-marker

**Старый формат** (positional):
```
Paragraph one text.
<z2m-sep/>
Paragraph two text.
<z2m-sep/>
Paragraph three text.
```

**Новый формат** (id-addressed, self-closing XML-tag):
```
<z2m-i1/>Paragraph one text.
<z2m-i2/>Paragraph two text.
<z2m-i3/>Paragraph three text.
```

Parsing на выходе — `re.findall(r'<z2m-i(\d+)/>([\s\S]*?)(?=<z2m-i\d+/>|\Z)', out)`.

### 4.2 Почему именно этот формат

| Вариант | Плюсы | Минусы |
|---------|-------|--------|
| `[1] text [2] text` | компактно | конфликт с bracket-citations `[5]` в теле статей |
| `### 1\ntext\n### 2\ntext` | markdown-natural | модель может вставить свои `###` при переводе |
| `{"1": "...", "2": "..."}` JSON | строгий формат | LLM часто ломает JSON (кавычки в тексте) |
| **`<z2m-i1/>text`** | ✅ совпадает с уже знакомым моделью `<z2m-sep/>`; ✅ нулевая вероятность коллизии с обычным научным текстом; ✅ однозначный regex | токены — ~3–4 штуки на маркер |
| `<!--1-->text` | нейтральный HTML | модель может удалить как «мусор» |

**Выбор: `<z2m-i{n}/>`** как прямое расширение текущего именования.
Модель уже видела `<z2m-sep/>` и `<z2m-p id=…/>` (prompt-leak mask) —
новый токен того же семейства встраивается естественно.

### 4.3 Преимущества перед `<z2m-sep/>`

1. **Потеря одного маркера ≠ потеря всех.** Если модель дропнет
   `<z2m-i5/>`, сегменты 1–4 и 6–N парсятся корректно. Потерян
   только один сегмент — его можно восстановить локально.
2. **Duplicate detection.** Если модель повторила `<z2m-i3/>` дважды —
   это тоже детектируется (uniqueness check), а не молча смешивает
   содержимое.
3. **Out-of-range detection.** Модель могла придумать `<z2m-i99/>` при
   N=10 — отловится как `id_out_of_range`.
4. **Coverage check.** Считаем множество id в выводе; если `set !=
   expected` — точно знаем, какие сегменты пропали.

---

## 5. Cascade восстановления (4 уровня)

Порядок попыток для каждого window:

### Level 1: Lenient recovery (без LLM вызова)

До того, как считать window неудачным:

* **Off-by-one match.** Если `len(parsed) == N-1` и все найденные id
  составляют непрерывный диапазон `[1..k-1, k+1..N]` — считаем что
  модель дропнула один маркер. Вставляем оригинальный (непереведённый)
  текст для пропущенного id как fallback и логируем
  `lenient_recovery_level=off_by_one_missing id=k`.
* **Split heuristic** (опционально): если удалось найти (N-1) маркеров,
  но между двумя соседними id сидит текст длиной примерно равной
  сумме оригинальных длин двух сегментов — можно попробовать
  heuristic split по длине (менее надёжно, логируем отдельно).

Stopping rule: lenient recovery допускается только при потере **≤
10%** сегментов окна. Иначе сразу переходим к Level 2.

### Level 2: Retry (один повтор того же окна)

Если парсер упал или lenient не применим:

* Повторяем вызов `translate_text(batch_text)` **один раз** с теми же
  параметрами.
* Логируем `retry_attempt=1 reason=<original_reason>`.
* Если retry тоже провалился — Level 3.

### Level 3: Bisect (рекурсивное разделение core-окна)

* Разбиваем `core_segments` пополам: `[0:N/2]` и `[N/2:N]`.
* Каждая половина переводится с own overlap (overlap сегментов из
  соседних половин).
* Рекурсивный вход в cascade (Level 1–3) с меньшим окном.
* **Invariant:** bisect **не режет merged heading nodes** (см. §7).
* Минимальный размер окна после bisect — 2 сегмента. Ниже — Level 4.

### Level 4: Per-segment fallback (последний уровень)

Существующий `_translate_text_segment()` для каждого оставшегося
сегмента. Логируется с метрикой сколько сегментов в итоге оказалось
на этом уровне: `final_fallback_segments=N`.

### Сводная схема

```
translate_window(segments)
  ↓
[L1] parse id-markers + lenient recovery (off-by-one)
  ↓  success? → return
[L2] retry_once
  ↓  success? → return
[L3] bisect into halves → recursive translate_window
  ↓  all halves succeeded? → return
[L4] per-segment fallback for unrecoverable tail
```

---

## 6. Валидация parse-результата

После парсинга id-blocks выполняются три проверки **до** возврата
результата:

| Проверка | Условие провала | Reason-код |
|----------|-----------------|------------|
| Structured parse | `re.findall(...)` вернул 0 matches | `structured_parse_failed` |
| Coverage | `set(found_ids) != set(expected_ids)` | `id_mismatch missing=... extra=...` |
| Uniqueness | `len(found_ids) != len(set(found_ids))` | `duplicate_ids ids=...` |
| Placeholder integrity | для каждого сегмента: формулы из `_apply_formula_mask` сохранены в переводе один-к-одному | `placeholder_mismatch seg=k expected=X got=Y` |

При любом провале — cascade переходит к следующему уровню.

**Placeholder integrity** особенно важна: если модель «перевела»
`__FORMULA_MASK_7__` как `__ФОРМУЛА_МАСКА_7__` или вставила её не
туда — мы этого хотим дождаться, а не молча выводить криво.

---

## 7. Invariants и корректность взаимодействия с существующим кодом

### 7.1 Heading merge (BUG-B, commits 6af0ddb/93057f2)

`_merge_heading_text_nodes()` объединяет несколько text-nodes внутри
одного `<h1>-<h6>` в **один сегмент** через Unicode PUA separator
`\uE001`. Для batch-протокола это **прозрачно**: merged node идёт
как обычный сегмент с одним id.

**Invariant 1:** `bisect` **не должен** разрывать merged heading
nodes. В `_merge_heading_text_nodes()` уже возвращается `heading_merges`
dict — использовать его, чтобы пометить merged indices как
«bisect-atomic». При разбиении core — соблюдать границы atomic-групп.

### 7.2 Overlap context

Текущая схема `overlap_segments=1` добавляет 1 сегмент слева и
справа для context. В id-протоколе overlap реализуется так же:
batch-окно состоит из `[ext_start:ext_end]`, id=1 присваивается
первому сегменту в extended, результаты overlap-сегментов
**отбрасываются** при сборке финального перевода (берутся только
core segments).

**Invariant 2:** id нумерация в batch стабильна и соответствует
positional индексу в `segments[ext_start:ext_end]`. Overlap
сегменты получают id=1 и id=N (крайние), core — всё что между.

### 7.3 Formula masks

`_apply_formula_mask()` заменяет LaTeX/math на токены
`__Z2M_FM_N__`. Эти токены должны пройти через модель неизменёнными.

**Invariant 3:** Placeholder integrity check (§6) применяется **до**
`_restore_formula_mask()`. Если проверка не прошла — cascade
переходит на следующий уровень, а не пытается восстановить
полуразрушенные формулы.

### 7.4 Prompt-leak mask

`_apply_prompt_leak_mask()` заменяет опасные строки (списки
аббревиатур) на токены `<z2m-p id="N"/>`. Аналогично formulas —
эти токены должны пройти через модель и восстановиться после.

**Invariant 4:** id-протокол batch (`<z2m-iN/>`) и prompt-leak
(`<z2m-p id="N"/>`) используют **разные** regex-namespace — коллизии
невозможны. Парсинг id-блоков использует строго `<z2m-i(\d+)/>` (без
атрибутов, без whitespace), prompt-leak — `<z2m-p id="(\d+)"/>`
(с атрибутом).

### 7.5 Existing polish steps (citation linking, URL auto-link)

`polish_html_document()` работает **после** batch/fallback, на уже
собранном HTML. Изменение batch-протокола не влияет на работу
`_add_reference_ids_and_citation_links`, `_autolink_plain_urls`,
`_link_paren_ref_citations` и прочих — они видят только финальный
HTML.

**Гарантия:** ни одна ссылка, URL, attr, id-якорь **не может
пострадать** от изменений в batch-протоколе. Весь линкинг
ответственен за себя, независимо от способа перевода.

---

## 8. Telemetry и диагностика

Каждый батч-перевод лога события с уровнями:

```
batch_ok           window=[12:20) ids=8/8 formulas=3/3
retry_success      window=[12:20) reason=separator_mismatch level=retry
lenient_recovery   window=[12:20) reason=off_by_one_missing missing=[5] level=lenient
bisect             window=[12:20) split=[12:16)+[16:20) level=bisect_depth=1
final_fallback     window=[12:20) segments=8 level=per_segment
```

В summary-логе файла — агрегат:

```
TranslateGemma summary:
  total_windows=42 total_segments=306
  L1_lenient=3  L2_retry_success=5  L3_bisect=2  L4_per_segment=0
  recovery_rate=100%  per_segment_fallback_rate=0%
```

Это позволяет в будущих прогонах видеть, **какие уровни cascade
срабатывают чаще всего** и решать, нужны ли дальнейшие улучшения
(например, увеличить retry до 2, добавить temperature=0 на retry,
и т.д.).

---

## 9. Non-Goals (в этом патче)

1. **Document-level glossary harmonization.** Отдельная задача —
   собрать глоссарий терминов из первых N сегментов документа и
   передавать в prompt как справочник. Планируется как follow-up.
2. **JSON-only response protocol.** JSON с LLM — фрагмильная идея
   (кавычки в тексте, экранирование, trailing commas). id-based XML
   tags дают почти ту же строгость при большей устойчивости.
3. **Per-document temperature tuning.** Сейчас temperature фиксирована;
   подбор temperature per retry — отдельный эксперимент.

---

## 10. Implementation plan (поэтапно)

### Phase 1 — id-protocol core
1. Добавить `_BATCH_ID_MARKER_FORMAT = "<z2m-i{n}/>"`.
2. Переписать `_try_batch_translate_with_reason`:
   * сборка: `"\n".join(f"{marker(i+1)}{seg}" for i, seg in enumerate(masked_segs))`;
   * parse: `_ID_BLOCK_PATTERN = re.compile(r'<z2m-i(\d+)/>([\s\S]*?)(?=<z2m-i\d+/>|\Z)')`;
   * валидация coverage + uniqueness.
3. Старые константы `_BATCH_SEPARATOR` и `_BATCH_SEP_PATTERN` — удалить.
4. Обновить prompt (если нужна явная инструкция «сохраняй маркеры
   `<z2m-iN/>`»).

### Phase 2 — cascade
1. Расширить `_try_windowed_batch_translate_with_reason`:
   * L1 lenient recovery (off-by-one).
   * L2 retry_once.
   * L3 bisect (рекурсия с min_window=2).
2. Добавить atomic-group check: не резать merged heading nodes.

### Phase 3 — validation
1. Placeholder integrity check (formulas).
2. Reason-rich logging (см. §8).

### Phase 4 — tests
Unit-тесты в `tests/test_translategemma_html.py`:

* `test_id_protocol_decode_success` — прямой случай, все id на месте.
* `test_id_protocol_off_by_one_lenient` — дропнутый маркер восстанавливается из оригинала.
* `test_id_protocol_duplicate_ids_triggers_retry` — duplicate → L2.
* `test_id_protocol_bisect_recovery` — L2 падает, L3 success на половинах.
* `test_id_protocol_placeholder_mismatch` — формула потерялась → fallback.
* `test_id_protocol_preserves_heading_merge` — merged heading node не разбивается bisect.
* Regression test: citation `<sup><a href="#ref-N">` не меняется после смены протокола.

---

## 11. Acceptance criteria

1. ✅ Существующие тесты проходят.
2. ✅ Новые тесты (см. §10 Phase 4) зелёные.
3. ✅ На реальных прогонах (Wang + Teo):
   * `per_segment_fallback_rate ≤ 5%` (было ~100% в последнем прогоне Teo).
   * Все internal references (`href="#ref-N"`) работают.
   * Все external URLs (`<a href="https://...">`) работают.
   * Figure anchors (`href="#fig-N"`) работают.
4. ✅ Telemetry в логе чётко показывает, какой уровень cascade
   сработал на каждом window.
5. ✅ Никаких регрессий по BUG-A/BUG-B (ref-number single, heading
   переводится корректно).

---

## 12. Ответ на вопрос «не потеряем ли ссылки?»

**Короткий ответ: нет, принципиально не потеряем.**

**Обоснование:**

1. `_SKIP_TRANSLATION_TAGS` исключает `a`, `sup`, `sub` и их
   содержимое из batch. Теги и атрибуты физически **не попадают** в
   модель — они живут в `parts[]` между текстовыми сегментами и
   собираются нетронутыми через `"".join(parts)`.
2. Изменение batch-протокола касается **только формата** передачи
   plain-text узлов модели. Структура HTML, теги `<a>`, атрибуты
   `href`, `id` не трогаются этим изменением.
3. Citation linking (`_add_reference_ids_and_citation_links`, …) и URL
   autolink работают **после** перевода, на уже собранном HTML. Они
   независимы от batch-протокола.
4. Heading merge (BUG-B) использует separator `\uE001` **внутри**
   одного сегмента — это orthogonal уровень к batch-протоколу и не
   конфликтует с новым id-маркером.

Единственный риск — **качество перевода самого текста**, но он как
раз улучшается за счёт резкого снижения per-segment fallback (с
~100% для Teo до ≤5%).
